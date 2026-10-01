import json
from pathlib import Path
import sys
import unittest

from agent_tasker_mcp.common import apply_output_mode
from agent_tasker_mcp.models import TaskType
from agent_tasker_mcp.remote import RemoteTools, StdioClient, validate_config
from agent_tasker_mcp.protocol import validate_tool_result
from unittest.mock import patch
from agent_tasker_mcp.server import AgentTasker, MCPServer

PEER = {"command": sys.executable, "args": [str(Path(__file__).parent / "fixtures" / "mcp_peer.py")]}
INITIALIZE_PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "1"}}


class RemoteTests(unittest.TestCase):
    def tasker(self, config=None):
        tasker = AgentTasker(max_workers=5, mcp_servers={"peer": PEER if config is None else config})
        self.addCleanup(tasker.close)
        return tasker

    def test_five_calls_share_one_process_and_match_out_of_order_responses(self):
        tasker = self.tasker()
        result = tasker.execute_tasks([(str(i), TaskType.MCP_TOOL, {"server": "peer", "tool": "echo", "arguments": {"value": i, "parties": 5, "delay": (5-i) / 100}}) for i in range(5)])
        self.assertEqual(result["completed"], 5, result)
        self.assertEqual([task["result"]["structuredContent"]["value"] for task in result["results"]], list(range(5)))
        client = tasker.remote.clients["peer"]
        pid = client.process.pid
        again = tasker.execute_tasks([("again", TaskType.MCP_TOOL, {"server": "peer", "tool": "echo"})])
        self.assertEqual(again["completed"], 1)
        self.assertEqual(client.process.pid, pid)
        self.assertFalse(client._pending)

    def test_identical_remote_calls_are_not_cached_or_coalesced(self):
        tasker = self.tasker()
        payload = {"server": "peer", "tool": "echo", "arguments": {"parties": 5}, "timeout": 8}
        result = tasker.execute_tasks([(str(i), TaskType.MCP_TOOL, payload) for i in range(5)])
        self.assertEqual(result["completed"], 5, result)

    def test_discovery_pagination_allowlist_and_no_launch_until_needed(self):
        tasker = self.tasker({**PEER, "allowed_tools": ["echo"]})
        self.assertIsNone(tasker.remote.clients["peer"].process)
        self.assertEqual(tasker.remote.list_tools(), {"servers": ["peer"]})
        self.assertIsNone(tasker.remote.clients["peer"].process)
        tools = tasker.remote.list_tools("peer")["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["echo"])
        self.assertIn("inputSchema", tools[0])
        with self.assertRaisesRegex(ValueError, "not allowed"):
            tasker.execute_tasks([("bad", TaskType.MCP_TOOL, {"server": "peer", "tool": "fail"})])
        with self.assertRaisesRegex(ValueError, "Unknown configured"):
            tasker.execute_tasks([("bad", TaskType.MCP_TOOL, {"server": "missing", "tool": "echo"})])

    def test_remote_errors_preserve_payload_and_compact_deduplicates_text(self):
        server = MCPServer(mcp_servers={"peer": PEER})
        self.addCleanup(server.close)
        server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INITIALIZE_PARAMS})
        server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "execute", "arguments": {
            "task_type": "mcp_tool", "server": "peer", "tool": "fail", "arguments": {"reason": "fixture"}, "output_mode": "full"}}})
        self.assertTrue(response["result"]["isError"])
        task = response["result"]["structuredContent"]["task"]
        self.assertTrue(task["result"]["isError"])
        self.assertIn("content", task["result"])
        compact = apply_output_mode({"status": "failed", "results": [task]}, "compact")["results"][0]
        self.assertTrue(compact["result"]["isError"])
        self.assertNotIn("content", compact["result"])

    def test_compact_preserves_non_text_content(self):
        tasker = self.tasker()
        result = tasker.execute_tasks([("binary", TaskType.MCP_TOOL, {"server": "peer", "tool": "binary"})])
        compact = apply_output_mode(result, "compact")["results"][0]["result"]
        self.assertEqual(compact["content"][0]["type"], "image")

    def test_compact_does_not_discard_additional_text_warnings(self):
        task = {"name": "remote", "task_type": "mcp_tool", "status": "completed", "result": {
            "structuredContent": {"value": 42}, "content": [
                {"type": "text", "text": '{"value": 42}'}, {"type": "text", "text": "Important warning"}]}}
        compact = apply_output_mode({"status": "completed", "results": [task]}, "compact")
        self.assertEqual(compact["results"][0]["result"]["content"], [{"type": "text", "text": "Important warning"}])

    def test_timeout_sends_cancellation_and_does_not_replay_call(self):
        tasker = self.tasker()
        result = tasker.execute_tasks([("hang", TaskType.MCP_TOOL, {"server": "peer", "tool": "hang", "timeout": 1})])
        self.assertEqual(result["failed"], 1)
        self.assertIn("may already have executed", result["results"][0]["error"])
        inspect = tasker.execute_tasks([("inspect", TaskType.MCP_TOOL, {"server": "peer", "tool": "inspect"})])
        self.assertEqual(inspect["results"][0]["result"]["structuredContent"]["cancelled"], 1)
        self.assertFalse(tasker.remote.clients["peer"]._pending)

    def test_disconnect_fails_pending_call_and_closes_process(self):
        tasker = self.tasker()
        result = tasker.execute_tasks([("disconnect", TaskType.MCP_TOOL, {"server": "peer", "tool": "disconnect"})])
        self.assertEqual(result["failed"], 1)
        self.assertIn("disconnected", result["results"][0]["error"])
        tasker.close()
        self.assertIsNotNone(tasker.remote.clients["peer"].process.poll())

    def test_remote_legacy_negotiation_accepts_batched_responses(self):
        tasker = self.tasker({**PEER, "env": {"PEER_PROTOCOL_VERSION": "2025-03-26", "PEER_BATCH_RESPONSES": "1"}})
        result = tasker.execute_tasks([("echo", TaskType.MCP_TOOL, {"server": "peer", "tool": "echo", "arguments": {"value": 42}})])
        self.assertEqual(result["completed"], 1, result)
        self.assertEqual(result["results"][0]["result"]["structuredContent"]["value"], 42)

    def test_invalid_remote_content_is_rejected_before_forwarding(self):
        for result in ({}, {"content": None}, {"content": [{"type": "text", "text": 42}]},
                       {"content": [], "isError": "false"}, {"content": [], "structuredContent": []},
                       {"content": [{"type": "unknown"}]}, {"content": [{"type": "resource", "resource": {}}]}):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                validate_tool_result(result)

    def test_initialization_timeout_is_never_cancelled(self):
        client = StdioClient(PEER)
        with patch.object(client, "_write") as write, self.assertRaisesRegex(RuntimeError, "timed out"):
            client.request("initialize", {}, timeout=0)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(write.call_args.args[0]["method"], "initialize")
        self.assertFalse(client._pending)

    def test_native_binary_content_is_exposed_to_the_outer_mcp_client(self):
        server = MCPServer(mcp_servers={"peer": PEER})
        self.addCleanup(server.close)
        server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INITIALIZE_PARAMS})
        server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "execute", "arguments": {
            "task_type": "mcp_tool", "server": "peer", "tool": "binary"}}})
        self.assertEqual(response["result"]["content"][1]["type"], "image")
        self.assertFalse(response["result"]["isError"])

    def test_invalid_config_is_rejected(self):
        for config in ([], {"peer": {}}, {"peer": {"command": "python", "env": []}}, {"peer": {"command": "python", "args": "-m"}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)
        with self.assertRaises(ValueError):
            RemoteTools([])
