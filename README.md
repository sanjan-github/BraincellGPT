<div align="center">

# BrainCellGPT

### One brain cell. Infinite confusion.

**A chaotic chatbot for conversations that make absolutely no sense.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)

</div>

BrainCellGPT is built for absurd conversations, unexpected tangents, ridiculous replies, and humour that takes a wrong turn and keeps going. It is not trying to be the most serious chatbot on the internet. The point is to make chatting entertainingly chaotic.

## Features

- **Chaotic conversations:** Absurd replies, unexpected tangents, and unpredictable humour.
- **Conversation context:** Earlier messages in the current session are included so replies can follow the conversation.
- **Multiple providers:** Choose from supported hosted services or a local model.
- **Flexible credentials:** Use a key from your local `.env` file or enter a key in the interface.
- **Lightweight interface:** Plain HTML, CSS, and JavaScript with no frontend build step.
- **Simple startup:** Run the app with one Python command.
- **Responsive layout:** Use the chat interface on desktop or mobile.

## Quick start

### Requirements

- Python 3.10 or later
- An internet connection and a valid key for your chosen hosted provider, or a locally running Ollama/LM Studio server

### 1. Get the project

```bash
git clone https://github.com/sanjan-github/BraincellGPT.git
cd BraincellGPT
```

### 2. Configure your provider

Copy the example environment file:

**Windows PowerShell**
```powershell
Copy-Item .env.example .env
```

**macOS / Linux**
```bash
cp .env.example .env
```

Open `.env` and add the key for the provider you intend to use. For example:

```dotenv
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
PORT=8000
```

Keep the key field in the interface empty to use the matching value from `.env`. A key entered in the interface overrides the environment key for that provider.

### 3. Start BrainCellGPT

```bash
python app.py
```

### 4. Open the chat

Visit **http://localhost:8000** in your browser, select a provider, and start chatting.

Keep the server running while you use the app. Stop it with `Ctrl+C`.

> **Important:** Open the app through the local server at `http://localhost:8000`. Do not open `web/index.html` directly or use a separate static-server extension.

## Supported providers

Select the provider in the chat interface. The credential must match the selected provider.

| Provider | Environment variable | Notes |
| --- | --- | --- |
| Groq | `GROQ_API_KEY` | Default provider |
| OpenAI | `OPENAI_API_KEY` | Set `OPENAI_MODEL` if needed |
| Anthropic | `ANTHROPIC_API_KEY` | Uses Anthropic's Messages API |
| OpenRouter | `OPENROUTER_API_KEY` | Set `OPENROUTER_MODEL` if needed |
| NVIDIA NIM | `NVIDIA_NIM_API_KEY` | Set `NVIDIA_NIM_MODEL` if needed |
| DeepSeek | `DEEPSEEK_API_KEY` | Set `DEEPSEEK_MODEL` if needed |
| Mistral | `MISTRAL_API_KEY` | Set `MISTRAL_MODEL` if needed |
| xAI | `XAI_API_KEY` | Set `XAI_MODEL` if needed |
| Together AI | `TOGETHER_API_KEY` | Set `TOGETHER_MODEL` if needed |
| Fireworks AI | `FIREWORKS_API_KEY` | Set `FIREWORKS_MODEL` if needed |
| Cerebras | `CEREBRAS_API_KEY` | Set `CEREBRAS_MODEL` if needed |
| Ollama | Not required by default | Ollama must be running locally with a model installed |
| LM Studio | Not required by default | LM Studio's local server must be running |
| Custom compatible API | `CUSTOM_API_KEY` | Configure its HTTPS base URL and model ID |

Model availability and pricing depend on the provider and account. A model shown as a default may not be available to every account. For local providers, install the model and start the local server before connecting.

## Configuration

All settings are optional unless required by your selected provider. Copy `.env.example` and configure only the providers you use.

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY`, `GROQ_MODEL` | Groq credentials and model |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI credentials and model |
| `ANTHROPIC_API_KEY` | Anthropic credential |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | OpenRouter credentials and model |
| `NVIDIA_NIM_API_KEY`, `NVIDIA_NIM_MODEL` | NVIDIA NIM credentials and model |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | DeepSeek credentials and model |
| `MISTRAL_API_KEY`, `MISTRAL_MODEL` | Mistral credentials and model |
| `XAI_API_KEY`, `XAI_MODEL` | xAI credentials and model |
| `TOGETHER_API_KEY`, `TOGETHER_MODEL` | Together AI credentials and model |
| `FIREWORKS_API_KEY`, `FIREWORKS_MODEL` | Fireworks credentials and model |
| `CEREBRAS_API_KEY`, `CEREBRAS_MODEL` | Cerebras credentials and model |
| `CUSTOM_API_BASE_URL` | HTTPS base URL for a compatible custom API |
| `CUSTOM_API_KEY`, `CUSTOM_MODEL` | Custom API credential and model ID |
| `PORT` | Local server port; defaults to `8000` |

Do not commit `.env` or place real credentials in source code. Provider keys are not interchangeable.

## Project structure

```text
BraincellGPT/
├── app.py                   # Local Python server and provider requests
├── .env.example             # Example configuration (no real secrets)
├── web/
│   └── index.html            # Chat interface
├── tests/
│   └── test_app.py           # Application tests
└── .github/
    └── workflows/
        └── ci.yml            # Automated checks
```

## Development and tests

BrainCellGPT uses Python's standard library for the server and vanilla HTML, CSS, and JavaScript for the interface. No npm install, frontend compilation, or third-party Python packages are required for the current app.

Check Python syntax:

```bash
python -m py_compile app.py
```

Run the test suite:

```bash
python -m unittest discover -s tests -v
```

Run the app locally:

```bash
python app.py
```

The health endpoint is available at `http://localhost:8000/health`.

## Security and privacy

- The server binds to `127.0.0.1` by default and is intended for local use.
- API keys entered in the interface are held in that browser tab's session storage and sent to the local server with requests. The server does not persist them.
- Keys in `.env` remain on your machine. Never commit or share that file.
- Provider requests are sent to the provider you select; review that provider's terms and privacy practices.
- Local Ollama and LM Studio endpoints are restricted to localhost. Custom remote endpoints must use HTTPS and pass URL validation.
- The app includes request-size limits, rate limiting, and security headers, but it is not designed to be exposed directly to the public internet.

Only enter credentials into a copy of the app you trust.

## Current limitations

- Conversation history is kept in the current browser session; it is not saved after a page refresh.
- Long-term memory, accounts, and persistent chat storage are not implemented.
- Hosted providers require valid credentials, network access, and access to the selected model.
- Local providers require their server and model to be installed and running.
- Provider support currently focuses on text chat; capabilities may differ by provider.

## Project principles

- Keep the app lightweight and easy to run.
- Preserve the one-command startup: `python app.py`.
- Prefer the Python standard library and browser-native technologies.
- Avoid adding dependencies, services, or build steps without a concrete need.
- Make focused changes and test them before claiming they work.
- Keep documentation aligned with the features that are actually implemented.

## License

BrainCellGPT is released under the [MIT License](LICENSE).
