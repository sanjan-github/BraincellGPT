import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent import run_agent
from backend.mcp_stdio import MCPToolRouter, call_tool, discover_tools, load_server_specs, _safe_child_environment


MODERN_SERVER = r'''
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    response = {"jsonrpc": "2.0", "id": request.get("id")}
    if method == "server/discover":
        response["result"] = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
    elif method == "tools/list":
        response["result"] = {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}
    elif method == "tools/call":
        response["result"] = {"content": [{"type": "text", "text": "echo: " + request["params"]["arguments"]["text"]}]}
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}
    print(json.dumps(response), flush=True)
'''

LEGACY_SERVER = r'''
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "notifications/initialized":
        continue
    response = {"jsonrpc": "2.0", "id": request.get("id")}
    if method == "server/discover":
        response["error"] = {"code": -32601, "message": "Method not found"}
    elif method == "initialize":
        response["result"] = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "legacy-test", "version": "1"}}
    elif method == "tools/list":
        response["result"] = {"tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
    elif method == "tools/call":
        response["result"] = {"content": [{"type": "text", "text": "legacy: " + request["params"]["arguments"]["text"]}]}
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}
    print(json.dumps(response), flush=True)
'''


def configured_env(script: str, name: str = "fixture") -> dict[str, str]:
    return {
        "BRAINCELLGPT_MCP_SERVERS": json.dumps([{
            "name": name,
            "command": sys.executable,
            "args": ["-u", "-c", script],
        }]),
    }


class MCPStdioClientTests(unittest.TestCase):
    def _discover_and_call(self, server_env):
        tools, routes = discover_tools(server_env, Path.cwd())
        self.assertEqual(len(tools), 1)
        provider_name = tools[0]["function"]["name"]
        spec, original_name = routes[provider_name]
        self.assertEqual(original_name, "echo")
        result = call_tool(spec, original_name, {"text": "hello"}, server_env, Path.cwd())
        return provider_name, result

    def test_discovers_and_calls_modern_mcp_server(self):
        provider_name, result = self._discover_and_call(configured_env(MODERN_SERVER, "modern"))
        self.assertEqual(provider_name, "mcp__modern__echo")
        self.assertEqual(result["text"], "echo: hello")

    def test_falls_back_to_legacy_initialize_protocol(self):
        provider_name, result = self._discover_and_call(configured_env(LEGACY_SERVER, "legacy"))
        self.assertEqual(provider_name, "mcp__legacy__echo")
        self.assertEqual(result["text"], "legacy: hello")

    def test_mcp_environment_does_not_inherit_provider_secrets(self):
        environment = _safe_child_environment(
            {"PATH": os.environ.get("PATH", ""), "OPENAI_API_KEY": "must-not-leak", "EXPLICIT_TOKEN": "visible"},
            {"SERVICE_TOKEN": "${EXPLICIT_TOKEN}"},
        )
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertEqual(environment["SERVICE_TOKEN"], "visible")

    def test_rejects_shell_string_arguments_and_missing_env_references(self):
        with self.assertRaisesRegex(ValueError, "arguments must be a list"):
            load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
                "name": "bad", "command": "python", "args": "-c print(1)",
            }])})
        with self.assertRaisesRegex(ValueError, "not configured"):
            load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
                "name": "bad", "command": "python", "env": {"TOKEN": "${MISSING_TOKEN}"},
            }])})

    def test_agent_exposes_mcp_tools_and_requires_per_call_approval(self):
        server_env = configured_env(MODERN_SERVER, "modern")
        tool_response = MagicMock()
        tool_response.read.return_value = json.dumps({"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "mcp-call", "type": "function", "function": {
                "name": "mcp__modern__echo", "arguments": '{"text":"hello"}',
            }}],
        }}]}).encode()
        tool_response.__enter__.return_value = tool_response
        final_response = MagicMock()
        final_response.read.return_value = b'{"choices":[{"message":{"content":"MCP complete."}}]}'
        final_response.__enter__.return_value = final_response
        approvals = []
        with patch("backend.mcp_stdio.subprocess.Popen", wraps=subprocess.Popen) as process_start:
            router = MCPToolRouter(server_env, Path.cwd())
            try:
                with patch("backend.agent.open_provider_request", side_effect=[tool_response, final_response]) as provider:
                    result = run_agent(
                        "use the MCP echo tool", [], Path.cwd(), "groq", "api-key", "", "", server_env,
                        mode="code", approval_callback=lambda event: (approvals.append(event) or True),
                        mcp_router=router,
                    )
                self.assertEqual(process_start.call_count, 1)
            finally:
                router.close()

        first_payload = json.loads(provider.call_args_list[0].args[0].data.decode())
        second_payload = json.loads(provider.call_args_list[1].args[0].data.decode())
        self.assertIn("mcp__modern__echo", {tool["function"]["name"] for tool in first_payload["tools"]})
        self.assertEqual(approvals[0]["server"], "modern")
        self.assertEqual(approvals[0]["server_tool"], "echo")
        self.assertIn("echo: hello", second_payload["messages"][-1]["content"])
        self.assertEqual(result["reply"], "MCP complete.")


if __name__ == "__main__":
    unittest.main()
