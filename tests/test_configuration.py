import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

try:
    import tomllib
except ImportError:
    tomllib = None

from agent_tasker_mcp.configuration import CLIENTS, SEARCH_PROVIDERS, render_config, search_providers, server_args
from agent_tasker_mcp.models import DEFAULT_MAX_WORKERS
from agent_tasker_mcp.server import main

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def test_json_client_wrappers_and_escaping(self):
        command = 'C:\\Users\\space and "quotes"\\python.exe'
        args = ["-m", "agent_tasker_mcp.server", "--mcp-config", "資料🚀/servers.json"]
        for client in CLIENTS:
            if client == "codex":
                continue
            with self.subTest(client=client):
                config = json.loads(render_config(client, command, args))
                if client == "opencode":
                    spec = config["mcp"]["agent-tasker"]
                    self.assertEqual(spec["command"], [command, *args])
                    self.assertEqual(spec["type"], "local")
                    self.assertTrue(spec["enabled"])
                else:
                    spec = config["servers" if client == "vscode" else "mcpServers"]["agent-tasker"]
                    self.assertEqual(spec, {"type": "stdio", "command": command, "args": args})
        with self.assertRaises(ValueError):
            render_config("unknown", command, args)

    @unittest.skipUnless(tomllib, "TOML parsing requires Python 3.11+")
    def test_codex_toml_escaping(self):
        command = 'C:\\Users\\space and "quotes"\\python.exe'
        args = ["-m", "agent_tasker_mcp.server", "--mcp-config", "資料🚀/servers.json"]
        spec = tomllib.loads(render_config("codex", command, args))["mcp_servers"]["agent-tasker"]
        self.assertEqual(spec, {"command": command, "args": args})

    def test_argument_generation_defaults_and_absolute_paths(self):
        self.assertEqual(server_args(DEFAULT_MAX_WORKERS), ["-m", "agent_tasker_mcp.server"])
        args = server_args(5, "providers.json", "~/servers.json")
        self.assertEqual(args[:4], ["-m", "agent_tasker_mcp.server", "--workers", "5"])
        self.assertEqual(args[4:], ["--providers-file", str(Path("providers.json").absolute()),
                                    "--mcp-config", str(Path("~/servers.json").expanduser().absolute())])
        self.assertEqual(server_args(10, "ignored.json", search_provider="brave"),
                         ["-m", "agent_tasker_mcp.server", "--search-provider", "brave"])

    def test_print_config_is_offline_and_never_constructs_server(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"AGENT_TASKER_PROVIDERS_FILE": "missing.json"}, clear=True), \
                patch("agent_tasker_mcp.server.AgentTasker", side_effect=AssertionError("must not start")), \
                contextlib.redirect_stdout(output):
            result = main(["--print-config", "vscode", "--workers", "5", "--mcp-config", "remotes.json"])
        self.assertEqual(result, 0)
        spec = json.loads(output.getvalue())["servers"]["agent-tasker"]
        self.assertEqual(spec["command"], sys.executable)
        self.assertEqual(spec["args"], server_args(5, "missing.json", "remotes.json"))
        self.assertNotIn("env", spec)

    def test_brave_preset_matches_example(self):
        example = json.loads((ROOT / "examples" / "brave-providers.json").read_text())
        self.assertEqual(SEARCH_PROVIDERS["brave"], example)

    def test_preset_overrides_environment_file_without_reading_it(self):
        with patch.dict(os.environ, {"AGENT_TASKER_PROVIDERS_FILE": "missing.json"}, clear=True), \
                patch("agent_tasker_mcp.server.create_server") as server, \
                patch("agent_tasker_mcp.server.Path.read_text", side_effect=AssertionError("must not read file")):
            self.assertEqual(main(["--search-provider", "brave"]), 0)
        server.assert_called_once_with(DEFAULT_MAX_WORKERS, SEARCH_PROVIDERS["brave"], {}, reader_fallback="none")
        server.return_value.serve_stdio.assert_called_once_with()

    def test_searxng_and_reader_options_reach_every_generated_client(self):
        for client in CLIENTS:
            with self.subTest(client=client), patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output, \
                    patch("agent_tasker_mcp.server.create_server", side_effect=AssertionError("must not start")):
                self.assertEqual(main(["--print-config", client, "--search-provider", "searxng", "--searxng-url",
                                       "https://search.example/base", "--reader-fallback", "jina"]), 0)
                printed = output.getvalue()
                self.assertIn("--search-provider", printed)
                self.assertIn("--searxng-url", printed)
                self.assertIn("--reader-fallback", printed)
                self.assertNotIn("API_KEY", printed)
                if client == "codex" and tomllib:
                    tomllib.loads(printed)
                elif client != "codex":
                    json.loads(printed)

    def test_searxng_and_reader_options_reach_startup(self):
        with patch.dict(os.environ, {}, clear=True), patch("agent_tasker_mcp.server.create_server") as server:
            self.assertEqual(main(["--search-provider", "searxng", "--searxng-url", "http://localhost:9090/base", "--reader-fallback", "jina"]), 0)
        server.assert_called_once_with(DEFAULT_MAX_WORKERS, search_providers("searxng", "http://localhost:9090/base"), {}, reader_fallback="jina")

    def test_invalid_options_fail_before_startup(self):
        for args in (["--print-config", "unknown"], ["--print-config", "cursor", "--workers", "0"],
                     ["--search-provider", "brave", "--providers-file", "providers.json"],
                     ["--search-provider", "unknown"], ["--searxng-url", "https://example.com"],
                     ["--print-config", "cursor", "--search-provider", "searxng", "--searxng-url", "file:///tmp"],
                     ["--reader-fallback", "unknown"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    patch("agent_tasker_mcp.server.AgentTasker", side_effect=AssertionError("must not start")):
                with self.assertRaises(SystemExit) as raised:
                    main(args)
                self.assertEqual(raised.exception.code, 2)

    @unittest.skipUnless(shutil.which("bash"), "bash is not installed")
    def test_setup_rejects_invalid_options_before_installation(self):
        for args in (["--client", "unknown"], ["--client"], ["--search-provider", "unknown"],
                     ["--search-provider", "brave", "--providers-file", "providers.json"],
                     ["--reader-fallback", "unknown"], ["--searxng-url", "https://example.com"]):
            with self.subTest(args=args):
                result = subprocess.run(["bash", str(ROOT / "setup.sh"), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Error:", result.stdout)
                self.assertNotIn("pip", result.stdout)


if __name__ == "__main__":
    unittest.main()
