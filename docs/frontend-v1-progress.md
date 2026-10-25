# Frontend V1 chat preparation progress

## Current state

- Status: VERIFIED for the committed backend and UI changes. Browser interaction and live provider integrations remain unverified as described below.
- Branch: `main`. Baseline SHA: `b409b646bbee746ad1185972f45b34a8e3bf924e`. Initial worktree was clean. Application code commit: `0044ecd07057c096ee26e1cffc511b071b6647ed`, pushed to origin/main.
- Latest baseline CI: [run 40185691493](https://github.com/sanjan-github/BraincellGPT/actions/runs/40185691493) for the baseline SHA; six Ubuntu/Windows/macOS application jobs passed on Python 3.11 and 3.12. Opt-in live provider/MCP/OAuth checks were skipped.
- First fresh CI: [run 40188928189](https://github.com/sanjan-github/BraincellGPT/actions/runs/40188928189) for commit `2ae55e9c3fea2e0ba757b543c91e0a026eee0734`; all six jobs exposed CHAT-LIMIT-002.
- Corrected commit CI: [run 40189104800](https://github.com/sanjan-github/BraincellGPT/actions/runs/40189104800) for `0044ecd07057c096ee26e1cffc511b071b6647ed`; all six application jobs passed, including syntax, UI presence, unit/security/native credential-store tests, tracked-file secret scan, and clean-install HTTP smoke. The opt-in live integration job was skipped.
- Browser: Brave blocked the existing localhost page and an isolated server on port 8011 with `net::ERR_BLOCKED_BY_CLIENT`. The isolated server used a temporary data directory and was stopped. Browser interaction and visual behavior remain UNVERIFIED.

## Defects and changes

### CHAT-INIT-001 - stale backend displayed a persistent "Not found" state

- Affected: `web/index.html::createConversation`, `initializeConversation`; conversation routes in `app.py`.
- Reproduction: GET `/api/conversations/usage?id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` and POST `{}` to `/api/conversations` on `http://127.0.0.1:8000` returned HTTP 404 `{"error":"Not found"}`.
- Root cause: the local server process was started before the current source routes were added. Current source had both routes.
- Fix: map route-level 404 responses to a restart/reload recovery message; recreate a conversation when the current API reports `Conversation not found.` for an old local ID.
- Regression: `tests.test_chat_ui.ChatInterfaceMarkupTests.test_stale_server_not_found_is_mapped_to_restart_recovery`.
- Verification: focused UI/API tests and full local suites pass. Browser reproduction against updated UI remains blocked.

### CHAT-MODE-001 - access mode was not authoritative and could fall back silently

- Affected: `app.py::generate_reply`, `Handler.do_GET`, `Handler.do_POST`; `backend/chat_usage.py`; settings in `web/index.html`.
- Root cause: no persistent server-selected access mode existed; normal chat could use environment credentials.
- Fix: persist shared-Groq versus own-key mode in SQLite. Shared mode accepts Groq only and ignores client/profile keys. Own-key mode uses a saved profile key and disables environment-key fallback. Nonsecret profile settings use browser `localStorage`; API keys do not enter browser storage. Added first-launch chooser and settings mode selector.
- Regression: request-security tests cover mode persistence/validation, shared key isolation, and own-key use without environment fallback.
- Verification: full local suites pass. Live provider verification was not run.

### CHAT-LIMIT-001 - new chats reset the shared-key allowance

- Affected: `backend/chat_usage.py::reserve_shared_message`; `app.py::Handler.do_POST`; chat UI.
- Root cause: previous allowance was per conversation.
- Fix: atomically limit shared mode to 10 provider attempts across the installation. Failed or timed-out calls count once the call starts; retries use new request IDs. Own-key mode has no BraincellGPT allowance. Existing per-conversation use migrates into the shared counter; deleting a chat does not restore use.
- Regression: store tests cover concurrency, cross-chat persistence, migration, own-key bypass, deduplication, and delete behavior. Request tests reject the eleventh attempt before provider invocation.
- Verification: local tests pass; first CI run exposed an ordering defect below.

### CHAT-HISTORY-001 - conversation history was tab-only

- Affected: `web/index.html` chat lifecycle.
- Root cause: transcripts were held in `sessionStorage` and no saved list existed.
- Fix: keep ordered messages, timestamps, titles, and create/update times in browser IndexedDB. Add conversation list, reopen/continue, and delete actions. Recover old session-only transcripts when possible. Keep provider profile metadata in `localStorage` and keys out of browser storage. Show a received reply even if saving it fails.
- Regression: `tests.test_chat_ui.ChatInterfaceMarkupTests.test_onboarding_and_local_conversation_storage_are_present` checks IndexedDB, writes, deletion, and nonsecret profile persistence.
- Verification: inline JavaScript parses and automated tests pass. Browser create/reopen/delete, resizing, and accessibility flows remain UNVERIFIED due to the localhost block.

### CHAT-LIMIT-002 - missing provider key masked an exhausted quota

- Affected: `app.py::Handler.do_POST` shared-mode validation.
- Reproduction: `python -m unittest tests.test_request_security.RequestSecurityIntegrationTests.test_chat_limit_cannot_be_bypassed_through_direct_api_request -q` with `GROQ_API_KEY` empty and shared use already at 10.
- Root cause: `generate_reply` checked the provider key before calling its request-start callback. CI has no Groq key, so the exhausted request returned 502 before quota enforcement.
- Fix: check the authoritative exhausted count before credential validation, while retaining atomic reservation at provider start for concurrent requests.
- Regression: the request-security test explicitly clears `GROQ_API_KEY`, expects HTTP 429, and confirms the provider is not called.
- Verification: focused API/store tests, full suites, Pyright, compileall, and all six corrected CI application jobs pass.

## Verification for implementation commit

- `python -m unittest discover -s tests -q` - 200 ran, passed; 8 skipped.
- `python -m pytest -q` - 192 passed, 8 skipped, 0 failed.
- `uvx --from pyright pyright --pythonversion 3.11 app.py backend tests` - 0 errors, warnings, or informations.
- `python -m compileall -q app.py backend tests scripts` - passed.
- `python scripts/smoke_clean_install.py` - passed.
- Node `--check` on the inline script extracted from `web/index.html` - passed.
- CI secret scan pattern via `git grep` - no tracked-file matches.
- `git diff --check` - passed.
- Fresh CI run `40189104800` - all six application jobs passed for application commit `0044ecd07057c096ee26e1cffc511b071b6647ed`; live provider/MCP/OAuth job skipped as opt-in.
- Browser flow - NOT VERIFIED; Brave returned `net::ERR_BLOCKED_BY_CLIENT` for localhost.
- Live provider/key verification - NOT RUN; provider requests are mocked in standard tests. Live MCP and OAuth checks are opt-in and were skipped.

## Limitations and next action

- Shared usage is installation-local, not authenticated per user. A user can bypass it by creating another installation or changing local state; it does not protect a public anonymous shared-key deployment.
- Own keys use the existing bounded server-memory cache, expire after 24 hours, and are cleared on server stop. Users must enter them again after restart.
- Browser IndexedDB history is device/browser-specific, may be removed by clearing browser data, and does not sync across devices.
- Next action: NOT APPLICABLE for implementation. Retry browser verification only if Brave permits localhost; live integrations remain optional and unverified.
