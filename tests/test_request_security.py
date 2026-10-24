import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app


class RequestSecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def test_untrusted_host_is_rejected_before_provider_is_called(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps({"message": "hello", "provider": "groq"})
        try:
            with patch("app.generate_reply") as provider:
                connection.request(
                    "POST",
                    "/api/chat",
                    body=body,
                    headers={
                        "Host": "attacker.example:8000",
                        "Content-Type": "application/json",
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 403)
                self.assertIn("trusted loopback", payload["error"])
                provider.assert_not_called()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
