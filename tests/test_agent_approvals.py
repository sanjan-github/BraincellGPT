import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent import run_agent
from backend.agent_approvals import ApprovalRegistry


class ApprovalRegistryTests(unittest.TestCase):
    def test_resolution_is_session_scoped_and_one_shot(self):
        registry = ApprovalRegistry(timeout=2)
        emitted = threading.Event()
        event_holder = {}
        result_holder = {}

        def run_request():
            result_holder["approved"] = registry.request(
                "a" * 32, {"type": "approval_required", "tool": "write_file"},
                lambda event: (event_holder.update(event), emitted.set()),
            )

        worker = threading.Thread(target=run_request)
        worker.start()
        self.assertTrue(emitted.wait(1))
        approval_id = event_holder["approval_id"]
        self.assertFalse(registry.resolve(approval_id, "b" * 32, True))
        self.assertTrue(registry.resolve(approval_id, "a" * 32, True))
        self.assertFalse(registry.resolve(approval_id, "a" * 32, False))
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result_holder["approved"])

    def test_pending_queue_is_bounded(self):
        registry = ApprovalRegistry(max_pending=1, timeout=2)
        started = threading.Event()
        thread = threading.Thread(target=lambda: registry.request("s" * 32, {}, lambda _event: started.set()))
        thread.start()
        self.assertTrue(started.wait(1))
        with self.assertRaisesRegex(ValueError, "queue is full"):
            registry.request("s" * 32, {}, lambda _event: None)
        thread.join(3)
        self.assertFalse(thread.is_alive())


class AgentToolApprovalTests(unittest.TestCase):
    @staticmethod
    def _tool_response():
        response = MagicMock()
        response.read.return_value = json.dumps({"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "write-1", "type": "function", "function": {
                "name": "write_file", "arguments": '{"path":"approved.txt","content":"approved"}',
            }}],
        }}]}).encode()
        response.__enter__.return_value = response
        return response

    @staticmethod
    def _final_response():
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"Finished."}}]}'
        response.__enter__.return_value = response
        return response

    def test_approved_write_is_exposed_without_request_wide_edit_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []

            def approve(event):
                events.append(event)
                return True

            with patch("backend.agent.open_provider_request", side_effect=[self._tool_response(), self._final_response()]):
                result = run_agent(
                    "write a file", [], root, "groq", "api-key", "", "", {},
                    mode="code", approval_callback=approve,
                )

            self.assertEqual(result["reply"], "Finished.")
            self.assertEqual((root / "approved.txt").read_text(encoding="utf-8"), "approved")
            approval = events[0]
            self.assertEqual(approval["tool"], "write_file")
            self.assertEqual(approval["arguments"]["path"], "approved.txt")

    def test_rejected_write_does_not_change_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("backend.agent.open_provider_request", side_effect=[self._tool_response(), self._final_response()]):
                result = run_agent(
                    "write a file", [], root, "groq", "api-key", "", "", {},
                    mode="code", approval_callback=lambda _event: False,
                )
            self.assertEqual(result["reply"], "Finished.")
            self.assertFalse((root / "approved.txt").exists())


if __name__ == "__main__":
    unittest.main()
