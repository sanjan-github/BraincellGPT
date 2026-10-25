# Changelog

## [1.0.0] - 2026-10-25

BrainCellGPT 1.0 centers the application on its original purpose: humorous, entertaining chat.

### Added

- A message composer that remains available throughout the conversation.
- First-launch choice between the server's shared Groq access and the user's own provider key.
- A local conversation list with create, reopen, continue, and delete actions, backed by browser IndexedDB.
- A server-enforced 10-attempt shared-Groq allowance across conversations on the local installation.
- Focus styling for the message composer and a larger send control.
- Regression coverage for the chat layout and the public HTTP route surface.

### Changed

- Removed the unused search field and simulated user profile from the chat sidebar.
- Renamed settings and API-key labels to describe their chat purpose.
- Removed coding-agent and MCP OAuth endpoints from the app's HTTP interface. Their backend modules and direct tests remain in the repository but are not part of the chatbot API.
- Rewrote the README, architecture, and security documentation for the chatbot product.
- Persisted nonsecret provider profile settings in browser `localStorage`; API keys remain in server memory and are not written to browser storage.
- Clarified that own-key mode bypasses the BrainCellGPT allowance but remains subject to provider billing, quotas, and rate limits.
- Updated the server and outbound provider user-agent version to 1.0.0.

### Fixed

- The composer was inside the welcome panel, which disappears after the first message. It now stays visible so users can continue a multi-turn conversation.
- A stale server's `Not found` response now explains how to restart and reload the current app.
- Shared-key usage persists across new conversations and is reserved atomically; failed provider attempts count after the provider call starts.

### Known limitations

- Chat history is stored in this browser's IndexedDB. Clearing browser storage can delete it; history is device-specific and does not sync across devices.
- The anonymous shared-key limit is installation-local and can be bypassed by creating another installation or modifying local state. It is not user authentication or a public-service abuse control.
- Chat replies are returned as complete responses; token streaming is not implemented in the chat interface.
- Automated provider tests use mocks. Live verification of provider, MCP, and OAuth integrations was not performed for this update.
- Passing the test suite does not establish production readiness.

## 0.1.0 — Single-file refactor

- Simplified the original app to a Python server using the standard library.
- Moved the frontend to `web/index.html` (vanilla HTML, CSS, and JavaScript).
- Added Groq Chat Completions integration and the first set of chat personalities.
- Added one-command startup with `python app.py`.

## 0.0.1 — Initial scaffold

- Initial project skeleton committed.
