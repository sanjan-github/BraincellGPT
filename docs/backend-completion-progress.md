# Backend completion progress

## Baseline

- Status: IN PROGRESS (local verification passed for FS-004; fresh CI pending)
- Branch / HEAD at resumed audit: `main` / `61ab0345a0f0f1c973bc6902602849726972c277`; matches `origin/main`, working tree clean.
- Working tree was clean at start; no pre-existing uncommitted changes.
- Instructions: user supplied `AGENTS.md` reference to `C:\Users\TestUser\.codex\RTK.md`; no repository `AGENTS.md` or `CLAUDE.md` found.
- Initial Python: 3.14.6. CI Python 3.11 and 3.12 installed with `uv` for verification.
- Baseline: `python -m pytest -q` — 168 passed, 7 skipped, 62 subtests passed.
- CI run `40170135255`: failed on Ubuntu/macOS/Windows with Python 3.11/3.12; syntax passed, unit tests failed, secret scan and clean-install smoke were skipped. POSIX logs show directory-swap tests failed because the swap did not occur. Windows logs show missing success keys but do not log the actual returned JSON errors.
- Diagnostics: Pyright 1.1.414 baseline, Python 3.11, `app.py backend tests`: 0 errors, 0 warnings, 0 informations. This is not a Pylance result; Pylance is unavailable locally and there is no project analyzer configuration.
- Requested “caveman” skill was not found in the available skill catalog, repository, or initial skill paths.

## Phases

- A baseline and diagnostics: VERIFIED
- B reported CI failures: VERIFIED (fresh CI run passed all six OS/Python jobs)
- C agent tool contracts: VERIFIED by code review and full test suites on Windows Python 3.11/3.12
- D filesystem security and cross-platform correctness: VERIFIED with documented Windows write/delete race limitation; POSIX race tests passed on hosted Linux/macOS.
- E HTTP API, streaming, approvals: VERIFIED by route/code review and tests; checkpoint restore partial-storage failure fixed and verified (FS-004).
- F providers and supporting backend modules: VERIFIED by code review and full test suites on Windows Python 3.11/3.12; live integrations remain untested.
- Final verification: IN PROGRESS (new checkpoint-storage failure fix awaits fresh CI)

## Confirmed defects and fixes

### FS-001 — dirfd capability detection changed under monkeypatch

- Affected: `backend/workspace_fs.py::_supports_dirfd`.
- Reproduction: `python -m unittest discover -s tests -p test_agent_files.py -v` on POSIX; observed in CI run `40170135255`.
- Root cause: the function compared the current `os.open` object to `os.supports_dir_fd`; the tests' wrapper changed the object identity and selected the unanchored path fallback.
- Fix: cache `os.open`, `os.stat`, and `os.unlink` dirfd capability at import. Added `test_dirfd_capability_survives_os_open_wrapper`; strengthened existing tests to print the returned tool error if a success key is missing.
- Verification: focused and full local suites pass; POSIX read/write path-swap tests passed in Linux/macOS CI run `40174615797`. This Windows host cannot run those tests locally.

### API-001 — workspace OS errors exposed absolute paths

- Affected: `backend/agent.py::run_tool` error serialization.
- Reproduction: `python -m unittest discover -s tests -p test_agent_files.py -v`.
- Root cause: raw `OSError` text was returned, and filesystem exception strings can include absolute workspace paths.
- Fix: return path-free messages for missing paths and permission errors; other OS errors include only the error number. Added a race-error regression test.
- Verification: full unittest suites pass on Windows Python 3.11/3.12; pytest passes on Python 3.14.6; Pyright reports 0 diagnostics.

### FS-003 — filesystem alias spellings rejected valid workspace paths

- Affected: `backend/agent.py::_safe_path`, `_visible_files`, and workspace-relative path serialization.
- Reproduction: CI run `40173975130`, Windows and macOS, with failures in file listing/search, search-replace, Architect writes, and checkpoint creation. Error payloads showed Windows 8.3 versus long paths and macOS `/var` versus `/private/var` spellings.
- Root cause: the code resolved the root once and each child independently, then compared or relativized path strings. The OS returned different valid spellings for the same directory.
- Fix: use a consistent absolute lexical workspace root for child construction and relative paths; reject absolute/drive-qualified paths and parent traversal; check each child component for symlinks. Actual file operations retain their existing no-follow/handle checks. Added a root-alias listing/search regression test.
- Verification: focused tests pass on local Windows Python 3.11, 3.12, and 3.14; full suites pass locally. Fresh CI `40174615797` passed across Linux, Windows, and macOS on Python 3.11 and 3.12.

### CI-001 — clean-install smoke readiness timeout on macOS

- Affected: `scripts/smoke_clean_install.py::main`.
- Reproduction: CI run `40174307311` and its failed-job rerun, on macOS Python 3.11 and 3.12; both timed out waiting for `/health` after 20 seconds. Unit tests, secret scan, and smoke steps passed on Ubuntu and Windows.
- Root cause: the smoke script's bounded 20-second startup allowance was insufficient on the macOS hosted runners. The script discards child output, so no application exception was reported; the same startup smoke passes locally.
- Fix: extend the bounded readiness allowance to 60 seconds and update the timeout message.
- Regression/verification: `uv run --python 3.11 python scripts/smoke_clean_install.py`, Python 3.12 equivalent, and `python scripts/smoke_clean_install.py` all pass locally. Fresh CI `40174615797` passed all six smoke jobs.

### FS-004 — grouped restore left partial redo checkpoints on storage failure

- Affected: `backend/agent.py::_restore_checkpoint_batch`.
- Reproduction: inject an `OSError` on the second `AgentStore.add_checkpoint` while restoring a two-file checkpoint group; before the fix, the workspace stayed unchanged but the checkpoint count increased from 2 to 3.
- Root cause: redo snapshots were inserted one at a time before the protected filesystem restore block, and failure during a later insertion did not delete earlier redo snapshots.
- Fix: on redo-snapshot creation failure, delete any redo checkpoint IDs already created, then re-raise the original exception. Added `test_group_restore_removes_redo_snapshots_when_snapshot_creation_fails`.
- Verification: focused regression and all seven tests in `test_agent_state.py` pass on Python 3.11; full unittest and pytest suites, Pyright, and compileall pass locally. Fresh CI is pending.

## Verified limitation

- Windows write replacement and deletion still resolve parent directories by path. The final `os.replace` can race a hostile directory/reparse-point swap. An attempted integration of the existing handle-rename helper failed Windows tests with `WinError 32` because the temporary handle lacks compatible delete sharing, so that experiment was reverted. The limitation is documented in `docs/architecture.md`; no stronger Windows guarantee is claimed.

## Documentation correction

- `docs/roo-code-parity.md` now describes OAuth authorization-code exchange as implemented and covered by unit tests; opt-in live OAuth tests only verify remote metadata discovery.

## Resumed security review

- Approval enforcement: streaming `/api/agent/stream` assigns an approval callback and gates mutating workspace, command, and MCP calls per call. Non-streaming `/api/agent` uses explicit request-level edit/command flags. Approval resolution is bound to a pending unguessable approval ID and session, and the session must belong to the configured workspace. Checkpoint restore remains a separate request with `allow_edits: true`.
- API exposure: loopback is the default. Non-loopback binding requires remote opt-in, a 32-character token, and an HTTPS public origin behind a TLS proxy; request Host/Origin validation and bearer/basic token checks are present. The app itself serves HTTP, so deployment depends on the documented trusted TLS proxy and must not expose the backend port directly.
- Credentials: provider profile secrets are process-memory only with a bounded cache/expiry; persistent MCP OAuth secrets use DPAPI or an available native keyring/secret-tool. Store failures return failure and do not write plaintext. Native store availability and remote provider behavior vary by host and are not asserted from mocks.
- Checkpoint restore: entries are session-scoped, workspace paths are revalidated, redo snapshots enable undo, and filesystem mutation failures roll back workspace contents. FS-004 closes the partial redo-record cleanup gap. Windows directory replacement/deletion races remain a documented platform limitation.
- Provider/MCP/OAuth review: outbound provider and remote MCP requests reject redirects and pin public DNS results; response/config limits and timeouts are in place. OAuth metadata/token endpoints require public HTTPS, state and PKCE S256 are checked, and tokens are redacted/persisted only via secure storage. These are code-review findings supported by mocked/unit tests; live external providers, MCP servers, and OAuth servers were skipped in CI.
- No additional confirmed defect found in these reviewed areas.

## Verification this session

- Final implementation commit: `5a6332b97baeb15b50db76efb315010661e71675`, pushed to `origin/main`. Only authorized backend, tests, smoke-check, and progress-checkpoint changes were committed; no unrelated changes were present.
- Fresh CI run for `95431bc`: https://github.com/sanjan-github/BraincellGPT/actions/runs/40173975130. Both Ubuntu jobs passed all tests, secret scan, and clean-install smoke. POSIX read/write path-swap race tests passed on Ubuntu and macOS. Both Windows jobs and both macOS jobs failed five agent filesystem operations because path aliases were compared as different directories. The tool assertions exposed the error payloads; the root cause and follow-up fix are recorded as FS-003. Live external integrations were skipped by workflow design.
- After the FS-003 local fix, `uv run --python 3.11 python -m unittest discover -s tests -q`: 178 run, 8 skipped, success.
- After the FS-003 local fix, `uv run --python 3.12 python -m unittest discover -s tests -q`: 178 run, 8 skipped, success.
- After the FS-003 local fix, `python -m unittest discover -s tests -q` (Python 3.14.6): 178 run, 8 skipped, success.
- After the FS-003 local fix, `uv run --python 3.11 --with pytest python -m pytest -q`: 170 passed, 8 skipped, success.
- After the FS-003 local fix, `uv run --python 3.12 --with pytest python -m pytest -q`: 170 passed, 8 skipped, success.
- After the FS-003 local fix, `python -m pytest -q` (Python 3.14.6): 170 passed, 8 skipped, success.
- Discovery on all three Python versions: 178 pytest items. The earlier direct test-ID set comparison found no difference between unittest and pytest; the added alias test is collected by both. Unittest reports 178 total (170 passing, 8 skipped); pytest splits these into `170 passed, 8 skipped` and reports 62 nested subtests separately.
- `uvx --from pyright pyright --pythonversion 3.11 app.py backend tests`: 0 errors, warnings, or informations.
- `python -m compileall -q app.py backend tests scripts` on Python 3.14, and equivalent `uv run` commands on 3.11 and 3.12: success.
- `uv run --python 3.11 python scripts/smoke_clean_install.py` and Python 3.12 equivalent: both passed after the FS-003 fix.
- Workflow secret scan passed in both Ubuntu CI jobs on commit `95431bc`. The tracked-file scan should run again on the follow-up commit.
- `git diff --check`: success.
- POSIX dirfd race tests passed on Linux/macOS CI for `95431bc`; local Windows cannot run them (no WSL or Docker). The Windows path-alias failures reproduced only on hosted runners before FS-003 and are not reproduced by the local root-alias regression test.
- Fresh run `40174307311` for `690ccf4` had all six unit-test jobs pass; all six syntax and secret-scan steps passed. Ubuntu and Windows clean-install smoke passed. Both macOS clean-install smoke steps timed out at the 20-second readiness limit, including on the failed-job rerun. The 60-second change is local and awaits fresh CI.
- Final fresh CI run `40174615797`: https://github.com/sanjan-github/BraincellGPT/actions/runs/40174615797. All six application jobs passed on Ubuntu, Windows, and macOS with Python 3.11/3.12. Each passed syntax compilation, web UI presence, unit tests, tracked-file secret scan, and clean-install HTTP smoke. The opt-in live integration job was skipped by its `workflow_dispatch` condition.
- Fresh CI `40174752640` for exact SHA `61ab0345a0f0f1c973bc6902602849726972c277` passed all six application jobs; opt-in live integrations were skipped.
- Resumed audit defect reproduction: injected second redo-snapshot storage failure yielded `checkpoints_before=2 checkpoints_after=3` before FS-004.
- After FS-004: `uv run --python 3.11 python -m unittest tests.test_agent_state.AgentCheckpointTests.test_group_restore_removes_redo_snapshots_when_snapshot_creation_fails -v` passed; `uv run --python 3.11 python -m unittest discover -s tests -p test_agent_state.py -v` passed 7 tests.
- Full post-fix local verification: `uv run --python 3.11 python -m unittest discover -s tests -q` passed (179 run, 8 skipped); `uv run --python 3.11 --with pytest python -m pytest -q` passed (171 passed, 8 skipped, 62 subtests); `uvx --from pyright pyright --pythonversion 3.11 app.py backend tests` reported 0 diagnostics; `uv run --python 3.11 python -m compileall -q app.py backend tests scripts` passed; `git diff --check` passed.
- Final full local suites after FS-003: Python 3.11, 3.12, and 3.14 unittest discovery each ran 178 tests with 8 skipped; all passed. Pytest on the same versions reported 170 passed, 8 skipped (178 collected); all passed. The direct ID comparison found 178 tests in each discovery system. The apparent 177 unittest versus 169 pytest difference is presentation: unittest includes the 8 skipped tests in its `Ran 178` total, while pytest separates them into `170 passed` and `8 skipped`; they collect the same tests. Pytest separately reports 62 nested subtests.
- Pyright (`uvx --from pyright pyright --pythonversion 3.11 app.py backend tests`): 0 errors, warnings, or informations; this is Pyright, not Pylance.
- `python -m compileall -q app.py backend tests scripts` on Python 3.14 and equivalent Python 3.11/3.12 commands passed. Clean-install HTTP smoke passed locally on all three Python versions.
- Tracked-file secret scan passed in fresh CI on all six application jobs. Local `git grep` scan found no matches (exit 1 is its expected no-match result). `git diff --check` passed before each commit.

## Next action

Next: commit and push only `backend/agent.py`, `tests/test_agent_state.py`, and this checkpoint update on `main`; inspect fresh CI for the restore cleanup change. Remaining limitations: Windows replacement and deletion remain vulnerable to hostile parent-directory/reparse-point races, as described in `docs/architecture.md`; POSIX race tests passed in Linux/macOS CI but cannot run on this local Windows host. Live external provider/MCP/OAuth checks were skipped because CI only runs them on manual dispatch with configured secrets.
