import os
import time
import uuid
import logging
import json
from datetime import datetime

import anthropic
from azure.search.documents import SearchClient
from azure.search.documents.models import QueryType
from azure.core.credentials import AzureKeyCredential

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are the administrative assistant for Midwest Aikido Center (MAC), a traditional Aikido dojo in Chicago, IL. You help board members, instructors, and staff find information from dojo records: board meeting minutes, bylaws, member data, email communications, event calendars, and broader Aikido resources from the US Aikido Federation and Aikikai.

Use retrieved documents as your primary source. For questions about Aikido history, lineage, or etiquette not in those documents, draw on your broader knowledge — but distinguish clearly between dojo records and general knowledge.

Tone: warm, respectful, and professional. Be direct and clear. Avoid flowery language, hyper-enthusiastic phrasing, and unnecessary filler. Write like a trusted colleague.

Format: use markdown for structure when it aids clarity. Keep responses concise. Cite the source document, date, or URL when relevant.

If a specific policy, price, date, or name is not found in the provided documents, do not speculate. Say: "I cannot find that specific detail in our records. Could you provide it, or would you like me to search more broadly?"

Treat "Fall" and "Autumn" as equivalent when referring to seminars or events."""

SEARCH_TOOL = {
    "name": "search_documents",
    "description": (
        "Search the Midwest Aikido Center document archive — emails, board meeting minutes, "
        "bylaws, member records, and calendar events — for relevant information. "
        "Call this whenever the user asks about dojo policies, events, members, financials, "
        "communications, or any specific factual question about MAC."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A concise, targeted search query"
            }
        },
        "required": ["query"]
    }
}

_claude = None
_search = None


def _get_claude():
    global _claude
    if _claude is None:
        _claude = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _claude


def _get_search():
    global _search
    if _search is None:
        service = os.environ.get("AZURE_SEARCH_SERVICE", "")
        key = os.environ.get("AZURE_SEARCH_KEY", "")
        index = os.environ.get("AZURE_SEARCH_INDEX", "bod-emails")
        endpoint = f"https://{service}.search.windows.net"
        _search = SearchClient(endpoint, index, AzureKeyCredential(key))
    return _search


async def _execute_search(query: str) -> str:
    try:
        client = _get_search()
        top_k = int(os.environ.get("AZURE_SEARCH_TOP_K", "5"))
        semantic_config = os.environ.get("AZURE_SEARCH_SEMANTIC_SEARCH_CONFIG", "default")

        results = client.search(
            search_text=query,
            query_type=QueryType.SEMANTIC,
            semantic_configuration_name=semantic_config,
            select=["content", "metadata_storage_name", "metadata_storage_path", "document_date"],
            top=top_k,
        )

        snippets = []
        for r in results:
            doc_date = r.get("document_date")
            date_str = (
                datetime.fromisoformat(str(doc_date)).strftime("%B %d, %Y")
                if doc_date else "Unknown date"
            )
            name = (r.get("metadata_storage_name") or "Unknown").replace(".txt", "")
            content = (r.get("content") or "").strip()
            snippets.append(f"[Source: {name} | Date: {date_str}]\n{content}")

        if not snippets:
            return "No relevant documents found for that query."

        return "\n\n---\n\n".join(snippets)

    except Exception as e:
        logging.exception("Search error")
        return f"Search failed: {str(e)}"


def _convert_messages(messages: list) -> list:
    """Convert frontend ChatMessage list to Anthropic message format."""
    result = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role not in ("user", "assistant"):
            continue
        if not content:
            continue
        if isinstance(content, list):
            # Take text parts only
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(text_parts).strip()
            if not content:
                continue
        result.append({"role": role, "content": str(content)})

    # Anthropic requires alternating user/assistant; deduplicate consecutive same-role
    clean = []
    for msg in result:
        if clean and clean[-1]["role"] == msg["role"]:
            # Merge consecutive same-role messages
            clean[-1]["content"] += "\n" + msg["content"]
        else:
            clean.append({"role": msg["role"], "content": msg["content"]})

    # Must start with user
    while clean and clean[0]["role"] != "user":
        clean.pop(0)

    return clean


def _make_chunk(msg_id: str, text: str, history_metadata: dict) -> dict:
    return {
        "id": msg_id,
        "model": MODEL,
        "created": int(time.time()),
        "object": "chat.completion.chunk",
        "choices": [{"messages": [{"role": "assistant", "content": text}]}],
        "history_metadata": history_metadata,
        "apim-request-id": "",
    }


def _today_str() -> str:
    return datetime.now().strftime("%B %d, %Y")


async def stream_response(messages: list, history_metadata: dict):
    """Async generator yielding NDJSON-compatible dicts for the frontend."""
    msg_id = str(uuid.uuid4())
    client = _get_claude()
    claude_messages = _convert_messages(messages)

    if not claude_messages:
        yield _make_chunk(msg_id, "No messages to process.", history_metadata)
        return

    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]

    # Inject today's date into the last user message
    today = _today_str()
    last_user = next((m for m in reversed(claude_messages) if m["role"] == "user"), None)
    if last_user:
        last_user["content"] = f"[Today is {today}]\n\n{last_user['content']}"

    try:
        # First call: non-streaming, allow tool use
        first_response = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_blocks,
            messages=claude_messages,
            tools=[SEARCH_TOOL],
        )

        if first_response.stop_reason == "tool_use":
            tool_block = next(
                (b for b in first_response.content if b.type == "tool_use"), None
            )
            if tool_block:
                search_results = await _execute_search(tool_block.input.get("query", ""))
                claude_messages.append({"role": "assistant", "content": first_response.content})
                claude_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": search_results,
                        }
                    ],
                })
        elif first_response.stop_reason == "end_turn":
            # Claude answered without needing search — stream the existing text
            text = next(
                (b.text for b in first_response.content if hasattr(b, "text")), ""
            )
            if text:
                # Yield in small chunks to keep streaming feel
                chunk_size = 4
                words = text.split(" ")
                for i in range(0, len(words), chunk_size):
                    chunk = " ".join(words[i:i + chunk_size])
                    if i + chunk_size < len(words):
                        chunk += " "
                    yield _make_chunk(msg_id, chunk, history_metadata)
            return

        # Second call: stream the final response (no tools to avoid re-searching)
        async with client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            system=system_blocks,
            messages=claude_messages,
        ) as stream:
            async for text in stream.text_stream:
                yield _make_chunk(msg_id, text, history_metadata)

    except Exception as e:
        logging.exception("Error in claude stream_response")
        yield _make_chunk(msg_id, f"\n\n[Error: {str(e)}]", history_metadata)


async def generate_title(messages: list) -> str:
    """Generate a short conversation title."""
    client = _get_claude()
    simple_messages = [
        {"role": m["role"], "content": str(m.get("content", ""))}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    simple_messages.append({
        "role": "user",
        "content": "Summarize this conversation in 4 words or fewer. No punctuation, no quotes, no commentary."
    })

    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=32,
            messages=simple_messages,
        )
        return response.content[0].text.strip()
    except Exception as e:
        logging.exception("Error generating title")
        # Fall back to first user message snippet
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                return str(m["content"])[:40]
        return "New conversation"


async def generate_email(purpose: str, details: str, tone: str) -> str:
    """Generate an email draft for dojo communications."""
    client = _get_claude()
    today = _today_str()

    tone_guidance = {
        "formal": "formal and professional, suitable for official communications",
        "warm": "warm but not effusive, clear, traditional martial arts community voice",
        "brief": "brief and direct — minimal prose, essential information only",
    }.get(tone, "warm but not effusive")

    prompt = (
        f"Today is {today}.\n\n"
        f"Write an email for Midwest Aikido Center with the following details.\n\n"
        f"Purpose: {purpose}\n"
        f"Key details: {details}\n"
        f"Tone: {tone_guidance}\n\n"
        "Format as plain text suitable for email — no markdown headers. "
        "Include a subject line as the first line prefixed with 'Subject: ', "
        "then a blank line, then the email body. "
        "Do not add a signature line."
    )

    system = (
        SYSTEM_PROMPT + "\n\n"
        "When writing emails: use clear, traditional martial arts community language. "
        "Avoid excessive exclamation points. Structure with a brief opening, essential details, "
        "and a clear call to action where appropriate."
    )

    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logging.exception("Error generating email")
        raise e
