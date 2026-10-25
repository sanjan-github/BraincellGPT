"""BrainCellGPT: one-command local app using only Python's standard library.

Run from the repository root: python app.py
Set GROQ_API_KEY in the environment or in a local .env file.
"""
from __future__ import annotations

import http.client
import json
import base64
import hmac
import ipaddress
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit
from collections import OrderedDict, defaultdict, deque
from threading import BoundedSemaphore, Lock
import time
import re
from typing import Any, Callable
from backend.provider_http import MAX_CONCURRENT_PROVIDER_REQUESTS, MAX_PROVIDER_RESPONSE_BYTES as SHARED_MAX_PROVIDER_RESPONSE_BYTES, PROVIDER_SLOTS, ProviderInvalidJSON, ProviderResponseTooLarge, open_provider_request, read_provider_json, resolve_public_addresses
from backend.media import anthropic_content, normalize_images, openai_content
from backend.provider_registry import AZURE_OPENAI_PROVIDER, OPENAI_COMPATIBLE_PROVIDERS, provider_capabilities
from backend.chat_usage import (
    ChatUsageStore, ConversationNotFound, DuplicateChatRequest, MAX_MESSAGES_PER_CONVERSATION,
    MAX_SHARED_MESSAGES_PER_INSTALLATION, MessageLimitReached, OWN_KEY_MODE, SHARED_GROQ_MODE,
)

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
_profile_secrets: OrderedDict[str, tuple[str, float]] = OrderedDict()
_profile_secrets_lock = Lock()
MAX_PROFILE_SECRETS = 128
PROFILE_SECRET_TTL_SECONDS = 24 * 60 * 60
MAX_HTTP_HANDLERS = 64
HTTP_READ_TIMEOUT_SECONDS = 10
MAX_RATE_BUCKETS = 2048
MAX_PROVIDER_RESPONSE_BYTES = SHARED_MAX_PROVIDER_RESPONSE_BYTES
MAX_CHAT_OUTPUT_TOKENS = 800
_provider_slots = PROVIDER_SLOTS
_chat_usage_store_instance: ChatUsageStore | None = None
_chat_usage_store_lock = Lock()
SUPPORTED_PROVIDER_NAMES = tuple(OPENAI_COMPATIBLE_PROVIDERS) + ("azure_openai", "anthropic", "ollama", "lmstudio", "custom")


def _chat_usage_store() -> ChatUsageStore:
    global _chat_usage_store_instance
    if _chat_usage_store_instance is None:
        with _chat_usage_store_lock:
            if _chat_usage_store_instance is None:
                _chat_usage_store_instance = ChatUsageStore()
    return _chat_usage_store_instance


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


def _get_profile_secret(profile_id: str) -> str:
    now = time.monotonic()
    with _profile_secrets_lock:
        expired = [key for key, (_secret, stored_at) in _profile_secrets.items()
                   if now - stored_at >= PROFILE_SECRET_TTL_SECONDS]
        for key in expired:
            _profile_secrets.pop(key, None)
        entry = _profile_secrets.get(profile_id)
        if entry is None:
            return ""
        _profile_secrets.move_to_end(profile_id)
        return entry[0]


def _set_profile_secret(profile_id: str, api_key: str) -> None:
    now = time.monotonic()
    with _profile_secrets_lock:
        expired = [key for key, (_secret, stored_at) in _profile_secrets.items()
                   if now - stored_at >= PROFILE_SECRET_TTL_SECONDS]
        for key in expired:
            _profile_secrets.pop(key, None)
        if not api_key:
            _profile_secrets.pop(profile_id, None)
            return
        _profile_secrets.pop(profile_id, None)
        _profile_secrets[profile_id] = (api_key, now)
        while len(_profile_secrets) > MAX_PROFILE_SECRETS:
            _profile_secrets.popitem(last=False)


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
HOST = os.environ.get("BRAINCELLGPT_HOST", "127.0.0.1").strip() or "127.0.0.1"
REMOTE_ACCESS_ENABLED = os.environ.get("BRAINCELLGPT_ALLOW_REMOTE", "").strip() == "1"
REMOTE_AUTH_TOKEN = os.environ.get("BRAINCELLGPT_REMOTE_AUTH_TOKEN", "")
ALLOWED_HOSTS = {value.strip().lower() for value in os.environ.get("BRAINCELLGPT_ALLOWED_HOSTS", "").split(",") if value.strip()}
PUBLIC_ORIGIN = os.environ.get("BRAINCELLGPT_PUBLIC_ORIGIN", "").rstrip("/")
PUBLIC_ORIGIN_PORT = None


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if not _is_loopback_host(HOST):
    if not REMOTE_ACCESS_ENABLED:
        raise RuntimeError("Remote binding is disabled. Set BRAINCELLGPT_ALLOW_REMOTE=1 to opt in.")
    if len(REMOTE_AUTH_TOKEN) < 32:
        raise RuntimeError("Remote binding requires BRAINCELLGPT_REMOTE_AUTH_TOKEN with at least 32 characters.")
    if not PUBLIC_ORIGIN.startswith("https://"):
        raise RuntimeError("Remote binding requires BRAINCELLGPT_PUBLIC_ORIGIN to be an HTTPS origin behind a TLS proxy.")
    allowed_origin = urlsplit(PUBLIC_ORIGIN)
    if not allowed_origin.hostname or allowed_origin.username or allowed_origin.password or allowed_origin.path not in ("", "/") or allowed_origin.query or allowed_origin.fragment:
        raise RuntimeError("BRAINCELLGPT_PUBLIC_ORIGIN must be a bare HTTPS origin without credentials, path, query, or fragment.")
    ALLOWED_HOSTS.add(allowed_origin.hostname.lower())
    PUBLIC_ORIGIN_PORT = allowed_origin.port or 443

# Resolve PORT after dotenv so a PORT entry in .env is respected.
try:
    PORT = int(os.environ.get("PORT", "8000"))
except (TypeError, ValueError) as exc:
    raise RuntimeError("PORT must be an integer between 1 and 65535.") from exc
if not 1 <= PORT <= 65535:
    raise RuntimeError("PORT must be an integer between 1 and 65535.")


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
        "You are BrainCellGPT, a funny, casual, occasionally chaotic chatbot for stress relief, curious questions, and entertaining conversation. "
        "Sound like a natural, native-speaking person chatting with another person. Be witty and occasionally "
        "absurd, but do not force a joke into every sentence. Keep replies short by default, usually one to four "
        "sentences. Respond to the user's actual message and match its tone and length: answer briefly when appropriate, and give a detailed "
        "answer when the user asks for one. Be direct; avoid repetitive introductions, summaries, dramatic roleplay, "
        "excessive emojis, unnecessary lists, and a question at the end of every reply. Do not repeatedly use "
        "nicknames for the user. Do not overexplain jokes. Use the conversation context and callbacks only when "
        "they fit naturally. Keep clarity first. Do not sound like a generic assistant, formal help desk, or corporate bot. "
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


def generate_reply(message: str, history: list[dict], personality: str, provider: str = "groq", user_api_key: str = "", base_url: str = "", model_override: str = "", images: list[str] | None = None, on_request_start: Callable[[], int | None] | None = None, allow_environment_key: bool = True) -> str:
    """Call Groq, Anthropic, Ollama, or an OpenAI-compatible endpoint."""
    provider = provider.strip().lower()
    api_key = user_api_key.strip()
    model = model_override.strip()
    pinned_addresses = None
    def configured_environment_key(name: str) -> str:
        return os.environ.get(name, "").strip() if allow_environment_key else ""

    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        config = OPENAI_COMPATIBLE_PROVIDERS[provider]
        api_key = api_key or (configured_environment_key(config.get("env_key", "")) if config.get("env_key") else "")
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
    elif provider == "azure_openai":
        api_key = api_key or configured_environment_key(AZURE_OPENAI_PROVIDER["env_key"])
        base_url = base_url.strip() or os.environ.get(AZURE_OPENAI_PROVIDER["env_endpoint"], "").strip()
        if not base_url:
            raise RuntimeError("Enter an Azure OpenAI v1 endpoint, or set AZURE_OPENAI_ENDPOINT in .env.")
        endpoint, pinned_addresses = validate_provider_url_with_addresses("custom", base_url)
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        model = model or os.environ.get(AZURE_OPENAI_PROVIDER["env_model"], "").strip()
        if not model:
            raise RuntimeError("Enter an Azure deployment name, or set AZURE_OPENAI_DEPLOYMENT in .env.")
        protocol = "azure-openai"
        label = AZURE_OPENAI_PROVIDER["label"]
    elif provider == "custom":
        # Custom API settings may be supplied by the UI or kept privately in .env.
        base_url = base_url.strip() or os.environ.get("CUSTOM_API_BASE_URL", "").strip()
        api_key = api_key or configured_environment_key("CUSTOM_API_KEY")
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
        api_key = api_key or configured_environment_key("ANTHROPIC_API_KEY")
        model = model or "claude-3-5-haiku-latest"
        endpoint = "https://api.anthropic.com/v1/messages"
        protocol = "anthropic"
        label = "Anthropic"
    else:
        raise RuntimeError("Choose a supported provider.")
    if not api_key and provider not in ("ollama", "lmstudio"):
        if not allow_environment_key:
            raise RuntimeError("Your own API key is not saved for " + label + ". Open Settings, save your key, and verify the connection.")
        env_name = ""
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            env_name = OPENAI_COMPATIBLE_PROVIDERS[provider].get("env_key", "")
        elif provider == "anthropic":
            env_name = "ANTHROPIC_API_KEY"
        elif provider == "custom":
            env_name = "CUSTOM_API_KEY"
        elif provider == "azure_openai":
            env_name = AZURE_OPENAI_PROVIDER["env_key"]
        if env_name:
            raise RuntimeError(
                "No API key is configured for " + label + ". The key field is optional, but .env must contain "
                + env_name + " for this provider. Keys are provider-specific: for example, GROQ_API_KEY cannot authenticate to NVIDIA NIM. Select the provider that matches the key already in .env, or add the matching variable."
            )
        raise RuntimeError("API key is missing for " + label + ". Add its provider-specific key to .env or enter a key in provider settings.")
    if len(api_key) > 500 or len(endpoint) > 2000 or len(model) > 300:
        raise RuntimeError("Provider settings are too long.")
    system_text = build_system_prompt(personality)
    image_parts = normalize_images(images)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
    for item in trim_history(history):
        messages.append(item)
    user_content = anthropic_content(message, image_parts) if protocol == "anthropic" else openai_content(message, image_parts)
    messages.append({"role": "user", "content": user_content})
    headers = {"Content-Type": "application/json"}
    if protocol == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        payload = {"model": model, "system": system_text, "messages": [m for m in messages if m["role"] != "system"], "max_tokens": MAX_CHAT_OUTPUT_TOKENS, "temperature": 0.7}
    else:
        headers["Authorization"] = "Bearer " + api_key if api_key else ""
        if not api_key:
            headers.pop("Authorization", None)
        if protocol == "azure-openai":
            headers.pop("Authorization", None)
            headers["api-key"] = api_key
        payload = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": MAX_CHAT_OUTPUT_TOKENS}
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        if not _provider_slots.acquire(timeout=2):
            raise RuntimeError("BrainCellGPT is busy handling other provider requests. Try again shortly.")
        try:
            if on_request_start is not None:
                on_request_start()
            with open_provider_request(request, timeout=60, pinned_addresses=pinned_addresses) as response:
                data = _read_provider_json(response, label)
        finally:
            _provider_slots.release()
    except json.JSONDecodeError as exc:
        raise RuntimeError(label + " returned an invalid JSON response.") from exc
    except urllib.error.HTTPError as exc:
        try:
            exc.close()
        except OSError:
            pass
        if exc.code == 400:
            raise RuntimeError(label + " rejected the request. Check the selected model and its settings.") from exc
        if exc.code == 401:
            raise RuntimeError("Your API key was rejected by " + label + ". Check the key in provider settings.") from exc
        if exc.code == 403:
            raise RuntimeError(label + " denied access. Check your account permissions and model access.") from exc
        if exc.code == 404:
            if provider == "custom":
                hint = " Verify the base URL is the provider's OpenAI-compatible API base (usually ending in /v1), and that the model ID is enabled for your account."
            else:
                hint = " Verify the model ID and provider endpoint."
            raise RuntimeError(label + " could not find the endpoint or model." + hint) from exc
        if exc.code == 429:
            raise RuntimeError(label + " rate limit or quota reached. Wait and try again.") from exc
        if exc.code >= 500:
            raise RuntimeError(label + " is temporarily unavailable. Try again later.") from exc
        raise RuntimeError(label + " could not process the request (HTTP " + str(exc.code) + ").") from exc
    except http.client.HTTPException as exc:
        raise RuntimeError("The response from " + label + " was interrupted. Please try again.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
            raise RuntimeError("The request to " + label + " timed out. Please try again.") from exc
        raise RuntimeError("Could not reach " + label + ". Check your connection and endpoint URL.") from exc
    return extract_provider_reply(data, protocol, label)

class Handler(BaseHTTPRequestHandler):
    server_version = "BrainCellGPT/1.0.0"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(HTTP_READ_TIMEOUT_SECONDS)

    def is_trusted_local_request(self) -> bool:
        """Reject non-loopback hosts and cross-origin browser requests."""
        host = self.headers.get("Host", "")
        try:
            parsed_host = urlsplit("http://" + host)
            hostname = (parsed_host.hostname or "").lower()
            local_host = _is_loopback_host(hostname)
            if local_host:
                valid_host = parsed_host.port == PORT
            else:
                valid_host = REMOTE_ACCESS_ENABLED and hostname in ALLOWED_HOSTS and (parsed_host.port or 443) == PUBLIC_ORIGIN_PORT
            if not valid_host:
                return False
        except ValueError:
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed_origin = urlsplit(origin)
                origin_host = (parsed_origin.hostname or "").lower()
                if _is_loopback_host(origin_host):
                    valid_origin = parsed_origin.scheme == "http" and parsed_origin.port == PORT
                else:
                    valid_origin = (
                        REMOTE_ACCESS_ENABLED and origin.rstrip("/") == PUBLIC_ORIGIN
                        and parsed_origin.scheme == "https" and origin_host in ALLOWED_HOSTS
                        and (parsed_origin.port or 443) == PUBLIC_ORIGIN_PORT
                    )
                if not valid_origin:
                    return False
            except ValueError:
                return False
        if self.headers.get("Sec-Fetch-Site", "").lower() in ("cross-site", "same-site"):
            # Local app UI is same-origin; cross-site and sibling-origin browser requests are not needed.
            origin = self.headers.get("Origin", "")
            if not origin:
                return False
        return True

    def is_authorized_request(self) -> bool:
        if not REMOTE_ACCESS_ENABLED or _is_loopback_host(HOST):
            return True
        authorization = self.headers.get("Authorization", "")
        if len(authorization) > 8192:
            return False
        supplied = ""
        if authorization.startswith("Bearer "):
            supplied = authorization[7:]
        elif authorization.startswith("Basic "):
            try:
                username, supplied = base64.b64decode(authorization[6:], validate=True).decode("utf-8").split(":", 1)
                if username != "braincell":
                    return False
            except (ValueError, UnicodeDecodeError):
                return False
        return hmac.compare_digest(supplied.encode("utf-8"), REMOTE_AUTH_TOKEN.encode("utf-8"))

    def require_authorization(self) -> bool:
        if self.is_authorized_request():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="BrainCellGPT", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        body = json.dumps({"error": "Authentication is required."}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

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
        if not self.require_authorization():
            return
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
            self.send_json(200, {"status": "ok", "service": "braincellgpt", "providers": list(SUPPORTED_PROVIDER_NAMES), "provider_capabilities": provider_capabilities()})
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
            configured = bool(_get_profile_secret(profile_id))
            self.send_json(200, {"configured": configured})
            return
        elif path == "/api/chat/config":
            if not self.is_trusted_local_request() or self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_json(403, {"error": "This local app only accepts requests from this computer."})
                return
            if rate_limited(self.client_address[0] + ":chat-config"):
                self.send_json(429, {"error": "Too many requests. Wait a minute and try again."})
                return
            usage_store = _chat_usage_store()
            mode = usage_store.credential_mode()
            self.send_json(200, {
                "credential_mode": mode,
                "shared_groq_available": bool(os.environ.get("GROQ_API_KEY", "").strip()),
                "shared_messages_used": usage_store.shared_usage() if mode == SHARED_GROQ_MODE else None,
                "shared_message_limit": MAX_SHARED_MESSAGES_PER_INSTALLATION,
            })
            return
        elif path == "/api/conversations/usage":
            if not self.is_trusted_local_request() or self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_json(403, {"error": "This local app only accepts requests from this computer."})
                return
            if rate_limited(self.client_address[0] + ":conversation-usage"):
                self.send_json(429, {"error": "Too many requests. Wait a minute and try again."})
                return
            conversation_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            try:
                used = _chat_usage_store().usage(conversation_id)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            except ConversationNotFound:
                self.send_json(404, {"error": "Conversation not found."})
                return
            self.send_json(200, {"messages_used": used, "message_limit": MAX_MESSAGES_PER_CONVERSATION})
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

    def _handle_profile_secret_post(self, payload: dict) -> None:
        profile_id = payload.get("profile_id", "")
        api_key = payload.get("api_key", "")
        if not isinstance(profile_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", profile_id):
            self.send_json(400, {"error": "Invalid profile ID."})
            return
        if not isinstance(api_key, str) or len(api_key) > 500:
            self.send_json(400, {"error": "API key is too long or invalid."})
            return
        _set_profile_secret(profile_id, api_key.strip())
        self.send_json(200, {"ok": True, "configured": bool(api_key.strip())})

    def _handle_chat_config_post(self, payload: dict) -> None:
        mode = payload.get("credential_mode")
        if mode == SHARED_GROQ_MODE and not os.environ.get("GROQ_API_KEY", "").strip():
            self.send_json(400, {"error": "BrainCellGPT free Groq access is not configured on this installation. Choose your own provider key instead."})
            return
        _chat_usage_store().set_credential_mode(mode)
        self.send_json(200, {"credential_mode": mode})

    def _handle_post_control_route(self, route: str, payload: dict) -> bool:
        if route == "/api/profile-secret":
            self._handle_profile_secret_post(payload)
            return True
        if route == "/api/chat/config":
            self._handle_chat_config_post(payload)
            return True
        if route == "/api/conversations":
            conversation_id = _chat_usage_store().create_conversation()
            self.send_json(201, {
                "conversation_id": conversation_id,
                "messages_used": 0,
                "message_limit": MAX_MESSAGES_PER_CONVERSATION,
            })
            return True
        return False

    def do_POST(self) -> None:
        if not self.require_authorization():
            return
        route = urlparse(self.path).path
        if route not in ("/api/chat", "/api/test-connection", "/api/profile-secret", "/api/conversations", "/api/chat/config"):
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
        reserved_count: int | None = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            max_body_bytes = 12_000_000 if route == "/api/chat" else 200_000
            if length <= 0 or length > max_body_bytes:
                self.send_json(413, {"error": "Request body is empty or too large."})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                self.send_json(400, {"error": "Request body must be a JSON object."})
                return
            if self._handle_post_control_route(route, payload):
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
            images = payload.get("images") if route == "/api/chat" else None
            def text_field(name: str, default: str = "") -> str:
                value = payload.get(name, default)
                return value if isinstance(value, str) else default
            profile_id = text_field("profile_id")
            saved_key = _get_profile_secret(profile_id)
            # An explicitly entered key is used only for the connection check; normal
            # chat requests resolve their credential from process memory or .env.
            supplied_key = text_field("api_key") if route == "/api/test-connection" else ""
            request_start = None
            credential_mode = _chat_usage_store().credential_mode() if route in ("/api/chat", "/api/test-connection") else None
            if route == "/api/test-connection" and credential_mode not in (SHARED_GROQ_MODE, OWN_KEY_MODE):
                raise ValueError("Choose how to power BraincellGPT in Settings before verifying a provider.")
            allow_environment_key = credential_mode != OWN_KEY_MODE
            if credential_mode == SHARED_GROQ_MODE:
                supplied_key = ""
                saved_key = ""
            if route == "/api/chat":
                if credential_mode not in (SHARED_GROQ_MODE, OWN_KEY_MODE):
                    raise ValueError("Choose how to power BraincellGPT in Settings before sending a message.")
                conversation_id = text_field("conversation_id")
                request_id = text_field("request_id")
                usage_store = _chat_usage_store()
                usage_store._validate_id(conversation_id, "conversation")
                usage_store._validate_id(request_id, "request")
                usage_store.usage(conversation_id)
                if credential_mode == SHARED_GROQ_MODE:
                    if text_field("provider", "groq").strip().lower() != "groq":
                        raise ValueError("BrainCellGPT free access uses Groq. Choose your own provider key to use another provider.")
                    if usage_store.shared_usage() >= MAX_SHARED_MESSAGES_PER_INSTALLATION:
                        raise MessageLimitReached

                def reserve_chat_message() -> int:
                    nonlocal reserved_count
                    if credential_mode == SHARED_GROQ_MODE:
                        reserved_count = usage_store.reserve_shared_message(conversation_id, request_id)
                        return reserved_count
                    usage_store.reserve_own_request(conversation_id, request_id)
                    return 0

                request_start = reserve_chat_message
            if route == "/api/test-connection" and credential_mode == SHARED_GROQ_MODE and text_field("provider", "groq").strip().lower() != "groq":
                raise ValueError("BrainCellGPT free access uses Groq. Choose your own provider key to use another provider.")
            if credential_mode == OWN_KEY_MODE and route == "/api/chat":
                saved_key = _get_profile_secret(profile_id)
                if not saved_key and text_field("provider", "groq").strip().lower() not in ("ollama", "lmstudio"):
                    raise ValueError("Your own provider key is not available in this server session. Open Settings and save it again.")
            reply = generate_reply(
                message, history, text_field("personality", "normal"),
                text_field("provider", "groq"), supplied_key or saved_key,
                text_field("base_url"), text_field("model"), images=images,
                on_request_start=request_start,
                allow_environment_key=allow_environment_key,
            )
            if route == "/api/test-connection":
                self.send_json(200, {"ok": True, "message": "Connection successful.", "provider": text_field("provider", "groq")})
            elif credential_mode == OWN_KEY_MODE:
                self.send_json(200, {"reply": reply, "credential_mode": OWN_KEY_MODE})
            else:
                self.send_json(200, {
                    "reply": reply,
                    "messages_used": reserved_count,
                    "message_limit": MAX_SHARED_MESSAGES_PER_INSTALLATION,
                    "credential_mode": SHARED_GROQ_MODE,
                })
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Invalid JSON request."})
        except ConversationNotFound:
            self.send_json(404, {"error": "Conversation not found. Start a new conversation."})
        except DuplicateChatRequest:
            self.send_json(409, {"error": "This message was already submitted. Check the conversation before trying again."})
        except MessageLimitReached:
            self.send_json(429, {
                "error": "BrainCellGPT free access has used all 10 messages for this installation. Add your own provider key in Settings to continue.",
                "messages_used": MAX_SHARED_MESSAGES_PER_INSTALLATION,
                "message_limit": MAX_SHARED_MESSAGES_PER_INSTALLATION,
            })
        except RuntimeError as exc:
            print(f"[BrainCellGPT] chat request failed: {str(exc)[:300]}")
            response_data = {"error": str(exc)}
            if route == "/api/chat" and reserved_count is not None:
                response_data.update({"messages_used": reserved_count, "message_limit": MAX_SHARED_MESSAGES_PER_INSTALLATION})
            self.send_json(502, response_data)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            # Log the exception type for diagnosis without returning internals to clients.
            print(f"[BrainCellGPT] request failed: {type(exc).__name__}")
            self.send_json(500, {"error": "Unexpected server error. Check the terminal for details."})

    def do_DELETE(self) -> None:
        if not self.require_authorization():
            return
        if urlparse(self.path).path == "/api/conversations":
            if self.client_address[0] not in ("127.0.0.1", "::1") or not self.is_trusted_local_request():
                self.send_json(403, {"error": "This local app only accepts requests from this computer."})
                return
            if rate_limited(self.client_address[0] + ":delete-conversation"):
                self.send_json(429, {"error": "Too many requests. Wait a minute and try again."})
                return
            conversation_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            try:
                _chat_usage_store().delete_conversation(conversation_id)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200, {"deleted": True})
            return
        self.send_json(404, {"error": "Not found"})

    def log_message(self, format: str, *args) -> None:
        print(f"[BrainCellGPT] {self.address_string()} - {format % args}")


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    request_queue_size = MAX_HTTP_HANDLERS
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args, **kwargs):
        self._handler_slots = BoundedSemaphore(MAX_HTTP_HANDLERS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._handler_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._handler_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handler_slots.release()


def main() -> None:
    server = BoundedThreadingHTTPServer((HOST, PORT), Handler)
    print("\nBrainCellGPT is running.")
    print(f"Open {PUBLIC_ORIGIN if REMOTE_ACCESS_ENABLED and not _is_loopback_host(HOST) else f'http://localhost:{PORT}'} in your browser.")
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
