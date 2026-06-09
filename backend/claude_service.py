import asyncio
import os
import time
import uuid
import logging
from datetime import datetime

import anthropic
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a knowledgeable assistant for Midwest Aikido Center (MAC), a traditional Aikido dojo in Chicago, IL. You help board members, instructors, and staff find information from dojo records and serve as a general resource on Aikido.

Source hierarchy:
- For MAC-specific topics (policies, fees, schedules, membership, events, leadership): rely on retrieved documents. Do not state specific facts unless clearly supported by those documents. Treat the MAC website as the authoritative source for current policies. Email threads and meeting minutes may reflect individual exceptions rather than general policy — note this distinction when relevant.
- When documents conflict, prefer the most recent based on the date in the document header or filename.
- For general Aikido topics (history, techniques, lineage, prominent figures, the broader Aikido world): draw on your general knowledge, but distinguish clearly between what comes from dojo records and what is general knowledge.

If a specific MAC policy, price, date, or name is not found in the retrieved documents, do not speculate. Say: "I cannot find that specific detail in our records. Could you provide it, or would you like me to search more broadly?"

Tone: warm, respectful, and professional, consistent with a traditional martial arts community. Be direct and clear. Avoid flowery language, hyper-enthusiastic phrasing, and filler.

Format: use markdown for structure when it aids clarity. Keep responses concise. Cite the source document, date, or URL when relevant. Treat "Fall" and "Autumn" as equivalent when referring to seminars or events."""

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


def _run_search(client, query: str, top_k: int, semantic: bool, semantic_config: str):
    """Run search synchronously — SearchClient is sync in azure-search-documents."""
    kwargs = {
        "search_text": query,
        "top": top_k,
        "select": ["content", "title", "filepath", "document_date"],
    }
    if semantic:
        kwargs["query_type"] = "semantic"
        kwargs["semantic_configuration_name"] = semantic_config
    return list(client.search(**kwargs))


def _extract_result(r: dict) -> tuple:
    """Extract (name, date_str, content) from a search result."""
    content = (r.get("content") or "").strip()

    name = (r.get("title") or r.get("filepath") or "Unknown")
    if isinstance(name, str):
        name = name.replace(".txt", "").rsplit("/", 1)[-1]  # strip path prefix if filepath used

    doc_date = r.get("document_date")
    try:
        date_str = datetime.fromisoformat(str(doc_date)).strftime("%B %d, %Y") if doc_date else "Unknown date"
    except Exception:
        date_str = str(doc_date) if doc_date else "Unknown date"

    return name, date_str, content


async def _execute_search(query: str) -> str:
    try:
        client = _get_search()
        top_k = int(os.environ.get("AZURE_SEARCH_TOP_K", "5"))
        semantic_config = os.environ.get("AZURE_SEARCH_SEMANTIC_SEARCH_CONFIG", "default")
        loop = asyncio.get_running_loop()

        try:
            raw = await loop.run_in_executor(
                None, lambda: _run_search(client, query, top_k, True, semantic_config)
            )
        except Exception as sem_err:
            logging.warning("Semantic search failed (%s), falling back to simple search", sem_err)
            raw = await loop.run_in_executor(
                None, lambda: _run_search(client, query, top_k, False, semantic_config)
            )

        snippets = []
        for r in raw:
            name, date_str, content = _extract_result(r)
            if not content:
                continue
            snippets.append(f"[Source: {name} | Date: {date_str}]\n{content}")

        if not snippets:
            return "No relevant documents found for that query."

        return "\n\n---\n\n".join(snippets)

    except Exception as e:
        logging.exception("Search error: %s", e)
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
        # Allow up to 3 searches before streaming the final answer
        for _ in range(3):
            response = await client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system_blocks,
                messages=claude_messages,
                tools=[SEARCH_TOOL],
            )

            if response.stop_reason != "tool_use":
                break

            tool_block = next((b for b in response.content if b.type == "tool_use"), None)
            if not tool_block:
                break

            search_results = await _execute_search(tool_block.input.get("query", ""))
            claude_messages.append({"role": "assistant", "content": response.content})
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

        # Stream the final response (no tools — search phase is done)
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


async def generate_email(details: str, tone: str, original_email: str = "") -> str:
    """Generate an email draft, optionally as a reply, using archive context for tone/substance."""
    client = _get_claude()
    today = _today_str()

    tone_guidance = {
        "formal": "formal and professional, suitable for official communications",
        "warm": "warm but not effusive, clear, traditional martial arts community voice",
        "brief": "brief and direct — minimal prose, essential information only",
    }.get(tone, "warm but not effusive")

    # Search the archive for relevant context
    context_str = ""
    try:
        context = await _execute_search(details[:300])
        if context and "No relevant documents" not in context and "Search failed" not in context:
            context_str = (
                "\n\nFor reference, here are relevant communications from the dojo archive "
                "that may inform the tone, content, or context of your draft:\n\n" + context
            )
    except Exception:
        pass  # Context is best-effort; proceed without it

    if original_email:
        task = (
            f"Write a reply to the following email on behalf of Midwest Aikido Center.\n\n"
            f"--- ORIGINAL EMAIL ---\n{original_email}\n--- END ORIGINAL EMAIL ---\n\n"
            f"Instructions: {details}"
        )
    else:
        task = f"Write an email for Midwest Aikido Center.\n\nDetails: {details}"

    prompt = (
        f"Today is {today}.\n\n"
        f"{task}\n\n"
        f"Tone: {tone_guidance}\n\n"
        "Format as plain text suitable for email — no markdown headers. "
        "Include a subject line as the first line prefixed with 'Subject: ', "
        "then a blank line, then the email body. "
        "Do not add a signature line."
        f"{context_str}"
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
