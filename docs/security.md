# Security guidance

BrainCellGPT is intended to run on the same computer as its user. The default bind address is `127.0.0.1`; keep that default for ordinary use.

## Provider credentials

- Never commit `.env` or paste a real credential into source code or an issue.
- Keys entered in **Chat settings** are sent to the local server and held in a bounded process-memory cache. The cache holds up to 128 profiles, expires keys after 24 hours, and is cleared when the server stops.
- The server does not return saved profile keys to the browser. The browser stores nonsecret provider profile metadata in `localStorage` and conversation messages in IndexedDB; API key values are not stored in browser storage.
- Keys in `.env` remain available to the server process. Protect the file and machine with the same care as the provider account.
- The local `chat-usage.sqlite3` database stores random conversation/request IDs, the selected access mode, and the shared-key allowance count. It does not store chat text or provider keys; on POSIX systems its directory and file are restricted to the current user where the platform permits.
- A hosted provider receives the submitted prompt and conversation history. Review that provider's privacy, retention, and training policies. Local Ollama and LM Studio requests go to the configured local service.

## Local HTTP protections

The app checks Host and Origin values, rejects untrusted browser origins, applies request-size and per-client rate limits, and sets security-related response headers. Its loopback HTTP connection is local plaintext; machine-local software with access to that connection can potentially inspect submitted data.

Do not forward port `8000` to the public internet, expose the server directly on a network, or treat loopback binding as user authentication.

## Advanced remote access

Remote binding is disabled unless explicitly enabled. The application requires `BRAINCELLGPT_ALLOW_REMOTE=1`, a `BRAINCELLGPT_REMOTE_AUTH_TOKEN` of at least 32 characters, and a bare HTTPS `BRAINCELLGPT_PUBLIC_ORIGIN`. The server validates the configured host and origin and accepts the token through Bearer authorization or Basic authentication (username `braincell`).

The Python app itself serves HTTP. Remote use therefore depends on a trusted TLS reverse proxy that terminates HTTPS and connects to the app over a protected interface. Keep the backend port inaccessible from untrusted networks, use a randomly generated token, and do not log authorization headers or request bodies at the proxy. The application does not provide user accounts or per-user isolation.

The app's security checks reduce common risks but do not constitute an independent deployment security assessment. The test suite uses mocks for provider calls and does not verify live service behavior.

## Shared-key allowance limits

Shared Groq access is limited to 10 started provider attempts in the local app database across conversations. This is useful for a personal local installation, but it is not authenticated per-user enforcement: a person able to create another installation or modify local state can bypass it. Do not rely on this allowance to protect a shared API key on a public anonymous deployment. The shared Groq key stays on the server and is never included in frontend responses.

Conversation transcripts stay in this browser's IndexedDB. They are not uploaded as a database or synchronized across devices. Clearing browser data can remove them.
