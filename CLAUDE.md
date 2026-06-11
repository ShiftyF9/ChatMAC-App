# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ChatMAC is an internal AI assistant for Midwest Aikido Center (MAC) board members and staff. It is a fork of Microsoft's `sample-app-aoai-chatGPT` in which the Azure OpenAI layer was **replaced with Anthropic Claude** (`backend/claude_service.py`). Much of the original Azure OpenAI plumbing (`backend/settings.py`, `backend/utils.py`, the `AZURE_OPENAI_*` env vars, README.md, TEST_CASE_FLOWS.md) is inherited from the fork and is largely vestigial — settings are still loaded for UI/chat-history config, but chat completions do NOT go through Azure OpenAI.

## Commands

### Backend (Quart, Python 3.12)
```
pip install -r requirements.txt          # runtime deps (requirements-dev.txt for tests/scripts)
python -m uvicorn app:app --port 50505 --reload   # run locally
```
`start.cmd` (Windows) / `start.sh` do install + frontend build + run in one step.

### Frontend (React 18 + TypeScript + Vite, in frontend/)
```
npm install
npm run build      # tsc + vite build → outputs to ../static (emptyOutDir: true)
npm run watch      # rebuild on change (backend serves static/, so use this during dev)
npm run lint       # eslint; npm run format = prettier:fix + lint:fix
npm run test       # jest
```

### Tests
```
python -m pytest tests/unit_tests                       # unit tests (settings/env parsing, utils)
python -m pytest tests/unit_tests/test_settings.py -k <name>   # single test
python -m pytest tests/integration_tests                # requires live Azure resources + dotenv templates
```

### Deployment — pushing to main deploys to production
GitHub Actions (`.github/workflows/main_chatmac-app.yml`) builds the frontend and deploys to the Azure Web App **ChatMAC-App** on every push to `main`. There is no staging slot. Do not push to `main` unless the change is ready to go live.

## Architecture

**Backend** — `app.py` is a single-file Quart app (one blueprint). Key routes:
- `POST /conversation` — chat. Delegates to `claude_stream_response()` and returns NDJSON (`format_as_ndjson` in `backend/utils.py`).
- `POST /email/generate` — email drafting endpoint backing the EmailAssistant UI (`{details, tone, original_email}` → plain-text draft).
- `/history/*` — conversation persistence in Azure CosmosDB via `backend/history/cosmosdbservice.py`. All history routes await the `cosmos_db_ready` asyncio.Event set during `before_serving`.
- The whole app is gated by HTTP Basic Auth when the `APP_PASSWORD` env var is set (`before_request` hook in `create_app`).

**Claude layer** — `backend/claude_service.py` is the heart of the app:
- Models are hardcoded in `MODEL` (chat/email) and `TITLE_MODEL` (conversation titles); the MAC-specific system prompt (source hierarchy, no-guessing policy, tone) lives in `SYSTEM_PROMPT`.
- `stream_response()` runs a single **streaming** agentic loop with adaptive thinking: every call carries the `search_documents` tool, text streams to the user as it arrives, and `tool_use` responses trigger searches (all tool blocks handled, run concurrently) before the loop continues. After `MAX_SEARCH_ROUNDS` searches the last call passes `tool_choice: none` to force an answer. Each round's last `tool_result` gets a `cache_control` breakpoint (3 rounds + system block = the 4-breakpoint API limit), and tools/system stay byte-identical across rounds so the prompt-cache prefix holds. Each chunk is wrapped by `_make_chunk()` to mimic the OpenAI `chat.completion.chunk` shape — this is what keeps the inherited frontend working unchanged. Preserve that shape if you touch streaming.
- The search tool queries an Azure AI Search index (default `bod-emails`: dojo emails, board minutes, bylaws, member records, calendar, web content) with semantic search and a plain-search fallback. Claude can scope queries via optional tool params: `source_type` (email/calendar/member/web), `date_from`/`date_to` (mapped to `document_date` OData filters), and `newest_first` (sorts by `document_date desc`; runs in simple mode since `$orderby` is unsupported with semantic ranking). `SearchClient` is sync, so calls run via `run_in_executor`.
- `_convert_messages()` normalizes frontend messages into Anthropic format (merges consecutive same-role messages, drops leading non-user messages). Today's date is injected into the last user message.
- `generate_title()` (conversation titles for history) and `generate_email()` (searches the archive for context before drafting) also live here.

**Frontend** — `frontend/src`, standard fork structure (pages/chat, components/Answer, components/ChatHistory, state/AppProvider). MAC additions: `components/EmailAssistant/`. The frontend reads runtime config from `GET /frontend_settings`. The build is committed to `static/`, which the backend serves as both static folder and template folder.

## Configuration

Local config via `.env` (see `.env.sample`; loaded both by `python-dotenv` and pydantic-settings `DOTENV_PATH`). The variables that actually matter for the Claude-based app:
- `ANTHROPIC_API_KEY` — required for all chat/title/email generation
- `AZURE_SEARCH_SERVICE`, `AZURE_SEARCH_KEY`, `AZURE_SEARCH_INDEX`, `AZURE_SEARCH_TOP_K`, `AZURE_SEARCH_SEMANTIC_SEARCH_CONFIG` — document search
- `AZURE_COSMOSDB_ACCOUNT`, `AZURE_COSMOSDB_DATABASE`, `AZURE_COSMOSDB_CONVERSATIONS_CONTAINER`, `AZURE_COSMOSDB_ACCOUNT_KEY` — chat history (optional; app runs without it but history routes fail)
- `APP_PASSWORD` — enables Basic Auth gate
- `UI_*` — branding (title, logos, chat title/description)

Production settings live in the Azure App Service app settings, not in the repo.
