<div align="center">

# BrainCellGPT

### One cell. Infinite confusion.

A small, funny chatbot for curious questions, strange tangents, and conversations with absolutely no plan.

**Your chats stay on your device. Your conversations are yours to keep—or delete whenever you want.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)

</div>

## Your chats. Your device. Your choice.

BrainCellGPT is a **local-first chatbot**. It runs on your computer, and your conversation history is saved in your browser's local IndexedDB storage—not in a BrainCellGPT cloud account.

- **Your conversations stay local.** Chat history is saved in the browser on your device and is not uploaded as a conversation database.
- **Pick up where you left off.** Reopen saved chats, continue a conversation later, and keep your history across browser restarts.
- **Delete chats whenever you want.** You control your conversation history and can delete individual conversations from the app. Clearing the browser's site data can also remove locally saved chats.
- **No BrainCellGPT account required.** Your conversations are stored locally, not synced to a BrainCellGPT account or across devices.
- **Choose how to power your chats.** Use BrainCellGPT's shared Groq access or configure your own supported provider key.
- **Your API key stays out of browser storage.** Keys entered in Settings are sent to the local server and held in server memory; the shared Groq key stays on the server.

**A privacy note:** local storage does not mean every message is processed locally. If you choose a hosted provider, the message and relevant conversation history are sent to that provider to generate a reply and are subject to its data-handling policies. For processing through a local model, configure a local Ollama or LM Studio server.

## What it does

- Chats with hosted LLM providers or locally running Ollama and LM Studio models.
- Saves conversations in this browser so you can reopen, continue, or delete them.
- Stores non-secret provider profile settings in browser storage.
- Offers shared Groq access with a limit of 10 provider attempts per local app installation.
- Lets you use your own provider key without BrainCellGPT's shared-key allowance.
- Checks provider connectivity from the app.
- Keeps the focus on chatting—not repository editing or autonomous coding tools.
- Serves a lightweight web interface from Python, with no frontend build step.

Provider capabilities and model availability vary. Replies currently arrive as complete responses; the chat interface does not stream tokens.

### About the shared-key allowance

Shared Groq access allows **10 started provider attempts across conversations for a local app installation**. An attempt counts once the provider request begins, even if the request later fails or times out. Requests rejected before the provider call starts do not count. The app does not automatically retry provider requests; manually submitting again starts another attempt.

This is an installation-level allowance, not an authenticated per-person quota or a guarantee against API spending. It can be bypassed by creating another installation or changing local state. Do not treat it as sufficient abuse protection for a public anonymous service. With your own key, BrainCellGPT's shared allowance does not apply, but your provider's billing, quotas, and rate limits still do.

## Privacy and data storage

BrainCellGPT is designed for local use. Here is what is stored and where:

| Data | Where it is stored | What to know |
| --- | --- | --- |
| Conversation titles, messages, and timestamps | This browser's IndexedDB | Stays on this browser/device; can be deleted from the app or by clearing site data. |
| Non-secret provider profile settings | This browser's local storage | Contains configuration metadata, not API key values. |
| Access mode and shared-key usage | Local SQLite database used by the app | Stores random request/conversation IDs and usage information, not prompts or replies. |
| API keys entered in Settings | Local server process memory | Not stored in browser storage; cleared when the server stops and must be entered again. |
| Keys configured through `.env` | The machine running the server | Protect the file and never commit real credentials. |

When you use a hosted provider, your prompts and the conversation context sent with them leave your device and are processed by that provider. Review its privacy, retention, and training policies. If you use Ollama or LM Studio, requests go to the local service you configured.

Local conversation history is browser-specific and does not automatically sync between devices. If you clear the browser's site data, your saved conversations may be deleted. **You remain in control of the history stored by BrainCellGPT.**

## Requirements

- Python 3.10 or later.
- A supported provider API key, or a local Ollama/LM Studio server with a model installed.

No package installation is required for the core app. The optional `keyring` package is used by backend credential-storage support; it is not required for ordinary chat.

## Quick start

Clone the repository and enter the project directory:

```bash
git clone https://github.com/sanjan-github/BraincellGPT.git
cd BraincellGPT
```

Create a local configuration file.

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**macOS and Linux**

```bash
cp .env.example .env
```

To enable shared Groq access, add your server-side Groq credentials to `.env`, for example:

```dotenv
GROQ_API_KEY=your_api_key
GROQ_MODEL=llama-3.3-70b-versatile
PORT=8000
```

Start BrainCellGPT:

```bash
python app.py
```

Open http://localhost:8000 and follow the setup choices. You can use the configured shared Groq access or configure your own provider in **Chat settings**. Keep the server running while you chat; press `Ctrl+C` in the terminal to stop it.

Opening `web/index.html` directly will not work because the interface communicates with the local HTTP API.

## Supported providers

Configure server-side credentials in `.env` or enter a key through **Chat settings**. Environment variable names and default model IDs are listed in [`.env.example`](.env.example).

| Provider | Configuration |
| --- | --- |
| Groq | `GROQ_API_KEY`, `GROQ_MODEL` |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| Azure OpenAI | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` |
| Google Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| NVIDIA NIM | `NVIDIA_NIM_API_KEY`, `NVIDIA_NIM_MODEL` |
| DeepSeek | `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` |
| Mistral | `MISTRAL_API_KEY`, `MISTRAL_MODEL` |
| xAI | `XAI_API_KEY`, `XAI_MODEL` |
| Together AI | `TOGETHER_API_KEY`, `TOGETHER_MODEL` |
| Fireworks | `FIREWORKS_API_KEY`, `FIREWORKS_MODEL` |
| Cerebras | `CEREBRAS_API_KEY`, `CEREBRAS_MODEL` |
| Ollama | Run the local server and install a model |
| LM Studio | Start its local server and load a model |
| Custom compatible API | `CUSTOM_API_BASE_URL`, `CUSTOM_API_KEY`, `CUSTOM_MODEL` |

Model IDs, capabilities, prices, and availability are controlled by each provider and can change. Provider and endpoint checks do not guarantee that every model is currently available.

## Security notes

- Never commit `.env` or publish real credentials.
- API keys entered in Settings are sent to the local server and held in a bounded in-memory cache. The cache supports up to 128 profiles, expires keys after 24 hours, and is cleared when the server stops.
- BrainCellGPT does not return saved API keys to the browser and does not store entered key values in browser storage.
- The server binds to loopback by default. Do not expose it directly to an untrusted network.
- Remote access is an advanced opt-in that requires an authentication token and a trusted TLS reverse proxy. Read [Security guidance](docs/security.md) before enabling it.
- The local app's protections do not replace a deployment security review.

## Development and verification

Run the unit tests:

```bash
python -m unittest discover -s tests -v
```

Run the clean-install HTTP smoke check and Python compilation check:

```bash
python scripts/smoke_clean_install.py
python -m compileall -q app.py backend tests scripts
```

The CI workflow runs application checks on Windows, macOS, and Linux with Python 3.11 and 3.12. Mocked provider tests do not establish live availability for every provider, model, account, or region.

## Project layout

```text
BrainCellGPT/
├── app.py                         # Local HTTP server and chatbot API
├── backend/                       # Provider, credential, and supporting modules
├── web/index.html                 # Browser chat interface
├── scripts/smoke_clean_install.py # Clean-environment HTTP smoke check
├── tests/                         # Unit and security regression tests
├── docs/                          # Architecture, security, and project notes
└── .github/workflows/ci.yml       # Cross-platform CI workflow
```

## License

BrainCellGPT is released under the [MIT License](LICENSE).
