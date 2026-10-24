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
`base_url`, `model`, `profile_id`, `mode`, `allow_edits`, and `allow_commands`.
It returns the final `reply`, tool-call count, step count, mode, and current
todo list and a persistent `session_id`. `GET /api/agent/sessions` lists local
tasks; add `?id=<session_id>` for turns and edit checkpoints. Delete a task with
`DELETE /api/agent/sessions?id=<session_id>`. Restore an edit with
`POST /api/agent/restore` and an explicit `allow_edits: true`. Use
`POST /api/agent/stream` with the same body for server-sent events (`start`,
`text_delta`, `tool_start`, `approval_required`, `tool_result`, `done`, and `error`). These APIs are
restricted to loopback clients like the other state-changing endpoints.
Streaming edit and command calls pause with `approval_required`; approve or
reject one with `POST /api/agent/approvals` scoped to the session and approval ID.

The agent stores bounded task histories and up to 100 reversible file-edit
checkpoints per session in a private SQLite database under the user's local
application-data directory. API keys are never stored in this database. The
agent supports OpenAI-compatible and Anthropic native tool calling. Its
server-enforced Code, Ask, Architect, Debug, and Orchestrator modes filter tools
by capability. It can read/search files, preview and apply exact-match edits,
run workspace commands behind request-level opt-in or streaming per-call
approval, maintain a task checklist, ask a follow-up, switch modes, and delegate
bounded subtasks. Configured MCP tools use per-call approval too. Tool rounds,
command timeouts and output, file sizes, diff previews, search results, and
workspace paths are bounded.

Roo Code parity work and remaining scope are tracked in
[`roo-code-parity.md`](roo-code-parity.md).

## Current limitations

- Agent session history is stored server-side in the local SQLite database;
  ordinary chat history remains browser-managed.
- The local API has no authentication and remains restricted to trusted loopback
  requests.
- MCP stdio tool and resource discovery/read are supported for streaming runs;
  remote Streamable HTTP MCP servers are not implemented yet.
- Non-streaming tool approval remains request-level; streaming agent calls also
  support a bounded per-tool approval queue. Streaming progress and checkpoint
  restore are implemented.
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
