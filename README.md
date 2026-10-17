# BrainCellGPT

**One brain cell. Infinite confusion.**

A context-aware conversational AI prototype.
One Python file. One HTML file. One command to run it.

---

## How to run

**Requirements:** Python 3.10 or later and an internet connection.

### 1 — Get a Groq API key

Sign up at [console.groq.com](https://console.groq.com) and create a free API key.

### 2 — Configure the environment

Copy `.env.example` to `.env` in the repository root:

```powershell
Copy-Item .env.example .env
```

Open `.env` and add your Groq API key:

```text
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
PORT=8000
```

### 3 — Start the server

```powershell
python app.py
```

### 4 — Open the app

Open **http://localhost:8000** in your browser.

> **Note:** Do not open `web/index.html` directly in your browser and
> do not use VS Code's Go Live extension. The chat interface makes API
> requests to the Python server, so the server must be running.

### Run from VS Code

Use `Terminal → Run Task… → BrainCellGPT: Run`, or run `python app.py`
in the terminal. Stop the server with **Ctrl+C**.

---

## Features

- One-command startup: `python app.py`.
- No npm, no Node.js, no virtual environment, no build step required.
- Groq Chat Completions API integration (configurable model).
- Five personalities: Normal, Sarcastic, Chaotic, Professional, and Brutally Honest.
- Conversation context preserved within the current browser session.
- Clear error messages for missing/rejected API keys, rate limits, and connection failures.
- Health check at `GET /health`.
- Mobile and desktop responsive layout.

---

## Environment variables

All variables are optional except `GROQ_API_KEY`. Set them in the root `.env` file.

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | *(required)* | Your Groq API key |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `PORT` | `8000` | Port the server listens on |

---

## Rules for future developers and AI agents

**Read these rules before changing code. They are project requirements, not optional suggestions.**

### Keep the project simple

- Preserve one-command startup: `python app.py`.
- Do not add frameworks, packages, build steps, or services unless a concrete requirement justifies the complexity.
- Do not reintroduce React, Next.js, npm, FastAPI, or a second server without explaining the need and getting approval from the project owner.
- Prefer Python's standard library and browser-native HTML, CSS, and JavaScript.
- Do not create abstractions, modules, or folders for hypothetical future flexibility. Add structure only to solve a current problem.

### Protect existing behaviour

- Treat `app.py` and `web/index.html` as the application entry points.
- Keep the API contract: `POST /api/chat` for chat, `GET /health` for health checks.
- Read the existing implementation before editing it. Do not rewrite working features unnecessarily.
- Keep Groq as the model provider unless the project owner explicitly approves a change.
- Read the API key from `GROQ_API_KEY` and the model from `GROQ_MODEL`. Never hardcode secrets.
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
python -m py_compile app.py
```

Then start the server and verify:

- `http://localhost:8000` loads the interface.
- `http://localhost:8000/health` returns a healthy JSON response.
- A chat request succeeds with a valid Groq key.

For UI changes, test the affected interactions. For API changes, test both success and error paths.
Do not claim tests passed unless they were actually run.
State what was tested and what remains unverified.

### Git and documentation

- Never commit `.env`, API keys, tokens, or private user data.
- Update `.env.example` whenever environment variables change; include placeholders only.
- Update this README when setup, commands, architecture, features, or limitations change.
- Clearly distinguish implemented features from planned features.
- Do not merge unverified changes to `main` while checks are failing, unless the owner explicitly asks.
- Use clear commit messages describing the change.

---

## Current limitations

Conversation history is held in the current browser session and is not saved after a page refresh.
Long-term memory, database storage, authentication, and tool execution are not implemented.
A valid Groq API key and an internet connection are required for AI responses.
The configured model must be available on your Groq account; change `GROQ_MODEL` in `.env` if needed.
This is a local development prototype, not a production-ready public service.

## Security

Keep the real API key in the root `.env` file. Never commit it or expose it to browser code.
The server binds to `127.0.0.1` by default. Do not expose the server publicly until authentication,
rate limiting, input validation, and deployment security have been implemented and reviewed.

## Roadmap

1. Keep the one-command app stable and testable.
2. Add durable conversation storage if needed.
3. Add user-controlled long-term memory.
4. Add safe tool calling and evaluation.
5. Consider authentication and deployment only when the core application is ready.

## License

MIT. See `LICENSE`.
