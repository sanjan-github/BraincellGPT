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
from urllib.parse import urlparse, urlsplit
from collections import defaultdict, deque
from threading import Lock
import time
import ipaddress
import socket

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
HOST = "127.0.0.1"
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 60
MAX_HISTORY_MESSAGES = 16
MAX_HISTORY_CHARS = 24000
MAX_HISTORY_ITEM_CHARS = 6000
_rate_events: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = Lock()


def rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        events = _rate_events[client_ip]
        while events and now - events[0] > RATE_WINDOW_SECONDS:
            events.popleft()
        if len(events) >= RATE_LIMIT:
            return True
        events.append(now)
        return False


def trim_history(history: list[dict], max_items: int = MAX_HISTORY_MESSAGES, max_chars: int = MAX_HISTORY_CHARS, max_item_chars: int = MAX_HISTORY_ITEM_CHARS) -> list[dict]:
    """Keep the newest valid messages within item and total prompt-size limits."""
    trimmed: list[dict] = []
    total_chars = 0
    for item in reversed(history):
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        content = content[-max_item_chars:]
        remaining = max_chars - total_chars
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[-remaining:]
        trimmed.append({"role": item["role"], "content": content})
        total_chars += len(content)
        if len(trimmed) >= max_items:
            break
    return list(reversed(trimmed))


def validate_provider_url(provider: str, value: str) -> str:
    raw = value.strip()
    if len(raw) > 2000:
        raise RuntimeError("Provider URL is too long.")
    parsed = urlsplit(raw)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Provider URL must not contain credentials, query strings, or fragments.")
    if provider in ("ollama", "lmstudio"):
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise RuntimeError("Local model URLs must use HTTP and point to localhost on this machine.")
        return raw.rstrip("/")
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("Custom provider URLs must use HTTPS.")
    try:
        addresses = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
        if any(not address.is_global for address in addresses):
            raise RuntimeError("Custom provider URL cannot resolve to a private or local network address.")
    except socket.gaierror as exc:
        raise RuntimeError("Could not resolve the custom provider host.") from exc
    return raw.rstrip("/")


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


# Small provider registry: shared OpenAI-compatible request format, provider-specific defaults.
# Native-protocol providers (currently Anthropic) and local/custom endpoints have dedicated paths below.
OPENAI_COMPATIBLE_PROVIDERS = {
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "env_key": "GROQ_API_KEY", "env_model": "GROQ_MODEL"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "env_key": "OPENAI_API_KEY", "env_model": "OPENAI_MODEL"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini", "env_key": "OPENROUTER_API_KEY", "env_model": "OPENROUTER_MODEL"},
    "nvidia_nim": {"label": "NVIDIA NIM", "base_url": "https://integrate.api.nvidia.com/v1", "model": "meta/llama-3.3-70b-instruct", "env_key": "NVIDIA_NIM_API_KEY", "env_model": "NVIDIA_NIM_MODEL"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "env_key": "DEEPSEEK_API_KEY", "env_model": "DEEPSEEK_MODEL"},
    "mistral": {"label": "Mistral", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest", "env_key": "MISTRAL_API_KEY", "env_model": "MISTRAL_MODEL"},
    "xai": {"label": "xAI", "base_url": "https://api.x.ai/v1", "model": "grok-3-mini", "env_key": "XAI_API_KEY", "env_model": "XAI_MODEL"},
    "together": {"label": "Together AI", "base_url": "https://api.together.xyz/v1", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "env_key": "TOGETHER_API_KEY", "env_model": "TOGETHER_MODEL"},
    "fireworks": {"label": "Fireworks AI", "base_url": "https://api.fireworks.ai/inference/v1", "model": "accounts/fireworks/models/llama-v3p3-70b-instruct", "env_key": "FIREWORKS_API_KEY", "env_model": "FIREWORKS_MODEL"},
    "cerebras": {"label": "Cerebras", "base_url": "https://api.cerebras.ai/v1", "model": "llama-3.3-70b", "env_key": "CEREBRAS_API_KEY", "env_model": "CEREBRAS_MODEL"},
}


def build_system_prompt(personality: str) -> str:
    """Build BrainCellGPT's entertainment-first personality instructions."""
    styles = {
        "normal": "Keep the chaos broadly accessible and easy to follow.",
        "sarcastic": "Use a sarcastic edge with sharp, playful sarcasm when it fits, without becoming mean or repetitive.",
        "chaotic": "Push the absurdity and unpredictability further while keeping the conversation coherent.",
        "professional": "Keep the wording a little cleaner, but do not remove the chaotic BrainCellGPT identity.",
        "brutally_honest": "Be blunt and mischievous, but never cruel, unsafe, or needlessly insulting.",
        "curious": "Be intensely inquisitive, questioning everything with childlike wonder and chaotic overthinking.",
        "weird": "Embrace surreal logic, bizarre metaphors, and unorthodox leaps of thought while remaining responsive.",
    }
    style = styles.get(personality, styles["normal"])
    return (
        "You are BrainCellGPT, an entertainment-first chaos companion built for stress relief and timepass. "
        "Your personality is the product: be completely chaotic, absurd, funny, mischievous, and unpredictable, "
        "but sensibly chaotic rather than random. Respond to the user's actual message, use the conversation "
        "context, and make callbacks when they are genuinely funny or useful. Keep your replies coherent enough "
        "to feel intentional. Do not sound like a generic assistant, formal help desk, therapist, or corporate bot. "
        "Do not force a joke into every sentence, repeat the same format or punchline, dump unnecessary lists, "
        "add disclaimers. Do not overexplain jokes. Match the user's energy and let the conversation breathe. "
        "When the user is clearly asking for serious or sensitive help, stay kind and sensible without abandoning "
        "your distinctive voice. Never invent certainty, encourage harm, or use chaos as an excuse to ignore the "
        "user's request. "
        + style
    )


def extract_provider_reply(data: object, protocol: str, label: str) -> str:
    """Extract text from a provider response or raise a readable runtime error."""
    if not isinstance(data, dict):
        raise RuntimeError(label + " returned an invalid response.")
    if protocol == "anthropic":
        parts = data.get("content")
        if not isinstance(parts, list):
            raise RuntimeError(label + " returned an invalid response.")
        reply = "".join(part.get("text", "") for part in parts if isinstance(part, dict) and part.get("type") == "text")
    else:
        choices = data.get("choices")
        if not isinstance(choices, list):
            raise RuntimeError(label + " returned an invalid response.")
        reply = ""
        if choices:
            first_choice = choices[0]
            message = first_choice.get("message") if isinstance(first_choice, dict) else None
            if not isinstance(message, dict):
                raise RuntimeError(label + " returned an invalid response.")
            reply = message.get("content", "")
            if isinstance(reply, list):
                reply = "".join(part.get("text", "") for part in reply if isinstance(part, dict))
            if not isinstance(reply, str):
                raise RuntimeError(label + " returned an invalid response.")
    if not reply.strip():
        raise RuntimeError(label + " returned an empty response.")
    return reply.strip()


def generate_reply(message: str, history: list[dict], personality: str, provider: str = "groq", user_api_key: str = "", base_url: str = "", model_override: str = "") -> str:
    """Call Groq, Anthropic, Ollama, or an OpenAI-compatible endpoint."""
    provider = provider.strip().lower()
    api_key = user_api_key.strip()
    model = model_override.strip()
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        config = OPENAI_COMPATIBLE_PROVIDERS[provider]
        api_key = api_key or (os.environ.get(config.get("env_key", ""), "").strip() if config.get("env_key") else "")
        if not model:
            model = (os.environ.get(config["env_model"], "").strip() or config["model"]) if config.get("env_model") else config["model"]
        endpoint = config["base_url"] + "/chat/completions"
        protocol = "openai"
        label = config["label"]
    elif provider == "lmstudio":
        base = validate_provider_url("lmstudio", base_url.strip() or "http://localhost:1234/v1")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        model = model or "local-model"
        protocol = "openai"
        label = "LM Studio"
        api_key = api_key or ""
    elif provider == "ollama":
        base = validate_provider_url("ollama", base_url.strip() or "http://localhost:11434/v1")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        model = model or "llama3.2"
        protocol = "openai"
        label = "Ollama"
        api_key = api_key or ""
    elif provider == "custom":
        # Custom API settings may be supplied by the UI or kept privately in .env.
        base_url = base_url.strip() or os.environ.get("CUSTOM_API_BASE_URL", "").strip()
        api_key = api_key or os.environ.get("CUSTOM_API_KEY", "").strip()
        model = model or os.environ.get("CUSTOM_MODEL", "").strip()
        if not base_url:
            raise RuntimeError("Enter an API base URL, or set CUSTOM_API_BASE_URL in .env.")
        endpoint = validate_provider_url("custom", base_url)
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        if not model:
            raise RuntimeError("Enter a model ID, or set CUSTOM_MODEL in .env.")
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
    if not api_key and provider not in ("ollama", "lmstudio"):
        env_name = ""
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            env_name = OPENAI_COMPATIBLE_PROVIDERS[provider].get("env_key", "")
        elif provider == "anthropic":
            env_name = "ANTHROPIC_API_KEY"
        elif provider == "custom":
            env_name = "CUSTOM_API_KEY"
        if env_name:
            raise RuntimeError(
                "No API key is configured for " + label + ". The key field is optional, but .env must contain "
                + env_name + " for this provider. Keys are provider-specific: for example, GROQ_API_KEY cannot authenticate to NVIDIA NIM. Select the provider that matches the key already in .env, or add the matching variable."
            )
        raise RuntimeError("API key is missing for " + label + ". Add its provider-specific key to .env or enter a key in provider settings.")
    if len(api_key) > 500 or len(endpoint) > 2000 or len(model) > 300:
        raise RuntimeError("Provider settings are too long.")
    system_text = build_system_prompt(personality)
    messages = [{"role": "system", "content": system_text}]
    for item in trim_history(history):
        messages.append(item)
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
    except json.JSONDecodeError as exc:
        raise RuntimeError(label + " returned an invalid JSON response.") from exc
    except urllib.error.HTTPError as exc:
        # Surface a short, sanitized provider error so a bad base URL can be
        # distinguished from a model/permission issue without exposing credentials.
        detail = ""
        try:
            raw_error = exc.read(8192).decode("utf-8", errors="replace")
            parsed_error = json.loads(raw_error)
            error_obj = parsed_error.get("error", parsed_error) if isinstance(parsed_error, dict) else {}
            if isinstance(error_obj, dict):
                detail = error_obj.get("message") or error_obj.get("detail") or error_obj.get("error_description") or ""
            elif isinstance(error_obj, str):
                detail = error_obj
            if not detail and isinstance(parsed_error, dict):
                detail = parsed_error.get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        detail = str(detail).replace(api_key, "[redacted]") if api_key else str(detail)
        detail = " ".join(detail.split())[:300]
        suffix = " Provider detail: " + detail if detail else ""
        if provider == "nvidia_nim" and exc.code == 403:
            raise RuntimeError(
                "NVIDIA NIM returned HTTP 403: Authorization failed. The API endpoint and model ID can be correct while your NVIDIA account still lacks hosted inference access. Confirm the key was generated in NVIDIA Build and try the model in its Playground. If Playground works but this API call fails, ask NVIDIA support to check hosted NIM inference access for your account."
                + suffix
            ) from exc
        if provider == "groq" and exc.code == 403:
            raise RuntimeError(
                "Groq returned HTTP 403. The key may be valid, but this model can be blocked by your Groq organization/project permissions. In Groq Console, check Settings → Organization → Limits and Settings → Projects → Limits. The model ID openai/gpt-oss-120b is a supported Groq model; try the default llama-3.3-70b-versatile to see whether the restriction is specific to GPT-OSS. If the provider detail below names a different cause, follow that message."
                + suffix
            ) from exc
        if exc.code in (400, 401, 403):
            raise RuntimeError(label + " returned HTTP " + str(exc.code) + ". Check the API key, account permissions, and model ID." + suffix) from exc
        if exc.code == 404:
            if provider == "custom":
                hint = " Verify the base URL is the provider's OpenAI-compatible API base (usually ending in /v1), and that the model ID is enabled for your account."
            else:
                hint = " Verify the model ID and provider endpoint."
            raise RuntimeError(label + " returned HTTP 404: endpoint or model not found." + hint + suffix) from exc
        if exc.code == 429:
            raise RuntimeError(label + " returned HTTP 429: rate limit or quota reached. Wait and try again." + suffix) from exc
        raise RuntimeError(label + " returned HTTP " + str(exc.code) + "." + suffix) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Could not reach " + label + ". Check your connection and endpoint URL.") from exc
    return extract_provider_reply(data, protocol, label)

class Handler(BaseHTTPRequestHandler):
    server_version = "BrainCellGPT/0.1"

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        super().end_headers()

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
            self.send_json(200, {"status": "ok", "service": "braincellgpt", "providers": ["groq", "openai", "anthropic", "openrouter", "nvidia_nim", "deepseek", "mistral", "xai", "together", "fireworks", "cerebras", "ollama", "lmstudio", "custom"]})
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
        route = urlparse(self.path).path
        if route not in ("/api/chat", "/api/test-connection"):
            self.send_json(404, {"error": "Not found"})
            return
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_json(403, {"error": "This local app only accepts requests from this computer."})
            return
        if rate_limited(self.client_address[0] + ":" + route):
            self.send_json(429, {"error": "Too many requests. Wait a minute and try again."})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self.send_json(415, {"error": "Content-Type must be application/json."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 200_000:
                self.send_json(413, {"error": "Request body is empty or too large."})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                self.send_json(400, {"error": "Request body must be a JSON object."})
                return
            message = str(payload.get("message", "")).strip()
            if route == "/api/test-connection":
                message = "Reply with exactly: connection successful"
            if not message:
                self.send_json(400, {"error": "Type a message first."})
                return
            if len(message) > 12000:
                self.send_json(400, {"error": "Message is too long (12,000 characters maximum)."})
                return
            history = payload.get("history", [])
            if not isinstance(history, list):
                history = []
            history = trim_history(history)
            def text_field(name: str, default: str = "") -> str:
                value = payload.get(name, default)
                return value if isinstance(value, str) else default
            reply = generate_reply(
                message, history, text_field("personality", "normal"),
                text_field("provider", "groq"), text_field("api_key"),
                text_field("base_url"), text_field("model")
            )
            if route == "/api/test-connection":
                self.send_json(200, {"ok": True, "message": "Connection successful.", "provider": text_field("provider", "groq")})
            else:
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
