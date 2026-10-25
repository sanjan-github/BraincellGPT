<div align="center">

<br>

# 🧠 BraincellGPT

**One Cell. Infinite Confusion.**

A self-hosted AI chatbot with a 3D WebGL globe, 15+ LLM providers, and zero cloud dependencies.  
Your chats never leave your machine.

<br>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![Build](https://img.shields.io/badge/Build-Passing-brightgreen)
![Providers](https://img.shields.io/badge/Providers-15%2B-orange)
![Zero Dependencies](https://img.shields.io/badge/Dependencies-Zero-success)

</div>

---

## ✨ Highlights

| | Feature |
| --- | --- |
| 🌍 | **3D WebGL Earth** — Interactive globe with parallax starfield on the welcome screen |
| 🔒 | **Local-first privacy** — Conversations stored in browser IndexedDB, never uploaded |
| 🤖 | **15+ providers** — Groq, OpenAI, Gemini, Anthropic, DeepSeek, Mistral, xAI, and more |
| 🏠 | **Self-hosted** — Single Python file, zero pip installs, runs on `localhost` |
| 🔑 | **Dual access modes** — Shared Groq key (10 free messages) or bring your own API key |
| 🖥️ | **Local model support** — Connect to Ollama or LM Studio for fully offline inference |
| 💾 | **Persistent history** — Pick up where you left off across browser restarts |
| ⚡ | **One-command startup** — `python app.py` and you're chatting |

---

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/sanjan-github/BraincellGPT.git
cd BraincellGPT

# Create your local configuration
cp .env.example .env          # macOS / Linux
# Copy-Item .env.example .env   # Windows PowerShell

# Add your Groq key (or any supported provider)
# Edit .env → GROQ_API_KEY=your_key_here

# Launch
python app.py
```

Open **http://localhost:8000** — that's it. Choose shared Groq access or configure your own provider in **Chat Settings**.

> [!NOTE]
> No `pip install` required. The server uses only Python's standard library.  
> Opening `web/index.html` directly won't work — the UI communicates through the local HTTP API.

---

## 🔌 Supported Providers

| Provider | Environment Variables |
| --- | --- |
| **Groq** | `GROQ_API_KEY`, `GROQ_MODEL` |
| **OpenAI** | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` |
| **Google Gemini** | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| **Anthropic** | `ANTHROPIC_API_KEY` |
| **OpenRouter** | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| **NVIDIA NIM** | `NVIDIA_NIM_API_KEY`, `NVIDIA_NIM_MODEL` |
| **DeepSeek** | `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` |
| **Mistral** | `MISTRAL_API_KEY`, `MISTRAL_MODEL` |
| **xAI (Grok)** | `XAI_API_KEY`, `XAI_MODEL` |
| **Together AI** | `TOGETHER_API_KEY`, `TOGETHER_MODEL` |
| **Fireworks** | `FIREWORKS_API_KEY`, `FIREWORKS_MODEL` |
| **Cerebras** | `CEREBRAS_API_KEY`, `CEREBRAS_MODEL` |
| **Ollama** | Run locally — no API key needed |
| **LM Studio** | Run locally — no API key needed |
| **Custom API** | `CUSTOM_API_BASE_URL`, `CUSTOM_API_KEY`, `CUSTOM_MODEL` |

Configure credentials in `.env` or enter them at runtime through **Chat Settings**. See [`.env.example`](.env.example) for all variable names and default model IDs.

---

## 🔒 Privacy & Data Storage

BraincellGPT is designed for **local-first operation**. Here's exactly where your data lives:

| Data | Storage Location | Details |
| --- | --- | --- |
| Conversations & messages | Browser IndexedDB | Device-local; deletable from the app or by clearing site data |
| Provider profile settings | Browser localStorage | Configuration metadata only — no API keys |
| Usage tracking | Local SQLite database | Request IDs and counts — no prompts or replies |
| API keys (runtime) | Server process memory | Never persisted to disk; cleared on server restart |
| API keys (.env) | Local `.env` file | Never commit this file to version control |

> [!IMPORTANT]
> When using a hosted provider, your messages are sent to that provider's API for processing. Review the provider's privacy and data retention policies. For fully offline operation, use **Ollama** or **LM Studio**.

---

## 🎮 How It Works

1. **Launch** — `python app.py` starts a lightweight HTTP server on `localhost:8000`
2. **Choose your mode** — Use the shared Groq key (10 messages included) or enter your own API key for any supported provider
3. **Chat** — Conversations are saved locally in your browser and persist across sessions
4. **Manage history** — Create, reopen, continue, or delete conversations at any time

### Shared Key Allowance

The built-in Groq access provides **10 message attempts per installation**. An attempt is counted once the provider request begins, regardless of whether it succeeds. With your own API key, this limit does not apply — only your provider's billing and rate limits govern usage.

---

## 🔐 Security

- API keys are held in a bounded in-memory cache (128 profiles max, 24-hour TTL) — never written to browser storage or returned to the client
- The server binds to `127.0.0.1` by default — not accessible from the network
- Remote access requires an authentication token and a TLS reverse proxy — see [Security Guidance](docs/security.md)
- Never commit `.env` or publish real credentials

---

## 🧪 Development

### Run Tests

```bash
python -m unittest discover -s tests -v
```

### Smoke Check

```bash
python scripts/smoke_clean_install.py
python -m compileall -q app.py backend tests scripts
```

CI runs on **Windows**, **macOS**, and **Linux** with Python 3.11 and 3.12.

---

## 📁 Project Structure

```
BraincellGPT/
├── app.py                          # HTTP server & chatbot API (single file, stdlib only)
├── backend/                        # Provider adapters, credentials, and utilities
│   ├── provider_registry.py        # Provider configuration & capabilities
│   ├── provider_http.py            # Outbound HTTP to LLM providers
│   ├── chat_usage.py               # Usage tracking & rate limiting
│   ├── secure_store.py             # Credential storage support
│   └── media.py                    # Image/media handling
├── web/index.html                  # Complete frontend (HTML + CSS + JS, single file)
├── tests/                          # Unit, integration, and security regression tests
├── docs/                           # Architecture, security, and project documentation
├── scripts/                        # CI smoke checks and utilities
└── .github/workflows/ci.yml        # Cross-platform CI pipeline
```

---

## 📄 License

Released under the [MIT License](LICENSE).

---

<div align="center">

**Built for curious minds and questionable conversations.**

*BraincellGPT — because one brain cell is all you need.*

</div>
