<div align="center">

# BrainCellGPT

### One brain cell. Infinite confusion.

An easy-to-run chatbot for entertaining conversations, with an optional local coding agent for working in a project directory.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)

</div>

BrainCellGPT is a Python application with a browser-based chat interface. Connect it to a hosted model provider or a local Ollama or LM Studio server. The backend also exposes a coding-agent API for inspecting and editing files in a configured workspace, running commands when enabled, keeping local edit checkpoints, and connecting to configured MCP servers.

The application is designed for local use. It does not require a frontend build step or a database service.

## Features

- Chat with hosted providers, local model servers, or a compatible custom endpoint.
- Configure multiple provider profiles in the browser session.
- Access the coding-agent API with Code, Ask, Architect, Debug, and Orchestrator modes.
- Browse and search workspace files, preview diffs, and apply edits with bounded file access.
- Restore file edits from checkpoints; agent sessions and checkpoints are stored locally in SQLite.
- Use streaming approvals for agent edits, command execution, and configured MCP tool calls. Non-streaming agent requests use request-level edit and command settings.
- Connect configured MCP stdio or Streamable HTTP servers during streaming agent runs. Remote MCP OAuth authorization-code flows are supported.
- Run the server with Python's standard library. An OS keyring adapter is optional for native credential storage.

Model capabilities vary by provider and model. Tool calling, image input, streaming, and available context depend on the selected service.

## Requirements

- Python 3.10 or later
- A supported provider API key, or a locally running Ollama or LM Studio server with a model installed

The core application does not require `pip install`. The optional `keyring` package enables supported macOS and Linux desktop keyrings when available.

## Quick start

Clone the repository and enter the project directory:

```bash
git clone https://github.com/sanjan-github/BraincellGPT.git
cd BraincellGPT
```

Copy the example configuration file:

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**macOS and Linux**

```bash
cp .env.example .env
```

Add the credentials for the provider you plan to use. For example:

```dotenv
GROQ_API_KEY=your_api_key
GROQ_MODEL=llama-3.3-70b-versatile
PORT=8000
```

Start the application:

```bash
python app.py
```

Open [http://localhost:8000](http://localhost:8000), select a provider profile, and start a conversation. Keep the server running while using the app; stop it with `Ctrl+C`.

Use the local server URL. Opening `web/index.html` directly does not provide the API needed by the interface.

## Providers

Configure only the providers you use in `.env`, or set up a profile in the browser. API keys are provider-specific.

| Provider | Environment variables | Details |
| --- | --- | --- |
| Groq | `GROQ_API_KEY`, `GROQ_MODEL` | Default provider |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI API |
| Azure OpenAI | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` | Use the Azure OpenAI `/openai/v1` base endpoint and deployment name |
| Google Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` | OpenAI-compatible endpoint |
| Anthropic | `ANTHROPIC_API_KEY` | Anthropic Messages API |
| OpenRouter | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | OpenAI-compatible endpoint |
| NVIDIA NIM | `NVIDIA_NIM_API_KEY`, `NVIDIA_NIM_MODEL` | OpenAI-compatible endpoint |
| DeepSeek | `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | OpenAI-compatible endpoint |
| Mistral | `MISTRAL_API_KEY`, `MISTRAL_MODEL` | OpenAI-compatible endpoint |
| xAI | `XAI_API_KEY`, `XAI_MODEL` | OpenAI-compatible endpoint |
| Together AI | `TOGETHER_API_KEY`, `TOGETHER_MODEL` | OpenAI-compatible endpoint |
| Fireworks | `FIREWORKS_API_KEY`, `FIREWORKS_MODEL` | OpenAI-compatible endpoint |
| Cerebras | `CEREBRAS_API_KEY`, `CEREBRAS_MODEL` | OpenAI-compatible endpoint |
| Ollama | None by default | Run Ollama locally and install a model |
| LM Studio | None by default | Start the LM Studio local server |
| Custom compatible API | `CUSTOM_API_BASE_URL`, `CUSTOM_API_KEY`, `CUSTOM_MODEL` | Configure the compatible API base URL and model ID |

Provider defaults are listed in `.env.example`. A provider's model availability and features depend on your account and its current service configuration.

## Coding agent API

The coding agent is available through the backend API; the browser interface currently provides chat, not coding-agent controls. It uses the configured workspace and offers these modes:

- **Code:** read, edit, and run permitted workspace commands.
- **Ask:** inspect and explain without editing files or running commands.
- **Architect:** inspect the project and make Markdown documentation edits only.
- **Debug:** investigate issues and make focused changes when permitted.
- **Orchestrator:** break a task into bounded follow-up tasks.

Workspace changes are constrained by server-side path validation, protected-file rules, file-size limits, and command limits. Enabling edits or commands is an explicit request setting. In streaming runs, each mutating tool call pauses for approval. Agent sessions and edit checkpoints are stored in a local SQLite database; ordinary chat history remains in browser session state.

Configured MCP tools are available during streaming agent runs and require per-call approval. Remote MCP endpoints require public HTTPS. OAuth authorization uses an authorization-code flow with PKCE. MCP server setup is environment-based; see [the architecture notes](docs/architecture.md) for current behavior and limitations.

## Configuration

`.env.example` lists the supported provider keys and model variables. Other common settings include:

| Variable | Purpose |
| --- | --- |
| `PORT` | Local HTTP server port; defaults to `8000` |
| `BRAINCELLGPT_MCP_SERVERS` | JSON configuration for MCP servers; credentials should be referenced through environment variables |
| `BRAINCELLGPT_DATA_DIR` | Optional location for local agent session and checkpoint data |

Do not commit `.env` or put real credentials in source code. Provider profiles and names are stored in browser `sessionStorage`. Profile API keys are held in server process memory, expire after 24 hours, and are cleared when the application stops. MCP OAuth credentials may be stored using a supported OS credential store; the application does not fall back to plaintext credential files.

## Security

The server binds to loopback by default. Do not expose the app directly to an untrusted network. Remote binding is an advanced opt-in that requires a long shared token, an HTTPS public origin, and a TLS reverse proxy; see [the security notes](docs/security.md).

Custom remote provider and MCP endpoints are validated. Remote MCP and custom provider requests use public-address checks and do not follow redirects. Review the selected provider's terms and privacy practices, since prompts and files included in requests are sent to that provider.

Windows filesystem writes and deletion have a known limitation: a hostile concurrent parent-directory or reparse-point swap can race path-based replacement or deletion. See [the architecture notes](docs/architecture.md) for details. Passing the test suite does not establish suitability for public or production deployment.

## Development

Run the unit tests:

```bash
python -m unittest discover -s tests -v
```

Run the HTTP clean-install smoke check:

```bash
python scripts/smoke_clean_install.py
```

Compile the Python sources:

```bash
python -m compileall -q app.py backend tests scripts
```

The CI workflow runs these checks on Windows, macOS, and Linux with Python 3.11 and 3.12. Optional live provider, MCP, and OAuth checks require a manual workflow run with credentials configured.

## Project structure

```text
BraincellGPT/
├── app.py                         # Local HTTP server and chat API
├── backend/                       # Agent, filesystem, provider, MCP, and credential modules
├── web/index.html                 # Browser interface
├── scripts/smoke_clean_install.py # Clean-environment HTTP smoke check
├── tests/                         # Unit and security regression tests
├── docs/                          # Architecture, security, and project notes
└── .github/workflows/ci.yml       # Cross-platform CI workflow
```

## Documentation

- [Architecture and API behavior](docs/architecture.md)
- [Security guidance](docs/security.md)
- [Feature parity and project scope](docs/roo-code-parity.md)

## License

BrainCellGPT is released under the [MIT License](LICENSE).
