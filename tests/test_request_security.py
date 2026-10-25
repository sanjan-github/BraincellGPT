import http.client
import json
import threading
import unittest
from typing import cast
from unittest.mock import MagicMock, patch

import app


class RequestSecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server = app.BoundedThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

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
                    body=json.dumps({"message": "describe", "provider": "groq", "images": [image]}),
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
