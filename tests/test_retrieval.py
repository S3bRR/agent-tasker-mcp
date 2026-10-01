from copy import deepcopy
import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from agent_tasker_mcp.cache import SearchCache
from agent_tasker_mcp.common import compact_task_result
from agent_tasker_mcp.configuration import SEARCH_PROVIDERS, search_providers, server_args
from agent_tasker_mcp.executors.discovery import execute_discovery_search
from agent_tasker_mcp.executors.http import execute_web_scrape
from agent_tasker_mcp.executors.reader import require_public_url
from agent_tasker_mcp.models import TaskType
from agent_tasker_mcp.registry import validate_payload
from agent_tasker_mcp.server import MCPServer

URL = "https://public.example/article"
PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
SHELL = "<title>App</title><div id='root'></div><script src='/app.js'></script>"


def response(body, media="text/html", **options):
    return {"status_code": 200, "headers": {"Content-Type": media}, "url": URL, "body": body, **options}


def reader_response(**options):
    return response(json.dumps({"code": 200, "data": {"title": "Rendered", "description": "Page summary",
        "content": "# Rendered\n\nUseful content with `code` and [a link](/next).", "url": URL}}), "application/json", **options)


class RetrievalTests(unittest.TestCase):
    def fetch(self, reply, **options):
        with patch("agent_tasker_mcp.executors.http.execute_http_request", return_value=reply) as fetch:
            result = execute_web_scrape({"url": URL, **options})
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(fetch.call_args.args[0]["headers"]["Accept"].startswith("text/markdown"))
        return result

    def test_markdown_negotiation_preserves_code_and_resolves_links(self):
        body = '# Documentation\n\n```python\n# not a heading\nprint("<T>")\n[not a link](/fake)\n```\n\n## API\n[Next](/next#section)\n![image](/image.png)\n[Bad](javascript:alert)'
        result = self.fetch(response(body, "Text/Markdown; charset=utf-8"))
        self.assertEqual(result["text"], body)
        self.assertEqual(result["extraction"], "markdown")
        self.assertEqual(result["source"], "direct")
        self.assertEqual([h["text"] for h in result["headings"]], ["Documentation", "API"])
        self.assertEqual(result["links"], [{"url": "https://public.example/next", "text": "Next"}])
        result = self.fetch(response(body, "text/markdown"), max_text_chars=20, extract_links=False, extract_headings=False)
        self.assertTrue(result["text_truncated"])
        self.assertEqual(result["links"], [])
        self.assertEqual(result["headings"], [])

    def test_plain_text_is_not_parsed_as_html(self):
        body = 'Use <T> as a type.\n\nKeep newlines and    spacing.'
        result = self.fetch(response(body, "text/plain"), include_html=True)
        self.assertEqual(result["text"], body)
        self.assertEqual(result["extraction"], "text")
        self.assertNotIn("html", result)

    def test_valid_html_is_not_regex_parsed_twice(self):
        with patch("agent_tasker_mcp.executors.http.fallback_html_extract", side_effect=AssertionError("unnecessary recovery")):
            result = self.fetch(response('<title>Types</title><h1>Reference</h1><p>Literal &lt;T&gt; &amp; text</p><a href="/next">Next</a>'))
        self.assertIn("<T> & text", result["text"])
        self.assertEqual(result["title"], "Types")
        self.assertEqual(result["links"][0]["url"], "https://public.example/next")

    def test_disabled_links_skip_url_resolution(self):
        for media, body in (("text/html", '<h1>Page</h1><a href="/next">Next</a>'), ("text/markdown", '# Page\n[Next](/next)')):
            with self.subTest(media=media), patch("agent_tasker_mcp.common.urljoin", side_effect=AssertionError("unnecessary URL work")):
                result = self.fetch(response(body, media), extract_links=False)
                self.assertEqual(result["links"], [])
                self.assertIn("Next", result["text"])

    def test_reader_relative_links_use_the_readers_final_url(self):
        reply = reader_response()
        envelope = json.loads(reply["body"])
        envelope["data"]["url"] = "https://destination.example/final"
        reply["body"] = json.dumps(envelope)
        with patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", return_value=PUBLIC_DNS), \
                patch("agent_tasker_mcp.executors.http.execute_http_request", side_effect=[response(SHELL), reply]):
            result = execute_web_scrape({"url": URL, "reader_fallback": "jina"})
        self.assertEqual(result["final_url"], "https://destination.example/final")
        self.assertEqual(result["links"][0]["url"], "https://destination.example/next")

    def test_malformed_title_and_unclosed_links_are_recovered(self):
        result = self.fetch(response("<title>Broken<title><a href='/next'>Open link"))
        self.assertEqual(result["title"], "Broken")
        self.assertEqual(result["links"][0]["text"], "Open link")

    def test_good_html_and_short_output_never_invoke_reader(self):
        body = '<title>Article</title><p>' + 'Meaningful article. ' * 100 + '</p><script src="/app.js"></script>'
        result = self.fetch(response(body), reader_fallback="jina", max_text_chars=10)
        self.assertNotIn("js_rendered_warning", result)
        self.assertNotIn("fallback_reason", result)
        self.assertTrue(result["text_truncated"])

    def test_reader_is_opt_in_and_disabled_by_default(self):
        with patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", side_effect=AssertionError("must not resolve")):
            result = self.fetch(response(SHELL))
        self.assertIn("js_rendered_warning", result)
        server = MCPServer(reader_fallback="none")
        self.addCleanup(server.close)
        with self.assertRaisesRegex(ValueError, "disabled"):
            server._execute({"task_type": "web_scrape", "url": URL, "reader_fallback": "jina"})

    def test_reader_success_headers_tls_budget_and_compact_provenance(self):
        now = [100.0]

        def fetch(payload, **kwargs):
            if payload["method"] == "GET":
                now[0] += 10
                return response(SHELL)
            return reader_response()

        limiter = Mock()
        with patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", return_value=PUBLIC_DNS), \
                patch("agent_tasker_mcp.executors.http.time.monotonic", side_effect=lambda: now[0]), \
                patch.dict(os.environ, {"JINA_API_KEY": "reader-secret"}), \
                patch("agent_tasker_mcp.executors.http.execute_http_request", side_effect=fetch) as request:
            result = execute_web_scrape({"url": URL, "reader_fallback": "jina", "verify_ssl": False, "timeout": 30}, limiter=limiter)
        self.assertEqual(request.call_count, 2)
        reader = request.call_args.args[0]
        self.assertEqual(json.loads(reader["body"]), {"url": URL})
        self.assertTrue(reader["verify_ssl"])
        self.assertEqual(reader["timeout"], 20)
        self.assertEqual(reader["headers"]["Authorization"], "Bearer reader-secret")
        self.assertNotIn("Cookie", reader["headers"])
        self.assertIs(request.call_args.kwargs["limiter"], limiter)
        self.assertAlmostEqual(request.call_args.kwargs["requests_per_second"], 1 / 3)
        self.assertNotIn("Authorization", request.call_args_list[0].args[0]["headers"])
        self.assertEqual(result["source"], "jina")
        self.assertEqual(result["fallback_reason"], "minimal_script_content")
        self.assertNotIn("js_rendered_warning", result)
        compact = compact_task_result({"name": "page", "task_type": "web_scrape", "status": "completed", "result": result})
        self.assertEqual(compact["result"]["source"], "jina")
        self.assertNotIn("reader-secret", json.dumps(compact))

    def test_http_403_can_fall_back_but_other_failures_cannot(self):
        with patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", return_value=PUBLIC_DNS), \
                patch("agent_tasker_mcp.executors.http.execute_http_request", side_effect=[response("denied", status_code=403), reader_response()]):
            result = execute_web_scrape({"url": URL, "reader_fallback": "jina"})
        self.assertEqual(result["fallback_reason"], "http_403")
        for status in (401, 404, 429, 500):
            with self.subTest(status=status), patch("agent_tasker_mcp.executors.http.execute_http_request", return_value=response("error", status_code=status)) as fetch:
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                    execute_web_scrape({"url": URL, "reader_fallback": "jina"})
                self.assertEqual(fetch.call_count, 1)
        with patch("agent_tasker_mcp.executors.http.execute_http_request", side_effect=RuntimeError("TLS failure")) as fetch:
            with self.assertRaisesRegex(RuntimeError, "TLS failure"):
                execute_web_scrape({"url": URL, "reader_fallback": "jina"})
            self.assertEqual(fetch.call_count, 1)

    def test_failed_or_truncated_reader_preserves_direct_warning(self):
        bad = [response("not JSON", "application/json"), response('{"data": {"content": ""}}', "application/json"),
               response('{"data": []}', "application/json"), reader_response(body_truncated=True), response("quota", status_code=429)]
        for reply in bad:
            with self.subTest(reply=reply), patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", return_value=PUBLIC_DNS), \
                    patch("agent_tasker_mcp.executors.http.execute_http_request", side_effect=[response(SHELL), reply]):
                result = execute_web_scrape({"url": URL, "reader_fallback": "jina"})
                self.assertEqual(result["source"], "direct")
                self.assertIn("reader_error", result)
                self.assertIn("js_rendered_warning", result)
        with patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", return_value=PUBLIC_DNS), \
                patch("agent_tasker_mcp.executors.http.execute_http_request", side_effect=[response("denied", status_code=403), response("not JSON")]):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403; Jina fallback failed"):
                execute_web_scrape({"url": URL, "reader_fallback": "jina"})

    def test_reader_does_not_get_an_extra_timeout_budget(self):
        now = [0]

        def fetch(payload, **kwargs):
            now[0] = 31
            return response(SHELL)

        with patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", return_value=PUBLIC_DNS), \
                patch("agent_tasker_mcp.executors.http.time.monotonic", side_effect=lambda: now[0]), \
                patch("agent_tasker_mcp.executors.http.execute_http_request", side_effect=fetch) as request:
            result = execute_web_scrape({"url": URL, "reader_fallback": "jina", "timeout": 30})
        self.assertEqual(request.call_count, 1)
        self.assertIn("No time left", result["reader_error"])

    def test_private_urls_and_credentials_are_never_disclosed(self):
        for url in ("http://localhost/page", "http://app.local/page", "http://service.internal/page", "http://myapp/page",
                    "https://user:secret@public.example/page", "file:///etc/passwd"):
            with self.subTest(url=url), patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo") as dns:
                with self.assertRaises(ValueError):
                    require_public_url(url)
                dns.assert_not_called()
        for address in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "::ffff:127.0.0.1", "192.0.2.1"):
            with self.subTest(address=address), patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", return_value=[(0, 0, 0, "", (address, 80))]):
                with self.assertRaisesRegex(ValueError, "non-public"):
                    require_public_url(URL)
        with patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", side_effect=socket.gaierror("missing")):
            with self.assertRaisesRegex(ValueError, "verify"):
                require_public_url(URL)
        with patch("agent_tasker_mcp.executors.http.execute_http_request", return_value=response(SHELL, url="http://127.0.0.1/private")) as fetch, \
                patch("agent_tasker_mcp.executors.reader.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("127.0.0.1", 80))]):
            result = execute_web_scrape({"url": URL, "reader_fallback": "jina"})
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("non-public", result["reader_error"])

    def test_reader_defaults_and_task_opt_out_are_exposed(self):
        server = MCPServer(reader_fallback="jina")
        self.addCleanup(server.close)
        catalog = {tool["name"]: tool["inputSchema"] for tool in server._list_tools()["tools"]}
        self.assertEqual(catalog["execute"]["properties"]["reader_fallback"]["default"], "jina")
        tasks = [("page", TaskType.WEB_SCRAPE, {"url": URL}), ("no-reader", TaskType.WEB_SCRAPE, {"url": URL, "reader_fallback": "none"})]
        prepared = server.tasker._prepare_tasks(tasks)
        self.assertEqual([task.payload["reader_fallback"] for task in prepared], ["jina", "none"])
        with self.assertRaises(ValueError):
            validate_payload(TaskType.WEB_SCRAPE, {"url": URL, "reader_fallback": True})

    def test_searxng_preset_subpaths_and_invalid_urls(self):
        provider = search_providers("searxng", "https://search.example/base/")[0]
        self.assertEqual(provider["url_template"], "https://search.example/base/search?q={query_encoded}&format=json")
        self.assertEqual(provider["errors_path"], "unresponsive_engines")
        from pathlib import Path
        example = json.loads((Path(__file__).resolve().parents[1] / "examples/searxng-providers.json").read_text())
        self.assertEqual(example, SEARCH_PROVIDERS["searxng"])
        self.assertNotIn("headers_env", provider)
        normalized = search_providers("searxng", "HTTP://localhost:8080/?")
        self.assertTrue(normalized[0]["url_template"].startswith("http://localhost:8080/search?"))
        validate_payload(TaskType.DISCOVERY_SEARCH, {"query": "test", "providers": normalized})
        for url in ("file:///tmp", "http://", "https://user:key@host/", "https://host/?q=a", "https://host/#a", "http://host:bad/", "http://host/{query}", "http://host/ space"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                search_providers("searxng", url)
        with self.assertRaises(ValueError):
            search_providers("brave", "http://localhost:8080")
        args = server_args(10, search_provider="searxng", searxng_url="http://localhost:8080", reader_fallback="jina")
        self.assertEqual(args[-4:], ["--searxng-url", "http://localhost:8080", "--reader-fallback", "jina"])

    def test_searxng_engine_failures_are_not_silent_empty_searches(self):
        payload = validate_payload(TaskType.DISCOVERY_SEARCH, {"query": "test", "providers": SEARCH_PROVIDERS["searxng"]})
        body = {"results": [{"title": "test", "url": URL, "content": "snippet"}], "unresponsive_engines": [["google", "CAPTCHA"]]}
        with patch("agent_tasker_mcp.executors.discovery.execute_http_request", return_value=response(json.dumps(body), "application/json")):
            result = execute_discovery_search(payload)
        self.assertEqual(result["provider_statuses"][0]["status"], "partial")
        self.assertEqual(len(result["results"]), 1)
        cache, search = SearchCache(), Mock(return_value=result)
        cache.run(payload, search)
        cache.run(payload, search)
        self.assertEqual(search.call_count, 2)
        body["results"] = []
        with patch("agent_tasker_mcp.executors.discovery.execute_http_request", return_value=response(json.dumps(body), "application/json")):
            with self.assertRaisesRegex(RuntimeError, "CAPTCHA"):
                execute_discovery_search(payload)

    def test_reader_context_failures_are_not_cached_and_keys_include_reader_auth(self):
        payload = validate_payload(TaskType.DISCOVERY_SEARCH, {"query": "test", "providers": SEARCH_PROVIDERS["searxng"], "fetch_top_results": 1, "reader_fallback": "jina"})
        result = {"provider_statuses": [{"status": "ok"}], "results": [{"title": "test", "url": URL, "page_context": {"reader_error": "failed"}}]}
        cache, search = SearchCache(), Mock(return_value=deepcopy(result))
        for _ in range(2):
            cache.run(payload, search)
        self.assertEqual(search.call_count, 2)
        del result["results"][0]["page_context"]["reader_error"]
        search.return_value = result
        for key in ("first", "second", "second"):
            with patch.dict(os.environ, {"JINA_API_KEY": key}):
                cache.run(payload, search)
        self.assertEqual(search.call_count, 4)


class _WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query).get("q", ["page"])[0]
        with self.server.lock:
            self.server.paths.append(parsed.path)
        if parsed.path == "/base/search":
            assert parse_qs(parsed.query)["format"] == ["json"]
            body = json.dumps({"results": [{"title": query, "url": self.server.base + "/page", "content": "search snippet"}], "unresponsive_engines": []}).encode()
            media = "application/json"
        else:
            if parsed.path == "/parallel":
                self.server.barrier.wait(5)
            assert self.headers["Accept"].startswith("text/markdown")
            body = f'# {query}\n\nNative Markdown with `<T>` and [Next](/next).'.encode()
            media = "text/markdown"
        self.send_response(200)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RetrievalIntegrationTests(unittest.TestCase):
    def setUp(self):
        class Server(ThreadingHTTPServer):
            request_queue_size = 32
        self.httpd = Server(("127.0.0.1", 0), _WebHandler)
        self.httpd.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.httpd.lock, self.httpd.paths, self.httpd.barrier = threading.Lock(), [], threading.Barrier(10)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(lambda: thread.join(5))
        self.addCleanup(self.httpd.shutdown)

    def test_real_searxng_shaped_search_context_and_cache(self):
        server = MCPServer(providers=search_providers("searxng", self.httpd.base + "/base"), reader_fallback="jina")
        self.addCleanup(server.close)
        task = {"task_type": "discovery_search", "query": "a quote \" & café", "fetch_top_results": 1}
        first = server._execute(task)["task"]["result"]["results"][0]
        second = server._execute(task)["task"]["result"]["results"][0]
        self.assertEqual(first, second)
        self.assertEqual(first["title"], task["query"])
        self.assertIn("<T>", first["page_context"]["text"])
        self.assertEqual(first["page_context"]["source"], "direct")
        self.assertEqual(first["page_context"]["extraction"], "markdown")
        self.assertEqual(self.httpd.paths, ["/base/search", "/page"])

    def test_ten_native_markdown_fetches_in_background(self):
        server = MCPServer(max_workers=10)
        self.addCleanup(server.close)
        batch = server._execute_batch({"wait": False, "concurrency": 10, "tasks": [
            {"name": str(i), "task_type": "web_scrape", "url": self.httpd.base + f"/parallel?q={i}", "retries": 0}
            for i in range(10)]})
        result = server._get_batch({"batch_id": batch["batch_id"], "wait_seconds": 5})
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual([item["result"]["title"] for item in result["results"]], [str(i) for i in range(10)])
        self.assertEqual(len(self.httpd.paths), 10)
