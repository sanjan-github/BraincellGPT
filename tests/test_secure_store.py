import tempfile
import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import secure_store


class SecureStoreTests(unittest.TestCase):
    def test_native_keyring_backends_are_selected_for_supported_platforms(self):
        macos_backend = type("MacOSKeyring", (), {})
        macos_backend.__module__ = "keyring.backends.macOS"
        linux_backend = type("SecretServiceKeyring", (), {})
        linux_backend.__module__ = "keyring.backends.SecretService"
        plain_backend = type("PlaintextKeyring", (), {})
        plain_backend.__module__ = "keyring.backends.fail"
        for platform, backend, expected in (
            ("darwin", macos_backend(), True),
            ("linux", linux_backend(), True),
            ("darwin", plain_backend(), False),
            ("linux", plain_backend(), False),
        ):
            fake_keyring = MagicMock()
            fake_keyring.get_keyring.return_value = backend
            with self.subTest(platform=platform, backend=type(backend).__module__), \
                 patch("backend.secure_store.sys.platform", platform), \
                 patch.dict("sys.modules", {"keyring": fake_keyring}):
                selected = secure_store._keyring_backend()
                self.assertEqual(selected is not None, expected)
                if expected:
                    self.assertIs(selected, fake_keyring)

    def test_native_keyring_secret_lifecycle_uses_hashed_account(self):
        fake_keyring = MagicMock()
        fake_keyring.get_password.return_value = "protected-token"
        with patch("backend.secure_store.sys.platform", "darwin"), \
             patch("backend.secure_store._keyring_backend", return_value=fake_keyring):
            self.assertTrue(secure_store.set_secret("oauth-token", "protected-token"))
            self.assertEqual(secure_store.get_secret("oauth-token"), "protected-token")
            self.assertTrue(secure_store.delete_secret("oauth-token"))
        account = secure_store._account_name("oauth-token")
        fake_keyring.set_password.assert_called_once_with(secure_store.SERVICE_NAME, account, "protected-token")
        fake_keyring.get_password.assert_called_once_with(secure_store.SERVICE_NAME, account)
        fake_keyring.delete_password.assert_called_once_with(secure_store.SERVICE_NAME, account)

    def test_native_keyring_errors_do_not_fall_back_to_plaintext(self):
        fake_keyring = MagicMock()
        fake_keyring.set_password.side_effect = RuntimeError("Keychain is locked")
        fake_keyring.get_password.side_effect = RuntimeError("Keychain is locked")
        fake_keyring.delete_password.side_effect = RuntimeError("Keychain is locked")
        with patch("backend.secure_store.sys.platform", "darwin"), \
             patch("backend.secure_store._keyring_backend", return_value=fake_keyring):
            self.assertFalse(secure_store.set_secret("oauth-token", "protected-token"))
            self.assertIsNone(secure_store.get_secret("oauth-token"))
            self.assertFalse(secure_store.delete_secret("oauth-token"))

    def test_account_names_are_hashed_before_os_store_calls(self):
        account = secure_store._account_name("mcp.oauth.token.https://mcp.example.test")
        self.assertRegex(account, r"^[0-9a-f]{64}$")
        self.assertNotIn("mcp.example", account)

    def test_linux_secret_tool_receives_secret_on_stdin_not_argv(self):
        result = MagicMock(returncode=0, stdout=b"stored-value\n")
        with patch("backend.secure_store.sys.platform", "linux"), \
             patch("backend.secure_store._keyring_backend", return_value=None), \
             patch("backend.secure_store.shutil.which", return_value="secret-tool"), \
             patch("backend.secure_store.subprocess.run", return_value=result) as run:
            self.assertTrue(secure_store.set_secret("oauth-token", "top-secret"))
            result.stdout = b"top-secret\n"
            self.assertEqual(secure_store.get_secret("oauth-token"), "top-secret")
        store_call = run.call_args_list[0]
        self.assertNotIn("top-secret", store_call.args[0])
        self.assertEqual(store_call.kwargs["input"], b"top-secret")

    def test_windows_credential_file_contains_only_protected_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            credential_path = Path(directory) / "token.bin"
            with patch("backend.secure_store._windows_credential_path", return_value=credential_path), \
                 patch("backend.secure_store._dpapi_protect", side_effect=base64.b64encode), \
                 patch("backend.secure_store._dpapi_unprotect", side_effect=base64.b64decode):
                self.assertTrue(secure_store._windows_set("oauth-token", "top-secret"))
                self.assertNotIn(b"top-secret", credential_path.read_bytes())
                self.assertEqual(secure_store._windows_get("oauth-token"), "top-secret")

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI is available only on Windows")
    def test_real_dpapi_round_trip_persists_ciphertext_only(self):
        with tempfile.TemporaryDirectory() as directory:
            credential_path = Path(directory) / "token.bin"
            with patch("backend.secure_store._windows_credential_path", return_value=credential_path):
                self.assertTrue(secure_store._windows_set("oauth-token", "top-secret"))
                ciphertext = credential_path.read_bytes()
                self.assertNotIn(b"top-secret", ciphertext)
                self.assertEqual(secure_store._windows_get("oauth-token"), "top-secret")

    def test_returns_process_only_fallback_when_native_store_is_unavailable(self):
        with patch("backend.secure_store.sys.platform", "darwin"), patch("backend.secure_store._keyring_backend", return_value=None):
            self.assertFalse(secure_store.set_secret("oauth-token", "top-secret"))
            self.assertIsNone(secure_store.get_secret("oauth-token"))


if __name__ == "__main__":
    unittest.main()
