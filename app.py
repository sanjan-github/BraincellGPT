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
from urllib.parse import parse_qs, urlparse, urlsplit
from collections import defaultdict, deque
from threading import Lock
import time
import re
from backend.agent import restore_checkpoint, restore_checkpoint_group, run_agent
from backend.agent_approvals import get_approval_registry
from backend.mcp_stdio import MCPToolRouter
from backend.agent_state import get_agent_store
from backend.provider_http import MAX_CONCURRENT_PROVIDER_REQUESTS, MAX_PROVIDER_RESPONSE_BYTES as SHARED_MAX_PROVIDER_RESPONSE_BYTES, PROVIDER_SLOTS, ProviderInvalidJSON, ProviderResponseTooLarge, open_provider_request, read_provider_json, resolve_public_addresses

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
_profile_secrets: dict[str, str] = {}
_profile_secrets_lock = Lock()
MAX_RATE_BUCKETS = 2048
MAX_PROVIDER_RESPONSE_BYTES = SHARED_MAX_PROVIDER_RESPONSE_BYTES
_provider_slots = PROVIDER_SLOTS


def rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        # Bound the dictionary as well as each individual event queue.
        for key in list(_rate_events):
            events = _rate_events[key]
            while events and now - events[0] > RATE_WINDOW_SECONDS:
                events.popleft()
            if not events:
                _rate_events.pop(key, None)
        if client_ip not in _rate_events and len(_rate_events) >= MAX_RATE_BUCKETS:
            return True
        events = _rate_events[client_ip]
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
        raw_content = item.get("content")
        if not isinstance(raw_content, str):
            continue
        content = raw_content.strip()
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


def validate_provider_url_with_addresses(provider: str, value: str) -> tuple[str, tuple | None]:
    raw = value.strip()
    if len(raw) > 2000:
        raise RuntimeError("Provider URL is too long.")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Provider URL is invalid.") from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Provider URL must not contain credentials, query strings, or fragments.")
    if port == 0:
        raise RuntimeError("Provider URL port must be between 1 and 65535.")
    if provider in ("ollama", "lmstudio"):
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise RuntimeError("Local model URLs must use HTTP and point to localhost on this machine.")
        return raw.rstrip("/"), None
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("Custom provider URLs must use HTTPS.")
    try:
        addresses = resolve_public_addresses(parsed.hostname, port or 443)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return raw.rstrip("/"), addresses


def validate_provider_url(provider: str, value: str) -> str:
    """Validate a provider URL and return its normalized base URL."""
    return validate_provider_url_with_addresses(provider, value)[0]


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
try:
    PORT = int(os.environ.get("PORT", "8000"))
except (TypeError, ValueError) as exc:
    raise RuntimeError("PORT must be an integer between 1 and 65535.") from exc
if not 1 <= PORT <= 65535:
    raise RuntimeError("PORT must be an integer between 1 and 65535.")


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


def _read_provider_json(response, label: str) -> object:
    """Read shared bounded provider JSON and map errors to chat-facing messages."""
    try:
        return read_provider_json(response, MAX_PROVIDER_RESPONSE_BYTES)
    except ProviderResponseTooLarge as exc:
        raise RuntimeError(label + " returned a response larger than the 2 MB limit.") from exc
    except ProviderInvalidJSON as exc:
        raise RuntimeError(label + " returned an invalid JSON response.") from exc


def generate_reply(message: str, history: list[dict], personality: str, provider: str = "groq", user_api_key: str = "", base_url: str = "", model_override: str = "") -> str:
    """Call Groq, Anthropic, Ollama, or an OpenAI-compatible endpoint."""
    provider = provider.strip().lower()
    api_key = user_api_key.strip()
    model = model_override.strip()
    pinned_addresses = None
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
        endpoint, pinned_addresses = validate_provider_url_with_addresses("custom", base_url)
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
        if not _provider_slots.acquire(timeout=2):
            raise RuntimeError("BrainCellGPT is busy handling other provider requests. Try again shortly.")
        try:
            with open_provider_request(request, timeout=60, pinned_addresses=pinned_addresses) as response:
                data = _read_provider_json(response, label)
        finally:
            _provider_slots.release()
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

    def is_trusted_local_request(self) -> bool:
        """Reject non-loopback hosts and cross-origin browser requests."""
        host = self.headers.get("Host", "")
        try:
            parsed_host = urlsplit("http://" + host)
            if parsed_host.hostname not in ("localhost", "127.0.0.1", "::1") or parsed_host.port != PORT:
                return False
        except ValueError:
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed_origin = urlsplit(origin)
                if parsed_origin.scheme != "http" or parsed_origin.hostname not in ("localhost", "127.0.0.1", "::1") or parsed_origin.port != PORT:
                    return False
            except ValueError:
                return False
        if self.headers.get("Sec-Fetch-Site", "").lower() in ("cross-site", "same-site"):
            # Local app UI is same-origin; cross-site and sibling-origin browser requests are not needed.
            origin = self.headers.get("Origin", "")
            if not origin:
                return False
        return True

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

    def send_sse_event(self, event: dict) -> None:
        body = ("data: " + json.dumps(event, separators=(",", ":")) + "\n\n").encode("utf-8")
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        content_type = "text/html; charset=utf-8"
        if path in ("/", "/index.html"):
            if not self.is_trusted_local_request():
                self.send_json(403, {"error": "Untrusted Host or Origin."})
                return
            file_path = WEB_DIR / "index.html"
        elif path == "/assets/earth-blue-marble.jpg":
            if not self.is_trusted_local_request():
                self.send_json(403, {"error": "Untrusted Host or Origin."})
                return
            file_path = WEB_DIR / "assets" / "earth-blue-marble.jpg"
            content_type = "image/jpeg"
        elif path == "/health":
            self.send_json(200, {"status": "ok", "service": "braincellgpt", "providers": ["groq", "openai", "anthropic", "openrouter", "nvidia_nim", "deepseek", "mistral", "xai", "together", "fireworks", "cerebras", "ollama", "lmstudio", "custom"]})
            return
        elif path == "/api/profile-secret-status":
            if not self.is_trusted_local_request() or self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_json(403, {"error": "This local app only accepts requests from this computer."})
                return
            if rate_limited(self.client_address[0] + ":profile-secret-status"):
                self.send_json(429, {"error": "Too many requests. Wait a minute and try again."})
                return
            profile_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            if not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", profile_id):
                self.send_json(400, {"error": "Invalid profile ID."})
                return
            with _profile_secrets_lock:
                configured = bool(_profile_secrets.get(profile_id))
            self.send_json(200, {"configured": configured})
            return
        elif path == "/api/agent/sessions":
            if not self.is_trusted_local_request() or self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_json(403, {"error": "This local app only accepts requests from this computer."})
                return
            if rate_limited(self.client_address[0] + ":agent-sessions"):
                self.send_json(429, {"error": "Too many requests. Wait a minute and try again."})
                return
            try:
                store = get_agent_store()
                query = parse_qs(urlparse(self.path).query)
                session_id = query.get("id", [""])[0]
                if session_id:
                    session = store.get_session(session_id)
                    if not session or Path(session["workspace"]).resolve() != ROOT.resolve():
                        self.send_json(404, {"error": "Agent session not found."})
                        return
                    self.send_json(200, {
                        "session": {key: session[key] for key in ("id", "title", "provider", "base_url", "model", "mode", "created_at", "updated_at")},
                        "turns": store.get_turns(session_id),
                        "checkpoints": store.list_checkpoints(session_id),
                        "checkpoint_groups": store.list_checkpoint_groups(session_id),
                    })
                else:
                    self.send_json(200, {"sessions": store.list_sessions(str(ROOT.resolve()))})
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            return
        else:
            self.send_json(404, {"error": "Not found"})
            return
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_json(500, {"error": "Could not load the requested web asset."})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route not in ("/api/chat", "/api/test-connection", "/api/profile-secret", "/api/agent", "/api/agent/stream", "/api/agent/approvals", "/api/agent/restore"):
            self.send_json(404, {"error": "Not found"})
            return
        if self.client_address[0] not in ("127.0.0.1", "::1") or not self.is_trusted_local_request():
            self.send_json(403, {"error": "This local app only accepts trusted loopback requests."})
            return
        if rate_limited(self.client_address[0] + ":" + route):
            self.send_json(429, {"error": "Too many requests. Wait a minute and try again."})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self.send_json(415, {"error": "Content-Type must be application/json."})
            return
        stream_started = False
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 200_000:
                self.send_json(413, {"error": "Request body is empty or too large."})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                self.send_json(400, {"error": "Request body must be a JSON object."})
                return
            if route == "/api/profile-secret":
                profile_id = payload.get("profile_id", "")
                api_key = payload.get("api_key", "")
                if not isinstance(profile_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", profile_id):
                    self.send_json(400, {"error": "Invalid profile ID."})
                    return
                if not isinstance(api_key, str) or len(api_key) > 500:
                    self.send_json(400, {"error": "API key is too long or invalid."})
                    return
                with _profile_secrets_lock:
                    if api_key.strip():
                        _profile_secrets[profile_id] = api_key.strip()
                    else:
                        _profile_secrets.pop(profile_id, None)
                self.send_json(200, {"ok": True, "configured": bool(api_key.strip())})
                return
            if route == "/api/agent/approvals":
                approval_id = payload.get("approval_id", "")
                session_id = payload.get("session_id", "")
                decision = payload.get("decision", "")
                if not isinstance(approval_id, str) or not isinstance(session_id, str) or decision not in ("approve", "reject"):
                    self.send_json(400, {"error": "Provide an approval ID, session ID, and approve/reject decision."})
                    return
                store = get_agent_store()
                session_id = store.validate_session_id(session_id)
                session = store.get_session(session_id)
                if not session or Path(session["workspace"]).resolve() != ROOT.resolve():
                    self.send_json(404, {"error": "Agent session not found."})
                    return
                resolved = get_approval_registry().resolve(approval_id, session_id, decision == "approve")
                if not resolved:
                    self.send_json(404, {"error": "Pending approval was not found or has already been resolved."})
                    return
                self.send_json(200, {"resolved": True, "decision": decision})
                return
            if route in ("/api/agent", "/api/agent/stream"):
                task = payload.get("task", "")
                if not isinstance(task, str) or not task.strip() or len(task) > 12000:
                    self.send_json(400, {"error": "Provide a task up to 12,000 characters."})
                    return
                provider = payload.get("provider", "") if isinstance(payload.get("provider", ""), str) else ""
                base_url = payload.get("base_url", "") if isinstance(payload.get("base_url", ""), str) else ""
                model = payload.get("model", "") if isinstance(payload.get("model", ""), str) else ""
                mode = payload.get("mode", "") if isinstance(payload.get("mode", ""), str) else ""
                supplied_session_id = payload.get("session_id", "")
                profile_id = payload.get("profile_id", "")
                with _profile_secrets_lock:
                    saved_key = _profile_secrets.get(profile_id, "") if isinstance(profile_id, str) else ""
                store = get_agent_store()
                if supplied_session_id:
                    session_id = store.validate_session_id(supplied_session_id)
                    session = store.get_session(session_id)
                    if not session or Path(session["workspace"]).resolve() != ROOT.resolve():
                        self.send_json(404, {"error": "Agent session not found."})
                        return
                    provider = provider or session["provider"]
                    base_url = base_url or session["base_url"]
                    model = model or session["model"]
                    mode = mode or session["mode"]
                    history = store.history(session_id)
                else:
                    provider = provider or "groq"
                    mode = mode or "code"
                    session_id = store.create_session(str(ROOT.resolve()), provider, base_url, model, mode, task.strip())
                    history = payload.get("history", [])
                    if not isinstance(history, list):
                        history = []
                store.add_turn(session_id, "user", task.strip())
                mcp_router = None
                try:
                    streaming = route == "/api/agent/stream"
                    callback = None
                    approval_callback = None
                    if streaming:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.send_header("X-Accel-Buffering", "no")
                        self.close_connection = True
                        self.end_headers()
                        stream_started = True
                        self.send_sse_event({"type": "start", "session_id": session_id})
                        callback = self.send_sse_event
                        approval_callback = lambda event: get_approval_registry().request(session_id, event, callback)
                        mcp_router = MCPToolRouter(os.environ, ROOT, callback)
                    result = run_agent(
                        task.strip(), history, ROOT, provider, saved_key, base_url, model, os.environ,
                        allow_edits=payload.get("allow_edits") is True,
                        allow_commands=payload.get("allow_commands") is True,
                        mode=mode,
                        session_id=session_id,
                        event_callback=callback,
                        approval_callback=approval_callback,
                        mcp_router=mcp_router,
                    )
                except (RuntimeError, ValueError) as exc:
                    store.add_turn(session_id, "assistant", str(exc), {"error": True})
                    raise
                finally:
                    if mcp_router is not None:
                        mcp_router.close()
                result["session_id"] = session_id
                store.update_session(session_id, provider=provider, base_url=base_url, model=model, mode=result.get("mode", mode))
                metadata = {key: value for key, value in result.items() if key not in {"reply", "session_id"}}
                store.add_turn(session_id, "assistant", str(result.get("reply", "")), metadata)
                if streaming:
                    self.send_sse_event({"type": "done", "result": result})
                else:
                    self.send_json(200, result)
                return
            if route == "/api/agent/restore":
                if payload.get("allow_edits") is not True:
                    self.send_json(403, {"error": "Restoring a checkpoint requires edits to be enabled for this request."})
                    return
                session_id = payload.get("session_id", "")
                group_id = payload.get("group_id")
                if group_id is not None:
                    result = restore_checkpoint_group(session_id, group_id, ROOT)
                else:
                    checkpoint_id = payload.get("checkpoint_id", "")
                    result = restore_checkpoint(session_id, checkpoint_id, ROOT)
                self.send_json(200, result)
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
            profile_id = text_field("profile_id")
            with _profile_secrets_lock:
                saved_key = _profile_secrets.get(profile_id, "")
            # An explicitly entered key is used only for the connection check; normal
            # chat requests resolve their credential from process memory or .env.
            supplied_key = text_field("api_key") if route == "/api/test-connection" else ""
            reply = generate_reply(
                message, history, text_field("personality", "normal"),
                text_field("provider", "groq"), supplied_key or saved_key,
                text_field("base_url"), text_field("model")
            )
            if route == "/api/test-connection":
                self.send_json(200, {"ok": True, "message": "Connection successful.", "provider": text_field("provider", "groq")})
            else:
                self.send_json(200, {"reply": reply})
        except json.JSONDecodeError:
            if stream_started:
                self.send_sse_event({"type": "error", "error": "Invalid JSON request."})
            else:
                self.send_json(400, {"error": "Invalid JSON request."})
        except RuntimeError as exc:
            if stream_started:
                self.send_sse_event({"type": "error", "error": str(exc)})
            else:
                self.send_json(502, {"error": str(exc)})
        except ValueError as exc:
            if stream_started:
                self.send_sse_event({"type": "error", "error": str(exc)})
            else:
                self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            # Log the exception type for diagnosis without returning internals to clients.
            print(f"[BrainCellGPT] request failed: {type(exc).__name__}")
            if stream_started:
                self.send_sse_event({"type": "error", "error": "Unexpected server error. Check the terminal for details."})
            else:
                self.send_json(500, {"error": "Unexpected server error. Check the terminal for details."})

    def do_DELETE(self) -> None:
        route = urlparse(self.path).path
        if route != "/api/agent/sessions":
            self.send_json(404, {"error": "Not found"})
            return
        if self.client_address[0] not in ("127.0.0.1", "::1") or not self.is_trusted_local_request():
            self.send_json(403, {"error": "This local app only accepts trusted loopback requests."})
            return
        if rate_limited(self.client_address[0] + ":agent-session-delete"):
            self.send_json(429, {"error": "Too many requests. Wait a minute and try again."})
            return
        session_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
        try:
            deleted = get_agent_store().delete_session(session_id, str(ROOT.resolve()))
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
            return
        if not deleted:
            self.send_json(404, {"error": "Agent session not found."})
            return
        self.send_json(200, {"deleted": True})

    def log_message(self, format: str, *args) -> None:
        print(f"[BrainCellGPT] {self.address_string()} - {format % args}")


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
