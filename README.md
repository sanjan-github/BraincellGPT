# BrainCellGPT

**One brain cell. Infinite confusion.**

BrainCellGPT is a context-aware conversational AI starter project. The goal is to combine conversational context, user-controlled memory, configurable personality, safe tool calling, and an evaluation suite for conversational reliability.

> Status: early scaffold. The backend includes a working chat API when configured with a Gemini API key, basic conversation history in process, memory abstractions, and tests for core logic. Persistent database storage, authentication, and production deployment are not implemented yet.

## Architecture

```text
Next.js UI -> FastAPI -> conversation service -> model provider
                           |                 -> memory store
                           |                 -> approved tools (planned)
                           -> response validation
```

## Current features

- FastAPI backend with `/health` and `/api/chat`.
- Model provider integration through the Gemini REST API.
- Configurable personality and bounded in-memory conversation context.
- User-controlled memory enabled/disabled flag in each request (persistent memory is not yet implemented).
- Minimal Next.js chat UI scaffold.
- Unit tests for context construction and personality instructions.

## Quick start

### Backend

Requirements: Python 3.11+

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # on Windows, copy .env.example .env
# Set GEMINI_API_KEY in .env
uvicorn app.main:app --reload
```

Backend runs at `http://127.0.0.1:8000`; interactive API docs at `/docs`.

### Frontend

Requirements: Node.js 20+

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:3000`. Set `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` in `frontend/.env.local` if needed.

## Configuration

See `backend/.env.example`. Never commit API keys. Do not expose provider keys through `NEXT_PUBLIC_*` variables.

## Roadmap

1. Persist conversations with PostgreSQL/Supabase and enforce per-user authorization.
2. Implement explicit memory creation, update, deletion, and retrieval.
3. Add safe, allow-listed tool execution with structured argument validation.
4. Add benchmark datasets for context retention, ambiguity, contradictions, tool honesty, and memory relevance.
5. Add authentication, rate limits, observability, and deployment.

## Security notes

This is a development scaffold, not a production-ready service. It has no authentication or durable storage. The in-memory conversation store is process-local and resets on restart. Do not deploy it publicly with sensitive user data until access controls, persistence policies, privacy controls, and rate limiting are implemented.

## License

MIT. See `LICENSE`.
