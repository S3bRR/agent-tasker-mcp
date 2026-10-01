import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest

from test_remote import INITIALIZE_PARAMS, PEER


class MCPProcess:
    def __init__(self, env=None):
        root = Path(__file__).resolve().parents[1]
        self.directory = tempfile.TemporaryDirectory()
        self._buffer, self._next_id = b"", 10
        config = Path(self.directory.name) / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"peer": PEER}}))
        self.process = subprocess.Popen([sys.executable, "-m", "agent_tasker_mcp.server", "--workers", "1", "--mcp-config", str(config)],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})})

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()
        self.directory.cleanup()

    def send_raw(self, payload):
        self.process.stdin.write(payload + "\n")
        self.process.stdin.flush()

    def send(self, payload):
        self.send_raw(json.dumps(payload))

    def read(self, timeout=1):
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buffer:
            ready, _, _ = select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                return None
            data = os.read(self.process.stdout.fileno(), 65536)
            if not data:
                return None
            self._buffer += data
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line)

    def request(self, payload, timeout=1):
        self.send(payload)
        response = self.read(timeout)
        if response is None:
            raise AssertionError("Expected MCP response")
        return response

    def initialize(self, version="2025-06-18"):
        response = self.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {**INITIALIZE_PARAMS, "protocolVersion": version}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def call(self, name, arguments, timeout=1):
        self._next_id += 1
        return self.request({"jsonrpc": "2.0", "id": self._next_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, timeout)["result"]


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.server = MCPProcess()
        self.addCleanup(self.server.close)

    def test_parse_invalid_requests_and_empty_batch(self):
        self.server.send_raw("{")
        self.assertEqual(self.server.read()["error"]["code"], -32700)
        for message in ([], {"id": 1, "method": "ping"}, 42):
            self.assertEqual(self.server.request(message)["error"]["code"], -32600)
        for params in ([], False, 0, "bad", None):
            response = self.server.request({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": params})
            self.assertEqual(response["error"]["code"], -32602)

    def test_lifecycle_and_notifications(self):
        def tools():
            return self.server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(tools()["error"]["code"], -32002)
        self.server.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INITIALIZE_PARAMS})
        self.assertEqual(tools()["error"]["code"], -32002)
        self.server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(self.server.read(0.1))
        catalog = tools()["result"]["tools"]
        self.assertEqual([tool["name"] for tool in catalog], ["execute", "execute_batch", "get_batch", "cancel_batch", "list_remote_tools"])

    def test_ping_and_unknown_method_tool(self):
        ping = self.server.request({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(ping["result"], {})
        self.server.initialize()
        response = self.server.request({"jsonrpc": "2.0", "id": 2, "method": "unknown"})
        self.assertEqual(response["error"]["code"], -32601)
        response = self.server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "unknown"}})
        self.assertEqual(response["error"]["code"], -32602)

    def test_jsonrpc_arrays_are_not_mcp_execute_batches(self):
        self.server.initialize()
        result = self.server.request([
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "execute", "arguments": {"task_type": "mcp_tool", "server": "peer", "tool": "echo", "arguments": {"value": 42}}}},
        ])
        self.assertEqual(result["error"]["code"], -32600)
        self.assertIsNone(self.server.read(0.1))

    def test_background_batch_keeps_stdio_responsive(self):
        self.server.initialize()
        submitted = self.server.call("execute_batch", {"wait": False, "tasks": [{"task_type": "mcp_tool", "server": "peer", "tool": "echo", "arguments": {"delay": 1.5, "value": 42}}]})
        self.assertFalse(submitted["isError"])
        batch_id = submitted["structuredContent"]["batch_id"]
        ping = self.server.request({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(ping["result"], {})
        progress = self.server.call("get_batch", {"batch_id": batch_id})["structuredContent"]
        self.assertEqual(progress["status"], "running")
        self.assertEqual(progress["results"], [])
        completed = self.server.call("get_batch", {"batch_id": batch_id, "wait_seconds": 5}, timeout=6)
        self.assertFalse(completed["isError"])
        self.assertEqual(completed["structuredContent"]["results"][0]["result"]["structuredContent"]["value"], 42)

    def test_remote_errors_and_cancel_via_mcp(self):
        self.server.initialize()
        failed = self.server.call("execute", {"task_type": "mcp_tool", "server": "peer", "tool": "fail"})
        self.assertTrue(failed["isError"])
        self.assertTrue(failed["structuredContent"]["task"]["result"]["isError"])
        batch = self.server.call("execute_batch", {"wait": False, "tasks": [{"name": str(i), "task_type": "mcp_tool", "server": "peer", "tool": "echo", "arguments": {"delay": 0.1}} for i in range(3)]})["structuredContent"]
        cancelling = self.server.call("cancel_batch", {"batch_id": batch["batch_id"]})
        self.assertFalse(cancelling["isError"])
        final = self.server.call("get_batch", {"batch_id": batch["batch_id"], "wait_seconds": 5}, timeout=6)["structuredContent"]
        self.assertEqual(final["status"], "cancelled")

    def test_remote_discovery_and_removed_local_execution(self):
        self.server.initialize()
        self.assertEqual(self.server.call("list_remote_tools", {})["structuredContent"], {"servers": ["peer"]})
        self.assertEqual(len(self.server.call("list_remote_tools", {"server": "peer"})["structuredContent"]["tools"]), 6)
        for kind in ("python_code", "shell_command", "file_read", "file_write", "http_request"):
            self.assertTrue(self.server.call("execute", {"task_type": kind})["isError"])

    def test_initialize_validation_and_version_negotiation(self):
        for params in ({"protocolVersion": "2025-11-25"}, {**INITIALIZE_PARAMS, "capabilities": []}, {**INITIALIZE_PARAMS, "clientInfo": {"name": "test"}}):
            response = self.server.request({"jsonrpc": "2.0", "id": 90, "method": "initialize", "params": params})
            self.assertEqual(response["error"]["code"], -32602)
        initialized = self.server.initialize(version="unsupported")
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(initialized["result"]["capabilities"], {"tools": {"listChanged": False}})
        self.assertNotIn("tasks", initialized["result"]["capabilities"])

    def test_invalid_ids_notifications_and_stray_responses(self):
        for request_id in (None, True, 1.5, [], {}):
            response = self.server.request({"jsonrpc": "2.0", "id": request_id, "method": "ping"})
            self.assertEqual(response["error"]["code"], -32600)
            self.assertNotIn("id", response)
        for message in ({"jsonrpc": "2.0", "method": "notifications/initialized", "params": []},
                        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": []}},
                        {"jsonrpc": "2.0", "id": 99, "result": {}}):
            self.server.send(message)
        self.assertIsNone(self.server.read(0.1))
        self.server.send_raw('{"jsonrpc":"2.0","id":3,"method":"ping","params":{"bad":NaN}}')
        self.assertEqual(self.server.read()["error"]["code"], -32700)

    def test_tool_argument_errors_are_actionable_execution_errors(self):
        self.server.initialize()
        for name, arguments in (("execute", {"task_type": "web_scrape", "url": None}),
                                ("execute", {"task_type": "mcp_tool", "server": "peer", "tool": "echo", "arguments": None}),
                                ("execute", {"task_type": "web_scrape", "url": "https://example.com", "typo": 1}),
                                ("execute_batch", {"tasks": [], "concurrency": None}),
                                ("get_batch", {"batch_id": "missing", "typo": 1})):
            result = self.server.call(name, arguments)
            self.assertTrue(result["isError"])
            self.assertIn("error", result["structuredContent"])
            self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])

    def test_legacy_client_gets_text_without_new_structured_fields(self):
        self.server.initialize(version="2024-11-05")
        tools = self.server.request({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})["result"]["tools"]
        self.assertNotIn("outputSchema", tools[0])
        self.assertNotIn("annotations", tools[0])
        result = self.server.call("list_remote_tools", {})
        self.assertNotIn("structuredContent", result)
        self.assertEqual(json.loads(result["content"][0]["text"]), {"servers": ["peer"]})

    def test_legacy_versions_still_receive_required_jsonrpc_batches(self):
        self.server.initialize(version="2025-03-26")
        response = self.server.request([
            {"jsonrpc": "2.0", "id": 20, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 999}},
            {"jsonrpc": "2.0", "id": 21, "method": "tools/list"},
        ])
        self.assertEqual([item["id"] for item in response], [20, 21])
        self.assertNotIn("outputSchema", response[1]["result"]["tools"][0])

    def test_synchronous_calls_allow_ping_and_standard_cancellation(self):
        self.server.initialize()
        self.server.send({"jsonrpc": "2.0", "id": 100, "method": "tools/call", "params": {"name": "execute_batch", "arguments": {
            "tasks": [{"name": str(i), "task_type": "mcp_tool", "server": "peer", "tool": "echo", "arguments": {"delay": 1}} for i in range(3)]}}})
        ping = self.server.request({"jsonrpc": "2.0", "id": 101, "method": "ping"}, timeout=0.5)
        self.assertEqual(ping["id"], 101)
        self.server.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 100}})
        self.assertIsNone(self.server.read(1.5))
        self.assertFalse(self.server.call("list_remote_tools", {})["isError"])

    def test_inflight_request_limit_does_not_block_control_messages(self):
        self.server.initialize()
        self.server.call("list_remote_tools", {"server": "peer"})  # Warm the child connection.
        for request_id in (100, 101, 102):
            self.server.send({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {
                "name": "execute", "arguments": {"task_type": "mcp_tool", "server": "peer", "tool": "echo", "arguments": {"delay": 1}}}})
        overloaded = self.server.read(0.5)
        self.assertEqual(overloaded["id"], 102)
        self.assertEqual(overloaded["error"]["code"], -32000)
        for request_id in (100, 101):
            self.server.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": request_id}})
        self.assertIsNone(self.server.read(1.5))
        self.assertEqual(self.server.request({"jsonrpc": "2.0", "id": 103, "method": "ping"})["result"], {})

    def test_stdio_is_utf8_even_when_python_io_defaults_to_ascii(self):
        server = MCPProcess(env={"PYTHONIOENCODING": "ascii"})
        self.addCleanup(server.close)
        server.initialize()
        value = "café 你好"
        result = server.call("execute", {"task_type": "mcp_tool", "server": "peer", "tool": "echo", "arguments": {"value": value}})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["task"]["result"]["structuredContent"]["value"], value)

    def test_oversized_line_is_drained_without_corrupting_next_message(self):
        self.server.send_raw("x" * 8_000_001)
        self.assertEqual(self.server.read()["error"]["code"], -32600)
        self.assertEqual(self.server.request({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"], {})

    def test_native_task_requests_are_not_silently_accepted(self):
        self.server.initialize(version="2025-11-25")
        response = self.server.request({"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {
            "name": "list_remote_tools", "arguments": {}, "task": {"ttl": 1000}}})
        self.assertEqual(response["error"]["code"], -32602)

    def test_module_execution_has_no_runtime_warning(self):
        result = subprocess.run([sys.executable, "-m", "agent_tasker_mcp.server", "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("RuntimeWarning", result.stderr)
