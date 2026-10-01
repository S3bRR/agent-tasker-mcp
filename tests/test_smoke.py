import unittest
from unittest.mock import patch

from agent_tasker_mcp.executors.http import execute_web_scrape
from agent_tasker_mcp.models import TaskType
from agent_tasker_mcp.server import AgentTasker, _tool_catalog


class SmokeTests(unittest.TestCase):
    def test_search_first_task_and_tool_surface(self):
        self.assertEqual([kind.value for kind in TaskType], ["discovery_search", "web_scrape", "mcp_tool"])
        catalog = {name: schema for name, _, schema in _tool_catalog()}
        self.assertEqual(len(catalog), 5)
        properties = catalog["execute_batch"]["properties"]["tasks"]["items"]["properties"]
        self.assertIn("depends_on", properties)
        self.assertNotIn("command", properties)
        self.assertNotIn("code", properties)

    def test_malformed_html_fallback_and_extraction_options(self):
        response = {"status_code": 200, "url": "https://example.com", "body": "<title>Broken<title><a href='/target'>Open link", "headers": {}}
        with patch("agent_tasker_mcp.executors.http.execute_http_request", return_value=response):
            result = execute_web_scrape({"url": "https://example.com"})
        self.assertEqual(result["title"], "Broken")
        self.assertEqual(result["links"][0]["url"], "https://example.com/target")
        with patch("agent_tasker_mcp.executors.http.execute_http_request", return_value=response):
            self.assertEqual(execute_web_scrape({"url": "https://example.com", "max_links": 0})["links"], [])

    def test_empty_engine_batch_and_worker_validation(self):
        tasker = AgentTasker()
        self.addCleanup(tasker.close)
        self.assertEqual(tasker.execute_tasks([])["results"], [])
        for value in (0, -1, True, "10"):
            with self.assertRaises(ValueError):
                AgentTasker(max_workers=value)
