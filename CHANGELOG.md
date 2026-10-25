# Changelog

## [1.0.0] - 2026-10-25

BrainCellGPT 1.0 centers the application on its original purpose: humorous, entertaining chat.

### Added

- A message composer that remains available throughout the conversation.
- A clear note that browser conversation history is temporary.
- Focus styling for the message composer and a larger send control.
- Regression coverage for the chat layout and the public HTTP route surface.

### Changed

- Removed the unused search field and simulated user profile from the chat sidebar.
- Renamed settings and API-key labels to describe their chat purpose.
- Removed coding-agent and MCP OAuth endpoints from the app's HTTP interface. Their backend modules and direct tests remain in the repository but are not part of the chatbot API.
- Rewrote the README, architecture, and security documentation for the chatbot product.
- Updated the server and outbound provider user-agent version to 1.0.0.

### Fixed

- The composer was inside the welcome panel, which disappears after the first message. It now stays visible so users can continue a multi-turn conversation.

### Known limitations

- Chat history is kept in browser memory and is cleared on refresh or when starting a new conversation.
- Chat replies are returned as complete responses; token streaming is not implemented in the chat interface.
- Automated tests use mocked provider responses. Live provider, local-model, MCP, and OAuth integrations have not been verified as part of this release preparation.
- Passing the test suite does not establish production readiness.

## 0.1.0 — Single-file refactor

- Simplified the original app to a Python server using the standard library.
- Moved the frontend to `web/index.html` (vanilla HTML, CSS, and JavaScript).
- Added Groq Chat Completions integration and the first set of chat personalities.
- Added one-command startup with `python app.py`.

## 0.0.1 — Initial scaffold

- Initial project skeleton committed.
