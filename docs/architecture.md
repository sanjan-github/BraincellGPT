# Architecture

## Chat request flow

1. The Python HTTP server serves `web/index.html` at `GET /`.
2. The browser loads the selected access mode and shared allowance from the local server, then creates or restores a random conversation ID.
3. The browser stores conversation titles, timestamps, and ordered user/assistant messages in IndexedDB. It sends the current message and bounded prior history to `POST /api/chat`.
4. `app.py` reads the access mode from the local SQLite database. Shared mode accepts only Groq and uses the server-side `GROQ_API_KEY`; own-key mode resolves the selected profile's key from the bounded server-memory cache and does not fall back to environment credentials.
5. Immediately before an outbound provider request begins, the server reserves a unique request. Shared mode atomically consumes one of 10 attempts across the local installation; own-key mode has no BraincellGPT allowance. Provider errors after request start still consume an attempt. There are no automatic provider retries.
6. The server returns the complete reply. The browser displays it and saves the completed turn locally.

SQLite stores random conversation/request IDs, the access-mode choice, and shared allowance counts; it does not store prompts, replies, or API keys. SQLite transactions serialize concurrent shared-key reservations. Duplicate request IDs are rejected. A local conversation list and full transcripts are stored only in the browser's IndexedDB. The active conversation and nonsecret provider profile settings use browser `localStorage`. Clearing browser storage can remove local conversations; there is no cross-device synchronization. The chatbot returns complete JSON replies rather than streaming tokens.

## Components

| Component | File | Responsibility |
| --- | --- | --- |
| Local HTTP server and chat API | `app.py` | Serves the interface, validates chat requests, manages profile secrets, and calls model providers |
| Browser interface | `web/index.html` | Chat composition, local conversation history, provider profiles, and settings |
| Local access and usage store | `backend/chat_usage.py` | Stores access-mode selection and atomic shared-key usage in SQLite |
| Provider registry | `backend/provider_registry.py` | Provider metadata, defaults, and capabilities |
| Outbound HTTP helpers | `backend/provider_http.py` | Endpoint validation, bounded provider requests, and response reading |
| Media helpers | `backend/media.py` | Image payload normalization and provider-specific content formatting |
| Credential helpers | `backend/secure_store.py` | Native secure storage for supporting credential flows |

The chatbot HTTP API includes `GET /`, `GET /health`, `GET /api/profile-secret-status`, `GET /api/chat/config`, `GET /api/conversations/usage`, `POST /api/conversations`, `POST /api/chat/config`, `POST /api/chat`, `POST /api/test-connection`, `POST /api/profile-secret`, and `DELETE /api/conversations`. Requests outside the chatbot API return `404 Not found`.

The repository retains backend modules and tests for workspace tools, checkpoint state, approvals, and MCP support. These modules are not exposed through the BraincellGPT chatbot HTTP interface.

## Providers and credentials

The provider registry includes hosted services, local Ollama and LM Studio endpoints, and a custom compatible endpoint. In shared mode, the server uses only its configured Groq key; the browser cannot read or override it. In own-key mode, a key entered in Settings is sent to the local server and held in a bounded process-memory cache. It is not saved in browser storage and is cleared when the server stops. Own-key mode does not fall back to a `.env` key.

Prompts and history are sent to the selected provider. Hosted providers may process them under their own privacy and retention terms; local providers receive requests at the configured local service.

## Security boundaries

The server binds to loopback by default. It validates request hosts and origins, limits request sizes and rates, and does not return saved keys to the browser. Custom and remote provider endpoints are validated, redirects are not followed, and outbound responses are bounded. Refer to [Security](security.md) before enabling remote access.

The shared allowance is installation-wide for the local app database, not an authenticated per-user quota. Since the app has no account system, a user can bypass the shared limit by creating another installation or modifying local state. Do not expose a shared-key installation as a public anonymous service and expect this limit to prevent abuse.

## Current product limitations

- Browser conversation history is device/browser-specific, can be deleted by clearing browser storage, and does not synchronize across devices.
- The shared Groq allowance counts a provider attempt when its call begins, including calls that later fail or time out.
- Own-key mode avoids the BraincellGPT allowance, but provider quotas, billing, and rate limits remain in effect.
- Anonymous shared usage is installation-limited rather than identity-limited.
- Mocked tests do not establish live availability for every provider, model, account, or region.
- The app is intended for local use. Remote access is an advanced opt-in and depends on a correctly configured TLS reverse proxy.
