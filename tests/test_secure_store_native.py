import secrets
import shutil
import sys
import unittest
import uuid

from backend import secure_store


class NativeCredentialStoreIntegrationTests(unittest.TestCase):
    def exercise_native_store(self):
        name = "integration-test." + uuid.uuid4().hex
        value = secrets.token_urlsafe(32)
        try:
            if not secure_store.set_secret(name, value):
                self.skipTest("No unlocked native credential store is available in this runner.")
            self.assertEqual(secure_store.get_secret(name), value)
        finally:
            secure_store.delete_secret(name)

    @unittest.skipUnless(sys.platform == "win32", "Runs against Windows DPAPI on Windows runners")
    def test_windows_dpapi_native_store_round_trip(self):
        self.exercise_native_store()

    @unittest.skipUnless(sys.platform == "darwin", "Runs against macOS Keychain on macOS runners")
    def test_macos_keychain_native_store_round_trip(self):
        if secure_store._keyring_backend() is None:
            self.skipTest("The macOS keyring adapter is unavailable.")
        self.exercise_native_store()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Runs against Secret Service on Linux runners")
    def test_linux_secret_service_native_store_round_trip(self):
        if secure_store._keyring_backend() is None and shutil.which("secret-tool") is None:
            self.skipTest("Secret Service and secret-tool are unavailable.")
        self.exercise_native_store()


if __name__ == "__main__":
    unittest.main()
