from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from agent_tasker_mcp.cache import SearchCache
from agent_tasker_mcp.common import apply_output_mode
from agent_tasker_mcp.executors.discovery import execute_discovery_search, render_provider_template
from agent_tasker_mcp.executors.http import RateLimiter, decode_json_response, execute_http_request, execute_web_scrape, retry_after_seconds
from agent_tasker_mcp.models import TaskType
from agent_tasker_mcp.registry import FIELDS, validate_payload

PROVIDER = {"name": "test", "url_template": "https://example.com/?q={query_encoded}", "items_path": "items", "title_path": "title", "url_path": "url", "snippet_path": "snippet"}
RESULT = {"provider_statuses": [{"provider": "test", "status": "ok"}], "results": [{"title": "one", "url": "https://one.test", "sources": ["test"]}]}


class ExecutionTests(unittest.TestCase):
    def payload(self, **options):
        return validate_payload(TaskType.DISCOVERY_SEARCH, {"query": "test", "providers": [PROVIDER], **options})

    def test_search_deduplicates_and_preserves_provider_failures(self):
        providers = [PROVIDER, {**PROVIDER, "name": "second"}, {**PROVIDER, "name": "bad", "items_path": "missing"}]
        response = {"status_code": 200, "body": '{"items": [{"title": "test", "url": "https://one.test", "snippet": "context"}]}'}
        with patch("agent_tasker_mcp.executors.discovery.execute_http_request", return_value=response):
            result = execute_discovery_search(self.payload(providers=providers))
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["sources"], ["test", "second"])
        self.assertEqual(result["provider_statuses"][2]["status"], "failed")
        compact = apply_output_mode({"status": "completed", "results": [{"name": "search", "task_type": "discovery_search", "status": "completed", "result": result}]}, "compact")
        self.assertEqual(len(compact["results"][0]["result"]["provider_errors"]), 1)
        self.assertNotIn("score", compact["results"][0]["result"]["results"][0])

    def test_templates_credentials_and_provider_validation(self):
        query = 'a "quote" & café'
        self.assertEqual(json.loads(render_provider_template('{{"query": {query_json}}}', query, 5))["query"], query)
        provider = {**PROVIDER, "headers_env": {"X-Key": "TASKER_TEST_KEY"}}
        response = {"status_code": 200, "body": '{"items": []}'}
        with patch.dict(os.environ, {"TASKER_TEST_KEY": "secret"}), patch("agent_tasker_mcp.executors.discovery.execute_http_request", return_value=response) as request:
            result = execute_discovery_search(self.payload(providers=[provider]))
        self.assertEqual(request.call_args.args[0]["headers"]["X-Key"], "secret")
        self.assertNotIn("secret", json.dumps(result))
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "Missing environment variable"):
            execute_discovery_search(self.payload(providers=[provider]))
        for update in ({"result_limit": 0}, {"headers_env": []}, {"requests_per_second": True}, {"url_template": "https://example.com/{unknown}"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.payload(providers=[{**PROVIDER, **update}])

    def test_manifest_defaults_and_invalid_field_types(self):
        payload = self.payload()
        self.assertEqual(payload["timeout"], FIELDS["timeout"]["default"])
        self.assertEqual(payload["max_results"], FIELDS["max_results"]["default"])
        for update in ({"max_results": True}, {"max_results": 0}, {"cache": "false"}, {"verify_ssl": "false"}, {"query": " "}, {"retry_backoff_seconds": float("nan")}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.payload(**update)
        with self.assertRaises(ValueError):
            validate_payload(TaskType.WEB_SCRAPE, {"url": "https://example.com", "link_include_pattern": "("})

    def test_cache_coalesces_inflight_and_isolates_result_objects(self):
        cache = SearchCache()
        started, release = threading.Event(), threading.Event()
        calls = []

        def search(payload):
            calls.append(payload)
            started.set()
            release.wait(5)
            return deepcopy(RESULT)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(cache.run, self.payload(), search) for _ in range(5)]
            self.assertTrue(started.wait(2))
            release.set()
            results = [future.result(2) for future in futures]
        self.assertEqual(len(calls), 1)
        results[0]["results"][0]["title"] = "changed"
        self.assertEqual(results[1]["results"][0]["title"], "one")
        self.assertEqual(cache.run(self.payload(), search)["results"][0]["title"], "one")
        self.assertFalse(cache._pending)

    def test_cache_fresh_bypass_and_failures_are_not_retained(self):
        cache = SearchCache()
        search = Mock(return_value=deepcopy(RESULT))
        for _ in range(2):
            cache.run(self.payload(), search)
        self.assertEqual(search.call_count, 1)
        for _ in range(2):
            cache.run(self.payload(cache=False), search)
        self.assertEqual(search.call_count, 3)
        failed = Mock(side_effect=RuntimeError("failure"))
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                cache.run(self.payload(query="failed"), failed)
        self.assertEqual(failed.call_count, 2)
        partial = Mock(return_value={**RESULT, "provider_statuses": [{"status": "failed"}]})
        for _ in range(2):
            cache.run(self.payload(query="partial"), partial)
        self.assertEqual(partial.call_count, 2)

    def test_cache_ttl_lru_byte_bound_and_credentials(self):
        cache = SearchCache(ttl_seconds=1, max_entries=1, max_bytes=1000)
        search = Mock(return_value=deepcopy(RESULT))
        cache.run(self.payload(query="one"), search)
        cache.run(self.payload(query="two"), search)
        cache.run(self.payload(query="one"), search)
        self.assertEqual(search.call_count, 3)
        with patch("agent_tasker_mcp.cache.time.monotonic", return_value=10**10):
            cache.run(self.payload(query="one"), search)
        self.assertEqual(search.call_count, 4)
        oversized = SearchCache(max_bytes=1)
        oversized.run(self.payload(), search)
        self.assertFalse(oversized._cached)
        payload = self.payload(providers=[{**PROVIDER, "headers_env": {"X-Key": "KEY"}}])
        for value in ("first", "second"):
            with patch.dict(os.environ, {"KEY": value}):
                cache.run(payload, search)
        self.assertEqual(search.call_count, 7)

    def test_http_errors_and_truncated_json_are_not_empty_successes(self):
        for response in ({"status_code": 429, "body": '{"items": []}'}, {"status_code": 200, "body": '{}', "body_truncated": True}):
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                decode_json_response(response, "test")
        with patch("agent_tasker_mcp.executors.http.execute_http_request", return_value={"status_code": 404}), self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            execute_web_scrape({"url": "https://example.com"})

    def test_file_urls_are_not_a_backdoor_to_removed_file_read(self):
        with self.assertRaisesRegex(ValueError, "HTTP"):
            validate_payload(TaskType.WEB_SCRAPE, {"url": "file:///etc/passwd"})
        with self.assertRaisesRegex(ValueError, "HTTP"):
            self.payload(providers=[{**PROVIDER, "url_template": "file:///tmp/search.json"}])
        with patch("agent_tasker_mcp.executors.http.urllib.request.urlopen") as fetch, self.assertRaisesRegex(ValueError, "HTTP"):
            execute_web_scrape({"url": "file:///etc/passwd"})
        fetch.assert_not_called()

    def test_compact_page_text_is_explicitly_truncated(self):
        raw = {"status": "completed", "results": [{"name": "page", "task_type": "web_scrape", "status": "completed", "result": {"text": "x" * 3000}}]}
        compact = apply_output_mode(raw, "compact")["results"][0]["result"]
        self.assertEqual(len(compact["text"]), 2000)
        self.assertTrue(compact["truncated"])
        self.assertIs(apply_output_mode(raw, "full"), raw)

    def test_rate_spacing_retry_after_and_shared_cooldown(self):
        now, sleeps, calls = [0.0], [], []

        def sleep(delay):
            sleeps.append(delay)
            now[0] += delay

        class Response(BytesIO):
            status, headers, url = 200, {}, "https://example.com"

        def fetch(*args, **kwargs):
            calls.append(now[0])
            if len(calls) == 1:
                raise HTTPError("https://example.com", 429, "slow", {"Retry-After": "3"}, BytesIO(b"error"))
            return Response(b'{}')

        limiter = RateLimiter()
        with patch("agent_tasker_mcp.executors.http.time.monotonic", side_effect=lambda: now[0]), patch("agent_tasker_mcp.executors.http.time.sleep", side_effect=sleep), patch("agent_tasker_mcp.executors.http.urllib.request.urlopen", side_effect=fetch):
            result = execute_http_request({"url": "https://example.com", "timeout": 10}, limiter=limiter, rate_key="provider", requests_per_second=1)
            limiter.wait("provider", 1, 10)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(calls, [0, 3])
        self.assertEqual(sleeps, [3, 1])
        self.assertEqual(retry_after_seconds("bad date"), None)
        self.assertEqual(retry_after_seconds("Wed, 01 Jan 2020 00:00:00 GMT"), 0)

    def test_long_retry_after_fails_instead_of_retrying_early(self):
        error = HTTPError("https://example.com", 429, "slow", {"Retry-After": "1000"}, BytesIO(b"error"))
        limiter = RateLimiter()
        with patch("agent_tasker_mcp.executors.http.urllib.request.urlopen", side_effect=error) as fetch, self.assertRaisesRegex(RuntimeError, "timeout"):
            execute_http_request({"url": "https://example.com", "timeout": 1}, limiter=limiter, rate_key="provider")
        self.assertEqual(fetch.call_count, 1)
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            limiter.wait("provider", None, 0)
