# Roo Code Backend Parity

This document tracks the backend behavior being ported into BrainCellGPT's
existing Python server. The reference is the archived, Apache-2.0
[`Roo-Code` repository](https://github.com/RooCodeInc/Roo-Code). BrainCellGPT
keeps its own implementation and local-app boundary; the VS Code extension UI
and extension-host APIs are outside this backend port.

## Implemented

- Code, Ask, Architect, Debug, and Orchestrator modes with server-side tool
  capability filtering.
- OpenAI-compatible tool calls and Anthropic's native Messages tool protocol.
- Bounded list, read, and literal-search tools that omit protected paths.
- Atomic file writes, exact-match search/replace, non-mutating unified diff
  previews, and guarded text diffs.
- Streaming approval queue for each edit and command call, alongside backward-
  compatible request-level edit and command flags. Commands have timeouts and
  bounded captured output.
- Follow-up questions, completion, todo updates, mode switching, and bounded
  sequential subtasks.
- Bounded accumulated tool context and repeat-call protection for identical
  mutating operations.
- Model-assisted semantic compaction of older agent context, preserving the
  current task and complete tool-call exchanges.
- Local SQLite task sessions with bounded turns, deletion, and no persisted API
  keys.
- Configured MCP stdio tool and resource discovery/reads, with modern and legacy
  protocol negotiation, per-call approval, bounded JSON-RPC frames, and filtered
  child environments.
- Remote Streamable HTTP MCP tool/resource discovery and calls over JSON or SSE,
  with modern stateless and legacy session negotiation, pinned public HTTPS
  destinations, redirect rejection, environment-backed request headers, and
  `x-mcp-header` tool parameter mirroring with schema validation.
- Remote MCP OAuth resource and authorization-server discovery, dynamic or
  pre-registered public-client authorization-code flow with PKCE S256, resource
  indicators, access-token refresh, insufficient-scope step-up, and loopback
  callbacks. Tokens use native OS-protected credential storage when available;
  there is no plaintext fallback.
- Reversible file-edit checkpoints and explicit restore/redo operations.
- Per-agent-run grouped checkpoint history with multi-file restore and redo groups.
- OpenAI-compatible and Anthropic server-sent event parsing, incremental text
  deltas, tool progress, and a local-only streaming agent endpoint.
- Provider transport limits, shared concurrency control, no redirects, and
  pinned public-IP connections for custom provider URLs.
- Bounded inline image input for chat and agent APIs, converted to native
  OpenAI-compatible or Anthropic image blocks; session history stores text only.

## Remaining backend work

1. Add OAuth Client ID Metadata Documents and broaden credential-store
   validation across supported desktop platforms.
2. Expand provider adapters and model capability metadata.
3. Add end-to-end acceptance coverage for OAuth callback, task persistence,
   approval, streaming,
   and rollback.

## Current API extension

`POST /api/agent` accepts `mode` (`code`, `ask`, `architect`, `debug`, or
`orchestrator`), `allow_edits`, and `allow_commands`. The latter two are
server-enforced per-request capabilities. The model cannot elevate them by
requesting a tool. Orchestrator subtasks run sequentially and are limited to two
nested levels. Sessions are managed through `GET /api/agent/sessions`,
`GET /api/agent/sessions?id=<id>`, and `DELETE /api/agent/sessions?id=<id>`.
Inspect grouped snapshots in the `checkpoint_groups` field returned for a
session. Restore an individual file snapshot or a complete agent-run group with
`POST /api/agent/restore` and `allow_edits: true`; provide either
`checkpoint_id` or `group_id`. Group restore creates a corresponding redo group.
Disconnect a remote MCP authorization with
`POST /api/mcp/oauth/disconnect` and `{ "server": "configured-server-name" }`;
this clears its in-memory token and reports whether persistent OS credentials
were removed.
Use `POST /api/agent/stream` with the same JSON body for a server-sent event
stream. JSON `data:` frames report `start`, `text_delta`, `tool_start`,
`approval_required`, `tool_result`, `context_compacting`,
`oauth_authorization_required`, `done`, or `error`; `done` carries the standard
agent result.
Mutating tools emit `approval_required` with a bounded action preview and pause
the stream. Resolve that specific action with `POST /api/agent/approvals` using
`{ "approval_id", "session_id", "decision": "approve" | "reject" }`. The queue
is in memory, bounded to 32 pending actions, and expires decisions after three
minutes.

Both `POST /api/chat` and agent endpoints optionally accept `images`, an array
of up to four PNG, JPEG, GIF, or WebP base64 data URLs. Each decoded image is
limited to 3 MB and the combined decoded image data to 8 MB. Provider/model
image support depends on the selected endpoint; text-only requests keep the
existing payload format.

Configure up to eight local or remote MCP servers in the
`BRAINCELLGPT_MCP_SERVERS` environment variable as a JSON list. A local entry
has `name`, `command`, optional `args`, and optional `env`; use
`${VARIABLE_NAME}` in an `env` value to pass a specific host environment
variable to that process. A remote entry has an HTTPS `url` and optional
`headers` and `oauth`; credential headers must reference environment variables,
such as `"Authorization": "Bearer ${MCP_ACCESS_TOKEN}"`. If no Authorization
header is configured, a 401 response starts OAuth automatically. The client
discovers protected-resource and authorization-server metadata, uses PKCE S256
and the MCP URL as the OAuth `resource`, then retries after the loopback callback.
Configure `oauth.client_id` for a pre-registered public client; otherwise the
client uses the authorization server's dynamic registration endpoint. OAuth
tokens are held only in memory and must be authorized again after restarting the
app. Windows tokens use DPAPI-protected files. Linux uses Secret Service through
`secret-tool` or a supported keyring backend. macOS Keychain support is
available when the optional `keyring` package is installed. If the OS store is
unavailable, tokens stay in process memory and must be authorized again after
app restart; no plaintext fallback is used. Remote URLs and OAuth endpoints
must resolve only to public IP addresses.
MCP servers are available for streaming Code, Debug, or Orchestrator runs. Every
MCP tool call pauses for the same session-scoped approval operation. Local
processes start without a shell and with filtered environments, and remain alive
only during one agent run. Remote calls use validated IP addresses and do not
follow redirects.

```json
[
  {
    "name": "project-tools",
    "command": "npx",
    "args": ["-y", "@example/mcp-server"],
    "env": {"SERVICE_TOKEN": "${PROJECT_MCP_TOKEN}"}
  }
]
```

Remote server example:

```json
[
  {
    "name": "remote-tools",
    "url": "https://mcp.example.com/mcp",
    "oauth": {"client_id": "registered-public-client"}
  }
]
```
