# Backend completion progress

## Baseline

- Status: IN PROGRESS
- Branch / HEAD: `main` / `235dbca0696860e21e5744b98ce68c7016dd9738` (`cleared errors`)
- Working tree was clean at start; no pre-existing uncommitted changes.
- Instructions: user supplied `AGENTS.md` reference to `C:\Users\TestUser\.codex\RTK.md`; no repository `AGENTS.md` or `CLAUDE.md` found.
- Initial Python: 3.14.6. CI Python 3.11 and 3.12 installed with `uv` for verification.
- Baseline: `python -m pytest -q` — 168 passed, 7 skipped, 62 subtests passed.
- CI run `40170135255`: failed on Ubuntu/macOS/Windows with Python 3.11/3.12; syntax passed, unit tests failed, secret scan and clean-install smoke were skipped. POSIX logs show directory-swap tests failed because the swap did not occur. Windows logs show missing success keys but do not log the actual returned JSON errors.
- Diagnostics: Pyright 1.1.414 baseline, Python 3.11, `app.py backend tests`: 0 errors, 0 warnings, 0 informations. This is not a Pylance result; Pylance is unavailable locally and there is no project analyzer configuration.
- Requested “caveman” skill was not found in the available skill catalog, repository, or initial skill paths.

## Phases

- A baseline and diagnostics: VERIFIED
- B reported CI failures: IN PROGRESS (POSIX race fix passed; path-alias fix is local only pending a second CI run)
- C agent tool contracts: VERIFIED by code review and full test suites on Windows Python 3.11/3.12
- D filesystem security and cross-platform correctness: IN PROGRESS (POSIX race tests passed on Linux/macOS; Windows write/delete limitation remains documented)
- E HTTP API, streaming, approvals: VERIFIED by code review and full test suites on Windows Python 3.11/3.12
- F providers and supporting backend modules: VERIFIED by code review and full test suites on Windows Python 3.11/3.12
- Final verification: IN PROGRESS

## Confirmed defects and fixes

### FS-001 — dirfd capability detection changed under monkeypatch

- Affected: `backend/workspace_fs.py::_supports_dirfd`.
- Reproduction: `python -m unittest discover -s tests -p test_agent_files.py -v` on POSIX; observed in CI run `40170135255`.
- Root cause: the function compared the current `os.open` object to `os.supports_dir_fd`; the tests' wrapper changed the object identity and selected the unanchored path fallback.
- Fix: cache `os.open`, `os.stat`, and `os.unlink` dirfd capability at import. Added `test_dirfd_capability_survives_os_open_wrapper`; strengthened existing tests to print the returned tool error if a success key is missing.
- Verification: Pyright and all local Windows tests pass. POSIX execution remains unverified because this host has no WSL or Docker; GitHub's failed run predates the fix.

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
- Verification: focused tests pass on local Windows Python 3.11, 3.12, and 3.14; full suites pass locally. Fresh CI for this fix is pending.

## Verified limitation

- Windows write replacement and deletion still resolve parent directories by path. The final `os.replace` can race a hostile directory/reparse-point swap. An attempted integration of the existing handle-rename helper failed Windows tests with `WinError 32` because the temporary handle lacks compatible delete sharing, so that experiment was reverted. The limitation is documented in `docs/architecture.md`; no stronger Windows guarantee is claimed.

## Documentation correction

- `docs/roo-code-parity.md` now describes OAuth authorization-code exchange as implemented and covered by unit tests; opt-in live OAuth tests only verify remote metadata discovery.

## Verification this session

- Current base commit: `95431bcf42233651f4b3917f4c357a5d6b7ace2d`, pushed to `origin/main`. The current follow-up filesystem alias fix is uncommitted in `backend/agent.py` and `tests/test_agent_files.py` only.
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

## Next action

Commit only `backend/agent.py`, `tests/test_agent_files.py`, and this checkpoint update on `main`; push to `origin/main`, then inspect all six fresh CI job logs. Fix any remaining reproduced failures and update this checkpoint with the final commit SHA and CI result.
