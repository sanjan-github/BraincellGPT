# BrainCellGPT

**One brain cell. Infinite confusion.**

BrainCellGPT is a chaotic, absurd, and funny chatbot built for conversations that make absolutely no sense. Expect unexpected replies, ridiculous tangents, and glorious nonsense.

## How to use

**Requirements:** Python 3.10+ and an API key for a supported provider, or a local model running through Ollama.

1. Copy `.env.example` to `.env`.
2. Add your provider's API key and, if needed, model name to `.env`.
3. Run the app:

   ```bash
   python app.py
   ```

4. Open **http://localhost:8000** in your browser.
5. Pick a provider, leave the API key field blank to use the key from `.env`, and start chatting. You can enter a key in the interface instead if you prefer.

Keep `.env` private. Never share or commit real API keys.

## Notes

- Runs locally with one command; no npm or extra Python packages required.
- Supports several cloud providers, Ollama, and compatible custom APIs.
- Conversation history lasts for the current browser session.
- Stop the server with `Ctrl+C`.

## License

MIT. See `LICENSE`.
