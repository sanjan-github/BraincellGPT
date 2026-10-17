# Architecture

## Current request path

1. The Next.js client sends a message, a bounded client-side history, and the selected personality to FastAPI.
2. FastAPI validates the request with Pydantic.
3. The conversation service builds a system instruction and sends the request to the Gemini `generateContent` REST endpoint.
4. The API returns the reply to the frontend.

## Current limitations

- History is supplied by the browser; it is not authoritative storage.
- There is no authentication, persistent database, memory retrieval, or tool execution yet.
- A client can submit fabricated history. Production systems should load conversation messages server-side after authorization.
- Provider keys belong only in backend environment variables.

## Target architecture

Frontend -> authenticated API -> conversation orchestrator -> context builder -> authorized memory retrieval -> model adapter -> validated tool calls (if any) -> final response -> durable persistence.
