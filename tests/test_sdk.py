"""Optional real-SDK interoperability; install the project's test extra to run."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest

HAS_SDK = importlib.util.find_spec("mcp") is not None and importlib.util.find_spec("jsonschema") is not None
if HAS_SDK:
    from jsonschema import Draft202012Validator
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client


@unittest.skipUnless(HAS_SDK, "Install .[test] for official MCP SDK/schema interoperability tests")
class SDKTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = Path(directory.name) / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"peer": {
            "command": sys.executable, "args": [str(root / "tests/fixtures/sdk_peer.py")], "allowed_tools": ["echo"]
        }}}))
        self.params = StdioServerParameters(command=sys.executable,
            args=["-m", "agent_tasker_mcp.server", "--mcp-config", str(config)], cwd=str(root))

    @asynccontextmanager
    async def connect(self):
        # AnyIO cancel scopes must be entered/exited in the same task, not unittest setup/cleanup tasks.
        async with stdio_client(self.params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                self.session = session
                self.initialized = await session.initialize()
                self.catalog = await session.list_tools()
                yield session

    async def test_initialization_schemas_errors_and_annotations(self):
        async with self.connect():
            self.assertEqual(self.initialized.protocolVersion, "2025-11-25")
            self.assertIsNone(self.initialized.capabilities.tasks)
            tools = {tool.name: tool for tool in self.catalog.tools}
            for tool in tools.values():
                Draft202012Validator.check_schema(tool.inputSchema)
                Draft202012Validator.check_schema(tool.outputSchema)
            self.assertFalse(tools["execute"].annotations.readOnlyHint)
            self.assertTrue(tools["execute"].annotations.destructiveHint)
            self.assertTrue(tools["get_batch"].annotations.readOnlyHint)
            self.assertTrue(tools["cancel_batch"].annotations.idempotentHint)
            schema = tools["execute"].inputSchema
            self.assertFalse(Draft202012Validator(schema).is_valid({"task_type": "web_scrape"}))
            self.assertFalse(Draft202012Validator(schema).is_valid({"task_type": "mcp_tool", "server": "peer"}))
            error = await self.session.call_tool("execute", {"task_type": "web_scrape"})
            self.assertTrue(error.isError)
            Draft202012Validator(tools["execute"].outputSchema).validate(error.structuredContent)
            self.assertEqual(json.loads(error.content[0].text), error.structuredContent)

    async def test_five_remote_calls_and_background_collection(self):
        async with self.connect():
            discovery = await self.session.call_tool("list_remote_tools", {"server": "peer"})
            self.assertEqual([tool["name"] for tool in discovery.structuredContent["tools"]], ["echo"])
            batch = await self.session.call_tool("execute_batch", {"wait": False, "concurrency": 5, "tasks": [
                {"name": str(i), "task_type": "mcp_tool", "server": "peer", "tool": "echo", "arguments": {"value": i, "parties": 5}}
                for i in range(5)]})
            result = await self.session.call_tool("get_batch", {"batch_id": batch.structuredContent["batch_id"], "wait_seconds": 5})
            self.assertFalse(result.isError, result)
            self.assertEqual(result.structuredContent["status"], "completed")
            self.assertEqual([task["result"]["structuredContent"]["value"] for task in result.structuredContent["results"]], list(range(5)))

    async def test_searxng_markdown_context_and_background_pages_over_stdio(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/search?"):
                    body = json.dumps({"results": [{"title": "Native page", "url": f"http://127.0.0.1:{self.server.server_port}/page", "content": "snippet"}], "unresponsive_engines": []}).encode()
                    media = "application/json"
                else:
                    body, media = '# Native 🌍\n\nVerbatim `<T>` and readable content.'.encode(), "text/markdown"
                self.send_response(200)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(lambda: thread.join(5))
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_port}"
        self.params.args.extend(["--search-provider", "searxng", "--searxng-url", base, "--reader-fallback", "jina"])
        async with self.connect():
            tools = {tool.name: tool for tool in self.catalog.tools}
            self.assertEqual(tools["execute"].inputSchema["properties"]["reader_fallback"]["default"], "jina")
            result = await self.session.call_tool("execute", {"task_type": "discovery_search", "query": "native page", "fetch_top_results": 1})
            self.assertFalse(result.isError, result)
            Draft202012Validator(tools["execute"].outputSchema).validate(result.structuredContent)
            context = result.structuredContent["task"]["result"]["results"][0]["page_context"]
            self.assertEqual((context["source"], context["extraction"]), ("direct", "markdown"))
            self.assertIn("<T>", context["text"])
            batch = await self.session.call_tool("execute_batch", {"wait": False, "concurrency": 5, "tasks": [
                {"name": str(i), "task_type": "web_scrape", "url": base + "/page"} for i in range(5)]})
            collected = await self.session.call_tool("get_batch", {"batch_id": batch.structuredContent["batch_id"], "wait_seconds": 5})
            self.assertFalse(collected.isError, collected)
            self.assertEqual(len(collected.structuredContent["results"]), 5)
            self.assertTrue(all(task["result"]["title"] == "Native 🌍" for task in collected.structuredContent["results"]))

    async def test_traditional_concurrent_tool_requests_leave_ping_responsive(self):
        async with self.connect():
            await self.session.call_tool("list_remote_tools", {"server": "peer"})
            calls = [asyncio.create_task(self.session.call_tool("execute", {"task_type": "mcp_tool", "server": "peer", "tool": "echo",
                      "arguments": {"value": i, "parties": 5, "delay": 0.1}})) for i in range(5)]
            await asyncio.wait_for(self.session.send_ping(), timeout=0.5)
            results = await asyncio.wait_for(asyncio.gather(*calls), timeout=6)
            self.assertTrue(all(not result.isError for result in results))
            self.assertEqual([result.structuredContent["task"]["result"]["structuredContent"]["value"] for result in results], list(range(5)))
