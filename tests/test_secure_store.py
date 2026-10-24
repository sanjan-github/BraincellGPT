import tempfile
import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import secure_store


class SecureStoreTests(unittest.TestCase):
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
