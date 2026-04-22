# AgentTasker MCP Server

<!-- mcp-name: io.github.s3brr/agent-tasker-mcp -->

AgentTasker is a small, stdio-only MCP server for AI agents that need to run multiple tasks quickly and get structured results back in one call.

It is intentionally narrow:

- two tools: `execute` and `execute_batch`
- local stdio transport only
- zero third-party runtime dependencies
- explicit dependency control with `depends_on`
- compact, model-friendly JSON responses

Repository: `https://github.com/S3bRR/agent-tasker-mcp`

## Why This Exists

Most agent orchestration layers are heavier than they need to be. This project is designed for the common case:

- run a few tasks in parallel
- let one task wait on another when needed
- keep the MCP surface small enough for models to use reliably

There is no queue service, no persistence layer, no background worker system, and no SDK dependency required at runtime.

## What It Supports

Task types:

- `python_code`
- `http_request`
- `discovery_search`
- `web_scrape`
- `shell_command`
- `file_read`
- `file_write`

Public MCP tools:

- `execute`
- `execute_batch`

## Install

### Recommended: `uvx`

After publishing to PyPI:

```bash
uvx agent-tasker-mcp-server --workers 8
```

Until then, run directly from GitHub:

```bash
uvx --from git+https://github.com/S3bRR/agent-tasker-mcp.git agent-tasker-mcp-server --workers 8
```

### `pipx`

After publishing to PyPI:

```bash
pipx install agent-tasker-mcp-server
```

Until then:

```bash
pipx install git+https://github.com/S3bRR/agent-tasker-mcp.git
```

### Local clone

```bash
git clone https://github.com/S3bRR/agent-tasker-mcp.git
cd agent-tasker-mcp
./setup.sh
```

## MCP Client Configuration

### Published package

```json
{
  "command": "uvx",
  "args": ["agent-tasker-mcp-server", "--workers", "8"]
}
```

### GitHub source

```json
{
  "command": "uvx",
  "args": [
    "--from",
    "git+https://github.com/S3bRR/agent-tasker-mcp.git",
    "agent-tasker-mcp-server",
    "--workers",
    "8"
  ]
}
```

### Local checkout

```json
{
  "command": "/absolute/path/to/agent-tasker-mcp/.venv/bin/agent-tasker-mcp-server",
  "args": ["--workers", "8"]
}
```

## Usage

### `execute`

Run one task immediately.

```json
{
  "task_type": "python_code",
  "code": "result = 6 * 7"
}
```

### `execute_batch`

Run multiple tasks concurrently.

```json
{
  "tasks": [
    {
      "name": "fetch_users",
      "task_type": "http_request",
      "url": "https://api.example.com/users"
    },
    {
      "name": "calc",
      "task_type": "python_code",
      "code": "result = 6 * 7"
    }
  ],
  "output_mode": "compact"
}
```

### `depends_on`

If one task must wait for another, make it explicit.

```json
{
  "tasks": [
    {
      "name": "write_file",
      "task_type": "file_write",
      "path": "/tmp/example.txt",
      "content": "hello"
    },
    {
      "name": "read_file",
      "task_type": "file_read",
      "path": "/tmp/example.txt",
      "depends_on": ["write_file"]
    }
  ]
}
```

If an upstream dependency fails, downstream tasks are marked failed and do not run.

## Output Shape

`output_mode` supports:

- `compact` (default)
- `full`

The response is ordered to match the input task list, which makes it easier for models to consume without extra reconciliation logic.

## Limits

Optional environment variables:

- `AGENT_TASKER_MAX_TASKS`: maximum tasks per `execute_batch`
- `AGENT_TASKER_MAX_PAYLOAD_BYTES`: maximum payload size per task
- `AGENT_TASKER_MAX_MEMORY_MB`: soft process memory guard

## Security Notes

This server is intended for trusted environments.

- `python_code` executes Python code
- `shell_command` executes shell commands
- `file_read` and `file_write` operate on the local filesystem

Do not expose this server directly to untrusted users.

## Development

Create a local environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

Run the server:

```bash
agent-tasker-mcp-server --workers 4
```

Run tests:

```bash
.venv/bin/python -m unittest discover -s tests
```

## Packaging

This repo includes [server.json](./server.json) for MCP Registry publication.

The intended public release model is:

1. publish `agent-tasker-mcp-server` to PyPI
2. keep the package version and `server.json` version identical
3. publish the MCP metadata to the MCP Registry

## License

MIT
