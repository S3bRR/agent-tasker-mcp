---
name: agent-tasker-mcp
description: MCP Server for parallel task execution. Gives AI agents the ability to execute Python code, HTTP requests, shell commands, and file operations concurrently across multiple workers.
homepage: https://github.com/agenttasker/agent-tasker-mcp
repository: https://github.com/agenttasker/agent-tasker-mcp
author: AgentTasker Contributors
version: 2.0.0
license: MIT
compatibility: Python 3.10+
metadata:
  mcp:
    transport: stdio
    tools:
      - create_task
      - run_tasks
      - execute_batch
      - get_task
      - list_tasks
      - get_summary
      - clear_tasks
  requires:
    bins: ["python3"]
    packages: ["mcp"]
---

# AgentTasker MCP Server

A Model Context Protocol (MCP) server that enables AI agents to execute tasks in parallel. Compatible with Claude, GPT, and any MCP-compliant AI system.

## What It Does

AgentTasker gives AI agents the power to:

1. **Execute tasks in parallel** - Run multiple operations concurrently instead of sequentially
2. **Run Python code** - Execute arbitrary Python with result capture
3. **Make HTTP requests** - Parallel API calls, web scraping
4. **Run shell commands** - System administration, build scripts
5. **File operations** - Read/write files concurrently

### Performance Impact

```
Without AgentTasker:  10 API calls x 1 second = 10 seconds
With AgentTasker:     10 API calls / 4 workers = ~2.5 seconds (4x faster)
```

## Installation

```bash
# Clone repository
git clone https://github.com/agenttasker/agent-tasker-mcp.git
cd agent-tasker-mcp

# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

### Claude Desktop

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agent-tasker": {
      "command": "/path/to/agent-tasker-mcp/venv/bin/python",
      "args": ["/path/to/agent-tasker-mcp/mcp_server.py", "--workers", "8"]
    }
  }
}
```

### Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "agent-tasker": {
      "command": "python",
      "args": ["/path/to/agent-tasker-mcp/mcp_server.py", "--workers", "8"]
    }
  }
}
```

### Generic MCP Client

```json
{
  "command": "python",
  "args": ["mcp_server.py", "--workers", "8"],
  "transport": "stdio"
}
```

## MCP Tools

### create_task

Create a task for later execution.

**Task Types:**
- `python_code` - Execute Python code string
- `http_request` - Make HTTP request
- `shell_command` - Run shell command
- `file_read` - Read file contents
- `file_write` - Write to file

**Example:**
```json
{
  "name": "fetch_users",
  "task_type": "http_request",
  "url": "https://api.example.com/users",
  "method": "GET",
  "timeout": 30
}
```

### run_tasks

Execute pending tasks in parallel.

**Example:**
```json
{
  "task_ids": ["a1b2c3d4", "e5f6g7h8"]
}
```

Or omit `task_ids` to run all pending tasks.

### execute_batch

Create and run multiple tasks in one call.

**Example:**
```json
{
  "tasks": [
    {"name": "api_1", "task_type": "http_request", "url": "https://api.example.com/1"},
    {"name": "api_2", "task_type": "http_request", "url": "https://api.example.com/2"},
    {"name": "api_3", "task_type": "http_request", "url": "https://api.example.com/3"}
  ]
}
```

### get_task

Get details of a specific task.

```json
{"task_id": "a1b2c3d4"}
```

### list_tasks

List all tasks, optionally filtered by status.

```json
{"status": "completed"}
```

### get_summary

Get execution statistics.

**Response:**
```json
{
  "total_tasks": 100,
  "completed": 95,
  "failed": 5,
  "running": 0,
  "pending": 0,
  "success_rate": "95.0%",
  "total_duration_seconds": 12.34,
  "max_workers": 8
}
```

### clear_tasks

Clear tasks from memory.

```json
{"status": "completed"}
```

## Task Types in Detail

### python_code

Execute Python code. Use `result` variable to return data.

```json
{
  "task_type": "python_code",
  "code": "import math; result = {'pi': math.pi, 'e': math.e}",
  "timeout": 30
}
```

**Available in namespace:** `json`, `time`, `datetime`, `Path`, `__import__`

### http_request

Make HTTP requests.

```json
{
  "task_type": "http_request",
  "url": "https://api.example.com/data",
  "method": "POST",
  "headers": {"Authorization": "Bearer token"},
  "body": "{\"key\": \"value\"}",
  "timeout": 30
}
```

**Response:**
```json
{
  "status_code": 200,
  "headers": {"Content-Type": "application/json"},
  "body": "...",
  "url": "https://api.example.com/data"
}
```

### shell_command

Run shell commands.

```json
{
  "task_type": "shell_command",
  "command": "ls -la /tmp",
  "timeout": 60
}
```

**Response:**
```json
{
  "stdout": "...",
  "stderr": "",
  "return_code": 0,
  "command": "ls -la /tmp"
}
```

### file_read

Read file contents.

```json
{
  "task_type": "file_read",
  "path": "/path/to/file.txt"
}
```

### file_write

Write to file.

```json
{
  "task_type": "file_write",
  "path": "/path/to/file.txt",
  "content": "Hello, World!",
  "mode": "w"
}
```

## Use Cases

### Parallel API Integration

Agent can fetch from multiple endpoints simultaneously:

```
User: "Get user data from all 5 services"
Agent: Uses execute_batch with 5 http_request tasks
Result: All 5 responses in ~1 second instead of ~5 seconds
```

### Batch Data Processing

Process multiple files concurrently:

```
User: "Analyze all log files in /var/log"
Agent: Uses execute_batch with file_read tasks
Result: All files read in parallel
```

### System Health Checks

Run multiple diagnostic commands:

```
User: "Check system health"
Agent: Uses execute_batch with shell_command tasks for df, free, uptime
Result: All metrics gathered simultaneously
```

### Web Scraping

Scrape multiple pages in parallel:

```
User: "Get prices from these 10 product URLs"
Agent: Uses execute_batch with http_request tasks
Result: All pages fetched concurrently
```

## Configuration Options

### Worker Count

```bash
python mcp_server.py --workers 8
```

| Workload | Workers | Example |
|----------|---------|---------|
| I/O heavy | 8-16 | API calls, file I/O |
| CPU heavy | 2-4 | Computation |
| Mixed | 4-8 | General purpose |

## Error Handling

Tasks that fail don't block others:

```json
{
  "tasks": {
    "task_1": {"status": "completed", "result": {...}},
    "task_2": {"status": "failed", "error": "Connection timeout"}
  }
}
```

## Best Practices

1. **Use execute_batch** for multiple similar tasks
2. **Set appropriate timeouts** for each task type
3. **Clear completed tasks** to free memory
4. **Check status** before using task results
5. **Handle failures** gracefully - some tasks may fail

## Limitations

- Tasks are independent (no dependencies between tasks)
- Results stored in memory (clear periodically)
- Python code runs in isolated namespace
- Thread-based (GIL affects CPU-bound Python code)

## Version History

### 2.0.0
- Complete rewrite as MCP server
- Universal AI agent compatibility
- Added task types: python_code, http_request, shell_command, file_read, file_write
- execute_batch for convenience
- Improved error handling and reporting

### 1.0.0
- Initial Python library release
