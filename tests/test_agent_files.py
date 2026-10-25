import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import workspace_fs
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
        seen_directory_names = set()
        for directory in (".ssh", ".aws", ".azure", ".gnupg", ".kube", ".SSH", ".AWS", ".GIT"):
            # Windows filesystems commonly collapse case-only directory names.
            if directory.casefold() in seen_directory_names:
                continue
            seen_directory_names.add(directory.casefold())
            sensitive_dir = self.root / directory
            sensitive_dir.mkdir()
            (sensitive_dir / "config").write_text("needle-protected", encoding="utf-8")
        (self.root / "visible.txt").write_text("needle-visible", encoding="utf-8")

        listed = json.loads(run_tool("list_files", {}, self.root, allow_edits=False))
        self.assertIn("files", listed, listed.get("error", str(listed)))
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

    def test_read_race_error_does_not_expose_absolute_workspace_path(self):
        target = self.root / "visible.txt"
        target.write_text("visible", encoding="utf-8")
        with patch(
            "backend.agent.workspace_fs.read_bytes",
            side_effect=FileNotFoundError(2, "No such file or directory", str(target)),
        ):
            result = json.loads(run_tool(
                "read_file", {"path": "visible.txt"}, self.root, allow_edits=False,
            ))

        self.assertIn("changed or is no longer available", result["error"])
        self.assertNotIn(str(self.root), result["error"])

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

    def test_hard_links_to_protected_data_are_not_read_or_listed(self):
        protected = self.root / "credentials.txt"
        protected.write_text("secret material", encoding="utf-8")
        alias = self.root / "ordinary.txt"
        try:
            alias.hardlink_to(protected)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links are unavailable in this environment")
        result = json.loads(run_tool("read_file", {"path": "ordinary.txt"}, self.root, allow_edits=False))
        listing = json.loads(run_tool("list_files", {}, self.root, allow_edits=False))
        self.assertIn("error", result)
        self.assertNotIn("ordinary.txt", listing["files"])

    def test_task_file_read_budget_is_enforced(self):
        (self.root / "sample.txt").write_text("a" * 20, encoding="utf-8")
        budget = {"file_bytes_read": 50_000_000 - 10}
        result = json.loads(run_tool(
            "read_file", {"path": "sample.txt"}, self.root, allow_edits=False,
            task_budget=budget,
        ))
        self.assertIn("cumulative file-read limit", result["error"])

    @unittest.skipUnless(workspace_fs._supports_dirfd(), "Anchored dirfd operations are POSIX-only")
    def test_dirfd_capability_survives_os_open_wrapper(self):
        with patch("backend.workspace_fs.os.open", wraps=workspace_fs.os.open):
            self.assertTrue(workspace_fs._supports_dirfd())

    @unittest.skipUnless(workspace_fs._supports_dirfd(), "Anchored dirfd operations are POSIX-only")
    def test_read_stays_on_open_directory_if_path_is_swapped_to_symlink(self):
        folder = self.root / "folder"
        outside = self.root.parent / (self.root.name + "-outside")
        folder.mkdir()
        outside.mkdir()
        (folder / "data.txt").write_text("inside", encoding="utf-8")
        (outside / "data.txt").write_text("outside", encoding="utf-8")
        original_open = workspace_fs.os.open
        swapped = False

        def swap_after_open(path, *args, **kwargs):
            nonlocal swapped
            fd = original_open(path, *args, **kwargs)
            if path == "folder" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                folder.rename(self.root / "folder-held")
                folder.symlink_to(outside, target_is_directory=True)
            return fd

        try:
            with patch("backend.workspace_fs.os.open", side_effect=swap_after_open):
                result = workspace_fs.read_bytes(self.root, "folder/data.txt", 100)
            self.assertEqual(result, b"inside")
            self.assertTrue(swapped)
        finally:
            folder.unlink(missing_ok=True)
            (self.root / "folder-held").rename(folder)
            import shutil
            shutil.rmtree(outside)

    @unittest.skipUnless(workspace_fs._supports_dirfd(), "Anchored dirfd operations are POSIX-only")
    def test_atomic_write_stays_on_open_directory_if_path_is_swapped_to_symlink(self):
        folder = self.root / "folder"
        outside = self.root.parent / (self.root.name + "-outside")
        folder.mkdir()
        outside.mkdir()
        (folder / "data.txt").write_text("inside-old", encoding="utf-8")
        (outside / "data.txt").write_text("outside-old", encoding="utf-8")
        original_open = workspace_fs.os.open
        swapped = False

        def swap_after_open(path, *args, **kwargs):
            nonlocal swapped
            fd = original_open(path, *args, **kwargs)
            if path == "folder" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                folder.rename(self.root / "folder-held")
                folder.symlink_to(outside, target_is_directory=True)
            return fd

        try:
            with patch("backend.workspace_fs.os.open", side_effect=swap_after_open):
                workspace_fs.atomic_write(self.root, "folder/data.txt", b"inside-new")
            self.assertEqual((self.root / "folder-held" / "data.txt").read_bytes(), b"inside-new")
            self.assertEqual((outside / "data.txt").read_bytes(), b"outside-old")
        finally:
            folder.unlink(missing_ok=True)
            (self.root / "folder-held").rename(folder)
            import shutil
            shutil.rmtree(outside)

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
