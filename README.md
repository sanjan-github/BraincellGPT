# BrainCellGPT

**One brain cell. Infinite confusion.**

BrainCellGPT is a context-aware conversational AI starter project. The goal is to combine conversational context, user-controlled memory, configurable personality, safe tool calling, and an evaluation suite for conversational reliability.

> Status: development scaffold. The chat API requires a Gemini API key. Conversation history is supplied by the frontend and is not saved to a database. Persistent storage, authentication, and production deployment are not implemented.

## Architecture

```text
Next.js UI -> FastAPI -> Gemini model API
```

## Current features

- FastAPI backend with `/health` and `/api/chat`.
- Gemini REST API integration.
- Five configurable response personalities.
- Bounded conversation context supplied with each request.
- Next.js chat interface.
- Backend unit tests.
- VS Code tasks to start the frontend and backend together.
- GitHub Actions checks for backend tests, frontend linting, TypeScript, and build.

## Requirements

- Python 3.11+
- Node.js 20+
- npm
- A Gemini API key for actual AI responses

## Run in VS Code (Windows)

Open the **repository root folder** in VS Code, not only `frontend` or `backend`.

1. Open a terminal in VS Code (`Terminal → New Terminal`).
2. Install backend dependencies:

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

3. Open `backend/.env` and set your own `GEMINI_API_KEY`. Never commit this file.
4. In a second terminal, install frontend dependencies:

   ```powershell
   cd frontend
   npm install
   ```

5. From the repository root, choose `Terminal → Run Task… → BrainCellGPT: Run both`. This launches FastAPI and Next.js in separate terminal panels. If prompted, allow VS Code to run the tasks.

Open:
- Frontend: http://localhost:3000
- Backend health check: http://127.0.0.1:8000/health
- Backend API docs: http://127.0.0.1:8000/docs

If the task does not start correctly, run the two commands manually in separate terminals:

Terminal 1:
```powershell
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2:
```powershell
cd frontend
npm run dev
```

### Why VS Code "Go Live" does not show this app

The Live Server **Go Live** button serves static files such as HTML, CSS, and browser JavaScript. BrainCellGPT is a Next.js app plus a separate FastAPI backend; Live Server does not start either service. Use `npm run dev` for the frontend and Uvicorn for the backend, or use the VS Code task above.

## Configuration

Backend configuration is in `backend/.env.example`. The frontend defaults to `http://127.0.0.1:8000` for the backend API; set `NEXT_PUBLIC_API_URL` in `frontend/.env.local` only if you need a different API address.

Do not expose model-provider API keys through `NEXT_PUBLIC_*` variables.

## Validation

From `backend`:
```powershell
python -m pytest -q
```

From `frontend`:
```powershell
npm run lint
npm run typecheck
npm run build
```

## Roadmap

1. Persist conversations with PostgreSQL/Supabase and enforce per-user authorization.
2. Implement memory creation, update, deletion, and retrieval.
3. Add safe, allow-listed tool execution with structured argument validation.
4. Expand benchmark cases for context retention, ambiguity, contradictions, tool honesty, and memory relevance.
5. Add authentication, rate limits, observability, and deployment.

## Security notes

This is a development scaffold, not a production-ready service. It has no authentication or durable storage. Do not deploy it publicly with sensitive user data until access controls, persistence policies, privacy controls, and rate limiting are implemented.

## License

MIT. See `LICENSE`.
