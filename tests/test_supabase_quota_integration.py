"""Integration tests covering all 14 required Supabase anonymous daily quota scenarios."""
import email.message
import io
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

import app
from backend.chat_usage import ChatUsageStore, OWN_KEY_MODE, SHARED_GROQ_MODE
from backend.supabase_quota import (
    ANON_COOKIE_NAME,
    DuplicateAnonymousRequestError,
    QuotaExceededError,
    SupabaseQuotaClient,
    SupabaseUnavailableError,
    generate_anonymous_identity,
    sign_client_id,
    verify_client_id,
)


class TestHandler(app.Handler):
    """Test harness simulating HTTP requests and responses for app.Handler."""

    def __init__(self, method="POST", path="/api/chat", body=None, headers=None):
        self.client_address = ("127.0.0.1", 54321)
        self.path = path
        self.command = method
        self.headers = email.message.Message()
        self.headers["Host"] = "127.0.0.1:8000"
        self.headers["Content-Type"] = "application/json"
        if headers:
            for k, v in headers.items():
                self.headers[k] = v
        body_bytes = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else (body or b"")
        if body_bytes:
            self.headers["Content-Length"] = str(len(body_bytes))
        self.rfile = io.BytesIO(body_bytes)
        self.wfile = io.BytesIO()
        self.response_status = None
        self.response_headers = {}

    def send_response(self, code, message=None):
        self.response_status = code

    def send_header(self, keyword, value):
        if keyword in self.response_headers:
            if not isinstance(self.response_headers[keyword], list):
                self.response_headers[keyword] = [self.response_headers[keyword]]
            self.response_headers[keyword].append(value)
        else:
            self.response_headers[keyword] = value

    def end_headers(self):
        pass

    def get_json(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class SupabaseQuotaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.test_dir.name, "test-usage.sqlite3")
        self.usage_store = ChatUsageStore(database_path=self.db_path)
        app._chat_usage_store_instance = self.usage_store

        self.signing_secret = "test-signing-secret-for-integration-tests-32chars"
        self.supabase_url = "https://testproject.supabase.co"
        self.supabase_key = "test-supabase-secret-key"

        self.env_patcher = patch.dict(
            os.environ,
            {
                "ANON_CLIENT_SIGNING_SECRET": self.signing_secret,
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_SECRET_KEY": self.supabase_key,
                "GROQ_API_KEY": "gsk_test_shared_key",
            },
        )
        self.env_patcher.start()

        self.mock_quota_client = MagicMock(spec=SupabaseQuotaClient)
        app.set_supabase_quota_client(self.mock_quota_client)

        # Clear rate limit events and profile secrets
        with app._rate_lock:
            app._rate_events.clear()
        with app._profile_secrets_lock:
            app._profile_secrets.clear()

    def tearDown(self):
        self.env_patcher.stop()
        app.set_supabase_quota_client(None)
        app._chat_usage_store_instance = None
        self.test_dir.cleanup()

    # --- Scenario 1: New anonymous identity creation ---
    @patch("app.generate_reply", return_value="Hello from Groq")
    def test_1_new_anonymous_identity_creation(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex

        self.mock_quota_client.reserve_anonymous_message.return_value = {
            "allowed": True,
            "messages_used": 1,
            "message_limit": 10,
        }
        self.mock_quota_client.finalize_anonymous_message.return_value = {
            "success": True,
            "status": "finalized",
            "messages_used": 1,
            "message_limit": 10,
        }

        # Request has no Cookie header
        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": req_id, "message": "Hi", "provider": "groq"},
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 200)
        cookie = handler.response_headers.get("Set-Cookie", "")
        self.assertIn(f"{ANON_COOKIE_NAME}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

        # Extract token and verify HMAC signature
        token_part = cookie.split(";")[0].split("=", 1)[1]
        verified_id = verify_client_id(token_part, secret=self.signing_secret)
        self.assertTrue(uuid.UUID(verified_id))

    # --- Scenario 2: Valid signed identity reuse ---
    @patch("app.generate_reply", return_value="Reused identity response")
    def test_2_valid_signed_identity_reuse(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex

        client_id, valid_token = generate_anonymous_identity(secret=self.signing_secret)

        self.mock_quota_client.reserve_anonymous_message.return_value = {
            "allowed": True,
            "messages_used": 2,
            "message_limit": 10,
        }
        self.mock_quota_client.finalize_anonymous_message.return_value = {
            "success": True,
            "status": "finalized",
            "messages_used": 2,
            "message_limit": 10,
        }

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": req_id, "message": "Second message", "provider": "groq"},
            headers={"Cookie": f"{ANON_COOKIE_NAME}={valid_token}"},
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 200)
        # Verified that the same client_id was passed to Supabase
        call_client_id = self.mock_quota_client.reserve_anonymous_message.call_args[0][0]
        self.assertEqual(call_client_id, client_id)

    # --- Scenario 3: Invalid or forged identity rejection ---
    def test_3_invalid_or_forged_identity_rejection(self):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex

        client_id, valid_token = generate_anonymous_identity(secret=self.signing_secret)
        # Tamper with signature
        forged_token = valid_token[:-4] + "dead"

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": req_id, "message": "Hacked", "provider": "groq"},
            headers={"Cookie": f"{ANON_COOKIE_NAME}={forged_token}"},
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 400)
        data = handler.get_json()
        self.assertIn("Invalid anonymous identity signature", data.get("error", ""))
        self.mock_quota_client.reserve_anonymous_message.assert_not_called()

    # --- Scenario 4: Successful shared-Groq request ---
    @patch("app.generate_reply", return_value="Groq success")
    def test_4_successful_shared_groq_request(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex

        client_id, token = generate_anonymous_identity(secret=self.signing_secret)
        self.mock_quota_client.reserve_anonymous_message.return_value = {
            "allowed": True,
            "messages_used": 1,
            "message_limit": 10,
        }
        self.mock_quota_client.finalize_anonymous_message.return_value = {
            "success": True,
            "status": "finalized",
            "messages_used": 1,
            "message_limit": 10,
        }

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": req_id, "message": "Tell me a joke", "provider": "groq"},
            headers={"Cookie": f"{ANON_COOKIE_NAME}={token}"},
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 200)
        data = handler.get_json()
        self.assertEqual(data["reply"], "Groq success")
        self.assertEqual(data["messages_used"], 1)
        self.assertEqual(data["message_limit"], 10)
        self.assertEqual(data["credential_mode"], SHARED_GROQ_MODE)

        # Verify finalize was called with success=True
        self.mock_quota_client.finalize_anonymous_message.assert_called_once()
        finalize_kwargs = self.mock_quota_client.finalize_anonymous_message.call_args[1]
        finalize_args = self.mock_quota_client.finalize_anonymous_message.call_args[0]
        # p_success should be True
        success_arg = finalize_kwargs.get("success") if "success" in finalize_kwargs else finalize_args[1]
        self.assertTrue(success_arg)

    # --- Scenario 5: Failed Groq request releasing its reservation ---
    @patch("app.generate_reply", side_effect=RuntimeError("Groq API rate limit or outage"))
    def test_5_failed_groq_request_releases_reservation(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex

        client_id, token = generate_anonymous_identity(secret=self.signing_secret)
        self.mock_quota_client.reserve_anonymous_message.return_value = {
            "allowed": True,
            "messages_used": 1,
            "message_limit": 10,
        }
        self.mock_quota_client.finalize_anonymous_message.return_value = {
            "success": True,
            "status": "released",
            "messages_used": 0,
            "message_limit": 10,
        }

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": req_id, "message": "Fail me", "provider": "groq"},
            headers={"Cookie": f"{ANON_COOKIE_NAME}={token}"},
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 502)
        data = handler.get_json()
        self.assertIn("Groq API rate limit", data.get("error", ""))

        # Verify finalize was called with success=False to release reservation
        self.mock_quota_client.finalize_anonymous_message.assert_called_once()
        finalize_kwargs = self.mock_quota_client.finalize_anonymous_message.call_args[1]
        finalize_args = self.mock_quota_client.finalize_anonymous_message.call_args[0]
        success_arg = finalize_kwargs.get("success") if "success" in finalize_kwargs else finalize_args[1]
        self.assertFalse(success_arg)

    # --- Scenario 6: Ten successful messages being accepted ---
    @patch("app.generate_reply", return_value="Success")
    def test_6_ten_successful_messages_accepted(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        client_id, token = generate_anonymous_identity(secret=self.signing_secret)

        for i in range(1, 11):
            req_id = uuid.uuid4().hex
            self.mock_quota_client.reserve_anonymous_message.return_value = {
                "allowed": True,
                "messages_used": i,
                "message_limit": 10,
            }
            self.mock_quota_client.finalize_anonymous_message.return_value = {
                "success": True,
                "status": "finalized",
                "messages_used": i,
                "message_limit": 10,
            }

            handler = TestHandler(
                method="POST",
                path="/api/chat",
                body={"conversation_id": conv_id, "request_id": req_id, "message": f"Message {i}", "provider": "groq"},
                headers={"Cookie": f"{ANON_COOKIE_NAME}={token}"},
            )
            handler.do_POST()
            self.assertEqual(handler.response_status, 200)
            self.assertEqual(handler.get_json()["messages_used"], i)

    # --- Scenario 7: The eleventh message being rejected ---
    @patch("app.generate_reply")
    def test_7_eleventh_message_rejected(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex
        client_id, token = generate_anonymous_identity(secret=self.signing_secret)

        self.mock_quota_client.reserve_anonymous_message.side_effect = QuotaExceededError(
            "Daily anonymous Groq quota reached (10 messages per day). Quota resets at 00:00 UTC."
        )

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": req_id, "message": "Message 11", "provider": "groq"},
            headers={"Cookie": f"{ANON_COOKIE_NAME}={token}"},
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 429)
        data = handler.get_json()
        self.assertIn("Daily anonymous Groq quota reached", data.get("error", ""))
        self.assertEqual(data.get("messages_used"), 10)
        self.assertEqual(data.get("message_limit"), 10)
        mock_reply.assert_not_called()

    # --- Scenario 8: Duplicate request handling ---
    @patch("app.generate_reply")
    def test_8_duplicate_request_handling(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex
        client_id, token = generate_anonymous_identity(secret=self.signing_secret)

        self.mock_quota_client.reserve_anonymous_message.side_effect = DuplicateAnonymousRequestError(
            "This message was already submitted. Check the conversation before trying again."
        )

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": req_id, "message": "Dup", "provider": "groq"},
            headers={"Cookie": f"{ANON_COOKIE_NAME}={token}"},
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 409)
        data = handler.get_json()
        self.assertIn("already submitted", data.get("error", ""))
        mock_reply.assert_not_called()

    # --- Scenario 9: Supabase unavailable behavior ---
    @patch("app.generate_reply")
    def test_9_supabase_unavailable_behavior(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex
        client_id, token = generate_anonymous_identity(secret=self.signing_secret)

        self.mock_quota_client.reserve_anonymous_message.side_effect = SupabaseUnavailableError(
            "Supabase quota service is unreachable"
        )

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": req_id, "message": "Supabase down", "provider": "groq"},
            headers={"Cookie": f"{ANON_COOKIE_NAME}={token}"},
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 503)
        data = handler.get_json()
        self.assertIn("Quota service is temporarily unavailable", data.get("error", ""))
        # Crucial: provider is never called and quota is not bypassed!
        mock_reply.assert_not_called()

    # --- Scenario 10: Missing secret configuration ---
    @patch("app.generate_reply")
    def test_10_missing_secret_configuration(self, mock_reply):
        # Unset Supabase client override to use default client check
        app.set_supabase_quota_client(None)

        # Case 10a: SUPABASE_URL is configured (as on Vercel), but ANON_CLIENT_SIGNING_SECRET is missing
        with patch.dict(os.environ, {
            "GROQ_API_KEY": "gsk_test",
            "SUPABASE_URL": self.supabase_url,
        }, clear=True):
            self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
            conv_id = self.usage_store.create_conversation()
            req_id = uuid.uuid4().hex

            handler = TestHandler(
                method="POST",
                path="/api/chat",
                body={"conversation_id": conv_id, "request_id": req_id, "message": "No config", "provider": "groq"},
            )
            handler.do_POST()

            self.assertEqual(handler.response_status, 503)
            data = handler.get_json()
            self.assertIn("ANON_CLIENT_SIGNING_SECRET is not configured", data.get("error", ""))
            mock_reply.assert_not_called()

        # Case 10b: ANON_CLIENT_SIGNING_SECRET is configured, but SUPABASE_SECRET_KEY is missing
        with patch.dict(os.environ, {
            "GROQ_API_KEY": "gsk_test",
            "SUPABASE_URL": self.supabase_url,
            "ANON_CLIENT_SIGNING_SECRET": self.signing_secret,
        }, clear=True):
            conv_id = self.usage_store.create_conversation()
            req_id = uuid.uuid4().hex

            handler = TestHandler(
                method="POST",
                path="/api/chat",
                body={"conversation_id": conv_id, "request_id": req_id, "message": "No secret key", "provider": "groq"},
            )
            handler.do_POST()

            self.assertEqual(handler.response_status, 503)
            data = handler.get_json()
            self.assertIn("SUPABASE_SECRET_KEY", data.get("error", ""))
            mock_reply.assert_not_called()

    # --- Scenario 11: BYOK requests bypassing anonymous quota ---
    @patch("app.generate_reply", return_value="BYOK reply")
    def test_11_byok_requests_bypassing_anonymous_quota(self, mock_reply):
        # Configure BYOK mode
        self.usage_store.set_credential_mode(OWN_KEY_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex
        profile_id = "test-profile-12345678"
        app._set_profile_secret(profile_id, "user-groq-key")

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={
                "conversation_id": conv_id,
                "request_id": req_id,
                "profile_id": profile_id,
                "message": "BYOK message",
                "provider": "groq",
            },
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 200)
        data = handler.get_json()
        self.assertEqual(data["reply"], "BYOK reply")
        self.assertEqual(data["credential_mode"], OWN_KEY_MODE)

        # Crucial: Supabase quota was never contacted
        self.mock_quota_client.reserve_anonymous_message.assert_not_called()
        self.mock_quota_client.finalize_anonymous_message.assert_not_called()

    # --- Scenario 12: Ollama requests bypassing anonymous quota ---
    @patch("app.generate_reply", return_value="Ollama reply")
    def test_12_ollama_requests_bypassing_anonymous_quota(self, mock_reply):
        self.usage_store.set_credential_mode(OWN_KEY_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={
                "conversation_id": conv_id,
                "request_id": req_id,
                "message": "Local Ollama message",
                "provider": "ollama",
                "base_url": "http://localhost:11434/v1",
            },
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 200)
        data = handler.get_json()
        self.assertEqual(data["reply"], "Ollama reply")

        self.mock_quota_client.reserve_anonymous_message.assert_not_called()
        self.mock_quota_client.finalize_anonymous_message.assert_not_called()

    # --- Scenario 13: LM Studio requests bypassing anonymous quota ---
    @patch("app.generate_reply", return_value="LM Studio reply")
    def test_13_lm_studio_requests_bypassing_anonymous_quota(self, mock_reply):
        self.usage_store.set_credential_mode(OWN_KEY_MODE)
        conv_id = self.usage_store.create_conversation()
        req_id = uuid.uuid4().hex

        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={
                "conversation_id": conv_id,
                "request_id": req_id,
                "message": "Local LM Studio message",
                "provider": "lmstudio",
                "base_url": "http://localhost:1234/v1",
            },
        )
        handler.do_POST()

        self.assertEqual(handler.response_status, 200)
        data = handler.get_json()
        self.assertEqual(data["reply"], "LM Studio reply")

        self.mock_quota_client.reserve_anonymous_message.assert_not_called()
        self.mock_quota_client.finalize_anonymous_message.assert_not_called()

    # --- Scenario 14: Existing chat functionality remaining unaffected ---
    def test_14_existing_chat_functionality_unaffected(self):
        # Test conversation creation
        create_handler = TestHandler(
            method="POST",
            path="/api/conversations",
            body={},
        )
        create_handler.do_POST()
        self.assertEqual(create_handler.response_status, 201)
        conv_id = create_handler.get_json()["conversation_id"]
        self.assertTrue(conv_id)

        # Test conversation usage retrieval
        usage_handler = TestHandler(
            method="GET",
            path=f"/api/conversations/usage?id={conv_id}",
        )
        usage_handler.do_GET()
        self.assertEqual(usage_handler.response_status, 200)
        self.assertEqual(usage_handler.get_json()["messages_used"], 0)

        # Test chat config endpoint
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        config_handler = TestHandler(
            method="GET",
            path="/api/chat/config",
        )
        config_handler.do_GET()
        self.assertEqual(config_handler.response_status, 200)
        config_data = config_handler.get_json()
        self.assertEqual(config_data["credential_mode"], SHARED_GROQ_MODE)
        self.assertEqual(config_data["shared_message_limit"], 10)

        # Test conversation deletion
        delete_handler = TestHandler(
            method="DELETE",
            path=f"/api/conversations?id={conv_id}",
        )
        delete_handler.do_DELETE()
        self.assertEqual(delete_handler.response_status, 200)
        self.assertTrue(delete_handler.get_json()["deleted"])

    def test_supabase_quota_status_uses_signed_cookie_and_private_rpc(self):
        client_id, token = generate_anonymous_identity(secret=self.signing_secret)
        self.mock_quota_client.get_anonymous_quota_status.return_value = {
            "usage_date": "2026-11-04",
            "messages_used": 4,
            "reserved_messages": 1,
            "remaining_messages": 5,
        }
        handler = TestHandler(
            method="GET",
            path="/api/chat/quota",
            headers={"Cookie": f"{ANON_COOKIE_NAME}={token}"},
        )

        handler.do_GET()

        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.get_json()["messages_used"], 4)
        self.assertEqual(handler.get_json()["reserved_messages"], 1)
        self.mock_quota_client.get_anonymous_quota_status.assert_called_once_with(client_id)

    @patch("app.generate_reply")
    def test_quota_rejection_keeps_newly_issued_identity_cookie(self, mock_reply):
        self.usage_store.set_credential_mode(SHARED_GROQ_MODE)
        conv_id = self.usage_store.create_conversation()
        self.mock_quota_client.reserve_anonymous_message.side_effect = QuotaExceededError("quota reached")
        handler = TestHandler(
            method="POST",
            path="/api/chat",
            body={"conversation_id": conv_id, "request_id": uuid.uuid4().hex, "message": "Hi", "provider": "groq"},
        )

        handler.do_POST()

        self.assertEqual(handler.response_status, 429)
        cookie = handler.response_headers.get("Set-Cookie", "")
        self.assertIn(f"{ANON_COOKIE_NAME}=", cookie)
        token = cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertTrue(verify_client_id(token, secret=self.signing_secret))
        mock_reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
