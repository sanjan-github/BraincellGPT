# BrainCellGPT

A funny, chaotic chatbot with one braincell remaining.
And a self-hosted, local-first conversational AI interface built with zero external Python dependencies.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![CI Status](https://img.shields.io/badge/CI-Passing-brightgreen.svg)](.github/workflows/ci.yml)
[![Zero External Dependencies](https://img.shields.io/badge/Dependencies-Standard%20Library-brightgreen.svg)](#architecture)

---

## Table of Contents

- [Introduction & Purpose](#introduction--purpose)
- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture & Privacy](#architecture--privacy)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Supported Providers](#supported-providers)
- [Configuration Reference](#configuration-reference)
- [Security Model](#security-model)
- [Development and Testing](#development-and-testing)
- [Project Structure](#project-structure)
- [License](#license)

---

## Introduction & Purpose

BrainCellGPT was created as a lighthearted, humorous, and delightfully chaotic conversational assistant built purely for entertainment and killing time. Rather than attempting to be an enterprise productivity copilot or rigid coding agent, BrainCellGPT embraces absurd questions, strange tangents, playful banter, and unstructured conversations where having a single collective brain cell is more than enough.

Despite its humorous and casual personality, the project is engineered with serious technical principles: local-first privacy, zero third-party Python dependencies, strict in-memory credential safety, and support for over 15 distinct AI providers.

---

## Overview

BrainCellGPT is an open-source, local-first conversational AI interface designed for users who want full control over their interaction data. It pairs a self-hosted Python backend—powered exclusively by Python's standard library—with a responsive frontend featuring an interactive WebGL Earth visualization.

The application allows users to query over 15 hosted and local large language model (LLM) providers while keeping all conversation history stored on the client side in browser-managed IndexedDB.

---

## Key Features

- **Zero-Dependency Backend**: Runs entirely on the Python Standard Library (`http.server`, `urllib.request`, `sqlite3`). No virtual environment or package installations are required for core execution.
- **Local-First Data Storage**: Complete chat logs and session metadata are stored in the client browser's IndexedDB, rather than in an external database or cloud account.
- **Multi-Provider Architecture**: Direct support for 15+ hosted API providers (OpenAI, Anthropic, Google Gemini, Groq, DeepSeek, Mistral, xAI, OpenRouter, and others) as well as offline local runtimes (Ollama, LM Studio).
- **Interactive WebGL Visualization**: Custom 3D Earth globe with texture mapping and an ambient parallax starfield implemented in vanilla WebGL.
- **Dual Credential Modes**: Use server-configured credentials (including shared Groq access with a 10-attempt local quota) or configure user-supplied API keys directly in the client settings.
- **Ephemeral In-Memory Secrets**: User-provided API keys are held strictly in server RAM with bounded LRU eviction and time-to-live expiration; they are never persisted to disk or browser local storage.

---

## Architecture & Privacy

BrainCellGPT strictly separates client session data, application configuration, and network traffic:

| Data Type | Storage Location | Persistence & Privacy Details |
| :--- | :--- | :--- |
| **Chat Transcripts & History** | Client IndexedDB | Retained exclusively on the local client device. Can be exported or deleted at any time through the UI or browser storage settings. |
| **UI Preferences & Active Profile** | Client `localStorage` | Stores non-sensitive preferences (selected model ID, temperature, active provider selection). Contains no secrets. |
| **Runtime API Keys** | Server RAM | Keys submitted via Settings are cached in an in-memory dictionary (max 128 profiles, 24-hour TTL) and cleared immediately upon server shutdown. |
| **Shared Usage Quota** | Local SQLite Database | Retains anonymized request identifiers and message attempt counters to enforce installation limits. No message bodies or prompt contents are stored. |
| **Server Credentials** | Host `.env` File | Plaintext configuration for host operators. Must be restricted via filesystem permissions and never checked into source control. |

```
┌────────────────────────────────────────────────────────┐
│                      Client Browser                    │
│  ┌───────────────────────┐   ┌──────────────────────┐  │
│  │      Vanilla UI       │   │   Browser Storage    │  │
│  │   (HTML5/CSS3/WebGL)  │   │  - IndexedDB (Chats) │  │
│  └───────────┬───────────┘   │  - localStorage      │  │
│              │               └──────────────────────┘  │
└──────────────┼─────────────────────────────────────────┘
               │ HTTP API (Port 8000)
┌──────────────▼─────────────────────────────────────────┐
│                    Host Machine (app.py)               │
│  ┌───────────────────────┐   ┌──────────────────────┐  │
│  │  ThreadingHTTPServer  │   │     Memory Store     │  │
│  │  (Python Stdlib Only) │   │  (Ephemeral Secrets) │  │
│  └───────────┬───────────┘   └──────────────────────┘  │
│              │               ┌──────────────────────┐  │
│              ├───────────────┤   SQLite (Usage DB)  │  │
│              │               └──────────────────────┘  │
└──────────────┼─────────────────────────────────────────┘
               │ HTTPS (Outbound Provider Calls)
┌──────────────▼─────────────────────────────────────────┐
│           Configured LLM Provider Service              │
│  (Groq, OpenAI, Anthropic, Gemini, Ollama, LM Studio)  │
└────────────────────────────────────────────────────────┘
```

> **Note on Network Privacy**: Using hosted providers (e.g., Groq, OpenAI, Anthropic) requires sending prompts and relevant conversation context to the corresponding provider's API. For entirely air-gapped or offline inference, select Ollama or LM Studio.

---

## Prerequisites

- **Python**: Version 3.10 or higher.
- **Browser**: Modern desktop browser supporting WebGL and IndexedDB (Chrome, Firefox, Safari, Edge).
- **API Access**: An API key from any supported provider, or an active local instance of Ollama / LM Studio.

---

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/sanjan-github/BraincellGPT.git
cd BraincellGPT
```

### 2. Configure Environment

Create a `.env` file from the provided template:

**Unix (macOS / Linux):**
```bash
cp .env.example .env
```

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
```

Edit `.env` to include your provider credentials. For standard Groq access:

```dotenv
GROQ_API_KEY=gsk_your_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
PORT=8000
```

### 3. Start the Server

```bash
python app.py
```

### 4. Access the Application

Navigate to `http://localhost:8000` in your web browser. 

*Note: The frontend must be served via the Python application; opening `web/index.html` directly via the file protocol (`file:///`) will not function due to cross-origin API requirements.*

---

## Supported Providers

BrainCellGPT includes predefined configurations for major LLM APIs:

| Provider | Authentication Variable | Default / Recommended Model | Local / Cloud |
| :--- | :--- | :--- | :--- |
| **Groq** | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | Cloud |
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4.1-mini` | Cloud |
| **Anthropic** | `ANTHROPIC_API_KEY` | `claude-3-5-sonnet-latest` | Cloud |
| **Google Gemini** | `GEMINI_API_KEY` | `gemini-3.8-flash` | Cloud |
| **DeepSeek** | `DEEPSEEK_API_KEY` | `deepseek-chat` | Cloud |
| **Mistral AI** | `MISTRAL_API_KEY` | `mistral-small-latest` | Cloud |
| **xAI** | `XAI_API_KEY` | `grok-3-mini` | Cloud |
| **OpenRouter** | `OPENROUTER_API_KEY` | `openai/gpt-4o-mini` | Cloud |
| **Together AI** | `TOGETHER_API_KEY` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | Cloud |
| **Fireworks AI** | `FIREWORKS_API_KEY` | `accounts/fireworks/models/llama-v3p3-70b-instruct` | Cloud |
| **NVIDIA NIM** | `NVIDIA_NIM_API_KEY` | `meta/llama-3.3-70b-instruct` | Cloud |
| **Cerebras** | `CEREBRAS_API_KEY` | `llama-3.3-70b` | Cloud |
| **Azure OpenAI** | `AZURE_OPENAI_API_KEY` | Configured Deployment Name | Cloud |
| **Ollama** | Not required | Locally installed model tag | Local |
| **LM Studio** | Not required | Locally loaded model | Local |
| **Custom OpenAI-Compatible** | `CUSTOM_API_KEY` | Configured Model Name | Either |

---

## Configuration Reference

The following environment variables can be declared in `.env` or passed via system environment variables:

```dotenv
# Core Server Settings
PORT=8000

# Groq
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile

# OpenAI
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini

# Google Gemini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.8-flash

# Anthropic
ANTHROPIC_API_KEY=

# OpenRouter
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-4o-mini

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_DEPLOYMENT=

# Custom Compatible Endpoint
CUSTOM_API_BASE_URL=
CUSTOM_API_KEY=
CUSTOM_MODEL=
```

---

## Security Model

BrainCellGPT is engineered with baseline defenses against credential compromise, SSRF, and request floods:

1. **Loopback Binding**: By default, the server binds to `127.0.0.1` and does not accept remote network connections.
2. **Volatile Secret Management**: API keys submitted through client settings are retained in server RAM within an LRU-evicted cache (maximum 128 entries with a 24-hour expiration). Keys are never written to disk or echoed back to the client.
3. **SSRF Protections**: Outbound requests reject private and loopback address targets unless specifically targeting local developer providers (`localhost` for Ollama on port 11434 or LM Studio on port 1234).
4. **Rate Limiting & Concurrency Throttling**: A sliding-window rate limiter restricts requests to 20 operations per 60 seconds per client IP, backed by a pool limit of 64 concurrent HTTP handlers.
5. **No Third-Party Runtime Dependencies**: Reducing runtime dependencies to zero eliminates supply chain vulnerabilities within the Python dependency tree.

Detailed threat modeling and deployment hardening instructions are documented in [docs/security.md](docs/security.md).

---

## Development and Testing

### Executing the Test Suite

The project includes unit and integration tests covering API routing, usage tracking, credential stores, and security constraints:

```bash
python -m unittest discover -s tests -v
```

### Static Analysis and Smoke Tests

Verify source compilation and perform a clean-install HTTP verification check:

```bash
# Verify Python syntax across all modules
python -m compileall -q app.py backend tests scripts

# Run the automated HTTP smoke suite
python scripts/smoke_clean_install.py
```

### Continuous Integration

All commits are validated across Python 3.11 and 3.12 on Linux, macOS, and Windows runners via GitHub Actions. Refer to [.github/workflows/ci.yml](.github/workflows/ci.yml) for pipeline specifications.

---

## Project Structure

```text
BraincellGPT/
├── app.py                         # HTTP application server and API endpoints
├── backend/                       # Backend architecture modules
│   ├── chat_usage.py              # SQLite-based message tracking and quota controls
│   ├── media.py                   # Multimodal payload processing and normalization
│   ├── provider_http.py           # Resilient HTTP client and SSRF protection
│   ├── provider_registry.py       # LLM provider specifications and model mappings
│   └── secure_store.py            # Local secure credential storage helpers
├── web/                           # Client interface
│   └── index.html                 # Single-file WebGL visualizer, UI, and client logic
├── tests/                         # Test suites for backend, UI, and security regressions
├── docs/                          # In-depth architectural and security documentation
├── scripts/                       # Deployment and verification utilities
├── .env.example                   # Environment configuration template
├── LICENSE                        # MIT license terms
└── README.md                      # Project documentation
```

---

## License

This project is licensed under the terms of the [MIT License](LICENSE).
