# BrainCellGPT

**One brain cell. Infinite confusion.**

A context-aware conversational AI prototype.
One Python file. One HTML file. One command to run it.

---

## How to run

**Requirements:** Python 3.10 or later and an internet connection.

### 1 — Choose a provider

Use the **Model Provider** selector in the app. Supported options:

- **Groq** — your Groq API key, or `GROQ_API_KEY` from `.env`.
- **OpenAI** — your OpenAI API key.
- **Anthropic Claude** — your Anthropic API key; uses Anthropic's Messages API.
- **OpenRouter** — your OpenRouter API key.
- **NVIDIA NIM** — your NVIDIA API key.
- **Ollama (local)** — run Ollama locally and enter a model installed on your machine; a key is normally not needed.
- **Custom OpenAI-compatible API** — enter a compatible API base URL, key, and model ID.

The default model IDs are starting examples, not guarantees that your account has access to them. Change the model ID in `.env` or provider settings when needed. For Ollama, install and run Ollama first, then pull a model such as `ollama pull llama3.2`.

### 2 — Choose how to provide your key

**Option A — Enter it in the app:** Start the server, enter your key in the **API Key** field, then click **Use key**. The key is kept in that browser tab's session storage and sent to the app server only with chat requests. Use **Clear** to remove it.

**Option B — Configure it locally:** Copy `.env.example` to `.env` in the repository root:

```powershell
Copy-Item .env.example .env
```

Open `.env` and add your Groq API key:

```text
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
PORT=8000
```

A key entered in the app is used for the selected provider. For Groq, if no personal key is entered, the server falls back to `GROQ_API_KEY` from `.env`.

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
- Provider selection for Groq, OpenAI, Anthropic, OpenRouter, NVIDIA NIM, Ollama, and custom OpenAI-compatible APIs.
- Five personalities: Normal, Sarcastic, Chaotic, Professional, and Brutally Honest.
- Conversation context preserved within the current browser session.
- Configure a provider, optional personal API key, custom endpoint, and model from the interface.
- A key entered in the browser overrides the selected provider's .env key; leave it blank to use the server-side environment key.
- Browser-entered API keys stay in the current tab's session storage; the server does not save them.
- Clear error messages for missing/rejected API keys, rate limits, and connection failures.
- Health check at `GET /health`.
- Mobile and desktop responsive layout.

---

## Environment variables

All variables are optional. A browser-entered key overrides the matching .env key. If the browser key is blank, BrainCellGPT falls back to .env. For the custom provider, you can configure the endpoint and model entirely in .env too.

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | *(optional)* | Fallback Groq API key |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | provider default | Optional OpenAI credentials/model |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | provider default | Optional OpenRouter credentials/model |
| `NVIDIA_NIM_API_KEY`, `NVIDIA_NIM_MODEL` | provider default | Optional NVIDIA NIM credentials/model |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | provider default | Optional DeepSeek credentials/model |
| `MISTRAL_API_KEY`, `MISTRAL_MODEL` | provider default | Optional Mistral credentials/model |
| `XAI_API_KEY`, `XAI_MODEL` | provider default | Optional xAI credentials/model |
| `TOGETHER_API_KEY`, `TOGETHER_MODEL` | provider default | Optional Together AI credentials/model |
| `FIREWORKS_API_KEY`, `FIREWORKS_MODEL` | provider default | Optional Fireworks credentials/model |
| `CEREBRAS_API_KEY`, `CEREBRAS_MODEL` | provider default | Optional Cerebras credentials/model |
| `ANTHROPIC_API_KEY` | — | Optional Anthropic credential |
| `CUSTOM_API_BASE_URL` | — | Custom OpenAI-compatible API base URL (HTTPS) |
| `CUSTOM_API_KEY` | — | Optional custom provider key |
| `CUSTOM_MODEL` | — | Custom provider model ID |
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
- Read the fallback API key from `GROQ_API_KEY` and the model from `GROQ_MODEL`. Never hardcode secrets.
- A user-entered key may be held in browser session storage and passed to the server only with chat requests. Never save it server-side or print it in logs, screenshots, commits, or documentation.
- Explain that user-entered keys are sent to the BrainCellGPT server; do not recommend entering a key into an instance the user does not trust.
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
Cloud providers require valid provider credentials and network access. Ollama requires a running local service and an installed model. The selected model ID must be available to the chosen provider.
This is a local development prototype, not a production-ready public service.

## Model provider architecture

BrainCellGPT takes inspiration from Roo Code's model-agnostic API layer, but keeps this project intentionally lightweight and dependency-free. Roo Code uses a provider factory, provider-specific handlers, shared stream/event types, model metadata, and validated provider settings. BrainCellGPT is being evolved toward those same separation-of-concerns principles without copying Roo Code's implementation.

### Supported providers

- **Cloud APIs:** Groq, OpenAI, Anthropic, OpenRouter, NVIDIA NIM, DeepSeek, Mistral AI, xAI, Together AI, Fireworks AI, and Cerebras.
- **Local APIs:** Ollama and LM Studio, restricted to localhost URLs.
- **Custom:** OpenAI-compatible APIs with an explicit HTTPS base URL and model ID.

Provider defaults and model availability can change independently. If a provider rejects the default model, enter a model ID enabled for your account. Local model servers must be running on the same computer. Provider-specific APIs do not all expose identical capabilities; this first compatibility layer normalizes basic text chat, while advanced capabilities such as native tool calls, image input, reasoning metadata, token accounting, and streaming still need dedicated adapters before they can be claimed as supported.

### Roo Code design lessons

The relevant architecture patterns are:
1. A provider factory/registry, rather than scattering provider decisions across UI code.
2. A consistent adapter contract with provider-specific request and response handling.
3. Explicit model metadata and capabilities, rather than assuming every model behaves the same.
4. Centralized timeout/error handling, with actionable user-facing errors that never expose credentials.
5. Validated provider settings and separate handling for local versus remote endpoints.
6. A streaming event model for text, usage, reasoning, and tool calls.
7. Tests for adapter behavior and settings validation.

This is a staged adaptation, not a claim of feature parity with Roo Code's much larger coding-agent extension.

## Security hardening

- API responses include security headers and disable caching.
- Chat and connection-test routes accept JSON POST requests only, enforce a request-size limit, and rate-limit requests per route.
- The server binds to 127.0.0.1 and rejects API requests from non-loopback clients.
- Custom provider URLs require HTTPS and are rejected when they resolve to non-public addresses; Ollama URLs are restricted to localhost.
- A **Test connection** button sends a small test prompt to the selected provider. This may consume provider quota.
- API keys are never written to server logs or committed to the repository. Browser session storage is not a secure vault; only use trusted local copies.

These controls reduce risk but do not make the app production-ready or safe to expose publicly. The current interface uses inline JavaScript, so the Content Security Policy permits inline scripts; a stricter policy would require moving scripts/styles to separate files.

## Security

You can enter a provider API key in the interface; it is stored in the current tab's session storage and sent to the BrainCellGPT server with chat requests. The server does not persist the supplied key, and request logging does not include request bodies. Alternatively, keep the key in the root `.env` file. Never commit real keys.

Only enter your key into an instance you trust. If you run someone else's hosted copy, that server receives the key and could modify its code to capture it. The server binds to `127.0.0.1` by default. Do not expose it publicly until authentication, rate limiting, input validation, and deployment security have been implemented and reviewed.

## Roadmap

1. Keep the one-command app stable and testable.
2. Add durable conversation storage if needed.
3. Add user-controlled long-term memory.
4. Add safe tool calling and evaluation.
5. Consider authentication and deployment only when the core application is ready.

## License

MIT. See `LICENSE`.
