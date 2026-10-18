import unittest

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


if __name__ == "__main__":
    unittest.main()
