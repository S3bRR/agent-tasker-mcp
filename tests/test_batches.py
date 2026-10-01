from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

from agent_tasker_mcp.batches import BatchManager
from agent_tasker_mcp.models import TaskType
from agent_tasker_mcp.registry import validate_payload
from agent_tasker_mcp.server import AgentTasker, MCPServer


class _SearchHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path == "/search":
            self.server.barrier.wait(5)
            query = parse_qs(urlparse(self.path).query)["q"][0]
            body = json.dumps({"items": [{"title": query, "url": f"http://{self.headers['Host']}/page?" + urlencode({"q": query})}]}).encode()
        else:
            body = b"<title>Local context</title><p>Fetched result text</p>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _SearchServer(ThreadingHTTPServer):
    request_queue_size = 32


class BatchTests(unittest.TestCase):
    def server(self, workers=10):
        server = MCPServer(max_workers=workers)
        self.addCleanup(server.close)
        return server

    @staticmethod
    def tasks(count):
        return [{"name": str(i), "task_type": "web_scrape", "url": f"https://example.com/{i}"} for i in range(count)]

    def mock_fetch(self, worker):
        context = patch("agent_tasker_mcp.server.execute_web_scrape", side_effect=lambda payload, **kwargs: worker(payload))
        context.start()
        self.addCleanup(context.stop)

    def collect(self, server, batch, **options):
        return server._get_batch({"batch_id": batch["batch_id"], "wait_seconds": 5, "output_mode": "full", **options})

    def test_ten_real_searches_and_context_on_one_worker_pool(self):
        httpd = _SearchServer(("127.0.0.1", 0), _SearchHandler)
        httpd.barrier = threading.Barrier(10)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(lambda: thread.join(5))
        self.addCleanup(httpd.shutdown)
        providers = [{"name": "local", "url_template": f"http://127.0.0.1:{httpd.server_port}/search?q={{query_encoded}}",
                      "items_path": "items", "title_path": "title", "url_path": "url"}]
        server = MCPServer(providers=providers)
        self.addCleanup(server.close)
        self.assertFalse(hasattr(server.tasker, "search_executor"))
        batch = server._execute_batch({"tasks": [{"name": str(i), "task_type": "discovery_search", "query": f"query {i}",
                                                 "fetch_top_results": 1, "retries": 0} for i in range(10)], "wait": False, "concurrency": 10})
        result = self.collect(server, batch)
        self.assertEqual(result["completed"], 10, result)
        self.assertEqual([item["result"]["query"] for item in result["results"]], [f"query {i}" for i in range(10)])
        for task in result["results"]:
            self.assertIn("Fetched result text", task["result"]["results"][0]["page_context"]["text"])

    def test_partial_progress_and_smaller_compact_output(self):
        server = self.server()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def fetch(payload):
            if payload["url"].endswith("/1"):
                started.set()
                release.wait(5)
            return {"text": "context", "final_url": payload["url"]}

        self.mock_fetch(fetch)
        batch = server._execute_batch({"tasks": self.tasks(3), "concurrency": 1, "wait": False})
        self.assertTrue(started.wait(2))
        full = self.collect(server, batch, wait_seconds=0)
        compact = server._get_batch({"batch_id": batch["batch_id"]})
        self.assertEqual([item["status"] for item in full["results"]], ["completed", "running", "queued"])
        self.assertEqual(compact["progress"], {"done": 1, "total": 3})
        self.assertEqual(len(compact["results"]), 1)
        for key in ("id", "started_at", "completed_at", "duration_seconds", "task_type"):
            self.assertNotIn(key, compact["results"][0])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(full)) / 2)
        release.set()
        self.assertEqual(self.collect(server, batch)["completed"], 3)

    def test_cancel_retains_running_result_and_skips_dependency_chain(self):
        server = self.server(workers=1)
        started, release, calls = threading.Event(), threading.Event(), []
        self.addCleanup(release.set)

        def fetch(payload):
            calls.append(payload["url"])
            started.set()
            release.wait(5)
            return {"text": "done"}

        self.mock_fetch(fetch)
        tasks = self.tasks(4)
        tasks[3]["depends_on"] = ["2"]
        batch = server._execute_batch({"tasks": tasks, "wait": False})
        self.assertTrue(started.wait(2))
        self.assertEqual(server._cancel_batch({"batch_id": batch["batch_id"]})["status"], "cancelling")
        release.set()
        result = self.collect(server, batch)
        self.assertEqual((result["status"], result["completed"], result["cancelled"]), ("cancelled", 1, 3))
        self.assertEqual(calls, ["https://example.com/0"])
        self.assertEqual(server._cancel_batch({"batch_id": batch["batch_id"]})["status"], "cancelled")

    def test_cancellation_racing_initial_submission_keeps_all_tasks(self):
        server = self.server(workers=1)

        class RaceCancel(threading.Event):
            calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls > 1

        prepared = server.tasker._prepare_tasks([("task", TaskType.WEB_SCRAPE, {"url": "https://unused.test"})])
        result = server.tasker._execute_prepared(prepared, concurrency=1, cancel=RaceCancel())
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(len(result["results"]), 1)

    def test_failure_blocks_downstream_but_keeps_independent_success(self):
        server = self.server()
        tasks = self.tasks(3)
        tasks[1]["depends_on"] = ["0"]

        def fetch(payload):
            if payload["url"].endswith("/0"):
                raise RuntimeError("upstream failed")
            return {"text": "independent"}

        self.mock_fetch(fetch)
        result = server._execute_batch({"tasks": tasks, "output_mode": "full"})
        self.assertEqual(result["failed"], 2)
        self.assertEqual(result["completed"], 1)
        self.assertIn("Blocked by failed dependencies", result["results"][1]["error"])

    def test_invalid_batch_is_rejected_before_any_execution(self):
        server = self.server()
        for concurrency in (True, 0, 11, "10", 1.5):
            with self.subTest(concurrency=concurrency), self.assertRaises(ValueError):
                server._execute_batch({"tasks": self.tasks(1), "wait": False, "concurrency": concurrency})
        invalid = [self.tasks(2), self.tasks(2), self.tasks(2)]
        invalid[0][1]["depends_on"] = ["missing"]
        invalid[1][0]["depends_on"], invalid[1][1]["depends_on"] = ["1"], ["0"]
        invalid[2][1]["name"] = "0"
        with patch("agent_tasker_mcp.server.execute_web_scrape") as fetch:
            for tasks in invalid:
                with self.assertRaises(ValueError):
                    server._execute_batch({"tasks": tasks, "wait": False})
            with self.assertRaises(ValueError):
                server._execute_batch({"tasks": self.tasks(1), "output_mode": "unknown"})
            fetch.assert_not_called()
        self.assertFalse(server.batches._jobs)
        for seconds in (True, -1, 31, "1"):
            with self.assertRaises(ValueError):
                server._get_batch({"batch_id": "missing", "wait_seconds": seconds})
        with self.assertRaisesRegex(ValueError, "Unknown or expired"):
            server._get_batch({"batch_id": "missing"})

    def test_capacity_eviction_expiry_and_closed_manager(self):
        tasker = AgentTasker(max_workers=1)
        self.addCleanup(tasker.close)
        manager = BatchManager(tasker, max_batches=1, ttl_seconds=1)
        self.addCleanup(manager.close)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def fetch(payload):
            started.set()
            release.wait(5)
            return {}

        self.mock_fetch(fetch)
        definitions = [("task", TaskType.WEB_SCRAPE, {"url": "https://example.com"})]
        batch = manager.submit(definitions, concurrency=1)
        self.assertTrue(started.wait(2))
        with self.assertRaisesRegex(RuntimeError, "limit reached"):
            manager.submit(definitions, concurrency=1)
        release.set()
        manager.get(batch["batch_id"], wait_seconds=5)
        next_batch = manager.submit(definitions, concurrency=1)
        with self.assertRaises(ValueError):
            manager.get(batch["batch_id"])
        manager.get(next_batch["batch_id"], wait_seconds=5)
        with patch("agent_tasker_mcp.batches.time.monotonic", return_value=time.monotonic() + 2):
            with self.assertRaises(ValueError):
                manager.get(next_batch["batch_id"])
        manager.close()
        with self.assertRaisesRegex(RuntimeError, "closing"):
            manager.submit(definitions, concurrency=1)

    def test_two_batches_share_worker_limit_and_cancel_queued_io(self):
        server = self.server(workers=2)
        lock, started, release = threading.Lock(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls, active, peak = [], 0, 0

        def fetch(payload):
            nonlocal active, peak
            with lock:
                calls.append(payload["url"])
                active += 1
                peak = max(peak, active)
                if active == 2:
                    started.set()
            release.wait(5)
            with lock:
                active -= 1
            return {}

        self.mock_fetch(fetch)
        first = server._execute_batch({"tasks": self.tasks(4), "wait": False})
        self.assertTrue(started.wait(2))
        second = server._execute_batch({"tasks": [{**task, "url": "https://other.test"} for task in self.tasks(3)], "wait": False})
        server._cancel_batch({"batch_id": second["batch_id"]})
        release.set()
        self.assertEqual(self.collect(server, first)["completed"], 4)
        self.assertEqual(self.collect(server, second)["cancelled"], 3)
        self.assertEqual(peak, 2)
        self.assertNotIn("https://other.test", calls)

    def test_operator_limits_and_utf8_payload_size(self):
        server = self.server(workers=3)
        schema = next(tool["inputSchema"] for tool in server._list_tools()["tools"] if tool["name"] == "execute_batch")
        self.assertEqual(schema["properties"]["concurrency"]["maximum"], 3)
        payload = {"server": "peer", "tool": "echo", "arguments": {"value": "界" * 5}}
        char_count = len(json.dumps(validate_payload(TaskType.MCP_TOOL, payload), ensure_ascii=False))
        tasker = AgentTasker(max_payload_bytes=char_count, max_tasks=1)
        self.addCleanup(tasker.close)
        with self.assertRaisesRegex(ValueError, "Payload too large"):
            tasker.execute_tasks([("call", TaskType.MCP_TOOL, payload)])
        with self.assertRaisesRegex(ValueError, "Task limit"):
            tasker.execute_tasks([("one", TaskType.MCP_TOOL, payload), ("two", TaskType.MCP_TOOL, payload)])
