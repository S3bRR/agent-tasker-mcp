"""Print client configuration without changing a user's harness settings."""

import json
from pathlib import Path

from .models import DEFAULT_MAX_WORKERS

SEARCH_PROVIDERS = {"brave": [{
    "name": "brave",
    "url_template": "https://api.search.brave.com/res/v1/web/search?q={query_encoded}&count={limit}",
    "headers": {"Accept": "application/json"},
    "headers_env": {"X-Subscription-Token": "BRAVE_SEARCH_API_KEY"},
    "items_path": "web.results", "title_path": "title", "url_path": "url", "snippet_path": "description",
    "requests_per_second": 1,
}]}

CLIENTS = ("claude", "codex", "cursor", "vscode", "opencode", "generic")


def render_config(client, command, args):
    spec = {"type": "stdio", "command": command, "args": args}
    if client == "codex":
        return (
            "[mcp_servers.agent-tasker]\n"
            f"command = {json.dumps(command, ensure_ascii=False)}\n"
            f"args = {json.dumps(args, ensure_ascii=False)}\n"
        )
    if client == "opencode":
        config = {"$schema": "https://opencode.ai/config.json", "mcp": {
            "agent-tasker": {"type": "local", "command": [command, *args], "enabled": True}}}
    elif client == "vscode":
        config = {"servers": {"agent-tasker": spec}}
    elif client in ("claude", "cursor", "generic"):
        config = {"mcpServers": {"agent-tasker": spec}}
    else:
        raise ValueError(f"Unknown client: {client}")
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def server_args(workers, providers_file=None, mcp_config=None, search_provider=None):
    args = ["-m", "agent_tasker_mcp.server"]
    if workers != DEFAULT_MAX_WORKERS:
        args.extend(["--workers", str(workers)])
    if search_provider:
        args.extend(["--search-provider", search_provider])
        providers_file = None
    for flag, path in (("--providers-file", providers_file), ("--mcp-config", mcp_config)):
        if path:
            args.extend([flag, str(Path(path).expanduser().absolute())])
    return args
