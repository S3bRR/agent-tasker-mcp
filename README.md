# AgentTasker MCP Server

A self-hosted MCP (Model Context Protocol) server that gives AI agents the power to execute tasks in parallel. Works with Claude, GPT, and any MCP-compatible AI agent.

## What is This?

AgentTasker is an MCP server that enables AI agents to:

- **Execute Python code** in parallel across multiple workers
- **Make HTTP requests** concurrently (API calls, web scraping)
- **Run shell commands** in parallel
- **Read/write files** simultaneously
- **Track task status** and aggregate results

Instead of running tasks sequentially, agents can now batch operations and execute them concurrently, dramatically improving throughput.

```
Sequential: 100 API calls × 1 second = 100 seconds
Parallel:   100 API calls ÷ 8 workers = ~13 seconds (8x faster)
```

## Quick Start

### 1. Install

```bash
git clone https://github.com/yourusername/agent-tasker-mcp.git
cd agent-tasker-mcp

# Option A: Quick setup script
chmod +x setup.sh && ./setup.sh

# Option B: Manual install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Your AI Client

**Claude Desktop** (`~/.config/claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "agent-tasker": {
      "command": "python",
      "args": ["/path/to/agent-tasker-mcp/mcp_server.py", "--workers", "8"],
      "env": {}
    }
  }
}
```

**Claude Code** (`~/.claude/settings.json`):

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

### 3. Use It

Once configured, your AI agent will have access to these tools:

| Tool | Description |
|------|-------------|
| `create_task` | Create a task (Python code, HTTP, shell, file ops) |
| `run_tasks` | Execute pending tasks in parallel |
| `execute_batch` | Create and run multiple tasks in one call |
| `get_task` | Get details of a specific task |
| `list_tasks` | List all tasks with optional status filter |
| `get_summary` | Get execution statistics |
| `clear_tasks` | Clear completed/all tasks |

## Usage Examples

### Example 1: Parallel API Calls

**Agent prompt**: "Fetch data from these 5 API endpoints in parallel"

The agent can use `execute_batch`:

```json
{
  "tasks": [
    {"name": "users", "task_type": "http_request", "url": "https://api.example.com/users"},
    {"name": "posts", "task_type": "http_request", "url": "https://api.example.com/posts"},
    {"name": "comments", "task_type": "http_request", "url": "https://api.example.com/comments"},
    {"name": "products", "task_type": "http_request", "url": "https://api.example.com/products"},
    {"name": "orders", "task_type": "http_request", "url": "https://api.example.com/orders"}
  ]
}
```

All 5 requests execute simultaneously instead of one-by-one.

### Example 2: Parallel Python Computation

**Agent prompt**: "Calculate prime numbers in ranges 1-10000, 10001-20000, and 20001-30000"

```json
{
  "tasks": [
    {
      "name": "primes_1",
      "task_type": "python_code",
      "code": "result = [n for n in range(1, 10001) if all(n % i != 0 for i in range(2, int(n**0.5)+1)) and n > 1]"
    },
    {
      "name": "primes_2",
      "task_type": "python_code",
      "code": "result = [n for n in range(10001, 20001) if all(n % i != 0 for i in range(2, int(n**0.5)+1))]"
    },
    {
      "name": "primes_3",
      "task_type": "python_code",
      "code": "result = [n for n in range(20001, 30001) if all(n % i != 0 for i in range(2, int(n**0.5)+1))]"
    }
  ]
}
```

### Example 3: Parallel File Processing

**Agent prompt**: "Read all config files in parallel"

```json
{
  "tasks": [
    {"name": "config1", "task_type": "file_read", "path": "/app/config/database.yml"},
    {"name": "config2", "task_type": "file_read", "path": "/app/config/redis.yml"},
    {"name": "config3", "task_type": "file_read", "path": "/app/config/app.yml"}
  ]
}
```

### Example 4: Parallel Shell Commands

**Agent prompt**: "Check disk usage, memory, and running processes"

```json
{
  "tasks": [
    {"name": "disk", "task_type": "shell_command", "command": "df -h"},
    {"name": "memory", "task_type": "shell_command", "command": "free -m"},
    {"name": "processes", "task_type": "shell_command", "command": "ps aux --sort=-%cpu | head -10"}
  ]
}
```

## MCP Tools Reference

### create_task

Create a single task for later execution.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Human-readable task name |
| `task_type` | string | Yes | One of: `python_code`, `http_request`, `shell_command`, `file_read`, `file_write` |
| `code` | string | For python_code | Python code to execute. Use `result = ...` to return data |
| `url` | string | For http_request | URL to request |
| `method` | string | No | HTTP method (GET, POST, PUT, DELETE, PATCH). Default: GET |
| `headers` | object | No | HTTP headers as key-value pairs |
| `body` | string | No | HTTP request body |
| `verify_ssl` | boolean | No | Verify SSL certificates (default: true). Set to false to bypass certificate errors |
| `command` | string | For shell_command | Shell command to execute |
| `path` | string | For file ops | File path to read/write |
| `content` | string | For file_write | Content to write |
| `mode` | string | No | Write mode: "w" (overwrite) or "a" (append). Default: w |
| `timeout` | integer | No | Timeout in seconds |

**Returns:**
```json
{
  "task_id": "a1b2c3d4",
  "name": "my_task",
  "task_type": "python_code",
  "status": "pending",
  "message": "Task 'my_task' created. Run with run_tasks to execute."
}
```

### run_tasks

Execute pending tasks in parallel.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_ids` | array | No | Specific task IDs to run. Default: all pending |

**Returns:**
```json
{
  "total": 5,
  "completed": 4,
  "failed": 1,
  "started_at": "2026-01-22T14:00:00",
  "completed_at": "2026-01-22T14:00:02",
  "tasks": {
    "a1b2c3d4": {
      "id": "a1b2c3d4",
      "name": "task_1",
      "task_type": "http_request",
      "status": "completed",
      "result": {"status_code": 200, "body": "..."},
      "error": null,
      "duration_seconds": 0.45
    }
  }
}
```

### execute_batch

Create and immediately run multiple tasks in one call. Best for running a batch of similar operations.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tasks` | array | Yes | Array of task definitions (same schema as create_task) |

### get_task

Get detailed information about a specific task.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | Yes | Task ID to retrieve |

### list_tasks

List all tasks with optional status filter.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `status` | string | No | Filter: `pending`, `running`, `completed`, `failed` |

### get_summary

Get execution statistics.

**Returns:**
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

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `status` | string | No | Only clear tasks with this status. Default: all |

## Task Types

### python_code

Execute Python code. Use the special `result` variable to return data.

```python
# Simple calculation
result = sum(range(100))

# Data processing
import json
data = [1, 2, 3, 4, 5]
result = {"sum": sum(data), "avg": sum(data)/len(data)}

# Available in namespace: json, time, datetime, Path
```

### http_request

Make HTTP requests with full control over method, headers, and body.

```json
{
  "task_type": "http_request",
  "url": "https://api.example.com/data",
  "method": "POST",
  "headers": {"Content-Type": "application/json", "Authorization": "Bearer token"},
  "body": "{\"key\": \"value\"}",
  "timeout": 30,
  "verify_ssl": true
}
```

Set `verify_ssl: false` to bypass SSL certificate verification (useful for local environments or self-signed certificates).

### shell_command

Run shell commands and capture output.

```json
{
  "task_type": "shell_command",
  "command": "ls -la /tmp",
  "timeout": 60
}
```

Returns: `{"stdout": "...", "stderr": "...", "return_code": 0}`

### file_read

Read file contents.

```json
{
  "task_type": "file_read",
  "path": "/path/to/file.txt"
}
```

Returns: `{"path": "...", "content": "...", "size_bytes": 1234}`

### file_write

Write content to a file.

```json
{
  "task_type": "file_write",
  "path": "/path/to/file.txt",
  "content": "Hello, World!",
  "mode": "w"
}
```

## Configuration

### Worker Count

Adjust based on your workload:

```bash
# I/O-bound (network, files) - more workers
python mcp_server.py --workers 16

# CPU-bound (computation) - fewer workers
python mcp_server.py --workers 4

# Mixed/default
python mcp_server.py --workers 8
```

### Performance Guidelines

| Workload Type | Recommended Workers | Example |
|--------------|---------------------|---------|
| API calls | 8-16 | Web scraping, REST APIs |
| File processing | 4-8 | Reading logs, configs |
| Computation | 2-4 | Data processing |
| Mixed | 8 | General purpose |

## Self-Hosting Options

### Direct Python

```bash
source venv/bin/activate
python mcp_server.py --workers 8
```

### With systemd

Create `/etc/systemd/system/agent-tasker.service`:

```ini
[Unit]
Description=AgentTasker MCP Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/agent-tasker-mcp
ExecStart=/path/to/agent-tasker-mcp/venv/bin/python mcp_server.py --workers 8
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable agent-tasker
sudo systemctl start agent-tasker
```

## How It Works

```
AI Agent
    |
    | MCP Protocol (JSON-RPC over stdio)
    v
+-------------------+
| AgentTasker MCP   |
| Server            |
+-------------------+
    |
    | Creates tasks
    v
+-------------------+
| Task Queue        |
| [task1, task2...] |
+-------------------+
    |
    | Distributes to workers
    v
+-------------------+
| Thread Pool       |
| [w1][w2][w3][w4]  |
+-------------------+
    |
    | Executes in parallel
    v
+-------------------+
| Results           |
| {task1: result1}  |
+-------------------+
    |
    | Returns to agent
    v
AI Agent
```

## Best Practices for Agents

1. **Batch similar operations**: Use `execute_batch` for multiple similar tasks
2. **Check results**: Always check task status before using results
3. **Clear when done**: Call `clear_tasks` after processing to free memory
4. **Use appropriate timeouts**: Set timeouts based on expected task duration
5. **Handle failures gracefully**: Some tasks may fail; process successful ones

## Troubleshooting

### Server not connecting

1. Check the path in your MCP config is absolute
2. Ensure Python virtual environment is set up
3. Verify mcp package is installed: `pip show mcp`

### Tasks timing out

Increase timeout in task definition:
```json
{"task_type": "http_request", "url": "...", "timeout": 60}
```

### High memory usage

Clear completed tasks periodically:
```json
{"tool": "clear_tasks", "arguments": {"status": "completed"}}
```

### Python code errors

Check the error field in task result:
```json
{
  "status": "failed",
  "error": "NameError: name 'undefined_var' is not defined"
}
```

## Requirements

- Python 3.10+
- MCP SDK (`pip install mcp`)
- No other dependencies (uses Python standard library)

## License

MIT License - See LICENSE file

## Version History

- **2.0.0** - MCP server implementation, universal AI agent support
- **1.0.0** - Original Python library
