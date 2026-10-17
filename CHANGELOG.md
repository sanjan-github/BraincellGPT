# Changelog

## [Unreleased]

### Fixed
- **`app.py`** — `PORT` was read from the environment before `load_dotenv()` ran,
  so a `PORT` entry in `.env` was silently ignored. `PORT` is now resolved after
  the dotenv call.
- **`README.md`** — Rewrote to describe the actual application (`python app.py`,
  Groq API, `web/index.html`). Removed incorrect FastAPI, Next.js, Gemini, npm,
  and uvicorn instructions.
- **`.github/workflows/ci.yml`** — Replaced the three-job workflow (which tested
  the obsolete FastAPI/Next.js stack) with a single job that tests the actual
  application: syntax check, `web/index.html` presence, and a live smoke-test suite.
- **`docs/architecture.md`** — Updated to describe the actual request path through
  `app.py` and Groq instead of FastAPI/Gemini/Next.js.

---

## 0.1.0 — Single-file refactor

- Simplified to a single Python server (`app.py`) using only the standard library.
- Frontend moved to `web/index.html` (vanilla HTML, CSS, and JS).
- Groq Chat Completions API integration.
- Five personalities: Normal, Sarcastic, Chaotic, Professional, and Brutally Honest.
- One-command startup: `python app.py`.
- Rules for future developers and AI agents added to README.

## 0.0.1 — Initial scaffold

- Initial project skeleton committed.
