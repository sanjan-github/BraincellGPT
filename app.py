"""BrainCellGPT: one-command local app using only Python's standard library.

Run from the repository root: python app.py
Set GROQ_API_KEY in the environment or in a local .env file.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def generate_reply(message: str, history: list[dict], personality: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is missing. Add it to a .env file in the project root."
        )

    styles = {
        "normal": "Be helpful, clear, and accurate. Ask a concise clarification question when needed.",
        "sarcastic": "Use light, playful sarcasm when appropriate. Never let a joke replace a useful answer.",
        "chaotic": "Be energetic and playful, but remain understandable, accurate, and safe.",
        "professional": "Use a calm, concise, workplace-appropriate tone.",
        "brutally_honest": "Be direct and analytical without being insulting. Separate evidence from assumptions.",
    }
    style = styles.get(personality, styles["normal"])
    messages = [{
        "role": "system",
        "content": (
            "You are BrainCellGPT, a context-aware conversational assistant. "
            "Preserve relevant context from the supplied messages. Be honest about "
            "uncertainty and never claim to have performed actions unless a real tool "
            "confirms it. Persistent memory and external tools are not available.\n"
            "Style: " + style
        ),
    }]
    for item in history[-16:]:
        role = item.get("role")
        content = str(item.get("content", ""))[:12000]
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    endpoint = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1200,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise RuntimeError(
                "Groq rejected the request. Check your API key and GROQ_MODEL value."
            ) from exc
        if exc.code == 429:
            raise RuntimeError("Groq rate limit or quota reached. Wait and try again.") from exc
        raise RuntimeError(f"Groq returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Could not reach Groq. Check your internet connection.") from exc

    choices = data.get("choices", [])
    reply = choices[0].get("message", {}).get("content", "") if choices else ""
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError("Groq returned an empty response. Try again.")
    return reply.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "BrainCellGPT/0.1"

    def send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            file_path = WEB_DIR / "index.html"
        elif path == "/health":
            self.send_json(200, {"status": "ok", "service": "braincellgpt", "provider": "groq"})
            return
        else:
            self.send_json(404, {"error": "Not found"})
            return
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_json(500, {"error": "Could not load web/index.html"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/chat":
            self.send_json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 200_000:
                self.send_json(413, {"error": "Request body is empty or too large."})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            message = str(payload.get("message", "")).strip()
            if not message:
                self.send_json(400, {"error": "Type a message first."})
                return
            if len(message) > 12000:
                self.send_json(400, {"error": "Message is too long (12,000 characters maximum)."})
                return
            history = payload.get("history", [])
            if not isinstance(history, list):
                history = []
            history = [
                item for item in history[-16:]
                if isinstance(item, dict) and item.get("role") in ("user", "assistant")
            ]
            reply = generate_reply(message, history, str(payload.get("personality", "normal")))
            self.send_json(200, {"reply": reply})
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Invalid JSON request."})
        except RuntimeError as exc:
            self.send_json(502, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Unexpected server error. Check the terminal for details."})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[BrainCellGPT] {self.address_string()} - {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("\nBrainCellGPT is running.")
    print(f"Open http://localhost:{PORT} in your browser.")
    print(f"Health check: http://localhost:{PORT}/health")
    print("Model provider: Groq")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping BrainCellGPT...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
