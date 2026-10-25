# Architecture

## Chat request flow

1. The Python HTTP server serves `web/index.html` at `GET /`.
2. The browser creates or restores a random conversation ID with `POST /api/conversations` and `GET /api/conversations/usage`.
3. The browser sends the current message, bounded conversation history, a unique request ID, and selected provider settings to `POST /api/chat`.
4. `app.py` validates the request, resolves the selected profile key from its bounded in-memory cache (or the matching environment variable), and builds the shared chatbot instruction.
5. Immediately before an outbound provider request begins, the server atomically reserves one of the conversation's 10 message attempts in SQLite. The provider adapter then sends the request with bounded timeouts and response handling.
6. The server returns a JSON reply and the authoritative message count. The browser stores the successful transcript for the current tab session and displays the count.

Conversation text is kept in the current browser tab's `sessionStorage`, restored after refresh in that tab, and sent by the browser with each request. The server trims it to the prompt message and character limits; chat text is not stored in the server database. The active conversation ID is retained in browser `localStorage`, while the server stores only that ID, random request IDs, and the count in `chat-usage.sqlite3`. SQLite transactions serialize reservations across simultaneous threads and processes. Duplicate request IDs are rejected. Requests blocked before the provider call begins do not count; requests count once the outbound provider call is about to start, including attempts that later fail or time out. There are no automatic provider retries. Starting a new conversation creates a new server-side allowance. This is a per-conversation limit, not a global API-key spending cap. The chatbot API returns a complete reply; it does not stream tokens.

## Components

| Component | File | Responsibility |
| --- | --- | --- |
| Local HTTP server and chat API | `app.py` | Serves the interface, validates chat requests, manages profile secrets, and calls model providers |
| Browser interface | `web/index.html` | Chat composition, conversation display, provider profiles, and settings |
| Provider registry | `backend/provider_registry.py` | Provider metadata, defaults, and capabilities |
| Outbound HTTP helpers | `backend/provider_http.py` | Endpoint validation, bounded provider requests, and response reading |
| Media helpers | `backend/media.py` | Image payload normalization and provider-specific content formatting |
| Credential helpers | `backend/secure_store.py` | Native secure storage for supporting credential flows |

The app's supported browser API includes `GET /`, `GET /health`, `GET /api/profile-secret-status`, `GET /api/conversations/usage`, `POST /api/conversations`, `POST /api/chat`, `POST /api/test-connection`, and `POST /api/profile-secret`. Requests that are not part of this chatbot API return `404 Not found`.

The repository retains backend modules and tests for workspace tools, checkpoint state, approvals, and MCP support. These modules are not exposed through the BrainCellGPT chatbot HTTP interface.

## Providers and credentials

The provider registry includes hosted services, local Ollama and LM Studio endpoints, and a custom compatible endpoint. Provider selection and model IDs are configurable through browser profiles or environment variables; see the project README and `.env.example`.

Keys entered in the UI are sent to the local app and stored only in a bounded process-memory cache. Environment keys remain in the server environment or local `.env`. Provider requests are made to the selected service; prompts and history may therefore leave the local machine when a hosted service is selected.

## Security boundaries

The server binds to loopback by default. It validates request hosts and origins, limits request sizes and rates, and does not return saved keys to the browser. Custom and remote provider endpoints are validated, redirects are not followed, and outbound responses are bounded. Refer to [Security](security.md) before enabling remote access.

## Current product limitations

- Conversation text is not stored server-side. The active conversation ID and usage count persist locally; its transcript is restored within the same browser tab session.
- Each conversation allows 10 provider request attempts. A started request consumes one even when its result is a provider error or timeout; requests rejected before the outbound call begins do not. A new conversation receives a new allowance. This does not cap total provider-account spending.
- Chat responses are returned as complete JSON replies rather than token streams.
- One live Groq request through `POST /api/chat` succeeded during 1.0 preparation. Mocked tests and this single spot check do not establish availability for other providers, models, accounts, or regions.
- The app is intended for local use. Remote access is an advanced opt-in and depends on a correctly configured TLS reverse proxy.
