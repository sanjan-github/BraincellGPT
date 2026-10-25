# Chat experience follow-up

## Current status

- Status: VERIFIED for implementation commit `1d34735c1a886a67ef7975cf8a0cab0d84c860a1`.
- Baseline: `main` at `ea340f3d7a9e35f40b3fc949a48c9df5a916f31a`; working tree was clean before this task. No pre-existing user changes were present. Changes are limited to chat prompt/provider handling, conversation allowance persistence, chat UI, related tests, smoke coverage, and affected documentation.
- Latest baseline CI: exact baseline commit `ea340f3d7a9e35f40b3fc949a48c9df5a916f31a` passed all six app jobs in run [40178397913](https://github.com/sanjan-github/BraincellGPT/actions/runs/40178397913). This predates the changes below; fresh CI is pending.
- Baseline focused tests: `python -m unittest tests.test_app tests.test_chat_ui tests.test_request_security -q` passed 46 tests.

## Defects and changes

### CHAT-001 — verbose theatrical default replies

- Affected: `app.py::build_system_prompt`, `generate_reply`.
- Reproduction: inspect `POST /api/chat` provider payloads; the shared instruction lacked a short, natural default and both protocols allowed 1,200 output tokens.
- Root cause: product instructions emphasized maximal chaos without setting a normal conversational length or style.
- Fix: shared instructions now request natural, casual, usually 1–4 sentence replies, match user tone, avoid forced/repeated jokes and nicknames, and honor explicit detail requests. Both OpenAI-compatible and Anthropic payloads use the shared 800-token ceiling.
- Regression tests: `tests.test_app.PersonalityPromptTests.test_prompt_requests_natural_brief_responses_without_forced_jokes`; OpenAI-compatible and Anthropic payload tests assert the shared behavior and token limit.
- Verification: focused provider/prompt tests pass; live behavioral quality across models remains unverified.

### CHAT-002 — no durable per-conversation message allowance

- Affected: new `backend/chat_usage.py`; `app.py::Handler.do_GET`, `do_POST`, `generate_reply`; UI chat lifecycle.
- Reproduction: direct `POST /api/chat` calls had no conversation identity or count and could continue without a limit.
- Root cause: chat conversations existed only in browser memory; there was no server-side state to enforce or preserve usage.
- Fix: a small SQLite store persists random conversation IDs, request IDs, and counts only. `BEGIN IMMEDIATE` serializes reservations. Each conversation permits 10 provider attempts; duplicate request IDs are rejected. Reservation occurs after provider validation and slot acquisition, immediately before opening the provider request. A started provider failure/timeout counts; requests rejected before provider initiation do not. There are no automatic retries. Active ID is retained in localStorage; successful transcript text remains in tab sessionStorage. The server never writes message text or keys to this database.
- Regression tests: `tests.test_chat_usage` covers persistence, 10/11 boundary, duplicate IDs, and concurrent reservations; `tests.test_request_security` covers HTTP creation/count, direct API limit bypass, failed-attempt counting, duplicate submission, and missing-key non-consumption. `scripts/smoke_clean_install.py` checks conversation creation and count retrieval.
- Verification: full unit and HTTP regression suites pass; fresh cross-platform CI is pending.

### CHAT-003 — error handling and failed-submit state

- Affected: `app.py::generate_reply`, `Handler.do_POST`; `web/index.html::sendMessage`.
- Reproduction: provider responses were embedded in errors and a failed request left its user message in the next prompt history.
- Root cause: provider HTTP error details were forwarded to the UI; failed user text was appended to committed history before a reply existed.
- Fix: map authentication, permission, bad request, not-found model/endpoint, quota, server outage, timeout, connection, invalid JSON, empty response, and interrupted-response cases to concise messages without provider body details or credentials. Failed UI turns are removed from history, text is restored to the composer, the authoritative usage count is refreshed, and busy state always clears. Resubmission creates a new request ID and is an explicit new attempt.
- Regression tests: `tests.test_app.ApiReliabilityTests` tests safe actionable provider errors and interruptions; `tests.test_request_security` tests a counted timeout and same-ID duplicate rejection.
- Verification: focused error and HTTP tests pass.

### CHAT-004 — box-heavy messages and oversized empty composer

- Affected: `web/index.html` chat message CSS, composer, counter.
- Root cause: message surfaces had card borders and shadows; textarea minimum height was 52px and its max-height did not enable internal scrolling.
- Fix: remove message card borders/background/shadows, preserve settings/composer boundaries and focus-visible control state, start textarea at one line, cap it at 160px with internal scrolling, retain Enter/Shift+Enter behavior, and show a live 10-message counter with disabled state at the limit.
- Regression tests: `tests.test_chat_ui` checks markup, CSS, focus boundary, counter, limit guard, keyboard behavior, and scrolling logic.
- Verification: inline JavaScript parses with Node; markup tests pass. Browser visual QA is unverified: the available browser blocked `http://127.0.0.1:8000` with `ERR_BLOCKED_BY_CLIENT`, and Python Playwright/Selenium are not installed.

## Verification this session

- `python -m unittest tests.test_chat_usage tests.test_app tests.test_chat_ui tests.test_request_security -q`: 59 tests passed.
- `python -m unittest discover -s tests -q`: 188 tests passed, 8 skipped (Python 3.14.6).
- `python -m pytest -q`: 180 passed, 8 skipped (Python 3.14.6).
- `uvx --from pyright pyright --pythonversion 3.11 app.py backend tests`: 0 errors, warnings, or informations.
- `python -m compileall -q app.py backend tests scripts`: passed.
- `python scripts/smoke_clean_install.py`: passed, including conversation creation, usage retrieval, and invalid chat validation; its database is isolated in the script's temporary directory.
- Node inline-script syntax check: passed for one script block.
- `git diff --check`: passed.
- CI-matching `git grep` secret scan on staged files: no matches.
- Fresh CI for implementation commit `1d34735c1a886a67ef7975cf8a0cab0d84c860a1`: run [40185527441](https://github.com/sanjan-github/BraincellGPT/actions/runs/40185527441), success. All six application jobs passed on Ubuntu, Windows, and macOS for Python 3.11 and 3.12. For each job, syntax compilation, UI presence, unit/security/native credential-store tests, tracked-file secret scanning, and clean-install HTTP smoke passed. Individual job summaries/logs were inspected. The opt-in live provider/MCP/OAuth job was skipped for the push event.
- Browser visual QA: NOT VERIFIED; local browser navigation was blocked by the browser environment.
- Live provider/model behavior: NOT VERIFIED for this change; payload contracts and error paths are mock-tested. Existing release-time single Groq spot check predates this change.

## Remaining work

- Implementation changes are committed and pushed on `main`; only the checkpoint has changed after local verification to record the fresh CI result.
- Browser desktop/mobile behavior remains unverified if the local browser restriction persists.
- Provider behavior style remains dependent on each selected model; live integrations are not part of the default CI workflow.

## Completion

- Commit `1d34735c1a886a67ef7975cf8a0cab0d84c860a1` is on `main`; the implementation commit's fresh CI run passed all six application jobs.
- No remaining confirmed implementation defect was found in the requested chat improvements. Browser visual QA and live provider behavior remain specifically unverified as described above.
