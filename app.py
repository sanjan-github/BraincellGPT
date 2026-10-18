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
# Resolve PORT after dotenv so a PORT entry in .env is respected.
PORT = int(os.environ.get("PORT", "8000"))


def generate_reply(message: str, history: list[dict], personality: str, provider: str = "groq", user_api_key: str = "", base_url: str = "", model_override: str = "") -> str:
    """Call Groq, Anthropic, Ollama, or an OpenAI-compatible endpoint."""
    provider = provider.strip().lower()
    api_key = user_api_key.strip()
    model = model_override.strip()
    if provider == "groq":
        api_key = api_key or os.environ.get("GROQ_API_KEY", "").strip()
        model = model or os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        endpoint = "https://api.groq.com/openai/v1/chat/completions"
        protocol = "openai"
        label = "Groq"
    elif provider == "openai":
        model = model or "gpt-4.1-mini"
        endpoint = "https://api.openai.com/v1/chat/completions"
        protocol = "openai"
        label = "OpenAI"
    elif provider == "openrouter":
        model = model or "openai/gpt-4o-mini"
        endpoint = "https://openrouter.ai/api/v1/chat/completions"
        protocol = "openai"
        label = "OpenRouter"
    elif provider == "nvidia_nim":
        model = model or "meta/llama-3.3-70b-instruct"
        endpoint = "https://integrate.api.nvidia.com/v1/chat/completions"
        protocol = "openai"
        label = "NVIDIA NIM"
    elif provider == "ollama":
        base = (base_url.strip() or "http://localhost:11434/v1").rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        model = model or "llama3.2"
        protocol = "openai"
        label = "Ollama"
        api_key = api_key or "ollama"
    elif provider == "custom":
        if not base_url.strip():
            raise RuntimeError("Enter the API base URL for your provider.")
        endpoint = base_url.strip().rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        if not model:
            raise RuntimeError("Enter a model ID for your provider.")
        protocol = "openai"
        label = "Custom provider"
    elif provider == "anthropic":
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        model = model or "claude-3-5-haiku-latest"
        endpoint = "https://api.anthropic.com/v1/messages"
        protocol = "anthropic"
        label = "Anthropic"
    else:
        raise RuntimeError("Choose a supported provider.")
    if not api_key and provider != "ollama":
        raise RuntimeError("API key is missing. Enter your key in provider settings.")
    if len(api_key) > 500 or len(endpoint) > 2000 or len(model) > 300:
        raise RuntimeError("Provider settings are too long.")
    styles = {
        "normal": "Be helpful, clear, and accurate. Ask a concise clarification question when needed.",
        "sarcastic": "Use light, playful sarcasm when appropriate. Never let a joke replace a useful answer.",
        "chaotic": "Be energetic and playful, but remain understandable, accurate, and safe.",
        "professional": "Use a calm, concise, workplace-appropriate tone.",
        "brutally_honest": "Be direct and analytical without being insulting. Separate evidence from assumptions.",
    }
    style = styles.get(personality, styles["normal"])
    system_text = ("You are BrainCellGPT, a context-aware conversational assistant. Preserve relevant context from supplied messages. Be honest about uncertainty. Persistent memory and external tools are not available.\nStyle: " + style)
    messages = [{"role": "system", "content": system_text}]
    for item in history[-16:]:
        role = item.get("role")
        content = str(item.get("content", ""))[:12000]
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    headers = {"Content-Type": "application/json"}
    if protocol == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        payload = {"model": model, "system": system_text, "messages": [m for m in messages if m["role"] != "system"], "max_tokens": 1200, "temperature": 0.7}
    else:
        headers["Authorization"] = "Bearer " + api_key if api_key else ""
        if not api_key:
            headers.pop("Authorization", None)
        payload = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 1200}
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise RuntimeError(label + " rejected the request. Check your API key and model ID.") from exc
        if exc.code == 404:
            raise RuntimeError(label + " endpoint or model was not found. Check the model ID and base URL.") from exc
        if exc.code == 429:
            raise RuntimeError(label + " rate limit or quota reached. Wait and try again.") from exc
        raise RuntimeError(label + " returned HTTP " + str(exc.code) + ".") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Could not reach " + label + ". Check your connection and endpoint URL.") from exc
    if protocol == "anthropic":
        parts = data.get("content", [])
        reply = "".join(part.get("text", "") for part in parts if isinstance(part, dict) and part.get("type") == "text")
    else:
        choices = data.get("choices", [])
        reply = choices[0].get("message", {}).get("content", "") if choices else ""
        if isinstance(reply, list):
            reply = "".join(part.get("text", "") for part in reply if isinstance(part, dict))
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError(label + " returned an empty response.")
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
            self.send_json(200, {"status": "ok", "service": "braincellgpt", "providers": ["groq", "openai", "anthropic", "openrouter", "nvidia_nim", "ollama", "custom"]})
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
            def text_field(name: str, default: str = "") -> str:
                value = payload.get(name, default)
                return value if isinstance(value, str) else default
            reply = generate_reply(
                message, history, text_field("personality", "normal"),
                text_field("provider", "groq"), text_field("api_key"),
                text_field("base_url"), text_field("model")
            )
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
