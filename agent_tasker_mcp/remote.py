"""Tools-only stdio MCP client: one process/reader per server, multiplexed calls."""

from concurrent.futures import Future, TimeoutError
import os
import subprocess
from threading import Lock, Thread
import uuid

from .protocol import PROTOCOLS, dumps, loads, valid_id, validate_tool_result
from .version import package_version


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("MCP config must map server names to stdio configurations")
    for name, spec in config.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(spec, dict):
            raise ValueError("Invalid MCP server configuration")
        if not isinstance(spec.get("command"), str) or not spec["command"].strip():
            raise ValueError(f"MCP server '{name}' needs a command (stdio only)")
        for key in ("args", "allowed_tools"):
            if key in spec and (not isinstance(spec[key], list) or not all(isinstance(v, str) for v in spec[key])):
                raise ValueError(f"MCP '{key}' must be an array of strings")
        env = spec.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ValueError("MCP 'env' must map strings to strings")
        if "cwd" in spec and not isinstance(spec["cwd"], str):
            raise ValueError("MCP 'cwd' must be a string")
    return config


class StdioClient:
    def __init__(self, config):
        self.config, self.process = config, None
        self._start_lock, self._write_lock, self._lock = Lock(), Lock(), Lock()
        self._pending, self._broken = {}, None
        self._protocol_version = PROTOCOLS[0]
        self._reader = None
        self._closed = False

    def start(self):
        with self._start_lock:
            if self._closed:
                raise RuntimeError("Remote MCP client is closed")
            if self.process is not None:
                return
            self.process = subprocess.Popen(
                [self.config["command"], *self.config.get("args", [])],
                cwd=self.config.get("cwd"), env={**os.environ, **self.config.get("env", {})},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
                # Inherit stderr: logs cannot fill an unread pipe or corrupt MCP stdout.
            )
            self._reader = Thread(target=self._read, daemon=True, name="tasker-mcp-reader")
            self._reader.start()
            try:
                initialized = self.request("initialize", {"protocolVersion": PROTOCOLS[0], "capabilities": {},
                    "clientInfo": {"name": "agent-tasker", "version": package_version()}}, timeout=30)
                capabilities, info = initialized.get("capabilities"), initialized.get("serverInfo")
                if (initialized.get("protocolVersion") not in PROTOCOLS or not isinstance(capabilities, dict)
                        or not isinstance(capabilities.get("tools"), dict) or not isinstance(info, dict)
                        or any(not isinstance(info.get(key), str) for key in ("name", "version"))):
                    raise RuntimeError("Remote MCP server has incompatible protocol or no tools capability")
                self._protocol_version = initialized["protocolVersion"]
                self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
            except Exception:
                self._stop()
                raise

    def _write(self, message):
        with self._write_lock:
            self.process.stdin.write(dumps(message) + "\n")
            self.process.stdin.flush()

    def _receive(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RuntimeError("Invalid remote MCP message")
        if "method" in message:
            if not isinstance(message["method"], str) or ("id" in message and not valid_id(message["id"])):
                raise RuntimeError("Invalid remote MCP request")
            if "id" in message:
                response = {"result": {}} if message["method"] == "ping" else {"error": {"code": -32601, "message": "Client capability not supported"}}
                self._write({"jsonrpc": "2.0", "id": message["id"], **response})
            return
        if not valid_id(message.get("id")) or (("error" in message) == ("result" in message)):
            raise RuntimeError("Invalid remote MCP response")
        with self._lock:
            future = self._pending.pop(message["id"], None)
        if future is not None:
            if "error" in message:
                future.set_exception(RuntimeError(f"Remote MCP error: {message['error']}"))
            elif isinstance(message.get("result"), dict):
                future.set_result(message["result"])
            else:
                future.set_exception(RuntimeError("Invalid remote MCP result"))

    def _read(self):
        error = "Remote MCP server disconnected"
        try:
            while True:
                line = self.process.stdout.readline(2_000_001)
                if not line:
                    break
                if len(line.encode("utf-8")) > 2_000_000:
                    raise RuntimeError("Remote MCP message exceeds 2MB")
                message = loads(line)
                if isinstance(message, list):
                    if not message or self._protocol_version >= "2025-06-18":
                        raise RuntimeError("JSON-RPC batches are not supported by this MCP version")
                    for item in message:
                        self._receive(item)
                else:
                    self._receive(message)
        except Exception as exc:
            error = str(exc)
        finally:
            with self._lock:
                self._broken = error
                pending, self._pending = self._pending, {}
            for future in pending.values():
                future.set_exception(RuntimeError(error))

    def request(self, method, params, *, timeout):
        request_id, future = uuid.uuid4().hex, Future()
        with self._lock:
            if self._broken:
                raise RuntimeError(self._broken)
            self._pending[request_id] = future
        try:
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            if method != "initialize":  # MCP explicitly forbids cancelling initialization.
                self._write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": request_id, "reason": "Request timed out"}})
            raise RuntimeError(f"Remote MCP request timed out after {timeout}s; it may already have executed") from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def _stop(self):
        if self.process is None:
            return
        self.process.stdin.close()
        for action in (None, self.process.terminate, self.process.kill):
            if action and self.process.poll() is None:
                action()
            try:
                self.process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue
        if self._reader is not None:
            self._reader.join(timeout=2)
        self.process.stdout.close()

    def close(self):
        with self._start_lock:
            self._closed = True
            self._stop()


class RemoteTools:
    def __init__(self, config=None):
        self.clients = {name: StdioClient(spec) for name, spec in validate_config({} if config is None else config).items()}

    def validate_call(self, server, tool):
        if server not in self.clients:
            raise ValueError(f"Unknown configured MCP server: {server}")
        allowed = self.clients[server].config.get("allowed_tools")
        if allowed is not None and tool not in allowed:
            raise ValueError(f"Remote tool '{tool}' is not allowed on '{server}'")

    def call(self, payload):
        self.validate_call(payload["server"], payload["tool"])
        client = self.clients[payload["server"]]
        client.start()
        # Never retry/deduplicate generic remote calls: they may have side effects.
        return validate_tool_result(client.request("tools/call", {"name": payload["tool"], "arguments": payload["arguments"]}, timeout=payload["timeout"]))

    def list_tools(self, server=None):
        if server is None:
            return {"servers": list(self.clients)}
        if server not in self.clients:
            raise ValueError(f"Unknown configured MCP server: {server}")
        client = self.clients[server]
        client.start()
        tools, seen, cursor = [], set(), None
        for _ in range(100):
            page = client.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=30)
            if not isinstance(page.get("tools"), list):
                raise RuntimeError("Invalid remote MCP tools/list result")
            tools.extend(page["tools"])
            cursor = page.get("nextCursor")
            if not cursor:
                allowed = client.config.get("allowed_tools")
                return {"server": server, "tools": [tool for tool in tools if allowed is None or tool.get("name") in allowed]}
            if not isinstance(cursor, str) or cursor in seen:
                break
            seen.add(cursor)
        raise RuntimeError("Remote tool pagination did not terminate")

    def close(self):
        for client in self.clients.values():
            client.close()
