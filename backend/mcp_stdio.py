"""Bounded MCP stdio client with 2026 and legacy protocol support."""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.provider_http import PROVIDER_SLOTS, open_provider_request, resolve_public_addresses
from backend.mcp_oauth import access_token as oauth_access_token, requires_scope_upgrade


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
    command: str | None
    args: tuple[str, ...]
    env: dict[str, str]
    url: str | None = None
    headers: dict[str, str] | None = None
    oauth_client_id: str | None = None
    oauth_client_metadata_url: str | None = None


@dataclass(frozen=True)
class MCPToolRoute:
    server: MCPServerSpec
    name: str
    operation: str = "tool"
    input_schema: dict | None = None


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
        name = item.get("name")
        if not isinstance(name, str) or not SERVER_NAME_RE.fullmatch(name) or name in seen:
            raise ValueError("MCP server names must be unique and use letters, digits, underscores, or hyphens.")
        url = item.get("url")
        if url is not None:
            if not isinstance(url, str) or len(url) > 2000:
                raise ValueError("Remote MCP URLs must be HTTPS URLs up to 2,000 characters.")
            try:
                parsed = urlsplit(url)
                port = parsed.port
            except ValueError as exc:
                raise ValueError("Remote MCP URL is invalid.") from exc
            if (any(ch.isspace() or ord(ch) < 0x20 for ch in url)
                    or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.fragment or parsed.query or (port is not None and not 1 <= port <= 65535)):
                raise ValueError("Remote MCP servers require an HTTPS URL without credentials, query, or fragment.")
            raw_headers = item.get("headers", {})
            oauth = item.get("oauth", {})
            if not isinstance(oauth, dict) or set(oauth) - {"client_id", "client_id_metadata_url"}:
                raise ValueError("Remote MCP oauth supports client_id or client_id_metadata_url.")
            oauth_client_id = oauth.get("client_id")
            if oauth_client_id is not None and (not isinstance(oauth_client_id, str) or not oauth_client_id.strip() or len(oauth_client_id) > 1000):
                raise ValueError("Remote MCP OAuth client_id must be a non-empty string up to 1,000 characters.")
            oauth_client_metadata_url = oauth.get("client_id_metadata_url")
            if oauth_client_metadata_url is not None:
                if (not isinstance(oauth_client_metadata_url, str) or len(oauth_client_metadata_url) > 2000
                        or oauth_client_metadata_url != oauth_client_metadata_url.strip()):
                    raise ValueError("Remote MCP OAuth client_id_metadata_url must be an HTTPS URL up to 2,000 characters.")
                try:
                    metadata_url = urlsplit(oauth_client_metadata_url)
                    metadata_port = metadata_url.port
                except ValueError as exc:
                    raise ValueError("Remote MCP OAuth client_id_metadata_url is invalid.") from exc
                if (metadata_url.scheme != "https" or not metadata_url.hostname or metadata_url.username
                        or metadata_url.password or metadata_url.fragment or metadata_url.query
                        or any(ch.isspace() or ord(ch) < 0x20 for ch in oauth_client_metadata_url)
                        or (metadata_port is not None and not 1 <= metadata_port <= 65535)):
                    raise ValueError("Remote MCP OAuth client_id_metadata_url must be public HTTPS without credentials, query, or fragment.")
                if oauth_client_id:
                    raise ValueError("Configure either oauth.client_id or oauth.client_id_metadata_url, not both.")
            if not isinstance(raw_headers, dict) or len(raw_headers) > 16:
                raise ValueError("Remote MCP headers must be an object with at most 16 entries.")
            resolved_headers = {}
            reserved_headers = {
                "host", "content-length", "connection", "keep-alive", "proxy-connection", "transfer-encoding",
                "te", "trailer", "upgrade", "proxy-authorization", "accept", "content-type",
                "mcp-session-id", "mcp-protocol-version", "mcp-method", "mcp-name",
            }
            sensitive_headers = {
                "authorization", "proxy-authorization", "x-api-key", "api-key", "cookie", "set-cookie",
                "x-auth-token", "x-access-token", "authentication-token",
            }
            seen_headers = set()
            for header_name, raw_value in raw_headers.items():
                if (not isinstance(header_name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}", header_name)
                        or header_name.lower() in reserved_headers or header_name.lower().startswith("mcp-param-")
                        or not isinstance(raw_value, str) or len(raw_value) > 4000):
                    raise ValueError("Remote MCP header names or values are invalid.")
                if header_name.lower() in seen_headers:
                    raise ValueError("Remote MCP header names must be unique, ignoring case.")
                seen_headers.add(header_name.lower())
                reference = ENV_REF_RE.fullmatch(raw_value)
                prefixed_reference = re.fullmatch(r"([A-Za-z][A-Za-z0-9+.-]{0,31}) \$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw_value)
                source_name = reference.group(1) if reference else (prefixed_reference.group(2) if prefixed_reference else None)
                if header_name.lower() in sensitive_headers and not source_name:
                    raise ValueError("Remote MCP credentials must be referenced from environment variables.")
                if source_name:
                    if source_name not in environ:
                        raise ValueError("A remote MCP header references an environment variable that is not configured.")
                    secret_value = environ[source_name]
                    raw_value = (prefixed_reference.group(1) + " " if prefixed_reference else "") + secret_value
                if len(raw_value) > 4000 or "\r" in raw_value or "\n" in raw_value:
                    raise ValueError("Remote MCP header values must be short and cannot contain newlines.")
                resolved_headers[header_name] = raw_value
            if "command" in item or "args" in item or "env" in item:
                raise ValueError("Remote MCP configuration uses url, headers, and oauth, not command, args, or env.")
            specs.append(MCPServerSpec(name, None, (), {}, url, resolved_headers, oauth_client_id, oauth_client_metadata_url))
            seen.add(name)
            continue
        command, args, extra_env = item.get("command"), item.get("args", []), item.get("env", {})
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


def _normalize_tool(server: str, data: Any, remote_http: bool = False) -> dict | None:
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    schema = data.get("inputSchema")
    if not isinstance(name, str) or not TOOL_NAME_RE.fullmatch(name) or not isinstance(schema, dict):
        return None
    if schema.get("type") != "object" or not _schema_is_bounded(schema):
        return None
    try:
        schema_size = len(json.dumps(schema).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return None
    if schema_size > MAX_TOOL_SCHEMA_BYTES:
        return None
    if remote_http and _mcp_header_annotations(schema) is None:
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
            "parameters": _strip_mcp_header_annotations(schema) if remote_http else schema,
        },
    }


def _tool_size(tool: dict) -> int:
    return len(json.dumps(tool, ensure_ascii=False, separators=(",", ":")).encode("utf-8", errors="replace"))


def _mcp_header_value(value: str) -> str:
    sentinel = value.startswith("=?base64?") and value.endswith("?=")
    safe = (
        bool(value)
        and value == value.strip(" \t")
        and all(0x20 <= ord(char) <= 0x7E for char in value)
        and not sentinel
    )
    if safe:
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return "=?base64?" + encoded + "?="


def _contains_mcp_header_annotation(value: Any) -> bool:
    if isinstance(value, dict):
        return "x-mcp-header" in value or any(_contains_mcp_header_annotation(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_mcp_header_annotation(item) for item in value)
    return False


def _mcp_header_annotations(schema: dict) -> list[tuple[tuple[str, ...], str, str]] | None:
    annotations = []
    seen = set()
    reserved = {
        "authorization", "proxy-authorization", "cookie", "set-cookie", "host", "content-length",
        "connection", "transfer-encoding", "content-type", "accept", "mcp-session-id",
        "mcp-protocol-version", "mcp-method", "mcp-name",
    }

    def visit(node, path):
        if not isinstance(node, dict):
            return True
        if "x-mcp-header" in node:
            header_name = node["x-mcp-header"]
            value_type = node.get("type")
            if (not path or not isinstance(header_name, str)
                    or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}", header_name)
                    or header_name.lower() in reserved or header_name.lower().startswith(("mcp-", "proxy-"))
                    or not isinstance(value_type, str) or value_type not in {"string", "integer", "boolean"}
                    or header_name.lower() in seen):
                return False
            seen.add(header_name.lower())
            annotations.append((path, header_name, value_type))
        properties = node.get("properties", {})
        if properties is not None and not isinstance(properties, dict):
            return False
        if isinstance(properties, dict):
            for key, child in properties.items():
                if not isinstance(key, str) or not visit(child, (*path, key)):
                    return False
        for key, child in node.items():
            if key != "properties" and _contains_mcp_header_annotation(child):
                return False
        return True

    if not visit(schema, ()):
        return None
    return annotations


def _schema_is_bounded(schema: dict, max_depth: int = 40) -> bool:
    stack = [(schema, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > max_depth:
            return False
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    return True


def _strip_mcp_header_annotations(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_mcp_header_annotations(item)
            for key, item in value.items()
            if key != "x-mcp-header"
        }
    if isinstance(value, list):
        return [_strip_mcp_header_annotations(item) for item in value]
    return value


def _resource_tool_definitions(server: str) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": _function_name(server, "resource_list"),
                "description": f"MCP server {server}: list available readable resources.",
                "parameters": {"type": "object", "properties": {"cursor": {"type": "string"}}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": _function_name(server, "resource_read"),
                "description": f"MCP server {server}: read a resource by its URI.",
                "parameters": {"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"], "additionalProperties": False},
            },
        },
    ]


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
        self.capabilities: dict = {}
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
            self.capabilities = result.get("capabilities", {}) if isinstance(result.get("capabilities"), dict) else {}
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
        self.capabilities = result["capabilities"]
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

    def list_resources(self, cursor: str | None = None) -> dict:
        params = {"cursor": cursor} if cursor else {}
        return self._request_result("resources/list", params)

    def read_resource(self, uri: str) -> dict:
        return self._request_result("resources/read", {"uri": uri})


class _StreamableHTTPClient:
    """Bounded remote MCP client for modern stateless and legacy session HTTP."""

    def __init__(self, spec: MCPServerSpec, oauth_port: int = 8000, event_callback=None):
        if not spec.url:
            raise ValueError("Remote MCP URL is not configured.")
        self.spec = spec
        self.oauth_port = oauth_port
        self.event_callback = event_callback
        parsed = urlsplit(spec.url)
        self.addresses = resolve_public_addresses(parsed.hostname or "", parsed.port or 443)
        self.headers = dict(spec.headers or {})
        secret_headers = {"authorization", "x-api-key", "api-key", "cookie", "x-auth-token", "x-access-token"}
        self._secret_values = []
        for header_name, value in self.headers.items():
            if header_name.lower() in secret_headers and value:
                self._secret_values.append(value)
                if " " in value:
                    self._secret_values.append(value.split(" ", 1)[1])
        self.session_id: str | None = None
        self.protocol_version = LEGACY_VERSION
        self.era = ""
        self.capabilities: dict = {}
        self._next_id = 1
        self._connecting = False
        self._oauth_authorized = False

    @staticmethod
    def _meta() -> dict:
        return {
            "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
            "io.modelcontextprotocol/clientInfo": {"name": "BrainCellGPT", "version": "0.1.0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self._secret_values:
                value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def _set_oauth_token(self, token: str) -> None:
        self.headers["Authorization"] = "Bearer " + token
        self._oauth_authorized = True
        self._secret_values.extend(value for value in (token, "Bearer " + token) if value not in self._secret_values)

    def _decode_response(self, response, request_id: int) -> dict:
        raw = response.read(MAX_FRAME_BYTES + 1)
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError("MCP server response exceeds the size limit.")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("MCP server returned invalid JSON.") from exc
            if not isinstance(message, dict):
                raise ValueError("MCP server returned a malformed JSON-RPC message.")
            return message
        if content_type != "text/event-stream":
            if not raw:
                raise ValueError("MCP server returned an empty response.")
            raise ValueError("MCP server returned an unsupported response content type.")
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("MCP server returned invalid UTF-8 event data.") from exc
        for event in re.split(r"\r?\n\r?\n", body):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if not data:
                continue
            try:
                message = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ValueError("MCP server returned invalid JSON in an event stream.") from exc
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise ValueError("MCP server event stream did not contain the requested response.")

    def _post(self, message: dict, request_id: int | None, extra_headers: dict[str, str] | None = None, _oauth_retry: bool = False) -> dict | None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_FRAME_BYTES:
            raise ValueError("MCP request exceeds the size limit.")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
            **(extra_headers or {}),
        }
        if self.era == "modern":
            method = message.get("method", "")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            headers.update({
                "MCP-Protocol-Version": MODERN_VERSION,
                "Mcp-Method": method,
            })
            if method == "tools/call" and isinstance(params.get("name"), str):
                name_header = _mcp_header_value(params["name"])
                if len(name_header) > 4000:
                    raise ValueError("MCP request name exceeds the header size limit.")
                headers["Mcp-Name"] = name_header
            elif method == "resources/read" and isinstance(params.get("uri"), str):
                name_header = _mcp_header_value(params["uri"])
                if len(name_header) > 4000:
                    raise ValueError("MCP resource URI exceeds the header size limit.")
                headers["Mcp-Name"] = name_header
            elif method == "prompts/get" and isinstance(params.get("name"), str):
                name_header = _mcp_header_value(params["name"])
                if len(name_header) > 4000:
                    raise ValueError("MCP prompt name exceeds the header size limit.")
                headers["Mcp-Name"] = name_header
        else:
            headers["MCP-Protocol-Version"] = self.protocol_version
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
        if self._oauth_authorized:
            try:
                token = oauth_access_token(self.spec.url, None, self.oauth_port, self.spec.oauth_client_id,
                                           self.event_callback, configured_client_metadata_url=self.spec.oauth_client_metadata_url)
                self._set_oauth_token(token)
            except ValueError as exc:
                raise ValueError(self._redact(str(exc))) from exc
            headers["Authorization"] = self.headers["Authorization"]
        request = urllib.request.Request(self.spec.url, data=encoded, headers=headers, method="POST")
        if not PROVIDER_SLOTS.acquire(timeout=2):
            raise ValueError("BrainCellGPT is busy handling provider and MCP requests. Try again shortly.")
        oauth_challenge = None
        scope_upgrade = False
        try:
            try:
                with open_provider_request(request, timeout=MCP_TIMEOUT_SECONDS, pinned_addresses=self.addresses) as response:
                    returned_session = response.headers.get("Mcp-Session-Id")
                    if returned_session:
                        if len(returned_session) > 200 or any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in returned_session):
                            raise ValueError("MCP server returned an invalid session ID.")
                        self.session_id = returned_session
                    if request_id is None:
                        raw = response.read(MAX_FRAME_BYTES + 1)
                        if len(raw) > MAX_FRAME_BYTES:
                            raise ValueError("MCP server response exceeds the size limit.")
                        return None
                    return self._decode_response(response, request_id)
            except urllib.error.HTTPError as exc:
                challenge = exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
                needs_oauth = exc.code == 401 or (exc.code == 403 and requires_scope_upgrade(challenge))
                if (needs_oauth and not _oauth_retry
                        and not any(name.lower() == "authorization" for name in (self.spec.headers or {}))):
                    oauth_challenge = challenge
                    scope_upgrade = exc.code == 403
                    exc.close()
                else:
                    if exc.code in (400, 404, 405):
                        body = exc.read(MAX_FRAME_BYTES + 1)
                        try:
                            error_payload = json.loads(body.decode("utf-8")) if len(body) <= MAX_FRAME_BYTES else {}
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            error_payload = {}
                        rpc_error = error_payload.get("error") if isinstance(error_payload, dict) else None
                        error_code = rpc_error.get("code") if isinstance(rpc_error, dict) else None
                        if self._connecting and error_code == -32601:
                            raise _MethodNotFound("MCP server does not support server discovery.") from exc
                        if self._connecting:
                            raise _LegacyFallback from exc
                    raise ValueError(f"MCP HTTP request failed with status {exc.code}.") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise ValueError("Could not reach the configured remote MCP server.") from exc
        finally:
            PROVIDER_SLOTS.release()
        if oauth_challenge is not None:
            try:
                token = oauth_access_token(
                    self.spec.url, oauth_challenge, self.oauth_port, self.spec.oauth_client_id, self.event_callback,
                    force_refresh=not scope_upgrade, force_reauthorize=scope_upgrade,
                    configured_client_metadata_url=self.spec.oauth_client_metadata_url,
                )
            except ValueError as exc:
                raise ValueError(self._redact(str(exc))) from exc
            self._set_oauth_token(token)
            return self._post(message, request_id, extra_headers, _oauth_retry=True)

    def _request_result(self, method: str, params: dict | None = None, extra_headers: dict[str, str] | None = None) -> dict:
        request_id = self._next_id
        self._next_id += 1
        request_params = dict(params or {})
        if self.era == "modern":
            request_params["_meta"] = self._meta()
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": request_params}
        response = self._post(message, request_id, extra_headers)
        if not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise ValueError("MCP server returned a malformed JSON-RPC response.")
        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict) and error.get("code") == -32601:
                raise _MethodNotFound(str(error.get("message", "Method not found."))[:300])
            message = error.get("message", "MCP request failed.") if isinstance(error, dict) else "MCP request failed."
            raise ValueError(str(self._redact(str(message)))[:500])
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError("MCP server returned an invalid result object.")
        return self._redact(result)

    def connect(self) -> None:
        self.era = "modern"
        self._connecting = True
        try:
            try:
                result = self._request_result("server/discover", {})
                supported = result.get("supportedVersions", [])
                if not isinstance(supported, list) or MODERN_VERSION not in supported:
                    raise _MethodNotFound("Remote MCP server does not advertise the modern protocol version.")
                capabilities = result.get("capabilities", {})
                self.capabilities = capabilities if isinstance(capabilities, dict) else {}
                self._next_id = 1
            except _MethodNotFound:
                # Discovery is optional in the stateless revision. Probe tools/list
                # before falling back to the legacy initialize handshake.
                try:
                    result = self._request_result("tools/list", {})
                    if not isinstance(result.get("tools"), list):
                        raise ValueError("MCP server returned an invalid tools/list result.")
                    self.capabilities = {"tools": {}}
                except (_LegacyFallback, _MethodNotFound):
                    self.era = "legacy"
                    self._next_id = 1
                    self.connect_legacy()
            except _LegacyFallback:
                self.era = "legacy"
                self._next_id = 1
                self.connect_legacy()
        finally:
            self._connecting = False

    def connect_legacy(self) -> None:
        result = self._request_result("initialize", {
            "protocolVersion": LEGACY_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "BrainCellGPT", "version": "0.1.0"},
        })
        negotiated = result.get("protocolVersion")
        capabilities = result.get("capabilities")
        if negotiated != LEGACY_VERSION or not isinstance(capabilities, dict):
            raise ValueError("Remote MCP server returned invalid initialization details.")
        self.protocol_version = negotiated
        self.capabilities = capabilities
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)

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

    def call_tool(self, tool_name: str, arguments: dict, input_schema: dict | None = None) -> dict:
        extra_headers = {}
        if self.era == "modern" and isinstance(input_schema, dict):
            annotations = _mcp_header_annotations(input_schema)
            if annotations is None:
                raise ValueError("MCP server tool schema contains invalid HTTP header annotations.")
            for path, header_name, value_type in annotations:
                value: Any = arguments
                for key in path:
                    value = value.get(key) if isinstance(value, dict) else None
                if value is None:
                    continue
                if value_type == "string" and not isinstance(value, str):
                    raise ValueError("MCP header annotated argument must be a string.")
                if value_type == "integer" and (type(value) is not int or abs(value) > (2**53 - 1)):
                    raise ValueError("MCP header annotated integer is outside the safe range.")
                if value_type == "boolean" and type(value) is not bool:
                    raise ValueError("MCP header annotated argument must be a boolean.")
                header_value = _mcp_header_value(str(value).lower() if type(value) is bool else str(value))
                if len(header_value) > 4000:
                    raise ValueError("MCP header annotated argument exceeds the size limit.")
                extra_headers["Mcp-Param-" + header_name] = header_value
        return self._request_result("tools/call", {"name": tool_name, "arguments": arguments}, extra_headers)

    def list_resources(self, cursor: str | None = None) -> dict:
        params = {"cursor": cursor} if cursor else {}
        return self._request_result("resources/list", params)

    def read_resource(self, uri: str) -> dict:
        return self._request_result("resources/read", {"uri": uri})

    def close(self) -> None:
        if self.era != "legacy" or not self.session_id:
            return
        headers = {**self.headers, "Mcp-Session-Id": self.session_id}
        request = urllib.request.Request(self.spec.url, headers=headers, method="DELETE")
        if not PROVIDER_SLOTS.acquire(timeout=1):
            return
        try:
            try:
                with open_provider_request(request, timeout=3, pinned_addresses=self.addresses) as response:
                    response.read(MAX_FRAME_BYTES + 1)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
                pass
        finally:
            PROVIDER_SLOTS.release()

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()


class _MethodNotFound(Exception):
    pass


class _LegacyFallback(ValueError):
    pass


def _connect_server(spec: MCPServerSpec, environ: dict[str, str], cwd: Path, oauth_port: int = 8000, event_callback=None):
    if spec.url:
        client = _StreamableHTTPClient(spec, oauth_port, event_callback)
        client.connect()
        return client
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


def discover_tools(environ: dict[str, str], cwd: Path, event_callback=None) -> tuple[list[dict], dict[str, MCPToolRoute]]:
    tools = []
    routes: dict[str, MCPToolRoute] = {}
    specs = load_server_specs(environ)
    total_schema_bytes = 0
    for spec in specs:
        try:
            with _connect_server(spec, environ, cwd) as client:
                for raw_tool in client.list_tools():
                    remote_http = isinstance(client, _StreamableHTTPClient) and client.era == "modern"
                    normalized = _normalize_tool(spec.name, raw_tool, remote_http=remote_http)
                    if normalized is None:
                        continue
                    name = normalized["function"]["name"]
                    if name in routes:
                        continue
                    size = _tool_size(normalized)
                    if total_schema_bytes + size > MAX_TOTAL_TOOL_SCHEMA_BYTES:
                        return tools, routes
                    total_schema_bytes += size
                    routes[name] = MCPToolRoute(spec, raw_tool["name"], input_schema=raw_tool["inputSchema"])
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
        if isinstance(client, _StreamableHTTPClient):
            input_schema = None
            if client.era == "modern":
                for raw_tool in client.list_tools():
                    if isinstance(raw_tool, dict) and raw_tool.get("name") == tool_name:
                        if _normalize_tool(spec.name, raw_tool, remote_http=True) is None:
                            raise ValueError("Remote MCP tool has an invalid HTTP header schema.")
                        input_schema = raw_tool["inputSchema"]
                        break
                if input_schema is None:
                    raise ValueError("Remote MCP tool is no longer available.")
            result = client.call_tool(tool_name, arguments, input_schema)
        else:
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


def _bounded_json_result(value: dict) -> dict:
    encoded = json.dumps(value)
    if len(encoded.encode("utf-8", errors="replace")) <= MAX_CALL_RESULT_BYTES:
        return value
    return {"text": encoded[:MAX_CALL_RESULT_BYTES]}


class MCPToolRouter:
    """Keep configured stdio servers alive for one complete agent run."""

    def __init__(self, environ: dict[str, str], cwd: Path, event_callback=None, oauth_port: int = 8000):
        self.environ = environ
        self.cwd = cwd
        self.oauth_port = oauth_port
        self.event_callback = event_callback
        self.tools: list[dict] = []
        self.routes: dict[str, MCPToolRoute] = {}
        self._clients: list[Any] = []
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
                client = _connect_server(spec, environ, cwd, self.oauth_port, event_callback)
                self._clients.append(client)
                for raw_tool in client.list_tools():
                    remote_http = isinstance(client, _StreamableHTTPClient) and client.era == "modern"
                    normalized = _normalize_tool(spec.name, raw_tool, remote_http=remote_http)
                    if normalized is None:
                        continue
                    name = normalized["function"]["name"]
                    if name in self.routes:
                        continue
                    size = _tool_size(normalized)
                    if total_schema_bytes + size > MAX_TOTAL_TOOL_SCHEMA_BYTES:
                        return
                    total_schema_bytes += size
                    self.routes[name] = MCPToolRoute(spec, raw_tool["name"], input_schema=raw_tool["inputSchema"])
                    self.tools.append(normalized)
                    if len(self.tools) >= MAX_TOOLS:
                        return
                if "resources" in client.capabilities and len(self.tools) < MAX_TOOLS:
                    resource_functions = _resource_tool_definitions(spec.name)
                    for function in resource_functions:
                        name = function["function"]["name"]
                        if name in self.routes:
                            continue
                        size = _tool_size(function)
                        if total_schema_bytes + size > MAX_TOTAL_TOOL_SCHEMA_BYTES:
                            return
                        total_schema_bytes += size
                        operation = "resources_list" if name.endswith("__resource_list") else "resource_read"
                        self.routes[name] = MCPToolRoute(spec, "", operation)
                        self.tools.append(function)
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
        spec = route.server
        client = next((item for item in self._clients if item.spec.name == spec.name), None)
        if client is None:
            raise ValueError("MCP server is unavailable in this run.")
        if route.operation == "resources_list":
            result = client.list_resources(arguments.get("cursor"))
            resources = result.get("resources", [])
            if not isinstance(resources, list):
                raise ValueError("MCP server returned an invalid resources/list result.")
            return _bounded_json_result({"resources": resources[:100], "nextCursor": result.get("nextCursor")})
        if route.operation == "resource_read":
            uri = arguments.get("uri")
            if not isinstance(uri, str) or not uri or len(uri) > 4000:
                raise ValueError("Provide a resource URI up to 4,000 characters.")
            result = client.read_resource(uri)
            contents = result.get("contents", [])
            text_parts = []
            if isinstance(contents, list):
                for item in contents:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        text_parts.append(item["text"])
            return {"uri": uri, "text": "\n".join(text_parts)[:MAX_CALL_RESULT_BYTES]}
        if isinstance(client, _StreamableHTTPClient):
            result = client.call_tool(route.name, arguments, route.input_schema)
        else:
            result = client.call_tool(route.name, arguments)
        return normalize_call_result(result)

    def close(self) -> None:
        for client in reversed(self._clients):
            client.close()
        self._clients.clear()

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()
