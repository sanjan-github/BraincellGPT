# Changelog

## [Unreleased]

### Fixed
- **`personality.py`** — Inverted `memory_enabled` condition in `build_system_instruction`.
  When `memory_enabled=True`, the model was being told to behave as if memory was *disabled*, and vice versa.
- **`tests/test_personality.py`** — Added two regression tests covering both branches of the memory policy logic.
- **`README.md`** — Completely rewritten to document the current FastAPI + Next.js + Gemini architecture instead of the outdated `python app.py` + Groq setup.
- **`.github/workflows/ci.yml`** — Added `backend` (pytest) and `frontend` (typecheck + ESLint) CI jobs. Kept the `legacy` job that checks `app.py` syntax and `web/index.html` presence.
- **`next.config.ts`** — Set `outputFileTracingRoot` to silence the Next.js multiple-lockfile workspace root warning.

---

## 0.1.0 — Initial scaffold

- Added FastAPI backend with Gemini API adapter and pydantic-settings config.
- Added a minimal Next.js chat UI with personality selector.
- Added architecture/security notes and evaluation test specifications.
- Marked persistence, authentication, durable memory, and tools as future work.

## 0.0.1 — Legacy prototype

- Single-file Python server (`app.py`) using only the standard library.
- Groq Chat Completions API integration.
- Vanilla HTML/CSS/JS UI served from `web/index.html`.
- Five personalities: Normal, Sarcastic, Chaotic, Professional, and Brutally Honest.
