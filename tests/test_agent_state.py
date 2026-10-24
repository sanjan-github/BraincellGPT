import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent import restore_checkpoint, run_tool
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


if __name__ == "__main__":
    unittest.main()
