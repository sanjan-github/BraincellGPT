import io
import json
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import app


class ProviderConfigurationTests(unittest.TestCase):
    def test_cloud_provider_registry_has_required_fields(self):
        self.assertIn("groq", app.OPENAI_COMPATIBLE_PROVIDERS)
        self.assertIn("deepseek", app.OPENAI_COMPATIBLE_PROVIDERS)
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

    def test_provider_settings_reject_url_credentials_and_query(self):
        with self.assertRaises(RuntimeError):
            app.validate_provider_url("custom", "https://user:pass@example.com/v1")
        with self.assertRaises(RuntimeError):
            app.validate_provider_url("custom", "https://example.com/v1?token=secret")

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "supported provider"):
            app.generate_reply("hello", [], "normal", provider="unknown-provider")


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
    def test_openai_compatible_request_uses_provider_defaults_and_auth_header(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response

        with patch("app.urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(app.generate_reply("hello", [], "normal", provider="groq", user_api_key="secret-key"), "reply")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        self.assertEqual(payload["model"], "llama-3.3-70b-versatile")
        self.assertNotIn("secret-key", json.dumps(payload))

    def test_blank_environment_model_uses_provider_default(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"reply"}}]}'
        response.__enter__.return_value = response

        with patch.dict(os.environ, {"GROQ_MODEL": ""}), patch("app.urllib.request.urlopen", return_value=response) as urlopen:
            app.generate_reply("hello", [], "normal", provider="groq", user_api_key="secret-key")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["model"], "llama-3.3-70b-versatile")

    def test_invalid_provider_json_becomes_readable_runtime_error(self):
        response = MagicMock()
        response.read.return_value = b"not-json"
        response.__enter__.return_value = response

        with patch("app.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON response"):
                app.generate_reply("hello", [], "normal", provider="groq", user_api_key="secret-key")

    def test_provider_http_error_redacts_api_key(self):
        error = urllib.error.HTTPError(
            "https://api.groq.com/openai/v1/chat/completions",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"error":{"message":"bad secret-key"}}'),
        )

        with patch("app.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Groq returned HTTP 403") as raised:
                app.generate_reply("hello", [], "normal", provider="groq", user_api_key="secret-key")

        self.assertNotIn("secret-key", str(raised.exception))


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

        with patch("app.urllib.request.urlopen", return_value=response) as urlopen:
            app.generate_reply("latest", history, "normal", provider="groq", user_api_key="secret-key")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        history_messages = payload["messages"][1:-1]
        self.assertLessEqual(sum(len(item["content"]) for item in history_messages), app.MAX_HISTORY_CHARS)


if __name__ == "__main__":
    unittest.main()
