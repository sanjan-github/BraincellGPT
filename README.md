# BrainCellGPT

**One brain cell. Infinite confusion.**

A context-aware conversational AI prototype with a deliberately simple setup: **one Python server, one HTML page, no Node.js, no npm, and no third-party Python packages.**

## Run it

Requirements: Python 3.10+ and internet access for Groq.

1. Open the repository root folder in VS Code.
2. Copy `.env.example` to `.env` in the root folder.
3. Add your Groq API key to `.env`:

   ```text
   GROQ_API_KEY=your_key_here
   GROQ_MODEL=llama-3.3-70b-versatile
   PORT=8000
   ```

4. Run from the repository root:

   ```powershell
   python app.py
   ```

5. Open **http://localhost:8000**.

No `npm install`, no virtual environment, no separate frontend server, and no VS Code **Go Live**.

### Run from VS Code

Use `Terminal → Run Task… → BrainCellGPT: Run`, or run `python app.py` in the terminal. Stop with `Ctrl+C`.

## Features

- One local Python server serves the UI and chat API.
- Groq Chat Completions API integration.
- Five personalities: Normal, Sarcastic, Chaotic, Professional, and Brutally Honest.
- Current-session conversation context.
- Clear errors for missing/rejected API keys and connection failures.
- Health check at `http://localhost:8000/health`.

## Rules for future developers and AI agents

**Read these rules before changing code. They are project requirements, not optional suggestions.**

### Keep the project simple

- Preserve one-command startup: `python app.py`.
- Do not add frameworks, packages, build steps, or services unless a concrete requirement justifies the complexity.
- Do not reintroduce React, Next.js, npm, FastAPI, or a second server without explaining the need and getting approval from the project owner.
- Prefer Python's standard library and browser-native HTML, CSS, and JavaScript.
- Do not create abstractions, modules, or folders for hypothetical future flexibility. Add structure only to solve a current problem.

### Protect existing behavior

- Treat `app.py` and `web/index.html` as the current application entry points.
- Keep the API contract aligned: `POST /api/chat` for chat and `GET /health` for health checks.
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

Then run `python app.py` and verify:
- `http://localhost:8000` loads the interface.
- `http://localhost:8000/health` returns a healthy status.
- A chat request succeeds with a valid Groq key, when API access is available.

For UI changes, test the affected interactions. For API changes, test both success and error paths. Do not claim tests passed unless they were actually run. State what was tested and what remains unverified.

### Git and documentation

- Never commit `.env`, API keys, tokens, or private user data.
- Update `.env.example` whenever environment variables change; include placeholders only.
- Update this README when setup, commands, architecture, features, or limitations change.
- Clearly distinguish implemented features from planned features.
- Do not merge unverified changes to `main` while checks are failing or important tests have not run, unless the owner explicitly asks.
- Use clear commit messages describing the change.

## Current limitations

Conversation history is held in the current browser session and is not saved after refresh. Long-term memory, database storage, authentication, and tool execution are not implemented. A valid Groq API key and internet access are required for AI responses. The configured model must be available to your Groq account; change `GROQ_MODEL` in `.env` if needed. This is a local development prototype, not a production-ready public service.

## Security

Keep the real API key in the root `.env` file. Never commit it or expose it to browser code. The app binds to `127.0.0.1` by default. Do not expose the server publicly until authentication, rate limiting, input validation, and deployment security have been implemented and reviewed.

## Roadmap

1. Keep the one-command app stable and testable.
2. Add durable conversation storage if needed.
3. Add user-controlled long-term memory.
4. Add safe tool calling and evaluation.
5. Consider authentication and deployment only when the core application is ready.

## License

MIT. See `LICENSE`.
