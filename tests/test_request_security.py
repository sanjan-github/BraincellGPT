import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import app
from backend.chat_usage import ChatUsageStore


class RequestSecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.usage_store = ChatUsageStore(Path(self.temp_dir.name) / "chat-usage.sqlite3")
        self.usage_store_patcher = patch("app._chat_usage_store", return_value=self.usage_store)
        self.usage_store_patcher.start()
        self.server = app.BoundedThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.usage_store_patcher.stop()
        self.temp_dir.cleanup()

    def test_cross_origin_chat_is_rejected_before_provider_is_called(self):
        class RequestStub:
            path = "/api/chat"
            client_address = ("127.0.0.1", 54321)
            headers = {
                "Host": f"127.0.0.1:{app.PORT}",
                "Origin": "http://127.0.0.1:1",
                "Sec-Fetch-Site": "cross-site",
            }

            def is_trusted_local_request(self):
                return app.Handler.is_trusted_local_request(cast(app.Handler, self))

            def require_authorization(self):
                return True

            def send_json(self, status, body):
                self.response = (status, body)

        request = RequestStub()
        with patch("app.generate_reply") as provider:
            app.Handler.do_POST(cast(app.Handler, request))

        self.assertEqual(request.response[0], 403)
        self.assertIn("trusted loopback", request.response[1]["error"])
        provider.assert_not_called()

    def test_health_includes_all_registered_providers_without_credentials(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
            self.assertEqual(set(payload["providers"]), set(app.SUPPORTED_PROVIDER_NAMES))
            self.assertIn("gemini", payload["providers"])
            self.assertNotIn("GEMINI_API_KEY", json.dumps(payload))
        finally:
            connection.close()

    def test_chat_api_accepts_images_and_converts_them_once(self):
        import base64

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        image = "data:image/png;base64," + base64.b64encode(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg==")).decode("ascii")
        provider_response = MagicMock()
        provider_response.read.return_value = b'{"choices":[{"message":{"content":"An image."}}]}'
        provider_response.__enter__.return_value = provider_response
        try:
            with patch("app.PORT", self.server.server_port), \
                 patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}), \
                 patch("app.open_provider_request", return_value=provider_response) as opener:
                connection.request(
                    "POST", "/api/chat",
                    body=json.dumps({
                        "message": "describe", "provider": "groq", "images": [image],
                        "conversation_id": self.usage_store.create_conversation(), "request_id": "a" * 32,
                    }),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                result = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
            self.assertEqual(result["reply"], "An image.")
            user_content = json.loads(opener.call_args.args[0].data.decode())["messages"][-1]["content"]
            self.assertEqual(user_content[0], {"type": "text", "text": "describe"})
            self.assertEqual(user_content[1]["type"], "image_url")
        finally:
            connection.close()

    def _post_json(self, route, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            with patch("app.PORT", self.server.server_port):
                connection.request("POST", route, body=json.dumps(payload), headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read().decode())
        finally:
            connection.close()

    def test_chat_conversation_count_persists_and_api_returns_it(self):
        status, created = self._post_json("/api/conversations", {})
        self.assertEqual(status, 201)
        self.assertEqual(created["messages_used"], 0)
        conversation_id = created["conversation_id"]
        response_body = MagicMock()
        response_body.read.return_value = b'{"choices":[{"message":{"content":"short reply"}}]}'
        response_body.__enter__.return_value = response_body
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}), patch("app.open_provider_request", return_value=response_body):
            status, result = self._post_json("/api/chat", {
                "message": "hello", "provider": "groq", "conversation_id": conversation_id,
                "request_id": "b" * 32,
            })
        self.assertEqual(status, 200)
        self.assertEqual(result["reply"], "short reply")
        self.assertEqual(result["messages_used"], 1)
        self.assertEqual(self.usage_store.usage(conversation_id), 1)

    def test_chat_limit_cannot_be_bypassed_through_direct_api_request(self):
        conversation_id = self.usage_store.create_conversation()
        for index in range(10):
            self.usage_store.reserve_message(conversation_id, f"{index:032x}")
        with patch("app.open_provider_request") as provider:
            status, result = self._post_json("/api/chat", {
                "message": "one more", "provider": "groq", "conversation_id": conversation_id,
                "request_id": "f" * 32,
            })
        self.assertEqual(status, 429)
        self.assertEqual(result["messages_used"], 10)
        self.assertIn("10-message limit", result["error"])
        provider.assert_not_called()

    def test_failed_provider_attempt_is_counted_and_duplicate_request_is_rejected(self):
        conversation_id = self.usage_store.create_conversation()
        payload = {
            "message": "hello", "provider": "groq", "conversation_id": conversation_id,
            "request_id": "c" * 32,
        }
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}), patch("app.open_provider_request", side_effect=TimeoutError()) as provider:
            status, result = self._post_json("/api/chat", payload)
            self.assertEqual(status, 502)
            self.assertIn("timed out", result["error"])
            self.assertEqual(result["messages_used"], 1)
            status, duplicate = self._post_json("/api/chat", payload)
        self.assertEqual(status, 409)
        self.assertIn("already submitted", duplicate["error"])
        self.assertEqual(self.usage_store.usage(conversation_id), 1)
        self.assertEqual(provider.call_count, 1)

    def test_missing_provider_key_does_not_consume_allowance(self):
        conversation_id = self.usage_store.create_conversation()
        with patch.dict("os.environ", {"GROQ_API_KEY": ""}), patch("app.open_provider_request") as provider:
            status, result = self._post_json("/api/chat", {
                "message": "hello", "provider": "groq", "conversation_id": conversation_id,
                "request_id": "d" * 32,
            })
        self.assertEqual(status, 502)
        self.assertIn("No API key is configured", result["error"])
        self.assertEqual(self.usage_store.usage(conversation_id), 0)
        provider.assert_not_called()

    def test_chatbot_http_surface_does_not_expose_coding_agent_or_mcp_oauth(self):
        requests = (
            ("GET", "/api/agent/sessions", None),
            ("GET", "/api/mcp/oauth/callback?state=unused", None),
            ("POST", "/api/agent", None),
            ("POST", "/api/agent/stream", None),
            ("POST", "/api/agent/approvals", None),
            ("POST", "/api/agent/restore", None),
            ("POST", "/api/mcp/oauth/disconnect", None),
            ("DELETE", "/api/agent/sessions?id=unused", None),
        )
        for method, path, payload in requests:
            with self.subTest(method=method, path=path):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
                body = json.dumps(payload) if payload is not None else None
                headers = {"Content-Type": "application/json"} if body is not None else {}
                try:
                    connection.request(method, path, body=body, headers=headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 404)
                    result = json.loads(response.read().decode())
                    self.assertEqual(result["error"], "Not found")
                finally:
                    connection.close()


if __name__ == "__main__":
    unittest.main()
