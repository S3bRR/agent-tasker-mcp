# AgentTasker MCP

<!-- mcp-name: io.github.S3bRR/agent-tasker-mcp -->

**Parallel searches and MCP tool calls for your coding agent.**

Run ten searches or five calls to another MCP tool in one batch. Your agent picks concurrency up to your limit (default: **10**). Background batches, search caching, rate limiting, and compact results are built in.

Python 3.10+. **Zero third-party runtime dependencies.** Local stdio MCP transport.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, then pick your harness. `uvx` installs the server on first use; no clone needed.

### Claude Code

```bash
claude mcp add --scope user --transport stdio agent-tasker -- uvx --from git+https://github.com/S3bRR/agent-tasker-mcp.git agent-tasker-mcp-server
```

### Codex CLI / IDE extension

```bash
codex mcp add agent-tasker -- uvx --from git+https://github.com/S3bRR/agent-tasker-mcp.git agent-tasker-mcp-server
```

### Cursor / Claude Desktop / other JSON-based clients

Merge this into your MCP config. Cursor uses `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project). Claude Desktop: **Settings → Developer → Edit Config**.

```json
{
  "mcpServers": {
    "agent-tasker": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/S3bRR/agent-tasker-mcp.git", "agent-tasker-mcp-server"]
    }
  }
}
```

For Cursor, also add `"type": "stdio"` inside the `agent-tasker` entry.

**VS Code / Copilot:** use `.vscode/mcp.json`, change `mcpServers` to `servers`, and add `"type": "stdio"` inside the entry. **OpenCode:** [copy-ready config](docs/setup.md#opencode).

Restart your harness and enable/trust the server if prompted. You should see **five tools**: `execute`, `execute_batch`, `get_batch`, `cancel_batch`, and `list_remote_tools`.

> GUI app cannot find `uvx`? Set `command` to its full path (`command -v uvx` on macOS/Linux, `where.exe uvx` on Windows). First launch needs network access; increase your client's startup timeout if necessary.

## Enable search or other MCP tools

**Fetch webpages:** works immediately, without an API key. Try:

> Use AgentTasker to fetch these five URLs in parallel and summarize them. Use a background batch if it might take a while.

**Web search:** append `--search-provider brave` to the server arguments and supply `BRAVE_SEARCH_API_KEY` in your harness's server environment. No provider file needed. [Key setup and other providers →](docs/setup.md#web-search)

> Use AgentTasker to run ten different searches about Python asyncio with concurrency 10, in the background. Poll for results and give me a concise summary.

**Other MCP tools:** append `--mcp-config /absolute/path/to/servers.json`. Start from [this example](examples/fetch-mcp.json), then ask:

> Use AgentTasker to call the fetch server's fetch tool for five URLs with concurrency 5.

AgentTasker starts its **own configured stdio connections**; it cannot reuse your harness's existing MCP sessions. Remote tools are not cached or retried because they may have side effects. Provider quotas still apply; Brave's preset defaults to one request/second.

## Prefer a local install?

On macOS/Linux, with Python 3.10+:

```bash
git clone https://github.com/S3bRR/agent-tasker-mcp.git
cd agent-tasker-mcp
./setup.sh --client cursor
```

Replace `cursor` with `claude`, `codex`, `vscode`, `opencode`, or `generic`. The installer prints ready-to-paste config with absolute paths; it **never overwrites your settings**. Keep the checkout/virtual environment in place.

```bash
# Include search or other MCP servers in the generated config:
./setup.sh --client codex --search-provider brave --mcp-config /absolute/path/to/servers.json

# Regenerate config later, without reinstalling:
.venv/bin/agent-tasker-mcp-server --print-config vscode
```

[Windows installation and full harness setup →](docs/setup.md)

## Reference

- [Batch examples](examples/ten-searches.json) · [Repeated MCP calls](examples/five-mcp-calls.json)
- [Tools, limits, caching, cancellation, and migration](docs/reference.md)
- [MIT license](LICENSE)

Only connect servers you trust. Cancelling a batch skips queued work; already-running calls may finish. Background jobs and caches are process-local and disappear when the server restarts.
