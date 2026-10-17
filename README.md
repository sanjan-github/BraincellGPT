# BrainCellGPT

**One brain cell. Infinite confusion.**

A context-aware conversational AI prototype built with a FastAPI backend and a Next.js frontend.

---

## Architecture

| Layer | Stack | Entry point |
|-------|-------|-------------|
| Backend | Python + FastAPI + Gemini API | `backend/app/main.py` |
| Frontend | Next.js 15 + React 19 + TypeScript | `frontend/app/page.tsx` |
| Legacy server | Pure-Python stdlib + Groq API | `app.py` (kept for reference) |

---

## Quick start

**Requirements:** Python 3.10+, Node.js 18+, and internet access for the Gemini API.

### 1 — Backend

```powershell
# From the repository root
Copy-Item backend\.env.example backend\.env
# Edit backend\.env and add your Gemini API key:
#   GEMINI_API_KEY=your_key_here

python -m venv backend\.venv
backend\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn app.main:app --reload --app-dir backend
```

The API will be available at **http://127.0.0.1:8000**.  
Health check: **http://127.0.0.1:8000/health**

### 2 — Frontend

```powershell
# In a second terminal, from the repository root
Copy-Item frontend\.env.example frontend\.env.local
# Default: NEXT_PUBLIC_API_URL=http://127.0.0.1:8000

cd frontend
npm install
npm run dev
```

Open **http://localhost:3000**.

### VS Code tasks

Use `Terminal → Run Task…` to start the backend or frontend from VS Code (see `.vscode/tasks.json`).

---

## Features

- FastAPI backend with CORS, input validation, and structured error responses.
- Gemini API integration (`gemini-2.5-flash` by default; change `GEMINI_MODEL` in `backend/.env`).
- Five personalities: Normal, Sarcastic, Chaotic, Professional, and Brutally Honest.
- Conversation context preserved per browser session (client-supplied history).
- Clear error messages for missing/rejected API keys and connection failures.
- Health check at `GET /health`.
- `pytest` test suite for backend logic.

---

## Environment variables

### `backend/.env`

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Your Google AI Studio API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model name |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | Allowed CORS origin |
| `MAX_CONTEXT_MESSAGES` | `16` | History messages sent to the model |

### `frontend/.env.local`

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8000` | Backend base URL |

---

## Running tests

```powershell
# Backend unit tests (from repository root)
backend\.venv\Scripts\Activate.ps1
cd backend
python -m pytest tests/ -v
```

---

## Rules for future developers and AI agents

**Read these rules before changing code. They are project requirements, not optional suggestions.**

### Keep the project focused

- Do not add frameworks, packages, build steps, or services unless a concrete requirement justifies the complexity.
- Prefer well-understood libraries over novel abstractions.
- Do not create abstractions, modules, or folders for hypothetical future flexibility. Add structure only to solve a current problem.

### Protect existing behaviour

- Keep the API contract stable: `POST /api/chat` for chat and `GET /health` for health checks.
- Read the existing implementation before editing it. Do not rewrite working features unnecessarily.
- Keep Gemini as the model provider unless the project owner explicitly approves a change.
- Read the API key from `GEMINI_API_KEY`. Never hardcode secrets.
- Never expose API keys in browser JavaScript, logs, screenshots, commits, or documentation.
- Handle provider errors without exposing credentials or authenticated request details.

### Make focused changes

- Solve the user's stated problem before proposing new features.
- Make the smallest complete change that fixes the issue.
- Avoid unrelated refactors, speculative features, and scope creep.
- Do not add a database, authentication, long-term memory, agents, tool execution, or deployment machinery until explicitly planned and justified.
- Ask the project owner before a major architecture change or adding a dependency that makes setup more complicated.

### Verify before claiming completion

After changing Python code, run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\ -v
```

After changing frontend code, run:

```powershell
cd frontend
npm run typecheck
npm run lint
```

Do not claim tests passed unless they were actually run. State what was tested and what remains unverified.

### Git and documentation

- Never commit `.env`, `.env.local`, API keys, tokens, or private user data.
- Update `.env.example` files whenever environment variables change; include placeholders only.
- Update this README when setup, commands, architecture, features, or limitations change.
- Clearly distinguish implemented features from planned features.
- Do not merge unverified changes to `main` while checks are failing, unless the owner explicitly asks.
- Use clear commit messages describing the change.

---

## Current limitations

Conversation history is held in the current browser session and is not saved after refresh. Long-term memory, database storage, authentication, and tool execution are not implemented. A valid Gemini API key and internet access are required for AI responses. The configured model must be available to your Google AI account; change `GEMINI_MODEL` in `backend/.env` if needed. This is a local development prototype, not a production-ready public service.

## Security

Keep the real API key in `backend/.env`. Never commit it or expose it to browser code. The backend binds to `127.0.0.1` by default. Do not expose either server publicly until authentication, rate limiting, input validation, and deployment security have been implemented and reviewed.

## Roadmap

1. Keep the FastAPI + Next.js app stable and testable.
2. Add durable conversation storage if needed.
3. Add user-controlled long-term memory.
4. Add safe tool calling and evaluation.
5. Consider authentication and deployment only when the core application is ready.

## License

MIT. See `LICENSE`.
