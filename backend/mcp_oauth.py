"""OAuth 2.1 authorization-code support for remote MCP Streamable HTTP."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from backend.provider_http import PROVIDER_SLOTS, open_provider_request, resolve_public_addresses


MAX_OAUTH_METADATA_BYTES = 256_000
MAX_OAUTH_TOKEN_BYTES = 8_192
OAUTH_TIMEOUT_SECONDS = 15
AUTHORIZATION_WAIT_SECONDS = 240
_TOKEN_LOCK = threading.RLock()
_TOKENS: dict[str, dict[str, Any]] = {}
_CLIENT_IDS: dict[tuple[str, str], str] = {}
_FLOW_LOCKS: dict[str, threading.Lock] = {}
_PENDING: dict[str, "_PendingAuthorization"] = {}


@dataclass
class _PendingAuthorization:
    server_url: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    verifier: str
    resource: str
    scopes: str
    event: threading.Event = field(default_factory=threading.Event)
    token: dict[str, Any] | None = None
    error: str | None = None


def _https_endpoint(url: Any) -> tuple[str, tuple]:
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("OAuth metadata contains an invalid endpoint URL.")
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("OAuth metadata contains an invalid endpoint URL.") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or any(ch.isspace() or ord(ch) < 0x20 for ch in url)):
        raise ValueError("OAuth authorization endpoints must be public HTTPS URLs without credentials or fragments.")
    return url, resolve_public_addresses(parsed.hostname, port)


def _request_json(url: str, body: dict | None = None, content_type: str = "application/json") -> dict:
    endpoint, pinned = _https_endpoint(url)
    headers = {"Accept": "application/json"}
    encoded = None
    if body is not None:
        if content_type == "application/x-www-form-urlencoded":
            encoded = urllib.parse.urlencode(body).encode("utf-8")
        else:
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_OAUTH_METADATA_BYTES:
            raise ValueError("OAuth request exceeds the size limit.")
        headers["Content-Type"] = content_type
    request = urllib.request.Request(endpoint, data=encoded, headers=headers, method="POST" if encoded is not None else "GET")
    if not PROVIDER_SLOTS.acquire(timeout=2):
        raise ValueError("BrainCellGPT is busy handling provider and MCP requests. Try again shortly.")
    try:
        try:
            with open_provider_request(request, timeout=OAUTH_TIMEOUT_SECONDS, pinned_addresses=pinned) as response:
                raw = response.read(MAX_OAUTH_METADATA_BYTES + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ValueError("OAuth authorization server returned HTTP " + str(exc.code) + ".") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError("Could not reach the OAuth authorization server.") from exc
    finally:
        PROVIDER_SLOTS.release()
    if len(raw) > MAX_OAUTH_METADATA_BYTES:
        raise ValueError("OAuth authorization server response exceeds the size limit.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("OAuth authorization server returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("OAuth authorization server returned an invalid JSON object.")
    return value


def _challenge_params(challenge: str | None) -> dict[str, str]:
    if not challenge or len(challenge) > 16_000:
        return {}
    # RFC 6750 auth-param values are quoted strings or tokens. Limit the parser
    # to known fields and reject escaped/ambiguous values by not matching them.
    import re
    return {key.lower(): value for key, value in re.findall(r'([A-Za-z][A-Za-z0-9_-]*)\s*=\s*"([^"\\\r\n]{1,2048})"', challenge)}


def requires_scope_upgrade(challenge: str | None) -> bool:
    return _challenge_params(challenge).get("error") == "insufficient_scope"


def _resource_metadata_urls(server_url: str, challenge: str | None) -> list[str]:
    challenge_url = _challenge_params(challenge).get("resource_metadata")
    if challenge_url:
        return [_https_endpoint(challenge_url)[0]]
    parsed = urlsplit(server_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    values = [origin + "/.well-known/oauth-protected-resource" + path]
    root = origin + "/.well-known/oauth-protected-resource"
    if root not in values:
        values.append(root)
    return values


def _authorization_server_metadata_urls(issuer: str) -> list[str]:
    parsed = urlsplit(issuer)
    issuer_path = parsed.path.rstrip("/")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    base = "/.well-known/oauth-authorization-server"
    if issuer_path:
        return [origin + base + issuer_path, origin + "/.well-known/openid-configuration" + issuer_path,
                issuer.rstrip("/") + "/.well-known/openid-configuration"]
    return [origin + base, origin + "/.well-known/openid-configuration"]


def _discover(server_url: str, challenge: str | None) -> tuple[dict, dict, str]:
    resource_metadata = None
    last_error = None
    for url in _resource_metadata_urls(server_url, challenge):
        try:
            resource_metadata = _request_json(url)
            break
        except ValueError as exc:
            last_error = exc
    if not isinstance(resource_metadata, dict):
        raise ValueError("Could not discover remote MCP OAuth metadata.") from last_error
    resource = resource_metadata.get("resource")
    authorization_servers = resource_metadata.get("authorization_servers")
    if resource != server_url:
        raise ValueError("Remote MCP OAuth metadata names a different protected resource.")
    if not isinstance(authorization_servers, list) or not authorization_servers or not isinstance(authorization_servers[0], str):
        raise ValueError("Remote MCP OAuth metadata has no authorization server.")
    issuer, _ = _https_endpoint(authorization_servers[0])
    if urlsplit(issuer).query:
        raise ValueError("OAuth authorization-server issuer cannot contain a query string.")
    metadata = None
    last_error = None
    for url in _authorization_server_metadata_urls(issuer):
        try:
            candidate = _request_json(url)
            if candidate.get("issuer") != issuer:
                raise ValueError("OAuth authorization server issuer does not match its metadata URL.")
            metadata = candidate
            break
        except ValueError as exc:
            last_error = exc
    if metadata is None:
        raise ValueError("Could not discover OAuth authorization server endpoints.") from last_error
    for key in ("authorization_endpoint", "token_endpoint"):
        _https_endpoint(metadata.get(key))
    return resource_metadata, metadata, issuer


def _register_client(metadata: dict, issuer: str, redirect_uri: str, configured_client_id: str | None) -> str:
    if configured_client_id:
        return configured_client_id
    cache_key = (issuer, redirect_uri)
    with _TOKEN_LOCK:
        if cache_key in _CLIENT_IDS:
            return _CLIENT_IDS[cache_key]
    registration_endpoint = metadata.get("registration_endpoint")
    if not registration_endpoint:
        raise ValueError("This OAuth server needs a pre-registered client_id or a dynamic registration endpoint.")
    _https_endpoint(registration_endpoint)
    registered = _request_json(registration_endpoint, {
        "client_name": "BrainCellGPT",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "application_type": "native",
        "token_endpoint_auth_method": "none",
    })
    client_id = registered.get("client_id")
    if not isinstance(client_id, str) or not client_id or len(client_id) > 1000:
        raise ValueError("OAuth dynamic client registration returned an invalid client ID.")
    with _TOKEN_LOCK:
        _CLIENT_IDS[cache_key] = client_id
    return client_id


def _validate_token(value: dict, previous: dict | None = None) -> dict:
    access_token = value.get("access_token")
    if (not isinstance(access_token, str) or not access_token or len(access_token) > MAX_OAUTH_TOKEN_BYTES
            or any(ord(ch) < 0x21 or ord(ch) > 0x7e for ch in access_token)):
        raise ValueError("OAuth token response did not include a valid access token.")
    if not isinstance(value.get("token_type", "Bearer"), str) or value.get("token_type", "Bearer").lower() != "bearer":
        raise ValueError("OAuth authorization server returned an unsupported token type.")
    refresh_token = value.get("refresh_token")
    if refresh_token is not None and (not isinstance(refresh_token, str) or len(refresh_token) > MAX_OAUTH_TOKEN_BYTES):
        raise ValueError("OAuth token response included an invalid refresh token.")
    scope = value.get("scope")
    if scope is not None and (not isinstance(scope, str) or len(scope) > 2000):
        raise ValueError("OAuth token response included an invalid scope value.")
    expires = value.get("expires_in", 3600)
    if type(expires) is not int or not 1 <= expires <= 31_536_000:
        expires = 3600
    return {
        "access_token": access_token,
        "refresh_token": refresh_token or (previous or {}).get("refresh_token"),
        "expires_at": time.time() + expires,
        "scope": scope or (previous or {}).get("scope", ""),
    }


def _refresh(server_url: str, cached: dict) -> dict | None:
    refresh_token = cached.get("refresh_token")
    if not refresh_token:
        return None
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": cached["client_id"],
        "resource": cached["resource"],
    }
    scopes = cached.get("scope")
    if scopes:
        payload["scope"] = scopes
    try:
        value = _request_json(cached["token_endpoint"], payload, "application/x-www-form-urlencoded")
        token = _validate_token(value, cached)
    except ValueError:
        return None
    return {**cached, **token}


def _access_token_once(server_url: str, challenge: str | None, callback_port: int, configured_client_id: str | None,
                       event_callback, timeout: int, force_refresh: bool, force_reauthorize: bool) -> str:
    """Return a live access token, launching a localhost PKCE flow if needed."""
    server_key = server_url
    cached = None
    with _TOKEN_LOCK:
        cached = _TOKENS.get(server_key)
    if cached and not force_reauthorize:
        if cached["expires_at"] > time.time() + 60 and not force_refresh:
            return cached["access_token"]
        refreshed = _refresh(server_key, cached)
        if refreshed:
            with _TOKEN_LOCK:
                _TOKENS[server_key] = refreshed
            return refreshed["access_token"]

    resource_metadata, metadata, issuer = _discover(server_url, challenge)
    challenge_methods = metadata.get("code_challenge_methods_supported")
    if not isinstance(challenge_methods, list) or "S256" not in challenge_methods:
        raise ValueError("OAuth server does not advertise PKCE S256; refusing insecure authorization.")
    redirect_uri = f"http://127.0.0.1:{callback_port}/api/mcp/oauth/callback"
    client_id = _register_client(metadata, issuer, redirect_uri, configured_client_id)
    token_endpoint = metadata["token_endpoint"]
    resource = server_url
    challenge_params = _challenge_params(challenge)
    requested_scopes = challenge_params.get("scope")
    if not requested_scopes:
        advertised = resource_metadata.get("scopes_supported", [])
        if isinstance(advertised, list):
            requested_scopes = " ".join(scope for scope in advertised if isinstance(scope, str) and len(scope) <= 200)[:2000]
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    pending = _PendingAuthorization(server_key, token_endpoint, client_id, redirect_uri, verifier, resource, requested_scopes or "")
    with _TOKEN_LOCK:
        _PENDING[state] = pending
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    }
    if requested_scopes:
        params["scope"] = requested_scopes
    authorization_parts = urlsplit(metadata["authorization_endpoint"])
    authorization_query = urllib.parse.parse_qsl(authorization_parts.query, keep_blank_values=True) + list(params.items())
    authorization_url = urllib.parse.urlunsplit((authorization_parts.scheme, authorization_parts.netloc, authorization_parts.path,
                                                 urllib.parse.urlencode(authorization_query), ""))
    opened = False
    try:
        opened = webbrowser.open(authorization_url, new=2)
    except Exception:
        opened = False
    if event_callback is not None:
        try:
            event_callback({"type": "oauth_authorization_required", "server": server_key, "browser_opened": bool(opened)})
        except Exception:
            pass
    if not opened:
        with _TOKEN_LOCK:
            _PENDING.pop(state, None)
        raise ValueError("Remote MCP OAuth is required, but BrainCellGPT could not open the authorization page.")
    if not pending.event.wait(timeout):
        with _TOKEN_LOCK:
            _PENDING.pop(state, None)
        raise ValueError("Remote MCP OAuth authorization timed out. Retry the MCP action to start again.")
    with _TOKEN_LOCK:
        _PENDING.pop(state, None)
    if pending.error or not pending.token:
        raise ValueError(pending.error or "Remote MCP OAuth authorization failed.")
    token_state = {
        **pending.token,
        "client_id": client_id,
        "token_endpoint": token_endpoint,
        "resource": resource,
        "scope": pending.token.get("scope") or requested_scopes or "",
        "issuer": issuer,
    }
    with _TOKEN_LOCK:
        _TOKENS[server_key] = token_state
    return token_state["access_token"]


def access_token(server_url: str, challenge: str | None, callback_port: int, configured_client_id: str | None = None,
                 event_callback=None, timeout: int = AUTHORIZATION_WAIT_SECONDS, force_refresh: bool = False,
                 force_reauthorize: bool = False) -> str:
    """Serialize token refresh/authorization to handle refresh-token rotation."""
    with _TOKEN_LOCK:
        flow_lock = _FLOW_LOCKS.setdefault(server_url, threading.Lock())
    with flow_lock:
        return _access_token_once(server_url, challenge, callback_port, configured_client_id,
                                  event_callback, timeout, force_refresh, force_reauthorize)


def complete_callback(query: dict[str, list[str]]) -> str:
    """Validate and finish one loopback authorization-code callback."""
    state_values = query.get("state", [])
    state = state_values[0] if len(state_values) == 1 else ""
    with _TOKEN_LOCK:
        pending = _PENDING.get(state)
    if pending is None:
        raise ValueError("OAuth callback state is invalid or expired.")
    errors = query.get("error", [])
    if errors:
        pending.error = "Remote MCP OAuth authorization was denied or failed."
        pending.event.set()
        raise ValueError(pending.error)
    codes = query.get("code", [])
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 4000:
        pending.error = "OAuth callback did not contain a valid authorization code."
        pending.event.set()
        raise ValueError(pending.error)
    try:
        token = _request_json(pending.token_endpoint, {
            "grant_type": "authorization_code",
            "code": codes[0],
            "redirect_uri": pending.redirect_uri,
            "client_id": pending.client_id,
            "code_verifier": pending.verifier,
            "resource": pending.resource,
        }, "application/x-www-form-urlencoded")
    except ValueError:
        pending.error = "OAuth authorization-code exchange failed. Return to BrainCellGPT and retry."
        pending.event.set()
        raise
    try:
        pending.token = _validate_token(token)
    except ValueError as exc:
        pending.error = str(exc)
        pending.event.set()
        raise
    if isinstance(token.get("scope"), str):
        pending.token["scope"] = token["scope"][:2000]
    pending.event.set()
    return "Authorization complete. You can close this tab and return to BrainCellGPT."


def clear_tokens() -> None:
    """Clear in-memory OAuth grants, useful for explicit disconnect and tests."""
    with _TOKEN_LOCK:
        _TOKENS.clear()
        _CLIENT_IDS.clear()
        _FLOW_LOCKS.clear()
        _PENDING.clear()
