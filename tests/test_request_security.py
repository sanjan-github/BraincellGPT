import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app
from backend.agent_state import AgentStore
from backend.agent_approvals import ApprovalRegistry


class RequestSecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def test_cross_origin_is_rejected_before_provider_is_called(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps({"message": "hello", "provider": "groq"})
        try:
            with patch("app.PORT", self.server.server_port), patch("app.generate_reply") as provider:
                connection.request(
                    "POST",
                    "/api/chat",
                    body=body,
                    headers={
                        "Host": f"127.0.0.1:{self.server.server_port}",
                        "Origin": "http://127.0.0.1:1",
                        "Sec-Fetch-Site": "cross-site",
                        "Content-Type": "application/json",
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 403)
                self.assertIn("trusted loopback", payload["error"])
                provider.assert_not_called()
        finally:
            connection.close()

    def test_agent_endpoint_passes_mode_and_command_approval_to_backend(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps({
            "task": "inspect the project",
            "provider": "groq",
            "mode": "debug",
            "allow_edits": True,
            "allow_commands": True,
        })
        try:
            with tempfile.TemporaryDirectory() as directory:
                store = AgentStore(Path(directory) / "agent.sqlite3")
                with patch("app.PORT", self.server.server_port), patch("app.get_agent_store", return_value=store), patch(
                    "app.run_agent", return_value={"reply": "ready", "mode": "debug", "steps": 1}
                ) as agent_call:
                    connection.request("POST", "/api/agent", body=body, headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(payload["reply"], "ready")
                    self.assertTrue(agent_call.call_args.kwargs["allow_edits"])
                    self.assertTrue(agent_call.call_args.kwargs["allow_commands"])
                    self.assertEqual(agent_call.call_args.kwargs["mode"], "debug")
                    session_id = payload["session_id"]

                    connection.request("GET", "/api/agent/sessions", headers={"Host": f"127.0.0.1:{self.server.server_port}"})
                    sessions_response = connection.getresponse()
                    sessions = json.loads(sessions_response.read().decode("utf-8"))["sessions"]
                    self.assertEqual(len(sessions), 1)
                    self.assertEqual(sessions[0]["id"], session_id)

                    connection.request("GET", f"/api/agent/sessions?id={session_id}", headers={"Host": f"127.0.0.1:{self.server.server_port}"})
                    detail_response = connection.getresponse()
                    detail = json.loads(detail_response.read().decode("utf-8"))
                    self.assertEqual(len(detail["turns"]), 2)

                    second_body = json.dumps({"task": "continue task", "session_id": session_id})
                    connection.request("POST", "/api/agent", body=second_body, headers={"Content-Type": "application/json"})
                    second_response = connection.getresponse()
                    self.assertEqual(second_response.status, 200)
                    self.assertEqual(
                        agent_call.call_args_list[-1].args[1],
                        [
                            {"role": "user", "content": "inspect the project"},
                            {"role": "assistant", "content": "ready"},
                        ],
                    )

                    connection.request("DELETE", f"/api/agent/sessions?id={session_id}", headers={"Host": f"127.0.0.1:{self.server.server_port}"})
                    delete_response = connection.getresponse()
                    self.assertEqual(delete_response.status, 200)
        finally:
            connection.close()

    def test_agent_stream_endpoint_emits_events_and_persists_final_result(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps({"task": "stream this", "provider": "groq"})
        try:
            with tempfile.TemporaryDirectory() as directory:
                store = AgentStore(Path(directory) / "agent.sqlite3")
                def fake_agent(*_args, **kwargs):
                    kwargs["event_callback"]({"type": "text_delta", "delta": "hello"})
                    return {"reply": "hello", "mode": "code", "steps": 1}
                with patch("app.PORT", self.server.server_port), patch("app.get_agent_store", return_value=store), patch(
                    "app.run_agent", side_effect=fake_agent
                ):
                    connection.request(
                        "POST", "/api/agent/stream", body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    payload = response.read().decode()
                    events = [json.loads(line[6:]) for line in payload.splitlines() if line.startswith("data: ")]
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
                    self.assertEqual([event["type"] for event in events], ["start", "text_delta", "done"])
                    self.assertEqual(events[-1]["result"]["reply"], "hello")
                    self.assertEqual(len(store.list_sessions(str(app.ROOT.resolve()))), 1)
        finally:
            connection.close()

    def test_streaming_tool_approval_pauses_until_session_scoped_decision(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps({"task": "write a file", "provider": "groq"})
        try:
            with tempfile.TemporaryDirectory() as directory:
                store = AgentStore(Path(directory) / "agent.sqlite3")
                approvals = ApprovalRegistry(timeout=3)

                def fake_agent(*_args, **kwargs):
                    kwargs["event_callback"]({"type": "tool_start", "name": "write_file", "id": "call-1"})
                    approved = kwargs["approval_callback"]({
                        "type": "approval_required", "tool": "write_file", "call_id": "call-1",
                        "arguments": {"path": "file.txt"},
                    })
                    return {"reply": "Approved." if approved else "Rejected.", "mode": "code", "steps": 1}

                with patch("app.PORT", self.server.server_port), patch("app.get_agent_store", return_value=store), patch(
                    "app.get_approval_registry", return_value=approvals
                ), patch("app.run_agent", side_effect=fake_agent):
                    connection.request(
                        "POST", "/api/agent/stream", body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()

                    def read_event():
                        while True:
                            line = response.readline()
                            if not line:
                                return None
                            if line.startswith(b"data: "):
                                data = json.loads(line[6:].decode())
                                response.readline()  # blank event separator
                                return data

                    start = read_event()
                    tool_started = read_event()
                    approval = read_event()
                    self.assertEqual(start["type"], "start")
                    self.assertEqual(tool_started["type"], "tool_start")
                    self.assertEqual(approval["type"], "approval_required")

                    decision = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
                    try:
                        decision.request(
                            "POST", "/api/agent/approvals",
                            body=json.dumps({
                                "approval_id": approval["approval_id"],
                                "session_id": start["session_id"],
                                "decision": "approve",
                            }),
                            headers={"Content-Type": "application/json"},
                        )
                        decision_response = decision.getresponse()
                        self.assertEqual(decision_response.status, 200)
                        self.assertTrue(json.loads(decision_response.read())["resolved"])
                    finally:
                        decision.close()

                    done = read_event()
                    self.assertEqual(done["type"], "done")
                    self.assertEqual(done["result"]["reply"], "Approved.")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
