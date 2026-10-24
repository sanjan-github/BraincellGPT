# Security checklist

Before public deployment:

- Add authentication and per-user authorization to every conversation and memory operation.
- Store conversation history server-side; never trust the client as the source of truth.
- Add rate limiting and request-size limits at the edge and application layers.
- Keep saved model-provider keys in server process memory; never persist or return them. Profile keys are capped at 128 and expire after 24 hours.
- Rotate any credentials that were exposed. Profile keys are cleared when the app stops.
- Avoid logging prompts, secrets, or personal data by default.
- Add deletion and export controls for user data.
- Treat model output and tool arguments as untrusted input.
- Restrict tools to an allow-list and validate structured arguments before execution.
- Add timeouts, retry limits, and cost budgets for model calls. Agent tasks currently cap runtime, tool calls, output, file scans, and file reads.
- Configure production CORS to exact trusted origins.
- Remote binding is disabled by default. Before enabling it, configure
  `BRAINCELLGPT_ALLOW_REMOTE=1`, a 32-character random
  `BRAINCELLGPT_REMOTE_AUTH_TOKEN`, and an HTTPS
  `BRAINCELLGPT_PUBLIC_ORIGIN`; bind only behind a TLS reverse proxy that
  connects to the app over loopback. The proxy hostname is restricted and every
  request requires the token using Bearer or browser Basic authentication.
