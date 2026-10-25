# Frontend V1 chat preparation progress

## Current state

- Status: IN PROGRESS; local implementation and verification are complete, but fresh remote CI and browser interaction could not be verified for these uncommitted changes.
- Branch and baseline: `main`, HEAD `b409b646bbee746ad1185972f45b34a8e3bf924e`; this is still the current commit. All 10 modified files are task changes; the initial worktree was clean. No changes have been committed or pushed.
- Latest remote CI before this work: [run 40185691493](https://github.com/sanjan-github/BraincellGPT/actions/runs/40185691493), for baseline commit `b409b646bbee746ad1185972f45b34a8e3bf924e`. All six Ubuntu/Windows/macOS application jobs passed on Python 3.11 and 3.12; the opt-in live provider/MCP/OAuth job was skipped. The run does not cover this worktree.
- Browser UI attempt: Brave blocked both the existing localhost page and the isolated current-source instance with `net::ERR_BLOCKED_BY_CLIENT`. The isolated server was run on port 8011 with a temporary data directory and then stopped. Browser interaction and visual behavior remain UNVERIFIED.

## Defects and changes

### CHAT-INIT-001 ? stale backend produced a persistent ?Not found? state

- Affected: `web/index.html::createConversation`, `initializeConversation`; conversation API routes in `app.py`.
- Reproduction: GET `/api/conversations/usage?id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` and POST `{}` to `/api/conversations` at `http://127.0.0.1:8000` both returned HTTP 404 `{"error":"Not found"}`. The server process was started before the current routes were added; current source contains both routes.
- Root cause: the running local server was stale relative to the current source, not a route or ID defect in current source.
- Fix: map route-level 404s to a specific restart/reload message; treat the current API's `Conversation not found.` as a stale local conversation and create a new one.
- Regression: `tests.test_chat_ui.ChatInterfaceMarkupTests.test_stale_server_not_found_is_mapped_to_restart_recovery`.
- Verification: focused UI/API tests and full suites pass. The browser itself could not be opened because Brave blocked localhost.

### CHAT-MODE-001 ? access mode and credentials could silently fall back

- Affected: `app.py::generate_reply`, `Handler.do_GET`, `Handler.do_POST`; `backend/chat_usage.py`; settings in `web/index.html`.
- Root cause: the prior chat route permitted environment credentials without an explicit choice, and there was no authoritative persisted access mode.
- Fix: persist shared-Groq versus own-key mode in local SQLite. Shared mode accepts only Groq and always uses the server's `GROQ_API_KEY`, ignoring client/profile keys. Own-key mode uses the selected profile's server-memory secret and disables environment-key fallback. Provider profiles persist as nonsecret `localStorage` data; key values are not written to browser storage. The UI exposes a first-run chooser and settings selector.
- Regression: `tests.test_request_security` covers config persistence/validation, shared mode ignoring client and saved profile keys, and own mode using its saved key despite an environment shared key.
- Verification: full unit and pytest suites pass; Pyright reports zero diagnostics. Live provider verification was not run.

### CHAT-LIMIT-001 ? shared quota reset when creating conversations

- Affected: `backend/chat_usage.py::reserve_shared_message`; `app.py::Handler.do_POST`; chat UI usage state.
- Root cause: the previous allowance was per conversation, so creating a new chat reset it.
- Fix: atomically reserve at most 10 started provider attempts per local installation across conversations; retain request IDs against duplicate submissions. Failed or timed-out provider calls count after they start, retries require a new request ID, and own-key mode does not use this allowance. Existing per-conversation counts migrate into the new allowance. Deleting chats does not restore it.
- Regression: `tests.test_chat_usage` covers concurrent reservations, cross-conversation persistence, old database migration, own-key bypass, duplicate IDs, and deletion behavior; `tests.test_request_security` verifies the eleventh shared request is rejected before provider invocation.
- Verification: focused tests and full unit/pytest suites pass.

### CHAT-HISTORY-001 ? conversation history was tab-only

- Affected: `web/index.html` local chat lifecycle.
- Root cause: the transcript lived in `sessionStorage`; there was no saved conversation list.
- Fix: store ordered messages, timestamps, titles, and creation/update times in browser IndexedDB; render a local conversation list; reopen, continue, and delete conversations. Recover old session-only transcripts into IndexedDB when available. A provider reply remains displayed if local persistence fails. Profile metadata moves to `localStorage`; API keys remain outside browser storage.
- Regression: `tests.test_chat_ui.ChatInterfaceMarkupTests.test_onboarding_and_local_conversation_storage_are_present` checks IndexedDB setup, writes, deletion, and nonsecret profile persistence.
- Verification: inline JavaScript parses and all automated tests pass. Actual browser create/reopen/delete/resize/accessibility flows remain UNVERIFIED due to the browser localhost block.

## Verification run for this worktree

- `python -m unittest discover -s tests -q` ? 200 tests ran, passed; 8 skipped.
- `python -m pytest -q` ? 192 passed, 8 skipped, 0 failed.
- `uvx --from pyright pyright --pythonversion 3.11 app.py backend tests` ? 0 errors, warnings, or informations.
- `python -m compileall -q app.py backend tests scripts` ? passed.
- `python scripts/smoke_clean_install.py` ? passed.
- Node `--check` on the inline script extracted from `web/index.html` ? passed.
- CI secret scan pattern via `git grep` ? no tracked-file matches.
- `git diff --check` ? passed.
- Fresh GitHub Actions for this worktree ? NOT RUN; current changes are uncommitted, and the most recent run is for the baseline commit above.
- Browser chat flow ? NOT VERIFIED; Brave returned `net::ERR_BLOCKED_BY_CLIENT` for localhost.
- Live provider/key verification ? NOT RUN; provider integration tests use mocks. Live MCP and OAuth checks are not part of push CI and remain unverified.

## Remaining risks and next action

- The 10-attempt shared Groq allowance is enforced in a local installation database, not per authenticated user. Anonymous users able to create another installation or alter local state can bypass it; do not rely on it for a public shared-key deployment.
- Own keys are held in the existing bounded process-memory cache, expire after 24 hours, and are cleared when the server stops; users must save them again after restart. They are not stored in browser storage.
- IndexedDB history is specific to the browser/device, can be deleted by clearing browser storage, and does not synchronize across devices.
- Actual browser interaction and live provider, MCP, and OAuth integrations remain unverified.
- Next action: review the final diff, then commit/push through the repository workflow if release preparation is authorized; inspect the resulting per-job CI. Repeat browser verification only if the local browser stops blocking localhost.
