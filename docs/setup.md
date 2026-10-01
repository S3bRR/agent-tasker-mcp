# Setup

[Quick start](../README.md#quick-start) · [Technical reference](reference.md)

## Choose your harness

The server runs locally over stdio. With [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git installed, use the Claude Code/Codex commands or JSON config in the README. `uvx` downloads the package on first launch.

For a local Python installation, `./setup.sh --client NAME` prints config tailored to your client. Merge it into the appropriate file; do not replace your existing settings.

| Harness | `NAME` | Configuration location |
| --- | --- | --- |
| Claude Code | `claude` | `.mcp.json` in your project; use the README CLI command for user-wide setup |
| Claude Desktop | `generic` | Settings → Developer → Edit Config |
| Codex CLI / IDE | `codex` | `~/.codex/config.toml` or trusted project's `.codex/config.toml` |
| Cursor | `cursor` | `~/.cursor/mcp.json` or project's `.cursor/mcp.json` |
| VS Code / Copilot | `vscode` | `.vscode/mcp.json` or user-profile MCP config |
| OpenCode | `opencode` | `opencode.json` or `~/.config/opencode/opencode.json` |
| Other stdio MCP clients | `generic` | Your client's MCP settings; adapt its wrapper if necessary |

Restart the harness and approve/enable AgentTasker. Confirm the five tools are visible. In Claude Code or Codex, `/mcp` shows MCP status.

### VS Code / Copilot

Merge into `.vscode/mcp.json`:

```json
{
  "servers": {
    "agent-tasker": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/S3bRR/agent-tasker-mcp.git", "agent-tasker-mcp-server"]
    }
  }
}
```

### OpenCode

Merge into `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "agent-tasker": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/S3bRR/agent-tasker-mcp.git", "agent-tasker-mcp-server"],
      "enabled": true
    }
  }
}
```

## Web search

For **SearXNG**, add `--search-provider searxng --searxng-url http://localhost:8080` to the server arguments. The preset needs no API key, but you must run/provide an instance with JSON enabled. [Full setup and curl example →](web-retrieval.md#searxng-search)

For **Brave**, obtain a [Brave Search API key](https://api-dashboard.search.brave.com/), append `--search-provider brave` to the server arguments, and make `BRAVE_SEARCH_API_KEY` available to the **server process**, not just your terminal.

### Copy-ready Cursor search config

Replace `YOUR_API_KEY`, merge into `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global), and restart Cursor:

```json
{
  "mcpServers": {
    "agent-tasker": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/S3bRR/agent-tasker-mcp.git", "agent-tasker-mcp-server", "--search-provider", "brave"],
      "env": {"BRAVE_SEARCH_API_KEY": "YOUR_API_KEY"}
    }
  }
}
```

### Other clients

In JSON configs, each server argument is a **separate array item**: `"--search-provider", "brave"`, not `"--search-provider brave"`. Add the following inside the `agent-tasker` entry:

```json
"env": {"BRAVE_SEARCH_API_KEY": "YOUR_API_KEY"}
```

OpenCode calls this field `environment`. For Codex, add this TOML table after its server configuration:

```toml
[mcp_servers.agent-tasker.env]
BRAVE_SEARCH_API_KEY = "YOUR_API_KEY"
```

Alternatively, register a search-enabled server directly with Codex (remove an existing entry first with `codex mcp remove agent-tasker` if needed):

```bash
codex mcp add agent-tasker --env BRAVE_SEARCH_API_KEY=YOUR_API_KEY -- uvx --from git+https://github.com/S3bRR/agent-tasker-mcp.git agent-tasker-mcp-server --search-provider brave
```

Codex can also forward an existing environment variable with `env_vars = ["BRAVE_SEARCH_API_KEY"]` in the server table. For Claude Code, register a search-enabled server directly (remove an existing `agent-tasker` entry first with `claude mcp remove agent-tasker` if needed):

```bash
claude mcp add --env BRAVE_SEARCH_API_KEY=YOUR_API_KEY --scope user --transport stdio agent-tasker -- uvx --from git+https://github.com/S3bRR/agent-tasker-mcp.git agent-tasker-mcp-server --search-provider brave
```

Prefer your harness's secret/environment facilities where available. Do not commit API keys or expose them in shell history. The preset defaults to one request/second; agent concurrency never bypasses provider quotas.

For other HTTP search APIs or custom quotas, use `--providers-file /absolute/path/to/providers.json` instead. See the [Brave provider template](../examples/brave-providers.json) and [provider reference](reference.md#http-search-providers). The preset overrides `AGENT_TASKER_PROVIDERS_FILE`; explicit `--providers-file` and `--search-provider` cannot be combined.

## Difficult websites

Page tasks prefer native Markdown, then extract HTML/plain text locally. Add `--reader-fallback jina` to opt in to hosted reading of public JavaScript-heavy pages or HTTP 403 responses. Optional reader authentication uses `JINA_API_KEY` in the server environment, configured like the Brave key above. This shares URLs with a third party; private URLs are rejected and tasks can opt out. [Behavior, quotas, and privacy →](web-retrieval.md#optional-hosted-reader)

## Other MCP servers

Save a configuration such as this as `servers.json`:

```json
{
  "mcpServers": {
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"],
      "allowed_tools": ["fetch"]
    }
  }
}
```

Append `--mcp-config /absolute/path/to/servers.json` to AgentTasker's arguments. Use `list_remote_tools` to discover tools, then batch `mcp_tool` tasks. [Five-call example →](../examples/five-mcp-calls.json)

AgentTasker starts these servers itself; merely adding a server to your harness does not expose it to AgentTasker. Only stdio remotes are supported. Connect trusted servers and restrict `allowed_tools` where possible. Remote credentials belong in each remote entry's `env` or inherited environment. Its `command` must be on the harness's PATH or be an absolute executable path.

## Local installation

### macOS / Linux

Python 3.10+ and Git are required:

```bash
git clone https://github.com/S3bRR/agent-tasker-mcp.git
cd agent-tasker-mcp
./setup.sh --client cursor --search-provider brave
```

The script creates `.venv`, installs the package, and prints configuration using its absolute Python path. It does not write harness settings. Keep the checkout/venv in place. Options: `--venv-dir PATH`, `--quiet`, `--recreate`, `--providers-file PATH`, `--mcp-config PATH`, `--searxng-url URL`, `--reader-fallback jina`.

### Windows (PowerShell)

Use the `uvx` configuration above, or install with Python 3.10+:

```powershell
git clone https://github.com/S3bRR/agent-tasker-mcp.git
cd agent-tasker-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\agent-tasker-mcp-server.exe --print-config cursor
```

Replace `cursor` with your client's name from the table. No environment activation is needed.

### Regenerate config

```bash
.venv/bin/agent-tasker-mcp-server --print-config codex --workers 10 --search-provider brave --mcp-config /absolute/path/to/servers.json
```

`--print-config` exits without starting the MCP server or contacting providers/remotes. It includes your options and makes file paths absolute. It never prints API keys or modifies settings. Run it from a stable installed environment, **not** an ephemeral `uvx` environment whose cached Python path may later disappear.

## Troubleshooting

- **Executable not found:** GUI apps may not inherit your terminal PATH. Use absolute paths for `uvx` and remote commands. The local install's generated config avoids this for AgentTasker itself.
- **Startup timeout:** first `uvx` launch may take longer to download/build. Warm its cache with `uvx --from git+https://github.com/S3bRR/agent-tasker-mcp.git agent-tasker-mcp-server --help`, or raise the client's startup timeout. Codex supports `startup_timeout_sec` in its server table.
- **Search key missing:** pass `BRAVE_SEARCH_API_KEY` through the harness environment and restart the server. Shell exports are not necessarily visible to GUI apps.
- **Unknown remote server:** supply AgentTasker's own `--mcp-config`; use `list_remote_tools` to check names and permissions.
- **Long batches:** use `execute_batch` with `wait: false`, then poll `get_batch`. Increasing concurrency does not change quotas or make a serial remote server parallel.
- **No HTTP URL to connect:** AgentTasker is a local stdio server, not a hosted HTTP endpoint. A client that supports only remote HTTP MCP cannot connect directly.

Configuration formats checked against official docs: [Claude Code](https://code.claude.com/docs/en/mcp), [Codex](https://developers.openai.com/codex/mcp/), [Cursor](https://cursor.com/docs/context/mcp), [VS Code](https://code.visualstudio.com/docs/copilot/reference/mcp-configuration), [OpenCode](https://opencode.ai/docs/mcp-servers/).
