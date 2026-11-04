"""Unit tests for Supabase anonymous daily quota client and HMAC signing."""
import json
import os
import unittest
import urllib.error
import uuid
from io import BytesIO
from unittest.mock import MagicMock, patch

from backend.supabase_quota import (
    ANON_COOKIE_NAME,
    DuplicateAnonymousRequestError,
    InvalidAnonymousIdentityError,
    MissingConfigurationError,
    QuotaExceededError,
    SupabaseQuotaClient,
    SupabaseQuotaError,
    SupabaseUnavailableError,
    generate_anonymous_identity,
    get_signing_secret,
    get_supabase_config,
    normalize_uuid,
    sign_client_id,
    verify_client_id,
)


class SupabaseQuotaIdentityTests(unittest.TestCase):
    def setUp(self):
        self.secret = "test-signing-secret-minimum-length-for-hmac-32chars"

    def test_generate_anonymous_identity_creates_valid_uuid_and_signature(self):
        client_id, token = generate_anonymous_identity(secret=self.secret)
        # Verify client_id is valid UUID v4
        parsed_uuid = uuid.UUID(client_id)
        self.assertEqual(parsed_uuid.version, 4)
        self.assertEqual(str(parsed_uuid), client_id)

        # Verify token format: <uuid>.<hex_hmac>
        parts = token.split(".", 1)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0], client_id)
        self.assertEqual(len(parts[1]), 64)  # sha256 hex length

    def test_verify_client_id_success(self):
        client_id = str(uuid.uuid4())
        token = sign_client_id(client_id, secret=self.secret)
        verified_id = verify_client_id(token, secret=self.secret)
        self.assertEqual(verified_id, client_id)

    def test_verify_client_id_forged_signature_rejected(self):
        client_id = str(uuid.uuid4())
        token = sign_client_id(client_id, secret=self.secret)
        id_part, sig_part = token.split(".", 1)

        # Tamper with the signature
        tampered_sig = ("0" if sig_part[0] != "0" else "1") + sig_part[1:]
        tampered_token = f"{id_part}.{tampered_sig}"

        with self.assertRaises(InvalidAnonymousIdentityError) as ctx:
            verify_client_id(tampered_token, secret=self.secret)
        self.assertIn("Forged or invalid", str(ctx.exception))

    def test_verify_client_id_forged_uuid_rejected(self):
        client_id = str(uuid.uuid4())
        token = sign_client_id(client_id, secret=self.secret)
        _, sig_part = token.split(".", 1)

        # Pair signature with a different UUID
        different_id = str(uuid.uuid4())
        tampered_token = f"{different_id}.{sig_part}"

        with self.assertRaises(InvalidAnonymousIdentityError):
            verify_client_id(tampered_token, secret=self.secret)

    def test_verify_client_id_malformed_token_rejected(self):
        invalid_tokens = [
            "",
            "not-a-token",
            "no-dot-here-at-all",
            "not-a-uuid.signaturepart",
            f"{str(uuid.uuid4())}.",
            f".{str(uuid.uuid4())}",
            "a.b.c",
        ]
        for token in invalid_tokens:
            with self.subTest(token=token):
                with self.assertRaises(InvalidAnonymousIdentityError):
                    verify_client_id(token, secret=self.secret)

    def test_normalize_uuid_accepts_32hex_and_hyphenated(self):
        u = uuid.uuid4()
        hex32 = u.hex
        hyphenated = str(u)

        norm_hex = normalize_uuid(hex32)
        norm_hyphen = normalize_uuid(hyphenated)

        self.assertEqual(norm_hex, hyphenated)
        self.assertEqual(norm_hyphen, hyphenated)

    def test_normalize_uuid_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            normalize_uuid("not-a-valid-uuid")
        with self.assertRaises(ValueError):
            normalize_uuid("12345")


class SupabaseQuotaConfigTests(unittest.TestCase):
    def test_get_signing_secret_missing_raises_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingConfigurationError) as ctx:
                get_signing_secret()
            self.assertIn("ANON_CLIENT_SIGNING_SECRET", str(ctx.exception))

    def test_get_supabase_config_missing_raises_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingConfigurationError) as ctx:
                get_supabase_config()
            self.assertIn("SUPABASE_URL", str(ctx.exception))
            self.assertIn("SUPABASE_SECRET_KEY", str(ctx.exception))

    def test_get_supabase_config_success(self):
        env = {
            "SUPABASE_URL": "https://example.supabase.co/",
            "SUPABASE_SECRET_KEY": "test-service-key",
        }
        with patch.dict(os.environ, env, clear=True):
            url, key = get_supabase_config()
            self.assertEqual(url, "https://example.supabase.co")
            self.assertEqual(key, "test-service-key")


class SupabaseQuotaRpcClientTests(unittest.TestCase):
    def setUp(self):
        self.supabase_url = "https://testproject.supabase.co"
        self.secret_key = "test-secret-service-key"
        self.client = SupabaseQuotaClient(
            supabase_url=self.supabase_url,
            secret_key=self.secret_key,
        )
        self.client_id = str(uuid.uuid4())
        self.request_id = str(uuid.uuid4())

    @patch("urllib.request.urlopen")
    def test_reserve_anonymous_message_successful(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "allowed": True,
            "messages_used": 1,
            "message_limit": 10,
            "status": "reserved",
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.reserve_anonymous_message(self.client_id, self.request_id)

        self.assertTrue(result["allowed"])
        self.assertEqual(result["messages_used"], 1)

        # Verify request details
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, f"{self.supabase_url}/rest/v1/rpc/reserve_anonymous_message")
        self.assertEqual(req.headers.get("Apikey"), self.secret_key)
        self.assertEqual(req.headers.get("Authorization"), f"Bearer {self.secret_key}")

        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["p_client_id"], self.client_id)
        self.assertEqual(payload["p_request_id"], self.request_id)

    @patch("urllib.request.urlopen")
    def test_reserve_anonymous_message_quota_exceeded(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "allowed": False,
            "messages_used": 10,
            "message_limit": 10,
            "error": "Daily quota reached",
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with self.assertRaises(QuotaExceededError) as ctx:
            self.client.reserve_anonymous_message(self.client_id, self.request_id)
        self.assertIn("10 messages per UTC day", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_reserve_anonymous_message_conflict_duplicate(self, mock_urlopen):
        error_fp = BytesIO(b'{"code": "23505", "message": "duplicate key value violates unique constraint"}')
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url=f"{self.supabase_url}/rest/v1/rpc/reserve_anonymous_message",
            code=409,
            msg="Conflict",
            hdrs={},
            fp=error_fp,
        )

        with self.assertRaises(DuplicateAnonymousRequestError):
            self.client.reserve_anonymous_message(self.client_id, self.request_id)

    @patch("urllib.request.urlopen")
    def test_reserve_anonymous_message_supabase_unavailable_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with self.assertRaises(SupabaseUnavailableError):
            self.client.reserve_anonymous_message(self.client_id, self.request_id)

    @patch("urllib.request.urlopen")
    def test_reserve_anonymous_message_supabase_server_error(self, mock_urlopen):
        error_fp = BytesIO(b"Internal Server Error")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url=f"{self.supabase_url}/rest/v1/rpc/reserve_anonymous_message",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=error_fp,
        )

        with self.assertRaises(SupabaseUnavailableError):
            self.client.reserve_anonymous_message(self.client_id, self.request_id)

    @patch("urllib.request.urlopen")
    def test_finalize_anonymous_message_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "success": True,
            "status": "finalized",
            "messages_used": 2,
            "message_limit": 10,
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.finalize_anonymous_message(
            request_id=self.request_id,
            success=True,
            model="llama-3.3-70b-versatile",
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "finalized")

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, f"{self.supabase_url}/rest/v1/rpc/finalize_anonymous_message")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["p_request_id"], self.request_id)
        self.assertTrue(payload["p_success"])
        self.assertEqual(payload["p_model"], "llama-3.3-70b-versatile")

    @patch("urllib.request.urlopen")
    def test_finalize_anonymous_message_failure_release(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "success": True,
            "status": "released",
            "messages_used": 1,
            "message_limit": 10,
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.finalize_anonymous_message(
            request_id=self.request_id,
            success=False,
            model="llama-3.3-70b-versatile",
            error_code="TimeoutError",
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "released")

        payload = json.loads(mock_urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertFalse(payload["p_success"])
        self.assertEqual(payload["p_error_code"], "TimeoutError")


if __name__ == "__main__":
    unittest.main()
