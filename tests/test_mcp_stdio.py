import json
import email.message
import io
import os
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent import run_agent
from backend.mcp_stdio import MCPToolRouter, _StreamableHTTPClient, call_tool, discover_tools, load_server_specs, _safe_child_environment, _mcp_header_value, _normalize_tool, _validate_tool_arguments


MODERN_SERVER = r'''
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    response = {"jsonrpc": "2.0", "id": request.get("id")}
    if method == "server/discover":
        response["result"] = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}, "resources": {}}}
    elif method == "tools/list":
        response["result"] = {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}
    elif method == "tools/call":
        response["result"] = {"content": [{"type": "text", "text": "echo: " + request["params"]["arguments"]["text"]}]}
    elif method == "resources/list":
        response["result"] = {"resources": [{"uri": "notes://guide", "name": "guide", "mimeType": "text/plain"}]}
    elif method == "resources/read":
        response["result"] = {"contents": [{"uri": request["params"]["uri"], "mimeType": "text/plain", "text": "MCP resource text"}]}
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
    def test_mcp_argument_validation_enforces_required_types_and_bounds(self):
        schema = {
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": 20},
                "count": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["text"],
            "additionalProperties": False,
        }
        _validate_tool_arguments(schema, {"text": "hello", "count": 2})
        for arguments in (
            {},
            {"text": 1},
            {"text": "hello", "count": 4},
            {"text": "hello", "extra": True},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                _validate_tool_arguments(schema, arguments)

    def test_mcp_argument_validation_rejects_oversized_and_deep_arguments(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            _validate_tool_arguments({"type": "object"}, {"value": "x" * 70_000})

    def _discover_and_call(self, server_env):
        tools, routes = discover_tools(server_env, Path.cwd())
        self.assertEqual(len(tools), 1)
        provider_name = tools[0]["function"]["name"]
        route = routes[provider_name]
        spec, original_name = route.server, route.name
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

    def test_router_lists_and_reads_mcp_resources(self):
        router = MCPToolRouter(configured_env(MODERN_SERVER, "modern"), Path.cwd())
        try:
            names = {tool["function"]["name"] for tool in router.tools}
            list_name = "mcp__modern__resource_list"
            read_name = "mcp__modern__resource_read"
            self.assertIn(list_name, names)
            self.assertIn(read_name, names)
            listed = router.call(list_name, {})
            self.assertIn("notes://guide", listed["resources"][0]["uri"])
            read = router.call(read_name, {"uri": "notes://guide"})
            self.assertEqual(read["text"], "MCP resource text")
        finally:
            router.close()

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

    def test_remote_mcp_accepts_a_hosted_cimd_url(self):
        server_env = {"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
            "name": "remote", "url": "https://mcp.example.test/mcp",
            "oauth": {"client_id_metadata_url": "https://client.example.test/braincellgpt.json"},
        }])}
        spec = load_server_specs(server_env)[0]
        self.assertEqual(spec.oauth_client_metadata_url, "https://client.example.test/braincellgpt.json")

    def test_cimd_configuration_rejects_unsafe_urls_and_conflicting_client_id(self):
        for url in ("http://client.example.test/client.json", "https://user@client.example.test/client.json", "https://client.example.test/client.json?token=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
                    "name": "remote", "url": "https://mcp.example.test/mcp",
                    "oauth": {"client_id_metadata_url": url},
                }])})
        with self.assertRaisesRegex(ValueError, "either oauth.client_id"):
            load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
                "name": "remote", "url": "https://mcp.example.test/mcp",
                "oauth": {"client_id": "registered", "client_id_metadata_url": "https://client.example.test/client.json"},
            }])})

    def test_remote_mcp_uses_pinned_https_and_explicit_environment_credentials(self):
        server_env = {
            "MCP_ACCESS_TOKEN": "remote-secret",
            "BRAINCELLGPT_MCP_SERVERS": json.dumps([{
                "name": "remote",
                "url": "https://mcp.example.test/mcp",
                "headers": {"Authorization": "Bearer ${MCP_ACCESS_TOKEN}"},
            }]),
        }
        schema = {"name": "lookup", "inputSchema": {"type": "object", "properties": {
            "region": {"type": "string", "x-mcp-header": "Region"},
        }}}
        calls = []

        def response_for(request, timeout, pinned_addresses):
            calls.append((request, timeout, pinned_addresses, json.loads(request.data)))
            method = calls[-1][3]["method"]
            result = (
                {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
                if method == "server/discover" else
                {"tools": [schema]} if method == "tools/list" else
                {"content": [{"type": "text", "text": "remote result remote-secret"}]}
            )
            response = MagicMock()
            response.headers = {"Content-Type": "application/json"}
            response.read.return_value = json.dumps({"jsonrpc": "2.0", "id": calls[-1][3]["id"], "result": result}).encode()
            response.__enter__.return_value = response
            return response

        pinned = ((2, 1, 6, ("203.0.113.40", 443)),)
        events = []
        with patch("backend.mcp_stdio.resolve_public_addresses", return_value=pinned) as resolve, patch(
            "backend.mcp_stdio.open_provider_request", side_effect=response_for
        ) as opener:
            router = MCPToolRouter(server_env, Path.cwd(), events.append)
            try:
                self.assertIn("mcp__remote__lookup", {tool["function"]["name"] for tool in router.tools}, events)
                provider_tool = next(tool for tool in router.tools if tool["function"]["name"] == "mcp__remote__lookup")
                self.assertNotIn("x-mcp-header", json.dumps(provider_tool["function"]["parameters"]))
                result = router.call("mcp__remote__lookup", {"region": "west"})
            finally:
                router.close()

        self.assertEqual(result["text"], "remote result [redacted]")
        resolve.assert_called_once_with("mcp.example.test", 443)
        self.assertEqual(opener.call_count, 3)
        for request, timeout, addresses, frame in calls:
            self.assertEqual(timeout, 15)
            self.assertEqual(addresses, pinned)
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers["authorization"], "Bearer remote-secret")
            self.assertEqual(headers["mcp-method"], frame["method"])
            if frame["method"] == "tools/call":
                self.assertEqual(headers["mcp-name"], "lookup")
                self.assertEqual(headers["mcp-param-region"], "west")
            else:
                self.assertNotIn("mcp-name", headers)
            self.assertEqual(frame["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"], "2026-07-28")

    def test_remote_schema_rejects_unsafe_header_annotations(self):
        self.assertEqual(_mcp_header_value("line1\nline2"), "=?base64?bGluZTEKbGluZTI=?=")
        invalid = {
            "name": "invalid",
            "inputSchema": {"type": "object", "properties": {
                "token": {"type": "string", "x-mcp-header": "Authorization"},
            }},
        }
        self.assertIsNone(_normalize_tool("remote", invalid, remote_http=True))

    def test_remote_mcp_rejects_local_transport_and_literal_credentials(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
                "name": "remote", "url": "http://mcp.example.test/mcp",
            }])})
        with self.assertRaisesRegex(ValueError, "environment variables"):
            load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
                "name": "remote", "url": "https://mcp.example.test/mcp",
                "headers": {"Authorization": "Bearer do-not-embed"},
            }])})

    def test_remote_mcp_accepts_preregistered_oauth_client_id(self):
        specs = load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
            "name": "remote", "url": "https://mcp.example.test/mcp",
            "oauth": {"client_id": "pre-registered-client"},
        }])})
        self.assertEqual(specs[0].oauth_client_id, "pre-registered-client")
        with self.assertRaisesRegex(ValueError, "client_id or client_id_metadata_url"):
            load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
                "name": "remote", "url": "https://mcp.example.test/mcp",
                "oauth": {"client_secret": "literal-secret"},
            }])})

    def test_remote_mcp_rejects_private_dns_before_http_request(self):
        server_env = {"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
            "name": "remote", "url": "https://mcp.example.test/mcp",
        }])}
        with patch("backend.mcp_stdio.resolve_public_addresses", side_effect=ValueError("private destination")), patch(
            "backend.mcp_stdio.open_provider_request"
        ) as opener:
            router = MCPToolRouter(server_env, Path.cwd())
            try:
                self.assertEqual(router.tools, [])
            finally:
                router.close()
        opener.assert_not_called()

    def test_remote_mcp_http_error_closes_response_body(self):
        spec = load_server_specs({"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
            "name": "remote", "url": "https://mcp.example.test/mcp",
        }])})[0]
        assert spec.url is not None
        error_body = io.BytesIO(b"provider unavailable")
        error = urllib.error.HTTPError(
            spec.url, 500, "Internal Server Error", email.message.Message(), error_body,
        )
        with patch("backend.mcp_stdio.resolve_public_addresses", return_value=((2, 1, 6, ("203.0.113.40", 443)),)):
            client = _StreamableHTTPClient(spec)
        with patch("backend.mcp_stdio.open_provider_request", side_effect=error):
            with self.assertRaisesRegex(ValueError, "status 500"):
                client._post({"jsonrpc": "2.0", "id": 1, "method": "test"}, 1)
        self.assertTrue(error_body.closed)

    def test_remote_legacy_http_negotiates_session_and_accepts_sse(self):
        server_env = {"BRAINCELLGPT_MCP_SERVERS": json.dumps([{
            "name": "legacy-http", "url": "https://mcp.example.test/mcp",
        }])}
        requests = []

        def response_for(request, timeout, pinned_addresses):
            if request.get_method() == "DELETE":
                response = MagicMock()
                response.headers = {}
                response.read.return_value = b""
                response.__enter__.return_value = response
                return response
            frame = json.loads(request.data)
            requests.append((request, frame))
            method = frame["method"]
            response = MagicMock()
            response.headers = {"Content-Type": "application/json"}
            if method == "server/discover":
                payload = {"jsonrpc": "2.0", "id": frame["id"], "error": {"code": -32601, "message": "Method not found"}}
                response.read.return_value = json.dumps(payload).encode()
            elif method == "initialize":
                payload = {"jsonrpc": "2.0", "id": frame["id"], "result": {
                    "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                }}
                response.headers["Mcp-Session-Id"] = "session-1"
                response.read.return_value = json.dumps(payload).encode()
            elif method == "notifications/initialized":
                response.headers = {"Content-Type": "application/json"}
                response.read.return_value = b""
            elif method == "tools/list":
                if not any(existing[1]["method"] == "initialize" for existing in requests):
                    payload = {"jsonrpc": "2.0", "id": frame["id"], "error": {"code": -32601, "message": "Initialize first"}}
                else:
                    response.headers = {"Content-Type": "text/event-stream; charset=utf-8"}
                    payload = {"jsonrpc": "2.0", "id": frame["id"], "result": {"tools": [{
                        "name": "lookup", "inputSchema": {"type": "object", "properties": {}},
                    }]}}
                if response.headers["Content-Type"].startswith("text/event-stream"):
                    response.read.return_value = ("data: " + json.dumps(payload) + "\n\n").encode()
                else:
                    response.read.return_value = json.dumps(payload).encode()
            else:
                raise AssertionError(method)
            response.__enter__.return_value = response
            return response

        with patch("backend.mcp_stdio.resolve_public_addresses", return_value=((2, 1, 6, ("203.0.113.40", 443)),)), patch(
            "backend.mcp_stdio.open_provider_request", side_effect=response_for
        ):
            events = []
            router = MCPToolRouter(server_env, Path.cwd(), events.append)
            try:
                self.assertIn("mcp__legacy-http__lookup", {tool["function"]["name"] for tool in router.tools}, (events, [frame["method"] for _, frame in requests]))
            finally:
                router.close()

        self.assertEqual(requests[2][1]["method"], "initialize")
        session_headers = {key.lower(): value for key, value in requests[4][0].header_items()}
        self.assertEqual(session_headers["mcp-session-id"], "session-1")
        self.assertEqual(session_headers["mcp-protocol-version"], "2025-11-25")

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
        self.assertIn("mcp__modern__resource_read", {tool["function"]["name"] for tool in first_payload["tools"]})
        self.assertEqual(approvals[0]["server"], "modern")
        self.assertEqual(approvals[0]["server_tool"], "echo")
        self.assertIn("echo: hello", second_payload["messages"][-1]["content"])
        self.assertEqual(result["reply"], "MCP complete.")


if __name__ == "__main__":
    unittest.main()
