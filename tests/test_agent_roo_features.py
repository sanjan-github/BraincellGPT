import email.message
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.provider_registry import OPENAI_COMPATIBLE_PROVIDERS
from backend.agent import (
    _bound_agent_messages,
    _summarize_agent_context,
    _anthropic_assistant,
    _anthropic_payload,
    _provider,
    run_agent,
    run_tool,
    tools_for_mode,
)


class AgentModeTests(unittest.TestCase):
    def test_openai_agent_forwards_images_and_retains_them_in_context_bounding(self):
        import base64
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"Answer"}}]}'
        response.__enter__.return_value = response
        image = "data:image/png;base64," + base64.b64encode(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg==")).decode()
        with patch("backend.agent.open_provider_request", return_value=response) as opener:
            run_agent("describe", [], Path.cwd(), "groq", "api-key", "", "", {}, mode="ask", images=[image])
        payload = json.loads(opener.call_args.args[0].data.decode())
        content = payload["messages"][-1]["content"]
        self.assertEqual(content[0]["text"], "describe")
        self.assertEqual(content[1]["type"], "image_url")
        with patch("backend.agent.MAX_AGENT_CONTEXT_CHARS", 120):
            bounded = _bound_agent_messages(payload["messages"])
        self.assertEqual(bounded[-1]["content"][1]["type"], "image_url")

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

    def test_agent_provider_resolves_azure_endpoint_and_deployment(self):
        with patch("backend.agent.resolve_public_addresses", return_value=(("ipv4",),)) as resolver:
            endpoint, key, model, addresses = _provider(
                "azure_openai", "manual-key", "", "", {
                    "AZURE_OPENAI_ENDPOINT": "https://resource.openai.azure.com/openai/v1",
                    "AZURE_OPENAI_DEPLOYMENT": "deployment-a",
                    "AZURE_OPENAI_API_KEY": "environment-key",
                },
            )
        self.assertEqual(endpoint, "https://resource.openai.azure.com/openai/v1/chat/completions")
        self.assertEqual(key, "manual-key")
        self.assertEqual(model, "deployment-a")
        self.assertEqual(addresses, (("ipv4",),))
        resolver.assert_called_once()

    def test_architect_mode_can_only_edit_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = json.loads(run_tool(
                "write_file", {"path": "plan.md", "content": "plan"}, root,
                allow_edits=True, mode="architect",
            ))
            self.assertIn("written", result, result.get("error", str(result)))
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
            self.assertIn("replacements", result, result.get("error", str(result)))
            self.assertEqual(result["replacements"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\ngamma\n")

            mismatch = json.loads(run_tool(
                "apply_diff",
                {"path": "sample.txt", "old_text": "missing", "new_text": "changed"},
                root, allow_edits=True,
            ))
            self.assertIn("error", mismatch)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\ngamma\n")

    def test_preview_diff_returns_patch_without_modifying_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "sample.txt"
            target.write_text("alpha\nbeta\nomega\n", encoding="utf-8")

            result = json.loads(run_tool(
                "preview_diff",
                {"path": "sample.txt", "old_text": "beta", "new_text": "gamma"},
                root, allow_edits=False,
            ))

            self.assertTrue(result["changed"])
            self.assertIn("-beta", result["diff"])
            self.assertIn("+gamma", result["diff"])
            self.assertTrue(result["can_apply"])
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\nbeta\nomega\n")

    def test_preview_diff_rejects_ambiguous_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("same\nsame\n", encoding="utf-8")
            result = json.loads(run_tool(
                "preview_diff",
                {"path": "sample.txt", "old_text": "same", "new_text": "changed"},
                root, allow_edits=False,
            ))
            self.assertIn("error", result)
            self.assertEqual((root / "sample.txt").read_text(encoding="utf-8"), "same\nsame\n")

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

    def test_command_timeout_kills_the_entire_posix_process_group(self):
        import subprocess
        process = MagicMock()
        process.pid = 4567
        process.stdout = io.BytesIO()
        process.stderr = io.BytesIO()
        process.wait.side_effect = [subprocess.TimeoutExpired("command", 1), -9]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with \
                patch("backend.agent.os.name", "posix"), \
                patch("backend.agent.signal.SIGKILL", 9, create=True), \
                patch("backend.agent.subprocess.Popen", return_value=process), \
                patch("backend.agent.os.killpg", create=True) as killpg:
                from backend.agent import _execute_command
                result = _execute_command("sleep 10", root, 1)
        killpg.assert_called_once_with(4567, 9)
        self.assertTrue(result["timed_out"])

    def test_command_timeout_uses_windows_process_tree_termination(self):
        import subprocess
        process = MagicMock()
        process.pid = 4568
        process.stdout = io.BytesIO()
        process.stderr = io.BytesIO()
        process.wait.side_effect = [subprocess.TimeoutExpired("command", 1), 1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            job = MagicMock()
            job.assign.return_value = True
            with patch("backend.agent.os.name", "nt"), \
                    patch("backend.agent.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                    patch("backend.agent._WindowsJob", return_value=job), \
                    patch("backend.agent.subprocess.Popen", return_value=process), \
                    patch("backend.agent.subprocess.run") as taskkill:
                from backend.agent import _execute_command
                result = _execute_command("timeout 10", root, 1)
        self.assertTrue(result["timed_out"])
        job.close.assert_called_once()
        taskkill.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Requires native Windows Job Objects")
    def test_windows_job_object_kills_real_grandchild_on_timeout(self):
        import ctypes
        import subprocess
        import time

        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "child.pid"
            child_code = "import time; time.sleep(30)"
            parent_code = (
                "import subprocess,sys,time; "
                f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
                f"open({str(pid_file)!r},'w').write(str(child.pid)); "
                "time.sleep(30)"
            )
            command = subprocess.list2cmdline([sys.executable, "-c", parent_code])
            from backend.agent import _execute_command
            result = _execute_command(command, Path(directory), 2)
            self.assertTrue(result["timed_out"])
            self.assertTrue(pid_file.exists())
            child_pid = int(pid_file.read_text(encoding="utf-8"))
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel32.WaitForSingleObject.restype = ctypes.c_ulong
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) | SYNCHRONIZE (0x00100000)
            process_handle = kernel32.OpenProcess(0x101000, False, child_pid)
            if process_handle:
                wait_result = kernel32.WaitForSingleObject(process_handle, 1000)
                kernel32.CloseHandle(process_handle)
                self.assertEqual(wait_result, 0, "The command's grandchild remained alive after the Job Object closed.")

    def test_command_slot_limit_fails_fast_when_saturated(self):
        from backend.agent import _execute_command
        with tempfile.TemporaryDirectory() as directory, \
                patch("backend.agent._COMMAND_SLOTS.acquire", return_value=False):
            with self.assertRaisesRegex(ValueError, "Too many workspace commands"):
                _execute_command("echo blocked", Path(directory), 1)

    def test_command_environment_excludes_provider_credentials(self):
        from backend.agent import _command_environment
        with patch.dict("os.environ", {
            "OPENAI_API_KEY": "must-not-be-inherited",
            "GROQ_API_KEY": "also-secret",
            "PATH": "safe-path",
        }, clear=True):
            environment = _command_environment()
        self.assertEqual(environment, {"PATH": "safe-path"})


class AgentWorkflowTests(unittest.TestCase):
    @staticmethod
    def _openai_tool_response(calls):
        response = MagicMock()
        response.read.return_value = json.dumps({"choices": [{"message": {
            "content": None, "tool_calls": calls,
        }}]}).encode()
        response.__enter__.return_value = response
        return response

    def test_context_compaction_summarizes_old_turns_and_keeps_tool_exchanges_whole(self):
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"Prior work: inspected src/app.py; fix the parser next."}}]}'
        response.__enter__.return_value = response
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Inspect parser"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "tool-1", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tool-1", "content": "parser source " + ("x" * 300)},
            {"role": "user", "content": "Now fix the issue"},
        ]
        with patch("backend.agent.MAX_AGENT_CONTEXT_CHARS", 220), patch("backend.agent.open_provider_request", return_value=response) as opener:
            self.assertTrue(_summarize_agent_context(messages, "groq", "https://provider.test/chat", "key", "model"))
        payload = json.loads(opener.call_args.args[0].data.decode())
        self.assertIn("Inspect parser", payload["messages"][1]["content"])
        self.assertEqual(messages[-1]["content"], "Now fix the issue")
        self.assertIn("Prior work", messages[1]["content"])
        self.assertFalse(any(message.get("role") == "tool" for message in messages))

    def test_agent_task_runtime_budget_fails_before_provider_request(self):
        with self.assertRaisesRegex(ValueError, "5-minute execution limit"):
            run_agent(
                "task", [], Path.cwd(), "groq", "api-key", "", "", {}, mode="ask",
                _task_budget={"deadline": 0, "tool_calls": 0, "tool_output_chars": 0},
            )

    def test_agent_task_tool_call_budget_is_aggregate_and_fails_closed(self):
        response = self._openai_tool_response([{
            "id": "read-1", "type": "function", "function": {
                "name": "read_file", "arguments": '{"path":"README.md"}',
            },
        }])
        with patch("backend.agent.open_provider_request", return_value=response):
            with self.assertRaisesRegex(ValueError, "total tool-call limit"):
                run_agent(
                    "read the readme", [], Path.cwd(), "groq", "api-key", "", "", {}, mode="code",
                    _task_budget={"deadline": 10**12, "tool_calls": 32, "tool_output_chars": 0},
                )

    def test_agent_task_tool_output_budget_is_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text("x" * 100, encoding="utf-8")
            response = self._openai_tool_response([{
                "id": "read-1", "type": "function", "function": {
                    "name": "read_file", "arguments": '{"path":"large.txt"}',
                },
            }])
            with patch("backend.agent.open_provider_request", return_value=response):
                with self.assertRaisesRegex(ValueError, "cumulative tool-output limit"):
                    run_agent(
                        "read the file", [], root, "groq", "api-key", "", "", {}, mode="code",
                        _task_budget={"deadline": 10**12, "tool_calls": 0, "tool_output_chars": 200_000},
                    )

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

    def test_gemini_provider_is_available_to_the_coding_agent(self):
        endpoint, secret, model, pinned = _provider(
            "gemini", "", "", "", {"GEMINI_API_KEY": "gemini-key", "GEMINI_MODEL": ""}
        )
        self.assertEqual(endpoint, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        self.assertEqual(secret, "gemini-key")
        self.assertEqual(model, "gemini-3.8-flash")
        self.assertIsNone(pinned)

    def test_chat_and_coding_agent_share_all_openai_compatible_providers(self):
        for name, config in OPENAI_COMPATIBLE_PROVIDERS.items():
            with self.subTest(provider=name):
                endpoint, _secret, model, _pinned = _provider(name, "", "", "", {})
                self.assertEqual(endpoint, config["base_url"].rstrip("/") + "/chat/completions")
                self.assertEqual(model, config["model"])

    def test_malformed_tool_call_batch_is_rejected_before_any_tool_runs(self):
        valid_write = {"id": "write-1", "type": "function", "function": {
            "name": "write_file", "arguments": '{"path":"should-not-exist.txt","content":"x"}',
        }}
        missing_id = {"type": "function", "function": {"name": "write_file", "arguments": "{}"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("backend.agent.open_provider_request", return_value=self._openai_tool_response([valid_write, missing_id])) as opener:
                with self.assertRaisesRegex(ValueError, "malformed tool call ID"):
                    run_agent("write safely", [], root, "groq", "key", "", "", {}, allow_edits=True)
            self.assertFalse((root / "should-not-exist.txt").exists())
            opener.assert_called_once()

    def test_agent_rejects_invalid_tool_call_shapes_and_arguments(self):
        malformed_calls = [
            ([{"id": "call-1", "function": {"arguments": "{}"}}], "malformed tool function"),
            ([{"id": "call-1", "function": {"name": "read_file", "arguments": "{"}}], "invalid tool argument JSON"),
            ([{"id": "call-1", "function": {"name": "read_file", "arguments": "[]"}}], "arguments must be an object"),
            ([{"id": "duplicate", "function": {"name": "read_file", "arguments": "{}"}},
              {"id": "duplicate", "function": {"name": "read_file", "arguments": "{}"}}], "duplicate tool call IDs"),
            ([{"id": "call-1", "function": {"name": "bad tool name", "arguments": "{}"}}], "malformed tool function"),
        ]
        for calls, expected in malformed_calls:
            with self.subTest(expected=expected):
                with patch("backend.agent.open_provider_request", return_value=self._openai_tool_response(calls)):
                    with self.assertRaisesRegex(ValueError, expected):
                        run_agent("inspect", [], Path.cwd(), "groq", "key", "", "", {})

    def test_agent_rejects_more_than_four_tool_calls(self):
        calls = [{"id": f"call-{index}", "function": {"name": "read_file", "arguments": "{}"}}
                 for index in range(5)]
        with patch("backend.agent.open_provider_request", return_value=self._openai_tool_response(calls)):
            with self.assertRaisesRegex(ValueError, "too many tool calls"):
                run_agent("inspect", [], Path.cwd(), "groq", "key", "", "", {})

    def test_agent_provider_http_error_closes_response(self):
        error_body = io.BytesIO(b'{"error":"forbidden"}')
        error = urllib.error.HTTPError(
            "https://api.groq.com/openai/v1/chat/completions", 403, "Forbidden",
            email.message.Message(), error_body,
        )
        with patch("backend.agent.open_provider_request", side_effect=error):
            with self.assertRaisesRegex(ValueError, "HTTP 403"):
                run_agent("inspect", [], Path.cwd(), "groq", "key", "", "", {})
        self.assertTrue(error_body.closed)

    def test_unknown_tool_returns_a_controlled_tool_error(self):
        final = MagicMock()
        final.read.return_value = b'{"choices":[{"message":{"content":"I could not use that tool."}}]}'
        final.__enter__.return_value = final
        with patch("backend.agent.open_provider_request", side_effect=[
            self._openai_tool_response([{"id": "unknown-1", "function": {"name": "unknown_tool", "arguments": "{}"}}]),
            final,
        ]) as opener:
            result = run_agent("inspect", [], Path.cwd(), "groq", "key", "", "", {})
        tool_message = json.loads(opener.call_args_list[1].args[0].data.decode())["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        error = json.loads(tool_message["content"])["error"]
        self.assertIn("tool is unavailable", error)
        self.assertEqual(result["reply"], "I could not use that tool.")

    def test_agent_step_limit_returns_controlled_completion_status(self):
        responses = [self._openai_tool_response([{
            "id": f"read-{index}", "function": {"name": "read_file", "arguments": '{"path":"missing.txt"}'},
        }]) for index in range(8)]
        with patch("backend.agent.open_provider_request", side_effect=responses) as opener:
            result = run_agent("inspect", [], Path.cwd(), "groq", "key", "", "", {})
        self.assertTrue(result["limit_reached"])
        self.assertEqual(result["steps"], 8)
        self.assertEqual(opener.call_count, 8)

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

    def test_anthropic_agent_forwards_native_image_blocks(self):
        import base64
        response = MagicMock()
        response.read.return_value = b'{"content":[{"type":"text","text":"Answer"}]}'
        response.__enter__.return_value = response
        image = "data:image/png;base64," + base64.b64encode(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg==")).decode()
        with patch("backend.agent.open_provider_request", return_value=response) as opener:
            run_agent(
                "describe", [], Path.cwd(), "anthropic", "api-key", "", "claude-test", {},
                mode="ask", images=[image],
            )
        payload = json.loads(opener.call_args.args[0].data.decode())
        content = payload["messages"][-1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "describe"})
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(content[1]["source"]["media_type"], "image/png")

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

    def test_context_budget_drops_complete_old_tool_exchange(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 200},
            {"role": "user", "content": "current"},
        ]
        with patch("backend.agent.MAX_AGENT_CONTEXT_CHARS", 180):
            bounded = _bound_agent_messages(messages)
        self.assertEqual(bounded[0]["role"], "system")
        self.assertEqual(bounded[-1]["content"], "current")
        self.assertFalse(any(message.get("role") == "tool" for message in bounded))

    def test_repeated_identical_mutating_call_is_blocked(self):
        def tool_response(call_id):
            response = MagicMock()
            response.read.return_value = json.dumps({"choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": call_id, "type": "function", "function": {
                    "name": "write_file", "arguments": '{"path":"created.txt","content":"safe"}',
                }}],
            }}]}).encode()
            response.__enter__.return_value = response
            return response

        final_response = MagicMock()
        final_response.read.return_value = b'{"choices":[{"message":{"content":"Finished."}}]}'
        final_response.__enter__.return_value = final_response
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("backend.agent.open_provider_request", side_effect=[tool_response("one"), tool_response("two"), final_response]) as opener:
                result = run_agent(
                    "create the file", [], root, "groq", "api-key", "", "", {},
                    allow_edits=True, mode="code",
                )
            second_tool_result = json.loads(opener.call_args_list[2].args[0].data.decode())["messages"][-1]["content"]
            self.assertIn("Repeated identical mutating", second_tool_result)
            self.assertEqual((root / "created.txt").read_text(encoding="utf-8"), "safe")
            self.assertEqual(result["reply"], "Finished.")


if __name__ == "__main__":
    unittest.main()
