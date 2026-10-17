# BrainCellGPT

**One brain cell. Infinite confusion.**

A small context-aware conversational AI prototype. The development setup is deliberately simple: **one Python file, one HTML page, no Node.js, no npm, and no third-party Python packages.**

## Run it

Requirements: Python 3.10+ and internet access for Groq.

1. Open the repository root folder in VS Code.
2. Copy `.env.example` to `.env` in the root folder.
3. Create a Groq API key in your Groq account and add it to `.env`:

   ```text
   GROQ_API_KEY=your_key_here
   GROQ_MODEL=llama-3.3-70b-versatile
   ```

4. Run one command from the repository root:

   ```powershell
   python app.py
   ```

5. Open **http://localhost:8000**.

No `npm install`, no virtual environment, no separate frontend server, no VS Code Go Live.

### Run from VS Code

Use `Terminal → Run Task… → BrainCellGPT: Run`, or type `python app.py` in the terminal. Stop it with `Ctrl+C`.

## Features

- One local Python server serves both the interface and chat API.
- Groq Chat Completions API integration.
- Five personalities: Normal, Sarcastic, Chaotic, Professional, and Brutally Honest.
- Current-session conversation context.
- Clear errors for missing/rejected API keys and connection failures.
- Health check at `http://localhost:8000/health`.

## Current limitations

Conversation history is not saved after refresh. Long-term memory, database storage, authentication, and tool execution are not implemented. A valid Groq API key and internet access are required for AI replies. The configured model must be available to your Groq account; change `GROQ_MODEL` in `.env` if needed. This is a local development prototype, not a production-ready public service.

## Security

Keep the API key in the root `.env` file. Never commit it or put it in browser JavaScript. The app binds to `127.0.0.1` by default, so only your computer can access it.

## Roadmap

1. Make the simple local app reliable.
2. Add durable conversation storage.
3. Add user-controlled long-term memory.
4. Add safe tool calling and evaluation.
5. Add authentication and deployment after the core works.

## License

MIT. See `LICENSE`.
