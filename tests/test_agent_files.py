import json
import tempfile
import unittest
from pathlib import Path

from backend.agent import run_tool


class AgentWorkspaceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_and_search_hide_all_protected_files(self):
        protected = {
            ".env.production": "needle-protected",
            "credentials.txt": "needle-protected",
            "token.txt": "needle-protected",
            "my_private_key_backup.txt": "needle-protected",
            "certificate.pem": "needle-protected",
            "id_rsa": "needle-protected",
            "authorized_keys": "needle-protected",
            ".npmrc": "needle-protected",
            ".pypirc": "needle-protected",
            ".netrc": "needle-protected",
        }
        for name, value in protected.items():
            (self.root / name).write_text(value, encoding="utf-8")
        for directory in (".ssh", ".aws", ".azure", ".gnupg", ".kube"):
            sensitive_dir = self.root / directory
            sensitive_dir.mkdir()
            (sensitive_dir / "config").write_text("needle-protected", encoding="utf-8")
        (self.root / "visible.txt").write_text("needle-visible", encoding="utf-8")

        listed = json.loads(run_tool("list_files", {}, self.root, allow_edits=False))
        self.assertEqual(listed["files"], ["visible.txt"])

        searched = json.loads(run_tool(
            "search_files", {"query": "needle-protected"}, self.root, allow_edits=False
        ))
        self.assertEqual(searched["matches"], [])

        visible = json.loads(run_tool(
            "search_files", {"query": "needle-visible"}, self.root, allow_edits=False
        ))
        self.assertEqual([match["path"] for match in visible["matches"]], ["visible.txt"])

    def test_read_rejects_protected_files(self):
        protected = self.root / "credentials.txt"
        protected.write_text("do not read", encoding="utf-8")
        result = json.loads(run_tool(
            "read_file", {"path": "credentials.txt"}, self.root, allow_edits=False
        ))
        self.assertIn("error", result)

    def test_write_rejects_path_outside_workspace(self):
        outside = self.root.parent / (self.root.name + "-outside.txt")
        try:
            result = json.loads(run_tool(
                "write_file", {"path": "../" + outside.name, "content": "bad"},
                self.root, allow_edits=True,
            ))
            self.assertIn("error", result)
            self.assertFalse(outside.exists())
        finally:
            outside.unlink(missing_ok=True)

    def test_write_rejects_symlink_target(self):
        target = self.root / "target.txt"
        target.write_text("original", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable in this environment")
        result = json.loads(run_tool(
            "write_file", {"path": "link.txt", "content": "overwritten"},
            self.root, allow_edits=True,
        ))
        self.assertIn("error", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_edits_disabled_and_oversized_write_leave_no_file(self):
        disabled = json.loads(run_tool(
            "write_file", {"path": "disabled.txt", "content": "no"},
            self.root, allow_edits=False,
        ))
        self.assertIn("error", disabled)
        self.assertFalse((self.root / "disabled.txt").exists())

        oversized = json.loads(run_tool(
            "write_file", {"path": "oversized.txt", "content": "x" * 200_001},
            self.root, allow_edits=True,
        ))
        self.assertIn("error", oversized)
        self.assertFalse((self.root / "oversized.txt").exists())


if __name__ == "__main__":
    unittest.main()
