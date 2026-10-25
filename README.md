<div align="center">

# BrainCellGPT

### One cell. Infinite confusion.

An entertaining chatbot for curious questions, strange tangents, and conversations with absolutely no plan.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)

</div>

BrainCellGPT is a small Python application with a browser-based chat interface. Choose a hosted model provider, connect a local Ollama or LM Studio server, or configure a compatible custom endpoint. The interface keeps the focus on chatting; it does not provide repository editing or autonomous development tools.

The server uses Python's standard library and serves the included web interface directly. No frontend build step or extra database package is required. SQLite stores the selected access mode and shared-key allowance on the app installation; conversation transcripts stay in browser IndexedDB.

## Features

- Chat with hosted or local language models.
- Save provider profiles and model settings in this browser for return visits.
- Choose BraincellGPT's shared Groq access (10 provider attempts per local installation) or use your own provider key. The shared key remains on the server.
- Check provider connectivity from the app.
- Start a new conversation and copy assistant replies.
- Keep chat history on this device, reopen previous conversations, continue them, and delete them.
- Use your own saved key without BraincellGPT's shared-key allowance. Provider billing, quotas, and rate limits still apply.
- Use a responsive, single-page interface with a playful space theme.

Provider features and model availability vary. Chat replies currently return as complete responses; the chatbot interface does not stream tokens. A shared-key attempt counts when its provider request begins, including a request that later fails or times out. Requests rejected before that point do not count. The app does not automatically retry provider requests; submitting again starts a new attempt and may use another message.

## Requirements

- Python 3.10 or later
- A supported provider API key, or a locally running Ollama or LM Studio server with a model installed

No package installation is required for the core app. The optional `keyring` package is used by backend credential-storage support; it is not required for ordinary chat.

## Quick start

Clone the repository and enter the project directory:

```bash
git clone https://github.com/sanjan-github/BraincellGPT.git
cd BraincellGPT
```

Create a local configuration file:

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**macOS and Linux**

```bash
cp .env.example .env
```

Add the API key and model for the provider you want to use. For example:

```dotenv
GROQ_API_KEY=your_api_key
GROQ_MODEL=llama-3.3-70b-versatile
PORT=8000
```

Start the app:

```bash
python app.py
```

Open [http://localhost:8000](http://localhost:8000) and choose shared free access or your own provider key. To use shared access, configure `GROQ_API_KEY` in the server's `.env` file. For your own key, open **Chat settings**, select a supported provider, save the key, and use **Verify**. Keep the server running while you chat; press `Ctrl+C` in the terminal to stop it.

Opening `web/index.html` directly will not work because the interface needs the local HTTP API.

## Supported providers

Set provider credentials in `.env`, or enter a key in **Chat settings**. Environment variable names and default model IDs are listed in [`.env.example`](.env.example).

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

Model IDs, capabilities, pricing, and service availability are controlled by each provider and may change. BrainCellGPT's provider and endpoint checks do not verify every model's live availability.

## API keys and privacy

- A key entered in the app is sent to the local BrainCellGPT server and kept in process memory. The cache is capped at 128 profiles and keys expire after 24 hours; stopping the app clears it.
- `.env` keys remain on the machine running the server. Do not commit `.env` or share real credentials.
- Provider profiles contain nonsecret settings and are stored in browser `localStorage`. API key values are not stored in browser storage; keys entered in Settings are held only in server memory and must be entered again after the app stops.
- The local SQLite file `chat-usage.sqlite3` stores random conversation/request IDs, the selected access mode, and shared-key usage; it does not store prompts, replies, or API keys. Conversation transcripts stay in this browser's IndexedDB and are not uploaded as a database. Clearing browser storage may delete them, and they do not sync to other devices. Its location follows `BRAINCELLGPT_DATA_DIR`, or the platform's local BrainCellGPT application-data directory.
- The shared Groq allowance is 10 provider attempts per local app installation, across chats. Requests count when the provider call begins, including failed or timed-out attempts; retries are explicit and count as new attempts. This anonymous installation-level limit is not account-based and can be bypassed by someone who creates another installation or changes the local data. It does not cap provider spending. Own-key mode bypasses this BraincellGPT allowance, while provider billing, quotas, and rate limits still apply.
- Prompts and conversation history sent to a hosted provider are subject to that provider's data handling and retention terms. Local models send requests to the configured local service.
- The server binds to loopback by default. Do not expose it directly to an untrusted network. Remote access is an explicit advanced configuration requiring an authentication token and a trusted TLS reverse proxy; see [Security](docs/security.md).

See [Architecture](docs/architecture.md) for the chat request flow and [Security](docs/security.md) for deployment guidance.

## Development and verification

Run the test suite:

```bash
python -m unittest discover -s tests -v
```

Run the clean-install HTTP smoke check and Python compilation check:

```bash
python scripts/smoke_clean_install.py
python -m compileall -q app.py backend tests scripts
```

The CI workflow runs the application checks on Windows, macOS, and Linux with Python 3.11 and 3.12. Provider tests use mock responses; one live Groq chat request was also verified during 1.0 preparation. This does not establish live availability for every provider or model.

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
