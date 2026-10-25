# Architecture

## Chat request flow

1. The Python HTTP server serves `web/index.html` at `GET /`.
2. The browser collects the current message, bounded conversation history, and selected provider settings.
3. The browser sends `POST /api/chat` to the same server.
4. `app.py` validates the request, resolves the selected profile key from its bounded in-memory cache (or the matching environment variable), and builds the product's chat instruction.
5. The provider adapter validates the configured endpoint, sends the request with bounded timeouts and response handling, and extracts the model reply.
6. The server returns a JSON reply. The browser adds it to the current in-memory conversation.

Conversation history is held in browser memory only. It is sent by the browser with each request, trimmed to the server's message and character limits, and cleared on refresh or when a new conversation starts. The chatbot API returns a complete reply; it does not stream tokens.

## Components

| Component | File | Responsibility |
| --- | --- | --- |
| Local HTTP server and chat API | `app.py` | Serves the interface, validates chat requests, manages profile secrets, and calls model providers |
| Browser interface | `web/index.html` | Chat composition, conversation display, provider profiles, and settings |
| Provider registry | `backend/provider_registry.py` | Provider metadata, defaults, and capabilities |
| Outbound HTTP helpers | `backend/provider_http.py` | Endpoint validation, bounded provider requests, and response reading |
| Media helpers | `backend/media.py` | Image payload normalization and provider-specific content formatting |
| Credential helpers | `backend/secure_store.py` | Native secure storage for supporting credential flows |

The app's supported browser API includes `GET /`, `GET /health`, `GET /api/profile-secret-status`, `POST /api/chat`, `POST /api/test-connection`, and `POST /api/profile-secret`. Requests that are not part of this chatbot API return `404 Not found`.

The repository retains backend modules and tests for workspace tools, checkpoint state, approvals, and MCP support. These modules are not exposed through the BrainCellGPT chatbot HTTP interface.

## Providers and credentials

The provider registry includes hosted services, local Ollama and LM Studio endpoints, and a custom compatible endpoint. Provider selection and model IDs are configurable through browser profiles or environment variables; see the project README and `.env.example`.

Keys entered in the UI are sent to the local app and stored only in a bounded process-memory cache. Environment keys remain in the server environment or local `.env`. Provider requests are made to the selected service; prompts and history may therefore leave the local machine when a hosted service is selected.

## Security boundaries

The server binds to loopback by default. It validates request hosts and origins, limits request sizes and rates, and does not return saved keys to the browser. Custom and remote provider endpoints are validated, redirects are not followed, and outbound responses are bounded. Refer to [Security](security.md) before enabling remote access.

## Current product limitations

- Browser conversation history is temporary and is not synchronized or persisted by the server.
- Chat responses are returned as complete JSON replies rather than token streams.
- Provider connectivity tests and mocked unit tests cannot establish that every model is currently available or that a live provider integration works in every account or region.
- The app is intended for local use. Remote access is an advanced opt-in and depends on a correctly configured TLS reverse proxy.
