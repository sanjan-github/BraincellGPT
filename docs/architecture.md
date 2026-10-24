# Architecture

## Current request path

1. The browser loads `web/index.html` from the Python server (`GET /`).
2. The user types a message and submits the form.
3. The frontend sends `POST /api/chat` to the same Python server with the
   message, bounded client-side history, selected provider profile ID, and settings.
4. `app.py` resolves the profile's in-memory credential (or the matching `.env`
   credential), builds a system instruction, and forwards the conversation to the provider.
5. Groq returns a reply; the server strips and returns it as `{"reply": "..."}`.
6. The frontend appends the reply to the conversation and updates the history array.

## Implementation

| Component | File | Role |
|-----------|------|------|
| HTTP server | `app.py` | Serves UI, handles `/api/chat` and `/health` |
| Chat UI | `web/index.html` | All frontend HTML, CSS, and JavaScript |
| Environment | `.env` (root) | Provider fallback credentials/models and `PORT` |
| Coding agent | `backend/agent.py` | Bounded workspace tools and a multi-step OpenAI-compatible tool-call loop |

## Coding agent API

`POST /api/agent` accepts a `task`, optional bounded `history`, `provider`,
`base_url`, `model`, and `profile_id`. It returns the final `reply`, tool-call
count, and step count. The agent can list, search, and read files beneath the
repository root. Set `allow_edits: true` to permit its `write_file` tool for
that request; edits are otherwise rejected. The API is restricted to loopback
clients like the other state-changing endpoints.

The agent currently uses OpenAI-compatible tool calling, including compatible
hosted APIs and local Ollama/LM Studio endpoints. It has no shell tool, durable
conversation store, authentication, MCP integration, or Anthropic tool adapter.
Tool rounds, file sizes, search results, and workspace paths are bounded.

## Current limitations

- History is supplied by the browser and is not authoritative server-side storage.
- There is no authentication, persistent database, memory retrieval, or tool execution.
- A client could submit fabricated history. Production systems should load conversation
  messages server-side after authorization.
- Saved profile keys live only in server process memory; they are not written to disk,
  returned to the browser, or included in chat requests. They are cleared on shutdown.
- Profile names and nonsecret settings are kept in browser `sessionStorage`.
- The save and verify actions send a key once over the loopback HTTP connection to the
  local server. Do not expose this local app to an untrusted network.

## Target architecture (future)

Browser → authenticated API → conversation orchestrator → context builder →
authorized memory retrieval → model adapter → validated tool calls (if any) →
final response → durable persistence.
