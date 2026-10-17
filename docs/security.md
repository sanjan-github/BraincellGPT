# Security checklist

Before public deployment:

- Add authentication and per-user authorization to every conversation and memory operation.
- Store conversation history server-side; never trust the client as the source of truth.
- Add rate limiting and request-size limits at the edge and application layers.
- Keep all model-provider keys server-side and rotate any exposed credentials.
- Avoid logging prompts, secrets, or personal data by default.
- Add deletion and export controls for user data.
- Treat model output and tool arguments as untrusted input.
- Restrict tools to an allow-list and validate structured arguments before execution.
- Add timeouts, retry limits, and cost budgets for model calls.
- Configure production CORS to exact trusted origins.
