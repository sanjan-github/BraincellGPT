<div align="center">

# BrainCellGPT

### One cell. Infinite confusion.

An entertaining chatbot for curious questions, strange tangents, and conversations with absolutely no plan.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)

</div>

BrainCellGPT is a small Python application with a browser-based chat interface. Choose a hosted model provider, connect a local Ollama or LM Studio server, or configure a compatible custom endpoint. The interface keeps the focus on chatting; it does not provide repository editing or autonomous development tools.

The server uses Python's standard library and serves the included web interface directly. No frontend build step or extra database package is required; SQLite from Python stores the per-conversation message allowance locally.

## Features

- Chat with hosted or local language models.
- Create browser-session profiles for different providers and models.
- Enter a provider API key in the settings panel or use a key from `.env`.
- Check provider connectivity from the app.
- Start a new conversation and copy assistant replies.
- Send up to 10 provider requests in each conversation, with the count enforced by the local server and preserved across reloads.
- Restore the current conversation's messages after a page refresh in the same browser tab; start a new chat to create a fresh allowance.
- Use a responsive, single-page interface with a playful space theme.

Provider features and model availability vary. Chat replies currently return as complete responses; the chatbot interface does not stream tokens. An attempt uses one of the 10 messages when its provider request begins, including a request that later fails or times out. Requests rejected before that point do not count. The app does not automatically retry provider requests; submitting again starts a new attempt and may use another message.

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

Open [http://localhost:8000](http://localhost:8000), choose a provider under **Chat settings**, and start a conversation. Keep the server running while you chat; press `Ctrl+C` in the terminal to stop it.

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
- Provider profiles contain nonsecret settings and are stored in browser `sessionStorage`.
- The local SQLite file `chat-usage.sqlite3` stores random conversation/request IDs and message counts only; it does not store prompts, replies, or API keys. Its location follows `BRAINCELLGPT_DATA_DIR`, or the platform's local BrainCellGPT application-data directory.
- The 10-message allowance applies per conversation on this app installation. It is not a global spending limit for a provider API key shared with other apps or users.
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
