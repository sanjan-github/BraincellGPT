"""Bounded MCP stdio client with 2026 and legacy protocol support."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_CONFIG_BYTES = 100_000
MAX_SERVERS = 8
MAX_TOOLS = 100
MAX_TOTAL_TOOL_SCHEMA_BYTES = 512_000
MAX_FRAME_BYTES = 512_000
MAX_TOOL_SCHEMA_BYTES = 64_000
MAX_CALL_RESULT_BYTES = 24_000
MCP_TIMEOUT_SECONDS = 15
MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-11-25"
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
FUNCTION_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class MCPServerSpec:
    name: str
    command: str
    args: tuple[str, ...]
    env: dict[str, str]


def _safe_child_environment(parent_env: dict[str, str], overrides: dict[str, str]) -> dict[str, str]:
    safe_keys = {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL",
    }
    environment = {key: value for key, value in parent_env.items() if key.upper() in safe_keys}
    for key, raw_value in overrides.items():
        if not isinstance(key, str) or not ENV_NAME_RE.fullmatch(key) or not isinstance(raw_value, str) or len(raw_value) > 4000:
            raise ValueError("MCP server environment overrides must use valid names and short string values.")
        reference = ENV_REF_RE.fullmatch(raw_value)
        if reference:
            source_name = reference.group(1)
            if source_name not in parent_env:
                raise ValueError("An MCP server references an environment variable that is not configured.")
            raw_value = parent_env[source_name]
        environment[key] = raw_value
    return environment


def load_server_specs(environ: dict[str, str]) -> list[MCPServerSpec]:
    raw = environ.get("BRAINCELLGPT_MCP_SERVERS", "")
    if not raw.strip():
        return []
    if len(raw.encode("utf-8", errors="replace")) > MAX_CONFIG_BYTES:
        raise ValueError("MCP server configuration exceeds the size limit.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("MCP server configuration is not valid JSON.") from exc
    if not isinstance(data, list) or len(data) > MAX_SERVERS:
        raise ValueError("MCP server configuration must be a list of at most eight servers.")
    specs = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Each MCP server configuration must be an object.")
        name, command, args, extra_env = item.get("name"), item.get("command"), item.get("args", []), item.get("env", {})
        if not isinstance(name, str) or not SERVER_NAME_RE.fullmatch(name) or name in seen:
            raise ValueError("MCP server names must be unique and use letters, digits, underscores, or hyphens.")
        if not isinstance(command, str) or not command.strip() or len(command) > 500:
            raise ValueError("Each MCP server needs a command up to 500 characters.")
        if not isinstance(args, list) or len(args) > 40 or any(not isinstance(arg, str) or len(arg) > 4000 for arg in args):
            raise ValueError("MCP server arguments must be a list of at most 40 short strings.")
        if not isinstance(extra_env, dict) or len(extra_env) > 64:
            raise ValueError("MCP server environment must be an object with at most 64 entries.")
        _safe_child_environment(environ, extra_env)
        specs.append(MCPServerSpec(name, command, tuple(args), dict(extra_env)))
        seen.add(name)
    return specs


def _function_name(server: str, tool: str) -> str:
    raw = "mcp__" + server + "__" + tool
    if len(raw) <= 64:
        return raw
    suffix = format(abs(hash((server, tool))) % (16 ** 8), "08x")
    return raw[:55] + "_" + suffix


def _normalize_tool(server: str, data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    schema = data.get("inputSchema")
    if not isinstance(name, str) or not TOOL_NAME_RE.fullmatch(name) or not isinstance(schema, dict):
        return None
    if schema.get("type") != "object" or len(json.dumps(schema).encode("utf-8")) > MAX_TOOL_SCHEMA_BYTES:
        return None
    provider_name = _function_name(server, name)
    description = data.get("description", "")
    if not isinstance(description, str):
        description = ""
    description = f"MCP server {server}: " + description[:1800]
    return {
        "type": "function",
        "function": {
            "name": provider_name,
            "description": description,
            "parameters": schema,
        },
    }


def _tool_size(tool: dict) -> int:
    return len(json.dumps(tool, ensure_ascii=False, separators=(",", ":")).encode("utf-8", errors="replace"))


class _StdioClient:
    def __init__(self, spec: MCPServerSpec, parent_env: dict[str, str], cwd: Path):
        self.spec = spec
        child_env = _safe_child_environment(parent_env, spec.env)
        executable = shutil.which(spec.command, path=child_env.get("PATH"))
        if executable is None:
            raise ValueError("Configured MCP server command was not found on PATH.")
        self.process = subprocess.Popen(
            [executable, *spec.args],
            cwd=str(cwd),
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            bufsize=0,
        )
        self._messages: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=16)
        self._next_id = 1
        self.era = ""
        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()

    def _read_stdout(self) -> None:
        try:
            assert self.process.stdout is not None
            while True:
                line = self.process.stdout.readline(MAX_FRAME_BYTES + 1)
                if not line:
                    self._messages.put(None, timeout=1)
                    return
                if len(line) > MAX_FRAME_BYTES:
                    self._messages.put(ValueError("MCP server sent a frame that exceeds the size limit."), timeout=1)
                    return
                if not line.endswith(b"\n"):
                    self._messages.put(ValueError("MCP server sent a frame without its required newline delimiter."), timeout=1)
                    return
                if not line.endswith(b"\n"):
                    self._messages.put(ValueError("MCP server sent a frame without its required newline delimiter."), timeout=1)
                    return
                self._messages.put(line, timeout=1)
        except BaseException as exc:
            try:
                self._messages.put(exc, timeout=1)
            except queue.Full:
                pass

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                except OSError:
                    pass
                self.process.wait(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()

    def _meta(self) -> dict:
        return {
            "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
            "io.modelcontextprotocol/clientInfo": {"name": "BrainCellGPT", "version": "0.1.0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }

    def _response(self, request_id: int, method: str, params: dict | None = None, timeout: float = MCP_TIMEOUT_SECONDS):
        assert self.process.stdin is not None
        request_params = dict(params or {})
        if self.era == "modern":
            request_params["_meta"] = self._meta()
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": request_params}
        encoded = json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_FRAME_BYTES:
            raise ValueError("MCP request exceeds the size limit.")
        try:
            self.process.stdin.write(encoded)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ValueError("MCP server closed its input stream.") from exc
        while True:
            try:
                line = self._messages.get(timeout=timeout)
            except queue.Empty as exc:
                raise ValueError("MCP server did not respond before the timeout.") from exc
            if isinstance(line, BaseException):
                raise ValueError(str(line)) from line
            if line is None:
                raise ValueError("MCP server closed its output stream.")
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("MCP server returned an invalid JSON-RPC frame.") from exc
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("MCP server returned a malformed JSON-RPC message.")
            if message.get("id") == request_id:
                return message
            if "method" in message and "id" in message:
                self._respond_to_server_request(message)

    def _notify(self, method: str, params: dict | None = None) -> None:
        assert self.process.stdin is not None
        frame = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = params
        self.process.stdin.write(json.dumps(frame, separators=(",", ":")).encode() + b"\n")
        self.process.stdin.flush()

    def _respond_to_server_request(self, message: dict) -> None:
        assert self.process.stdin is not None
        if message.get("method") == "ping":
            response = {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}
        else:
            response = {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": "Client method not supported."}}
        self.process.stdin.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        self.process.stdin.flush()

    def _request_result(self, method: str, params: dict | None = None) -> dict:
        response = self._response(self._next_id, method, params)
        self._next_id += 1
        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict) and error.get("code") == -32601:
                raise _MethodNotFound(str(error.get("message", "Method not found.")))
            message = error.get("message", "MCP request failed.") if isinstance(error, dict) else "MCP request failed."
            raise ValueError(str(message)[:500])
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError("MCP server returned an invalid result object.")
        return result

    def connect(self) -> None:
        try:
            discovery = self._response(0, "server/discover", {"_meta": self._meta()}, timeout=2)
            if "error" in discovery:
                raise _MethodNotFound("MCP modern discovery was not accepted.")
            result = discovery.get("result")
            if not isinstance(result, dict) or MODERN_VERSION not in result.get("supportedVersions", []):
                raise _MethodNotFound("MCP server does not advertise the current protocol version.")
            self.era = "modern"
            self._next_id = 1
            return
        except (ValueError, queue.Empty, _MethodNotFound):
            # A legacy stdio server may exit or stay silent when probed with an
            # unknown method. The caller closes this process and retries fresh.
            raise _LegacyFallback

    def connect_legacy(self) -> None:
        self.era = "legacy"
        result = self._request_result("initialize", {
            "protocolVersion": LEGACY_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "BrainCellGPT", "version": "0.1.0"},
        })
        if not isinstance(result.get("capabilities"), dict):
            raise ValueError("MCP server returned invalid initialization capabilities.")
        self._notify("notifications/initialized")

    def list_tools(self) -> list[dict]:
        tools = []
        cursor = None
        for _ in range(10):
            params = {"cursor": cursor} if cursor else {}
            result = self._request_result("tools/list", params)
            page = result.get("tools")
            if not isinstance(page, list):
                raise ValueError("MCP server returned an invalid tools/list result.")
            tools.extend(page)
            if len(tools) >= MAX_TOOLS:
                return tools[:MAX_TOOLS]
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                break
        return tools

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        return self._request_result("tools/call", {"name": tool_name, "arguments": arguments})


class _MethodNotFound(Exception):
    pass


class _LegacyFallback(Exception):
    pass


def _connect_server(spec: MCPServerSpec, environ: dict[str, str], cwd: Path) -> _StdioClient:
    client = _StdioClient(spec, environ, cwd)
    try:
        client.connect()
        return client
    except _LegacyFallback:
        client.close()
    except BaseException:
        client.close()
        raise
    legacy = _StdioClient(spec, environ, cwd)
    try:
        legacy.connect_legacy()
        return legacy
    except BaseException:
        legacy.close()
        raise


def discover_tools(environ: dict[str, str], cwd: Path, event_callback=None) -> tuple[list[dict], dict[str, tuple[MCPServerSpec, str]]]:
    tools = []
    routes = {}
    specs = load_server_specs(environ)
    total_schema_bytes = 0
    for spec in specs:
        try:
            with _connect_server(spec, environ, cwd) as client:
                for raw_tool in client.list_tools():
                    normalized = _normalize_tool(spec.name, raw_tool)
                    if normalized is None:
                        continue
                    name = normalized["function"]["name"]
                    if name in routes:
                        continue
                    size = _tool_size(normalized)
                    if total_schema_bytes + size > MAX_TOTAL_TOOL_SCHEMA_BYTES:
                        return tools, routes
                    total_schema_bytes += size
                    routes[name] = (spec, raw_tool["name"])
                    tools.append(normalized)
                    if len(tools) >= MAX_TOOLS:
                        return tools, routes
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if event_callback is not None:
                event_callback({"type": "mcp_server_error", "server": spec.name, "error": str(exc)[:300]})
            continue
    return tools, routes


def call_tool(spec: MCPServerSpec, tool_name: str, arguments: dict, environ: dict[str, str], cwd: Path) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("MCP tool arguments must be an object.")
    with _connect_server(spec, environ, cwd) as client:
        result = client.call_tool(tool_name, arguments)
    return normalize_call_result(result)


def normalize_call_result(result: dict) -> dict:
    if result.get("isError") is True:
        raise ValueError("MCP tool returned an error result.")
    content = result.get("content", [])
    text_parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
    normalized = {"text": "\n".join(text_parts)[:MAX_CALL_RESULT_BYTES]}
    if isinstance(result.get("structuredContent"), dict):
        normalized["structured_content"] = result["structuredContent"]
    encoded = json.dumps(normalized)
    if len(encoded.encode("utf-8", errors="replace")) > MAX_CALL_RESULT_BYTES:
        normalized = {"text": encoded[:MAX_CALL_RESULT_BYTES]}
    return normalized


class MCPToolRouter:
    """Keep configured stdio servers alive for one complete agent run."""

    def __init__(self, environ: dict[str, str], cwd: Path, event_callback=None):
        self.environ = environ
        self.cwd = cwd
        self.tools: list[dict] = []
        self.routes: dict[str, tuple[MCPServerSpec, str]] = {}
        self._clients: list[_StdioClient] = []
        total_schema_bytes = 0
        try:
            specs = load_server_specs(environ)
        except ValueError as exc:
            if event_callback is not None:
                event_callback({"type": "mcp_error", "error": str(exc)[:300]})
            return

        for spec in specs:
            client = None
            try:
                client = _connect_server(spec, environ, cwd)
                self._clients.append(client)
                for raw_tool in client.list_tools():
                    normalized = _normalize_tool(spec.name, raw_tool)
                    if normalized is None:
                        continue
                    name = normalized["function"]["name"]
                    if name in self.routes:
                        continue
                    size = _tool_size(normalized)
                    if total_schema_bytes + size > MAX_TOTAL_TOOL_SCHEMA_BYTES:
                        return
                    total_schema_bytes += size
                    self.routes[name] = (spec, raw_tool["name"])
                    self.tools.append(normalized)
                    if len(self.tools) >= MAX_TOOLS:
                        return
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                if client is not None:
                    client.close()
                    if client in self._clients:
                        self._clients.remove(client)
                if event_callback is not None:
                    event_callback({"type": "mcp_server_error", "server": spec.name, "error": str(exc)[:300]})

    def call(self, provider_name: str, arguments: dict) -> dict:
        if not isinstance(arguments, dict):
            raise ValueError("MCP tool arguments must be an object.")
        route = self.routes.get(provider_name)
        if route is None:
            raise ValueError("MCP tool is no longer available in this run.")
        spec, original_name = route
        client = next((item for item in self._clients if item.spec.name == spec.name), None)
        if client is None:
            raise ValueError("MCP server is unavailable in this run.")
        result = client.call_tool(original_name, arguments)
        return normalize_call_result(result)

    def close(self) -> None:
        for client in reversed(self._clients):
            client.close()
        self._clients.clear()

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()
