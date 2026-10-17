# Architecture

## Current request path

1. The browser loads `web/index.html` from the Python server (`GET /`).
2. The user types a message and submits the form.
3. The frontend sends `POST /api/chat` to the same Python server with the
   message text, bounded client-side conversation history, and the selected personality.
4. `app.py` validates the request, builds a system instruction, and forwards
   the conversation to the Groq Chat Completions API (`api.groq.com`).
5. Groq returns a reply; the server strips and returns it as `{"reply": "..."}`.
6. The frontend appends the reply to the conversation and updates the history array.

## Implementation

| Component | File | Role |
|-----------|------|------|
| HTTP server | `app.py` | Serves UI, handles `/api/chat` and `/health` |
| Chat UI | `web/index.html` | All frontend HTML, CSS, and JavaScript |
| Environment | `.env` (root) | `GROQ_API_KEY`, `GROQ_MODEL`, `PORT` |

## Current limitations

- History is supplied by the browser and is not authoritative server-side storage.
- There is no authentication, persistent database, memory retrieval, or tool execution.
- A client could submit fabricated history. Production systems should load conversation
  messages server-side after authorization.
- Provider keys belong only in the root `.env` file and must never reach the browser.

## Target architecture (future)

Browser → authenticated API → conversation orchestrator → context builder →
authorized memory retrieval → model adapter → validated tool calls (if any) →
final response → durable persistence.
