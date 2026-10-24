import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent import (
    _anthropic_assistant,
    _anthropic_payload,
    _provider,
    run_agent,
    run_tool,
    tools_for_mode,
)


class AgentModeTests(unittest.TestCase):
    def test_modes_filter_capabilities_server_side(self):
        ask_tools = {tool["function"]["name"] for tool in tools_for_mode("ask", True, True)}
        self.assertIn("read_file", ask_tools)
        self.assertNotIn("write_file", ask_tools)
        self.assertNotIn("execute_command", ask_tools)

        architect_tools = {tool["function"]["name"] for tool in tools_for_mode("architect", True, False)}
        self.assertIn("write_file", architect_tools)
        self.assertNotIn("execute_command", architect_tools)

        command_tools = {tool["function"]["name"] for tool in tools_for_mode("code", False, True)}
        self.assertIn("execute_command", command_tools)
        self.assertNotIn("write_file", command_tools)

    def test_architect_mode_can_only_edit_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = json.loads(run_tool(
                "write_file", {"path": "plan.md", "content": "plan"}, root,
                allow_edits=True, mode="architect",
            ))
            self.assertEqual(result["written"], "plan.md")
            rejected = json.loads(run_tool(
                "write_file", {"path": "main.py", "content": "bad"}, root,
                allow_edits=True, mode="architect",
            ))
            self.assertIn("error", rejected)
            self.assertFalse((root / "main.py").exists())


class AgentEditingToolTests(unittest.TestCase):
    def test_search_replace_requires_exact_match_count_and_writes_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "sample.txt"
            target.write_text("alpha\nbeta\n", encoding="utf-8")

            result = json.loads(run_tool(
                "search_replace",
                {"path": "sample.txt", "old_text": "beta", "new_text": "gamma"},
                root, allow_edits=True,
            ))
            self.assertEqual(result["replacements"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\ngamma\n")

            mismatch = json.loads(run_tool(
                "apply_diff",
                {"path": "sample.txt", "old_text": "missing", "new_text": "changed"},
                root, allow_edits=True,
            ))
            self.assertIn("error", mismatch)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\ngamma\n")

    def test_command_tool_is_opt_in_and_returns_bounded_structured_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disabled = json.loads(run_tool(
                "execute_command", {"command": "echo must-not-run"}, root,
                allow_edits=False,
            ))
            self.assertIn("error", disabled)

            enabled = json.loads(run_tool(
                "execute_command", {"command": "echo braincell-agent-ok", "timeout": 5}, root,
                allow_edits=False, allow_commands=True,
            ))
            self.assertEqual(enabled["exit_code"], 0)
            self.assertIn("braincell-agent-ok", enabled["stdout"])
            self.assertFalse(enabled["timed_out"])


class AgentWorkflowTests(unittest.TestCase):
    def test_followup_completion_and_mode_tools_return_structured_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            followup = json.loads(run_tool(
                "ask_followup_question", {"question": "Which module should I inspect?"}, root,
                allow_edits=False,
            ))
            self.assertTrue(followup["awaiting_user"])

            completion = json.loads(run_tool(
                "attempt_completion", {"result": "Done."}, root, allow_edits=False,
            ))
            self.assertTrue(completion["completed"])

    def test_anthropic_provider_uses_messages_api(self):
        endpoint, secret, model, pinned = _provider(
            "anthropic", "", "", "", {"ANTHROPIC_API_KEY": "test-key"}
        )
        self.assertEqual(endpoint, "https://api.anthropic.com/v1/messages")
        self.assertEqual(secret, "test-key")
        self.assertTrue(model)
        self.assertIsNone(pinned)

    def test_anthropic_payload_translates_function_calls_and_results(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "inspect file"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "tool-1", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
            }]},
            {"role": "tool", "tool_call_id": "tool-1", "content": "print(1)"},
        ]
        tools = [{"type": "function", "function": {
            "name": "read_file", "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }}]

        payload = _anthropic_payload("claude-test", messages, tools, 500)

        self.assertEqual(payload["system"], "system prompt")
        self.assertEqual(payload["messages"][1]["content"][0]["type"], "tool_use")
        self.assertEqual(payload["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual(payload["tools"][0]["input_schema"]["type"], "object")

    def test_anthropic_response_normalizes_tool_use_blocks(self):
        assistant, calls = _anthropic_assistant({"content": [
            {"type": "text", "text": "Reading."},
            {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {"path": "a.py"}},
        ]})

        self.assertEqual(assistant["content"], "Reading.")
        self.assertEqual(calls[0]["id"], "tool-1")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "a.py"})

    def test_anthropic_agent_sends_native_messages_request(self):
        response = MagicMock()
        response.read.return_value = b'{"content":[{"type":"text","text":"Answer"}]}'
        response.__enter__.return_value = response

        with patch("backend.agent.open_provider_request", return_value=response) as opener:
            result = run_agent(
                "explain this project", [], Path.cwd(), "anthropic", "api-key", "",
                "claude-test", {}, mode="ask",
            )

        request = opener.call_args.args[0]
        self.assertEqual(result["reply"], "Answer")
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "api-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "claude-test")
        self.assertIn("system", payload)
        self.assertIn("tools", payload)
        self.assertNotIn("write_file", {tool["name"] for tool in payload["tools"]})

    def test_attempt_completion_ends_agent_loop(self):
        response = MagicMock()
        response.read.return_value = json.dumps({"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "complete-1", "type": "function", "function": {
                "name": "attempt_completion", "arguments": '{"result":"Finished safely."}',
            }}],
        }}]}).encode()
        response.__enter__.return_value = response

        with patch("backend.agent.open_provider_request", return_value=response) as opener:
            result = run_agent(
                "do a small task", [], Path.cwd(), "groq", "api-key", "", "", {},
                allow_edits=False, mode="code",
            )

        self.assertEqual(result["reply"], "Finished safely.")
        self.assertTrue(result["completed"])
        opener.assert_called_once()

    def test_switch_mode_updates_tools_and_system_prompt_on_next_turn(self):
        switch_response = MagicMock()
        switch_response.read.return_value = json.dumps({"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "switch-1", "type": "function", "function": {
                "name": "switch_mode", "arguments": '{"mode":"ask"}',
            }}],
        }}]}).encode()
        switch_response.__enter__.return_value = switch_response
        answer_response = MagicMock()
        answer_response.read.return_value = b'{"choices":[{"message":{"content":"Answer"}}]}'
        answer_response.__enter__.return_value = answer_response

        with patch("backend.agent.open_provider_request", side_effect=[switch_response, answer_response]) as opener:
            result = run_agent(
                "explain the project", [], Path.cwd(), "groq", "api-key", "", "", {},
                allow_edits=True, allow_commands=True, mode="code",
            )

        self.assertEqual(result["mode"], "ask")
        second_payload = json.loads(opener.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("Current mode: Ask", second_payload["messages"][0]["content"])
        self.assertNotIn("write_file", {tool["function"]["name"] for tool in second_payload["tools"]})
        self.assertNotIn("execute_command", {tool["function"]["name"] for tool in second_payload["tools"]})


if __name__ == "__main__":
    unittest.main()
