import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent import restore_checkpoint, restore_checkpoint_group, run_tool
from backend.agent_state import AgentStore


class AgentSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "workspace"
        self.root.mkdir()
        self.store = AgentStore(Path(self.temp_dir.name) / "state" / "agent.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_session(self):
        return self.store.create_session(
            str(self.root.resolve()), "custom", "https://api.example/v1", "model-x", "code", "A persistent task"
        )

    def test_sessions_and_turns_persist_without_provider_credentials(self):
        session_id = self.create_session()
        self.store.add_turn(session_id, "user", "Inspect the project")
        self.store.add_turn(session_id, "assistant", "I will start with the source tree.", {"steps": 1})

        reloaded = AgentStore(self.store.path)
        session = reloaded.get_session(session_id)
        assert session is not None
        self.assertEqual(session["title"], "A persistent task")
        self.assertEqual(session["base_url"], "https://api.example/v1")
        self.assertEqual(reloaded.history(session_id), [
            {"role": "user", "content": "Inspect the project"},
            {"role": "assistant", "content": "I will start with the source tree."},
        ])
        self.assertNotIn("api_key", session)

    def test_session_delete_cascades_turns_and_checkpoints(self):
        session_id = self.create_session()
        self.store.add_turn(session_id, "user", "delete me")
        self.assertTrue(self.store.delete_session(session_id, str(self.root.resolve())))
        self.assertIsNone(self.store.get_session(session_id))
        self.assertEqual(self.store.get_turns(session_id), [])


class AgentCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.store = AgentStore(self.base / "state" / "agent.sqlite3")
        self.session_id = self.store.create_session(str(self.root.resolve()), "groq", "", "model", "code", "Checkpoint task")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_restore_checkpoint_reverts_file_and_creates_redo_checkpoint(self):
        target = self.root / "source.py"
        target.write_text("before = True\n", encoding="utf-8")
        with patch("backend.agent.get_agent_store", return_value=self.store):
            result = run_tool(
                "write_file", {"path": "source.py", "content": "after = True\n"},
                self.root, allow_edits=True, session_id=self.session_id,
            )
            checkpoint_id = json.loads(result)["checkpoint_id"]
            restored = restore_checkpoint(self.session_id, checkpoint_id, self.root)

        self.assertEqual(target.read_text(encoding="utf-8"), "before = True\n")
        self.assertTrue(restored["redo_checkpoint_id"])
        self.assertEqual(len(self.store.list_checkpoints(self.session_id)), 2)

    def test_restore_removes_a_file_created_by_agent(self):
        with patch("backend.agent.get_agent_store", return_value=self.store):
            result = run_tool(
                "write_file", {"path": "new.txt", "content": "new"},
                self.root, allow_edits=True, session_id=self.session_id,
            )
            checkpoint_id = json.loads(result)["checkpoint_id"]
            restore_checkpoint(self.session_id, checkpoint_id, self.root)

        self.assertFalse((self.root / "new.txt").exists())

    def test_restore_checkpoint_group_reverts_all_files_to_run_start(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text("first before\n", encoding="utf-8")
        second.write_text("second before\n", encoding="utf-8")
        group_id = "a" * 32
        with patch("backend.agent.get_agent_store", return_value=self.store):
            run_tool("write_file", {"path": "first.txt", "content": "first changed\n"}, self.root, True, session_id=self.session_id, checkpoint_group_id=group_id)
            run_tool("write_file", {"path": "first.txt", "content": "first changed again\n"}, self.root, True, session_id=self.session_id, checkpoint_group_id=group_id)
            run_tool("write_file", {"path": "second.txt", "content": "second changed\n"}, self.root, True, session_id=self.session_id, checkpoint_group_id=group_id)

            groups = self.store.list_checkpoint_groups(self.session_id)
            self.assertEqual(groups[0]["id"], group_id)
            self.assertEqual(set(groups[0]["files"]), {"first.txt", "second.txt"})
            self.assertEqual(len(groups[0]["checkpoints"]), 3)

            restored = restore_checkpoint_group(self.session_id, group_id, self.root)

        self.assertEqual(first.read_text(encoding="utf-8"), "first before\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "second before\n")
        self.assertEqual(set(restored["restored"]), {"first.txt", "second.txt"})
        self.assertTrue(restored["redo_group_id"])

    def test_group_restore_rolls_back_workspace_when_later_write_fails(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text("first before\n", encoding="utf-8")
        second.write_text("second before\n", encoding="utf-8")
        group_id = "b" * 32
        with patch("backend.agent.get_agent_store", return_value=self.store):
            for path, content in (("first.txt", "first changed\n"), ("second.txt", "second changed\n")):
                run_tool("write_file", {"path": path, "content": content}, self.root, True,
                         session_id=self.session_id, checkpoint_group_id=group_id)
            baseline_count = len(self.store.list_checkpoints(self.session_id))
            original_write = __import__("backend.agent", fromlist=["_write_text_atomically"])._write_text_atomically
            write_count = 0

            def fail_second_restore(path, content, root=None):
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise OSError("injected restore failure")
                return original_write(path, content, root)

            with patch("backend.agent._write_text_atomically", side_effect=fail_second_restore):
                with self.assertRaisesRegex(OSError, "injected restore failure"):
                    restore_checkpoint_group(self.session_id, group_id, self.root)

        self.assertEqual(first.read_text(encoding="utf-8"), "first changed\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "second changed\n")
        self.assertEqual(len(self.store.list_checkpoints(self.session_id)), baseline_count)


if __name__ == "__main__":
    unittest.main()
