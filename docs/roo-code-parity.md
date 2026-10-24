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
- Atomic file writes, exact-match search/replace, and guarded text diffs.
- Request-level edit and command execution controls; commands have timeouts and
  bounded captured output.
- Follow-up questions, completion, todo updates, mode switching, and bounded
  sequential subtasks.
- Local SQLite task sessions with bounded turns, deletion, and no persisted API
  keys.
- Reversible file-edit checkpoints and explicit restore/redo operations.
- OpenAI-compatible and Anthropic server-sent event parsing, incremental text
  deltas, tool progress, and a local-only streaming agent endpoint.
- Provider transport limits, shared concurrency control, no redirects, and
  pinned public-IP connections for custom provider URLs.

## Remaining backend work

1. Replace request-level command/edit switches with a pending tool approval
   queue and explicit approve/reject/resume operations.
2. Add diff previews and grouped undo/history views for the existing checkpoints.
3. Add configurable MCP stdio servers with tool/resource discovery and the same
   approval policy.
4. Expand provider adapters and model capability metadata, then add context
   budgeting, compaction, image inputs, and repeat-call safeguards.
5. Add end-to-end acceptance coverage for task persistence, approval, streaming,
   and rollback.

## Current API extension

`POST /api/agent` accepts `mode` (`code`, `ask`, `architect`, `debug`, or
`orchestrator`), `allow_edits`, and `allow_commands`. The latter two are
server-enforced per-request capabilities. The model cannot elevate them by
requesting a tool. Orchestrator subtasks run sequentially and are limited to two
nested levels. Sessions are managed through `GET /api/agent/sessions`,
`GET /api/agent/sessions?id=<id>`, and `DELETE /api/agent/sessions?id=<id>`.
Restore a file snapshot with `POST /api/agent/restore` and `allow_edits: true`.
Use `POST /api/agent/stream` with the same JSON body for a server-sent event
stream. JSON `data:` frames report `start`, `text_delta`, `tool_start`,
`tool_result`, `done`, or `error`; `done` carries the standard agent result.
