# Security guidance

BrainCellGPT is intended to run on the same computer as its user. The default bind address is `127.0.0.1`; keep that default for ordinary use.

## Provider credentials

- Never commit `.env` or paste a real credential into source code or an issue.
- Keys entered in **Chat settings** are sent to the local server and held in a bounded process-memory cache. The cache holds up to 128 profiles, expires keys after 24 hours, and is cleared when the server stops.
- The server does not return saved profile keys to the browser. The browser stores provider profile metadata in `sessionStorage`; API key values are not stored there.
- Keys in `.env` remain available to the server process. Protect the file and machine with the same care as the provider account.
- A hosted provider receives the submitted prompt and conversation history. Review that provider's privacy, retention, and training policies. Local Ollama and LM Studio requests go to the configured local service.

## Local HTTP protections

The app checks Host and Origin values, rejects untrusted browser origins, applies request-size and per-client rate limits, and sets security-related response headers. Its loopback HTTP connection is local plaintext; machine-local software with access to that connection can potentially inspect submitted data.

Do not forward port `8000` to the public internet, expose the server directly on a network, or treat loopback binding as user authentication.

## Advanced remote access

Remote binding is disabled unless explicitly enabled. The application requires `BRAINCELLGPT_ALLOW_REMOTE=1`, a `BRAINCELLGPT_REMOTE_AUTH_TOKEN` of at least 32 characters, and a bare HTTPS `BRAINCELLGPT_PUBLIC_ORIGIN`. The server validates the configured host and origin and accepts the token through Bearer authorization or Basic authentication (username `braincell`).

The Python app itself serves HTTP. Remote use therefore depends on a trusted TLS reverse proxy that terminates HTTPS and connects to the app over a protected interface. Keep the backend port inaccessible from untrusted networks, use a randomly generated token, and do not log authorization headers or request bodies at the proxy. The application does not provide user accounts or per-user isolation.

The app's security checks reduce common risks but do not constitute an independent deployment security assessment. The test suite uses mocks for provider calls and does not verify live service behavior.
