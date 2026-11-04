"""Supabase anonymous daily quota client and HMAC identity management for BraincellGPT."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from typing import Any

ANON_COOKIE_NAME = "bc_anon_id"
ANON_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year in seconds
MAX_ANONYMOUS_DAILY_MESSAGES = 10


class SupabaseQuotaError(Exception):
    """Base exception for Supabase quota operations."""


class MissingConfigurationError(SupabaseQuotaError):
    """A required Supabase or signing secret environment variable is missing."""


class InvalidAnonymousIdentityError(SupabaseQuotaError):
    """The client provided an invalid, malformed, or forged anonymous identity."""


class SupabaseUnavailableError(SupabaseQuotaError):
    """The Supabase quota service is unreachable or returned a server error."""


class QuotaExceededError(SupabaseQuotaError):
    """The anonymous client has exceeded their daily 10-message quota."""


class DuplicateAnonymousRequestError(SupabaseQuotaError):
    """The request ID has already been reserved or finalized."""


def get_signing_secret() -> str:
    """Retrieve the secret key used to sign anonymous client IDs."""
    secret = os.environ.get("ANON_CLIENT_SIGNING_SECRET", "").strip()
    if not secret:
        raise MissingConfigurationError(
            "ANON_CLIENT_SIGNING_SECRET is not configured. "
            "Server-side HMAC signing secret is required for anonymous shared Groq access."
        )
    return secret


def get_supabase_config() -> tuple[str, str]:
    """Retrieve validated Supabase URL and secret service key."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

    missing: list[str] = []
    if not url:
        missing.append("SUPABASE_URL")
    if not key:
        missing.append("SUPABASE_SECRET_KEY")

    if missing:
        raise MissingConfigurationError(
            f"Missing required Supabase server configuration: {', '.join(missing)}. "
            "Configure these variables to enable anonymous shared Groq access."
        )
    return url, key


def sign_client_id(client_id: str, secret: str | None = None) -> str:
    """Generate an HMAC-SHA256 signature for a client UUID."""
    resolved_secret = secret if secret is not None else get_signing_secret()
    norm_id = str(uuid.UUID(client_id))
    signature = hmac.new(
        resolved_secret.encode("utf-8"),
        norm_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{norm_id}.{signature}"


def verify_client_id(token: str, secret: str | None = None) -> str:
    """Validate a signed anonymous identity token and return the normalized client UUID.

    Raises InvalidAnonymousIdentityError if the token is forged, tampered, or malformed.
    """
    if not token or not isinstance(token, str):
        raise InvalidAnonymousIdentityError("Missing or invalid anonymous identity token.")

    parts = token.split(".", 1)
    if len(parts) != 2:
        raise InvalidAnonymousIdentityError("Malformed anonymous identity token structure.")

    client_id_raw, signature = parts
    try:
        norm_id = str(uuid.UUID(client_id_raw))
    except (ValueError, AttributeError):
        raise InvalidAnonymousIdentityError("Anonymous identity client ID is not a valid UUID.")

    resolved_secret = secret if secret is not None else get_signing_secret()
    expected_sig = hmac.new(
        resolved_secret.encode("utf-8"),
        norm_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        raise InvalidAnonymousIdentityError("Forged or invalid anonymous identity signature.")

    return norm_id


def generate_anonymous_identity(secret: str | None = None) -> tuple[str, str]:
    """Generate a new random UUID v4 and return (client_uuid, signed_token)."""
    client_id = str(uuid.uuid4())
    token = sign_client_id(client_id, secret=secret)
    return client_id, token


def normalize_uuid(value: str, name: str = "ID") -> str:
    """Normalize a 32-hex or hyphenated UUID string to canonical UUID format."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid {name} format: must be a valid UUID.")


class SupabaseQuotaClient:
    """Zero-dependency HTTP client for Supabase anonymous quota RPCs."""

    def __init__(
        self,
        supabase_url: str | None = None,
        secret_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._custom_url = supabase_url
        self._custom_key = secret_key
        self.timeout = timeout

    def _get_target(self) -> tuple[str, str]:
        if self._custom_url and self._custom_key:
            return self._custom_url.rstrip("/"), self._custom_key
        return get_supabase_config()

    def _call_rpc(self, function_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        url, key = self._get_target()
        endpoint = f"{url}/rest/v1/rpc/{function_name}"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "User-Agent": "BraincellGPT-Backend/1.1.0",
        }
        request = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                if not body.strip():
                    return {}
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    result = parsed
                elif isinstance(parsed, list):
                    # PostgREST serializes RETURNS TABLE RPCs as a JSON array.
                    # These quota functions return at most one row per request.
                    if len(parsed) > 1 or (parsed and not isinstance(parsed[0], dict)):
                        raise SupabaseUnavailableError("Unexpected rows returned by Supabase quota service.")
                    result = parsed[0] if parsed else {}
                elif isinstance(parsed, bool):
                    result = {"allowed": parsed}
                else:
                    result = {"result": parsed}

                # Normalize both the original JSONB contract and the live table
                # RPC contract into the keys consumed by the backend.
                if "accepted" in result and "allowed" not in result:
                    result["allowed"] = result["accepted"]
                if "successful_messages" in result and "messages_used" not in result:
                    result["messages_used"] = result["successful_messages"]
                if "reservation_state" in result and "status" not in result:
                    result["status"] = result["reservation_state"]
                return result
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8")
            except Exception:
                err_body = ""
            try:
                exc.close()
            except OSError:
                pass

            # Detect duplicate request or conflict in PostgREST error responses
            if exc.code == 409 or "already" in err_body.lower() or "duplicate" in err_body.lower():
                raise DuplicateAnonymousRequestError(
                    "This message was already submitted. Check the conversation before trying again."
                ) from exc

            # PostgREST database exceptions usually return 400 with a message
            if exc.code == 400 and ("already been finalized" in err_body.lower() or "already been released" in err_body.lower()):
                raise DuplicateAnonymousRequestError(
                    "This request has already been processed."
                ) from exc

            if exc.code >= 500:
                raise SupabaseUnavailableError(
                    f"Supabase quota service returned server error HTTP {exc.code}."
                ) from exc

            raise SupabaseQuotaError(
                f"Supabase RPC {function_name} failed (HTTP {exc.code}): {err_body[:200]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as exc:
            raise SupabaseUnavailableError(
                "Supabase quota service is temporarily unreachable or timed out."
            ) from exc
        except json.JSONDecodeError as exc:
            raise SupabaseUnavailableError("Invalid JSON returned by Supabase quota service.") from exc

    def reserve_anonymous_message(self, client_id: str, request_id: str) -> dict[str, Any]:
        """Atomically reserve one message slot for today (UTC) via Supabase RPC.

        Raises:
            MissingConfigurationError: If required secrets are missing.
            QuotaExceededError: If daily 10-message quota is exhausted.
            DuplicateAnonymousRequestError: If request_id is already in flight.
            SupabaseUnavailableError: If Supabase cannot be reached.
        """
        norm_client_id = normalize_uuid(client_id, "client ID")
        norm_request_id = normalize_uuid(request_id, "request ID")

        result = self._call_rpc(
            "reserve_anonymous_message",
            {"p_client_id": norm_client_id, "p_request_id": norm_request_id},
        )

        allowed = result.get("allowed", False)
        if not allowed:
            state = result.get("status")
            if state and state != "limit_reached":
                raise DuplicateAnonymousRequestError(
                    "This message was already submitted. Check the conversation before trying again."
                )
            limit = MAX_ANONYMOUS_DAILY_MESSAGES
            raise QuotaExceededError(
                f"Daily anonymous Groq quota reached ({limit} messages per UTC day). "
                "Quota resets at 00:00 UTC. Add your own provider key in Settings to continue."
            )

        return result

    def get_anonymous_quota_status(self, client_id: str) -> dict[str, Any]:
        """Read today's quota through the privileged, narrowly scoped status RPC."""
        norm_client_id = normalize_uuid(client_id, "client ID")
        return self._call_rpc(
            "get_anonymous_quota_status",
            {"p_client_id": norm_client_id},
        )

    def finalize_anonymous_message(
        self,
        request_id: str,
        success: bool,
        model: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        """Finalize a message reservation.

        If success is True, increments quota usage.
        If success is False, releases the reservation so quota is not consumed.
        """
        norm_request_id = normalize_uuid(request_id, "request ID")

        return self._call_rpc(
            "finalize_anonymous_message",
            {
                "p_request_id": norm_request_id,
                "p_success": bool(success),
                "p_model": model or "",
                "p_error_code": error_code or "",
            },
        )
