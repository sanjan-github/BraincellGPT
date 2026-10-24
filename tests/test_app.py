import email.message
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import app


class ProviderConfigurationTests(unittest.TestCase):
    def test_cloud_provider_registry_has_required_fields(self):
        self.assertIn("groq", app.OPENAI_COMPATIBLE_PROVIDERS)
        self.assertIn("deepseek", app.OPENAI_COMPATIBLE_PROVIDERS)
        self.assertIn("gemini", app.OPENAI_COMPATIBLE_PROVIDERS)
        for name, config in app.OPENAI_COMPATIBLE_PROVIDERS.items():
            with self.subTest(provider=name):
                self.assertTrue(config["label"])
                self.assertTrue(config["base_url"].startswith("https://"))
                self.assertTrue(config["model"])

    def test_local_provider_urls_are_loopback_only(self):
        self.assertEqual(app.validate_provider_url("ollama", "http://localhost:11434/v1"), "http://localhost:11434/v1")
        self.assertEqual(app.validate_provider_url("lmstudio", "http://127.0.0.1:1234/v1"), "http://127.0.0.1:1234/v1")
        with self.assertRaises(RuntimeError):
            app.validate_provider_url("ollama", "http://192.168.1.5:11434/v1")

    def test_custom_provider_requires_https(self):
        with self.assertRaises(RuntimeError):
            app.validate_provider_url("custom", "http://example.com/v1")

    def test_custom_provider_rejects_invalid_ports_before_dns(self):
        for url in ("https://example.com:0/v1", "https://example.com:not-a-port/v1", "https://[broken/v1"):
            with self.subTest(url=url), patch("app.resolve_public_addresses") as resolver:
                with self.assertRaises(RuntimeError):
                    app.validate_provider_url("custom", url)
                resolver.assert_not_called()

    def test_provider_settings_reject_url_credentials_and_query(self):
        with self.assertRaises(RuntimeError):
            app.validate_provider_url("custom", "https://user:pass@example.com/v1")
        with self.assertRaises(RuntimeError):
            app.validate_provider_url("custom", "https://example.com/v1?token=secret")

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "supported provider"):
            app.generate_reply("hello", [], "normal", provider="unknown-provider")

    def test_custom_url_rejects_local_and_private_addresses(self):
        with patch("app.resolve_public_addresses", side_effect=ValueError("Custom provider URL cannot resolve to a private or local network address.")):
            with self.assertRaisesRegex(RuntimeError, "private or local"):
                app.validate_provider_url("custom", "https://example.com/v1")

    def test_custom_provider_request_uses_the_addresses_that_were_validated(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response
        pinned = ((2, 1, 6, ("93.184.216.34", 443)),)

        with patch("app.resolve_public_addresses", return_value=pinned), patch("app.open_provider_request", return_value=response) as opener:
            result = app.generate_reply(
                "hello", [], "normal", provider="custom", user_api_key="custom-key",
                base_url="https://provider.example/v1", model_override="custom-model",
            )

        self.assertEqual(result, "reply")
        self.assertEqual(opener.call_args.kwargs["pinned_addresses"], pinned)

    def test_custom_provider_rejects_dns_failure_before_request(self):
        with patch("app.resolve_public_addresses", side_effect=ValueError("Could not resolve the custom provider host.")), patch("app.open_provider_request") as opener:
            with self.assertRaisesRegex(RuntimeError, "Could not resolve"):
                app.generate_reply(
                    "hello", [], "normal", provider="custom", user_api_key="custom-key",
                    base_url="https://provider.example/v1", model_override="custom-model",
                )
        opener.assert_not_called()

    def test_history_rejects_non_string_content(self):
        history = [{"role": "user", "content": {"unexpected": "object"}}, {"role": "user", "content": "valid"}]
        self.assertEqual(app.trim_history(history), [{"role": "user", "content": "valid"}])

    def test_invalid_port_fails_fast(self):
        for port in ("abc", "0", "65536", "-1"):
            with self.subTest(port=port):
                env = os.environ.copy()
                env["PORT"] = port
                result = subprocess.run(
                    [sys.executable, "-c", "import app"],
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("PORT must be an integer between 1 and 65535", result.stderr)


class PersonalityPromptTests(unittest.TestCase):
    def test_prompt_makes_chaos_the_core_identity(self):
        prompt = app.build_system_prompt("normal")

        self.assertIn("stress relief", prompt.lower())
        self.assertIn("chaotic", prompt.lower())
        self.assertIn("actual message", prompt.lower())
        self.assertIn("context", prompt.lower())

    def test_prompt_discourages_generic_assistant_behaviour(self):
        prompt = app.build_system_prompt("sarcastic")

        self.assertIn("do not sound like a generic assistant", prompt.lower())
        self.assertIn("do not overexplain jokes", prompt.lower())
        self.assertIn("sarcastic", prompt.lower())


class ApiReliabilityTests(unittest.TestCase):
    def test_chat_sends_bounded_image_as_openai_compatible_content(self):
        import base64
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response
        image = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode()
        with patch("app.open_provider_request", return_value=response) as opener:
            app.generate_reply("describe", [], "normal", provider="groq", user_api_key="secret-key", images=[image])
        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["messages"][-1]["content"][0], {"type": "text", "text": "describe"})
        self.assertEqual(payload["messages"][-1]["content"][1]["type"], "image_url")

    def test_openai_compatible_request_uses_provider_defaults_and_auth_header(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response

        with patch.dict(os.environ, {"GROQ_MODEL": ""}), patch("app.open_provider_request", return_value=response) as urlopen:
            self.assertEqual(app.generate_reply("hello", [], "normal", provider="groq", user_api_key="secret-key"), "reply")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        self.assertEqual(payload["model"], "llama-3.3-70b-versatile")
        self.assertNotIn("secret-key", json.dumps(payload))

    def test_gemini_uses_openai_compatible_endpoint_and_provider_defaults(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response

        with patch.dict(os.environ, {"GEMINI_MODEL": ""}), patch("app.open_provider_request", return_value=response) as opener:
            self.assertEqual(app.generate_reply("hello", [], "normal", provider="gemini", user_api_key="gemini-key"), "reply")

        request = opener.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer gemini-key")
        self.assertEqual(payload["model"], "gemini-3.8-flash")

    def test_blank_environment_model_uses_provider_default(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response

        with patch.dict(os.environ, {"GROQ_MODEL": ""}), patch("app.open_provider_request", return_value=response) as urlopen:
            app.generate_reply("hello", [], "normal", provider="groq", user_api_key="secret-key")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["model"], "llama-3.3-70b-versatile")

    def test_invalid_provider_json_becomes_readable_runtime_error(self):
        response = MagicMock()
        response.read.return_value = b"not-json"
        response.__enter__.return_value = response

        with patch("app.open_provider_request", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON response"):
                app.generate_reply("hello", [], "normal", provider="groq", user_api_key="secret-key")

    def test_provider_http_error_redacts_api_key(self):
        error_body = io.BytesIO(b'{"error":{"message":"bad secret-key"}}')
        error = urllib.error.HTTPError(
            "https://api.groq.com/openai/v1/chat/completions",
            403,
            "Forbidden",
            email.message.Message(),
            error_body,
        )

        with patch("app.open_provider_request", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Groq returned HTTP 403") as raised:
                app.generate_reply("hello", [], "normal", provider="groq", user_api_key="secret-key")

        self.assertNotIn("secret-key", str(raised.exception))
        self.assertTrue(error_body.closed)


class ConversationMemoryTests(unittest.TestCase):
    def test_trim_history_keeps_recent_context_within_item_and_total_limits(self):
        history = [
            {"role": "user", "content": "old context " + ("x" * 5000)},
            {"role": "assistant", "content": "old reply " + ("y" * 5000)},
            {"role": "user", "content": "latest context"},
        ]

        trimmed = app.trim_history(history, max_items=16, max_chars=100, max_item_chars=60)

        self.assertLessEqual(sum(len(item["content"]) for item in trimmed), 100)
        self.assertLessEqual(len(trimmed), 16)
        self.assertEqual(trimmed[-1]["content"], "latest context")

    def test_generate_reply_sends_bounded_history_to_provider(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response
        history = [{"role": "user", "content": "message " + ("z" * 5000)} for _ in range(16)]

        with patch("app.open_provider_request", return_value=response) as urlopen:
            app.generate_reply("latest", history, "normal", provider="groq", user_api_key="secret-key")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        history_messages = payload["messages"][1:-1]
        self.assertLessEqual(sum(len(item["content"]) for item in history_messages), app.MAX_HISTORY_CHARS)


class ProviderResponseTests(unittest.TestCase):
    def test_extracts_openai_compatible_text_response(self):
        data = {"choices": [{"message": {"content": "reply"}}]}

        self.assertEqual(app.extract_provider_reply(data, "openai", "Groq"), "reply")

    def test_extracts_anthropic_text_response(self):
        data = {"content": [{"type": "text", "text": "reply"}]}

        self.assertEqual(app.extract_provider_reply(data, "anthropic", "Anthropic"), "reply")

    def test_rejects_non_object_provider_response(self):
        with self.assertRaisesRegex(RuntimeError, "invalid response"):
            app.extract_provider_reply([], "openai", "Groq")

    def test_rejects_empty_provider_response(self):
        with self.assertRaisesRegex(RuntimeError, "empty response"):
            app.extract_provider_reply({"choices": []}, "openai", "Groq")


class ModelCompatibilityTests(unittest.TestCase):
    def test_anthropic_chat_sends_native_image_blocks(self):
        import base64
        response = MagicMock()
        response.read.return_value = b'{"content":[{"type":"text","text":"reply"}]}'
        response.__enter__.return_value = response
        image = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode()
        with patch("app.open_provider_request", return_value=response) as opener:
            app.generate_reply("describe", [], "normal", provider="anthropic", user_api_key="secret-key", images=[image])
        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        content = payload["messages"][-1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "describe"})
        self.assertEqual(content[1]["source"]["media_type"], "image/png")

    def test_anthropic_uses_native_headers_and_separate_system_prompt(self):
        response = MagicMock()
        response.read.return_value = b'{"content":[{"type":"text","text":"reply"}]}'
        response.__enter__.return_value = response

        with patch("app.open_provider_request", return_value=response) as urlopen:
            self.assertEqual(app.generate_reply("hello", [], "normal", provider="anthropic", user_api_key="secret-key"), "reply")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "secret-key")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertNotIn("Authorization", dict(request.header_items()))
        self.assertIn("system", payload)
        self.assertNotIn("system", {message["role"] for message in payload["messages"]})

    def test_ollama_uses_local_openai_compatible_endpoint_without_auth(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response

        with patch("app.open_provider_request", return_value=response) as urlopen:
            self.assertEqual(app.generate_reply("hello", [], "normal", provider="ollama"), "reply")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://localhost:11434/v1/chat/completions")
        self.assertNotIn("Authorization", dict(request.header_items()))
        self.assertEqual(payload["model"], "llama3.2")


class LocalRequestBoundaryTests(unittest.TestCase):
    def make_handler(self, host="localhost:8000", origin=None, fetch_site=None):
        handler = app.Handler.__new__(app.Handler)
        handler.headers = email.message.Message()
        handler.headers["Host"] = host
        if origin is not None:
            handler.headers["Origin"] = origin
        if fetch_site is not None:
            handler.headers["Sec-Fetch-Site"] = fetch_site
        return handler

    def test_allows_loopback_host_without_origin_for_local_clients(self):
        self.assertTrue(self.make_handler(host="localhost:8000").is_trusted_local_request())
        self.assertTrue(self.make_handler(host="127.0.0.1:8000").is_trusted_local_request())

    def test_allows_matching_loopback_origin(self):
        handler = self.make_handler(
            host="localhost:8000", origin="http://localhost:8000", fetch_site="same-origin"
        )
        self.assertTrue(handler.is_trusted_local_request())

    def test_rejects_untrusted_host_and_cross_origin(self):
        for handler in (
            self.make_handler(host="evil.example:8000"),
            self.make_handler(host="localhost:8000", origin="https://localhost:8000"),
            self.make_handler(host="localhost:8000", origin="http://evil.example:8000"),
            self.make_handler(host="localhost:8000", origin="http://localhost:9000"),
            self.make_handler(host="localhost:8000", fetch_site="cross-site"),
        ):
            with self.subTest(headers=dict(handler.headers)):
                self.assertFalse(handler.is_trusted_local_request())

    def test_oauth_callback_allows_cross_site_redirect_but_keeps_host_guard(self):
        handler = self.make_handler(fetch_site="cross-site")
        self.assertFalse(handler.is_trusted_local_request())
        self.assertTrue(handler.is_trusted_local_request(allow_cross_site_redirect=True))
        handler = self.make_handler(host="evil.example:8000", fetch_site="cross-site")
        self.assertFalse(handler.is_trusted_local_request(allow_cross_site_redirect=True))

    def test_rejects_malformed_host(self):
        self.assertFalse(self.make_handler(host="localhost:not-a-port").is_trusted_local_request())

    def test_chat_and_agent_share_one_provider_concurrency_limiter(self):
        from backend import agent, provider_http

        self.assertIs(app._provider_slots, provider_http.PROVIDER_SLOTS)
        self.assertIs(agent._PROVIDER_SLOTS, provider_http.PROVIDER_SLOTS)


if __name__ == "__main__":
    unittest.main()
