# AgentTasker MCP Server - Usage Examples

This document shows how AI agents can use the AgentTasker MCP tools.

## Example 1: Parallel API Calls

**Scenario**: Fetch data from multiple API endpoints simultaneously.

### Using execute_batch (Recommended)

```json
{
  "tool": "execute_batch",
  "arguments": {
    "tasks": [
      {
        "name": "users",
        "task_type": "http_request",
        "url": "https://jsonplaceholder.typicode.com/users",
        "method": "GET"
      },
      {
        "name": "posts",
        "task_type": "http_request",
        "url": "https://jsonplaceholder.typicode.com/posts",
        "method": "GET"
      },
      {
        "name": "comments",
        "task_type": "http_request",
        "url": "https://jsonplaceholder.typicode.com/comments",
        "method": "GET"
      }
    ]
  }
}
```

**Result**: All 3 requests complete in parallel (~1 second total instead of ~3 seconds).

---

## Example 2: Create Tasks Then Run

**Scenario**: Create tasks separately, then run them together.

### Step 1: Create Tasks

```json
{
  "tool": "create_task",
  "arguments": {
    "name": "fetch_weather_ny",
    "task_type": "http_request",
    "url": "https://api.weather.gov/points/40.7128,-74.0060"
  }
}
```

```json
{
  "tool": "create_task",
  "arguments": {
    "name": "fetch_weather_la",
    "task_type": "http_request",
    "url": "https://api.weather.gov/points/34.0522,-118.2437"
  }
}
```

### Step 2: Run All Pending Tasks

```json
{
  "tool": "run_tasks",
  "arguments": {}
}
```

---

## Example 3: Parallel Python Computation

**Scenario**: Run CPU-intensive calculations in parallel.

```json
{
  "tool": "execute_batch",
  "arguments": {
    "tasks": [
      {
        "name": "factorial_100",
        "task_type": "python_code",
        "code": "import math; result = math.factorial(100)"
      },
      {
        "name": "fibonacci_50",
        "task_type": "python_code",
        "code": "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\nresult = fib(50)"
      },
      {
        "name": "primes_1000",
        "task_type": "python_code",
        "code": "result = [n for n in range(2, 1000) if all(n % i != 0 for i in range(2, int(n**0.5)+1))]"
      }
    ]
  }
}
```

---

## Example 4: Parallel File Operations

**Scenario**: Read multiple configuration files simultaneously.

```json
{
  "tool": "execute_batch",
  "arguments": {
    "tasks": [
      {
        "name": "read_config",
        "task_type": "file_read",
        "path": "/etc/hostname"
      },
      {
        "name": "read_hosts",
        "task_type": "file_read",
        "path": "/etc/hosts"
      },
      {
        "name": "read_passwd",
        "task_type": "file_read",
        "path": "/etc/passwd"
      }
    ]
  }
}
```

---

## Example 5: Parallel Shell Commands

**Scenario**: Gather system information from multiple commands.

```json
{
  "tool": "execute_batch",
  "arguments": {
    "tasks": [
      {
        "name": "disk_usage",
        "task_type": "shell_command",
        "command": "df -h"
      },
      {
        "name": "memory_info",
        "task_type": "shell_command",
        "command": "free -m"
      },
      {
        "name": "cpu_info",
        "task_type": "shell_command",
        "command": "cat /proc/cpuinfo | grep 'model name' | head -1"
      },
      {
        "name": "uptime",
        "task_type": "shell_command",
        "command": "uptime"
      }
    ]
  }
}
```

---

## Example 6: POST Requests with JSON Body

**Scenario**: Send data to multiple API endpoints.

```json
{
  "tool": "execute_batch",
  "arguments": {
    "tasks": [
      {
        "name": "create_user_1",
        "task_type": "http_request",
        "url": "https://jsonplaceholder.typicode.com/posts",
        "method": "POST",
        "headers": {
          "Content-Type": "application/json"
        },
        "body": "{\"title\": \"Post 1\", \"body\": \"Content 1\", \"userId\": 1}"
      },
      {
        "name": "create_user_2",
        "task_type": "http_request",
        "url": "https://jsonplaceholder.typicode.com/posts",
        "method": "POST",
        "headers": {
          "Content-Type": "application/json"
        },
        "body": "{\"title\": \"Post 2\", \"body\": \"Content 2\", \"userId\": 2}"
      }
    ]
  }
}
```

---

## Example 7: Checking Task Status

**Scenario**: Monitor execution progress.

### Get Summary

```json
{
  "tool": "get_summary",
  "arguments": {}
}
```

**Response**:
```json
{
  "total_tasks": 10,
  "completed": 8,
  "failed": 1,
  "running": 1,
  "pending": 0,
  "success_rate": "88.9%",
  "total_duration_seconds": 5.23,
  "max_workers": 8
}
```

### List Failed Tasks

```json
{
  "tool": "list_tasks",
  "arguments": {
    "status": "failed"
  }
}
```

### Get Specific Task

```json
{
  "tool": "get_task",
  "arguments": {
    "task_id": "a1b2c3d4"
  }
}
```

---

## Example 8: Cleanup

**Scenario**: Free memory after processing.

### Clear All Tasks

```json
{
  "tool": "clear_tasks",
  "arguments": {}
}
```

### Clear Only Completed Tasks

```json
{
  "tool": "clear_tasks",
  "arguments": {
    "status": "completed"
  }
}
```

---

## Example 9: Complex Data Processing

**Scenario**: Download, process, and aggregate data.

```json
{
  "tool": "execute_batch",
  "arguments": {
    "tasks": [
      {
        "name": "download_data",
        "task_type": "http_request",
        "url": "https://jsonplaceholder.typicode.com/users"
      },
      {
        "name": "process_locally",
        "task_type": "python_code",
        "code": "data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]\nresult = {'sum': sum(data), 'avg': sum(data)/len(data), 'count': len(data)}"
      }
    ]
  }
}
```

---

## Example 10: Timeout Handling

**Scenario**: Set custom timeouts for slow operations.

```json
{
  "tool": "create_task",
  "arguments": {
    "name": "slow_api_call",
    "task_type": "http_request",
    "url": "https://httpbin.org/delay/5",
    "timeout": 10
  }
}
```

```json
{
  "tool": "create_task",
  "arguments": {
    "name": "long_computation",
    "task_type": "python_code",
    "code": "import time; time.sleep(2); result = 'done'",
    "timeout": 30
  }
}
```

---

## Best Practices

1. **Use execute_batch** when you have multiple tasks to run together
2. **Set appropriate timeouts** based on expected operation duration
3. **Check task status** before using results
4. **Handle failures gracefully** - some tasks may fail while others succeed
5. **Clear tasks periodically** to free memory
6. **Use descriptive task names** for easier debugging
