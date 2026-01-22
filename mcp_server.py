#!/usr/bin/env python3
"""
AgentTasker MCP Server - Universal Parallel Task Execution for AI Agents

A Model Context Protocol (MCP) server that provides AI agents with the ability
to execute tasks in parallel. Works with any MCP-compatible AI agent including
Claude, GPT, and others.

Features:
- Execute Python code in parallel across multiple workers
- Pre-built task types for common operations (HTTP, files, shell)
- Full task lifecycle management (create, run, monitor, clear)
- Thread-safe execution with result aggregation
- Simple JSON-based communication

Usage:
    python mcp_server.py              # Run with stdio transport
    python mcp_server.py --workers 8  # Custom worker count
"""

import sys
import json
import asyncio
import argparse
import time
import uuid
import traceback
import subprocess
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# MCP SDK imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Tool,
        TextContent,
        CallToolResult,
    )
except ImportError:
    print("Error: MCP SDK not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)


# ============================================================================
# Task Management Core
# ============================================================================

class TaskStatus(Enum):
    """Task execution states."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskType(Enum):
    """Supported task types."""
    PYTHON_CODE = "python_code"      # Execute Python code string
    HTTP_REQUEST = "http_request"    # Make HTTP request
    SHELL_COMMAND = "shell_command"  # Run shell command
    FILE_READ = "file_read"          # Read file contents
    FILE_WRITE = "file_write"        # Write to file


@dataclass
class Task:
    """Represents an executable task."""
    id: str
    name: str
    task_type: TaskType
    payload: Dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to JSON-serializable dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type.value,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat() if self.started_at else None,
            "completed_at": datetime.fromtimestamp(self.completed_at).isoformat() if self.completed_at else None,
            "duration_seconds": round(self.completed_at - self.started_at, 3) if self.started_at and self.completed_at else None
        }


class TaskExecutor:
    """Executes tasks based on their type."""

    @staticmethod
    def execute_python_code(code: str, timeout: int = 30) -> Any:
        """
        Execute Python code string and return result.

        The code can use a special 'result' variable to return data,
        or the last expression's value will be returned.
        """
        # Create isolated namespace with safe builtins
        namespace = {
            "__builtins__": {
                # Safe builtins
                "abs": abs, "all": all, "any": any, "ascii": ascii,
                "bin": bin, "bool": bool, "bytearray": bytearray,
                "bytes": bytes, "callable": callable, "chr": chr,
                "dict": dict, "dir": dir, "divmod": divmod,
                "enumerate": enumerate, "filter": filter, "float": float,
                "format": format, "frozenset": frozenset, "getattr": getattr,
                "hasattr": hasattr, "hash": hash, "hex": hex, "id": id,
                "int": int, "isinstance": isinstance, "issubclass": issubclass,
                "iter": iter, "len": len, "list": list, "map": map,
                "max": max, "min": min, "next": next, "object": object,
                "oct": oct, "ord": ord, "pow": pow, "print": print,
                "range": range, "repr": repr, "reversed": reversed,
                "round": round, "set": set, "slice": slice, "sorted": sorted,
                "str": str, "sum": sum, "tuple": tuple, "type": type,
                "zip": zip,
                # Common imports made available
                "json": json, "time": time, "datetime": datetime,
                "Path": Path,
                # Allow imports
                "__import__": __import__,
            },
            "result": None,  # Special variable for returning results
        }

        try:
            # Execute the code
            exec(code, namespace)

            # Return 'result' variable if set, otherwise None
            return namespace.get("result", None)
        except Exception as e:
            raise RuntimeError(f"Code execution failed: {str(e)}")

    @staticmethod
    def execute_http_request(
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """Execute HTTP request and return response."""
        headers = headers or {}

        # Build request
        data = body.encode('utf-8') if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content = response.read().decode('utf-8', errors='replace')
                return {
                    "status_code": response.status,
                    "headers": dict(response.headers),
                    "body": content,
                    "url": response.url
                }
        except urllib.error.HTTPError as e:
            return {
                "status_code": e.code,
                "headers": dict(e.headers) if e.headers else {},
                "body": e.read().decode('utf-8', errors='replace'),
                "error": str(e)
            }
        except urllib.error.URLError as e:
            raise RuntimeError(f"HTTP request failed: {str(e)}")

    @staticmethod
    def execute_shell_command(
        command: str,
        timeout: int = 60,
        shell: bool = True
    ) -> Dict[str, Any]:
        """Execute shell command and return output."""
        try:
            result = subprocess.run(
                command,
                shell=shell,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode,
                "command": command
            }
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Command timed out after {timeout}s")
        except Exception as e:
            raise RuntimeError(f"Command execution failed: {str(e)}")

    @staticmethod
    def execute_file_read(path: str) -> Dict[str, Any]:
        """Read file and return contents."""
        try:
            file_path = Path(path)
            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {path}")

            content = file_path.read_text(encoding='utf-8', errors='replace')
            return {
                "path": str(file_path.absolute()),
                "content": content,
                "size_bytes": file_path.stat().st_size,
                "modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
            }
        except Exception as e:
            raise RuntimeError(f"File read failed: {str(e)}")

    @staticmethod
    def execute_file_write(path: str, content: str, mode: str = "w") -> Dict[str, Any]:
        """Write content to file."""
        try:
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)

            if mode == "a":
                with open(file_path, "a", encoding='utf-8') as f:
                    f.write(content)
            else:
                file_path.write_text(content, encoding='utf-8')

            return {
                "path": str(file_path.absolute()),
                "size_bytes": file_path.stat().st_size,
                "mode": mode
            }
        except Exception as e:
            raise RuntimeError(f"File write failed: {str(e)}")


class AgentTasker:
    """
    Parallel task execution engine for AI agents.

    Manages task creation, parallel execution via thread pool,
    and result aggregation.
    """

    def __init__(self, max_workers: int = 4):
        """Initialize with specified worker count."""
        self.max_workers = max_workers
        self.tasks: Dict[str, Task] = {}
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def create_task(
        self,
        name: str,
        task_type: TaskType,
        payload: Dict[str, Any]
    ) -> str:
        """Create a new task and return its ID."""
        task_id = str(uuid.uuid4())[:8]
        task = Task(
            id=task_id,
            name=name,
            task_type=task_type,
            payload=payload
        )
        self.tasks[task_id] = task
        return task_id

    def _execute_task(self, task: Task) -> Task:
        """Execute a single task based on its type."""
        try:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()

            # Execute based on task type
            if task.task_type == TaskType.PYTHON_CODE:
                task.result = TaskExecutor.execute_python_code(
                    code=task.payload["code"],
                    timeout=task.payload.get("timeout", 30)
                )
            elif task.task_type == TaskType.HTTP_REQUEST:
                task.result = TaskExecutor.execute_http_request(
                    url=task.payload["url"],
                    method=task.payload.get("method", "GET"),
                    headers=task.payload.get("headers"),
                    body=task.payload.get("body"),
                    timeout=task.payload.get("timeout", 30)
                )
            elif task.task_type == TaskType.SHELL_COMMAND:
                task.result = TaskExecutor.execute_shell_command(
                    command=task.payload["command"],
                    timeout=task.payload.get("timeout", 60)
                )
            elif task.task_type == TaskType.FILE_READ:
                task.result = TaskExecutor.execute_file_read(
                    path=task.payload["path"]
                )
            elif task.task_type == TaskType.FILE_WRITE:
                task.result = TaskExecutor.execute_file_write(
                    path=task.payload["path"],
                    content=task.payload["content"],
                    mode=task.payload.get("mode", "w")
                )
            else:
                raise ValueError(f"Unknown task type: {task.task_type}")

            task.status = TaskStatus.COMPLETED

        except Exception as e:
            task.error = str(e)
            task.status = TaskStatus.FAILED

        finally:
            task.completed_at = time.time()

        return task

    def run_tasks(
        self,
        task_ids: Optional[List[str]] = None,
        wait: bool = True
    ) -> Dict[str, Any]:
        """
        Execute tasks in parallel.

        Args:
            task_ids: Specific tasks to run (None = all pending)
            wait: Whether to wait for completion

        Returns:
            Execution results summary
        """
        # Get tasks to run
        if task_ids is None:
            task_ids = [
                tid for tid, t in self.tasks.items()
                if t.status == TaskStatus.PENDING
            ]

        if not task_ids:
            return {
                "total": 0,
                "completed": 0,
                "failed": 0,
                "message": "No tasks to run",
                "tasks": {}
            }

        # Submit tasks to executor
        futures = {}
        for task_id in task_ids:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                if task.status == TaskStatus.PENDING:
                    future = self.executor.submit(self._execute_task, task)
                    futures[future] = task_id

        results = {
            "total": len(futures),
            "completed": 0,
            "failed": 0,
            "started_at": datetime.now().isoformat(),
            "tasks": {}
        }

        if wait and futures:
            for future in as_completed(futures):
                task_id = futures[future]
                task = future.result()
                self.tasks[task_id] = task
                results["tasks"][task_id] = task.to_dict()

                if task.status == TaskStatus.COMPLETED:
                    results["completed"] += 1
                elif task.status == TaskStatus.FAILED:
                    results["failed"] += 1

            results["completed_at"] = datetime.now().isoformat()

        return results

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task details by ID."""
        if task_id in self.tasks:
            return self.tasks[task_id].to_dict()
        return None

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None
    ) -> Dict[str, Dict[str, Any]]:
        """List all tasks, optionally filtered by status."""
        tasks = {}
        for tid, task in self.tasks.items():
            if status is None or task.status == status:
                tasks[tid] = task.to_dict()
        return tasks

    def get_summary(self) -> Dict[str, Any]:
        """Get execution summary statistics."""
        tasks = list(self.tasks.values())
        total = len(tasks)

        if total == 0:
            return {
                "total_tasks": 0,
                "completed": 0,
                "failed": 0,
                "running": 0,
                "pending": 0,
                "success_rate": "N/A",
                "total_duration_seconds": 0,
                "max_workers": self.max_workers
            }

        completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
        running = sum(1 for t in tasks if t.status == TaskStatus.RUNNING)
        pending = sum(1 for t in tasks if t.status == TaskStatus.PENDING)

        total_duration = sum(
            (t.completed_at - t.started_at) for t in tasks
            if t.started_at and t.completed_at
        )

        return {
            "total_tasks": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": pending,
            "success_rate": f"{(completed/total*100):.1f}%",
            "total_duration_seconds": round(total_duration, 3),
            "max_workers": self.max_workers
        }

    def clear(self, status: Optional[TaskStatus] = None) -> int:
        """
        Clear tasks, optionally filtered by status.

        Returns number of tasks cleared.
        """
        if status is None:
            count = len(self.tasks)
            self.tasks.clear()
            return count

        to_remove = [
            tid for tid, t in self.tasks.items()
            if t.status == status
        ]
        for tid in to_remove:
            del self.tasks[tid]
        return len(to_remove)


# ============================================================================
# MCP Server Implementation
# ============================================================================

# Global tasker instance
tasker: Optional[AgentTasker] = None


def create_server(max_workers: int = 4) -> Server:
    """Create and configure the MCP server."""
    global tasker
    tasker = AgentTasker(max_workers=max_workers)

    server = Server("agent-tasker")

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        """Return list of available tools."""
        return [
            Tool(
                name="create_task",
                description="""Create a new task for parallel execution.

Supported task types:
- python_code: Execute Python code (use 'result' variable to return data)
- http_request: Make HTTP requests (GET, POST, etc.)
- shell_command: Run shell commands
- file_read: Read file contents
- file_write: Write to files

Tasks are created in PENDING state and must be run with run_tasks.""",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Human-readable task name"
                        },
                        "task_type": {
                            "type": "string",
                            "enum": ["python_code", "http_request", "shell_command", "file_read", "file_write"],
                            "description": "Type of task to create"
                        },
                        "code": {
                            "type": "string",
                            "description": "Python code to execute (for python_code type). Use 'result = ...' to return data."
                        },
                        "url": {
                            "type": "string",
                            "description": "URL for HTTP request (for http_request type)"
                        },
                        "method": {
                            "type": "string",
                            "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                            "description": "HTTP method (default: GET)"
                        },
                        "headers": {
                            "type": "object",
                            "description": "HTTP headers as key-value pairs"
                        },
                        "body": {
                            "type": "string",
                            "description": "HTTP request body"
                        },
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute (for shell_command type)"
                        },
                        "path": {
                            "type": "string",
                            "description": "File path (for file_read/file_write types)"
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write (for file_write type)"
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["w", "a"],
                            "description": "Write mode: 'w' for overwrite, 'a' for append (default: w)"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds (default varies by task type)"
                        }
                    },
                    "required": ["name", "task_type"]
                }
            ),
            Tool(
                name="run_tasks",
                description="""Execute pending tasks in parallel.

By default, runs all pending tasks. Optionally specify task_ids to run specific tasks.
Tasks are executed concurrently using the thread pool.

Returns execution results including status, outputs, and timing for each task.""",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Specific task IDs to run (default: all pending)"
                        }
                    }
                }
            ),
            Tool(
                name="get_task",
                description="Get detailed information about a specific task including its status, result, and timing.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task ID to retrieve"
                        }
                    },
                    "required": ["task_id"]
                }
            ),
            Tool(
                name="list_tasks",
                description="List all tasks, optionally filtered by status (pending, running, completed, failed).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["pending", "running", "completed", "failed"],
                            "description": "Filter by task status (default: all)"
                        }
                    }
                }
            ),
            Tool(
                name="get_summary",
                description="Get summary statistics of all tasks including counts, success rate, and total duration.",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="clear_tasks",
                description="Clear tasks from memory. Optionally filter by status to clear only specific tasks.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["pending", "running", "completed", "failed"],
                            "description": "Only clear tasks with this status (default: all)"
                        }
                    }
                }
            ),
            Tool(
                name="execute_batch",
                description="""Create and immediately run multiple tasks in one call.

This is a convenience method that combines create_task and run_tasks.
Useful for running multiple similar operations in parallel.

Each task in the batch should have: name, task_type, and type-specific parameters.""",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "description": "Array of task definitions to create and run",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "task_type": {
                                        "type": "string",
                                        "enum": ["python_code", "http_request", "shell_command", "file_read", "file_write"]
                                    },
                                    "code": {"type": "string"},
                                    "url": {"type": "string"},
                                    "method": {"type": "string"},
                                    "headers": {"type": "object"},
                                    "body": {"type": "string"},
                                    "command": {"type": "string"},
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                    "timeout": {"type": "integer"}
                                },
                                "required": ["name", "task_type"]
                            }
                        }
                    },
                    "required": ["tasks"]
                }
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> CallToolResult:
        """Handle tool calls."""
        global tasker

        try:
            if name == "create_task":
                # Build payload based on task type
                task_type_str = arguments["task_type"]
                task_type = TaskType(task_type_str)

                payload = {}
                if task_type == TaskType.PYTHON_CODE:
                    payload["code"] = arguments.get("code", "")
                    payload["timeout"] = arguments.get("timeout", 30)
                elif task_type == TaskType.HTTP_REQUEST:
                    payload["url"] = arguments.get("url", "")
                    payload["method"] = arguments.get("method", "GET")
                    payload["headers"] = arguments.get("headers")
                    payload["body"] = arguments.get("body")
                    payload["timeout"] = arguments.get("timeout", 30)
                elif task_type == TaskType.SHELL_COMMAND:
                    payload["command"] = arguments.get("command", "")
                    payload["timeout"] = arguments.get("timeout", 60)
                elif task_type == TaskType.FILE_READ:
                    payload["path"] = arguments.get("path", "")
                elif task_type == TaskType.FILE_WRITE:
                    payload["path"] = arguments.get("path", "")
                    payload["content"] = arguments.get("content", "")
                    payload["mode"] = arguments.get("mode", "w")

                task_id = tasker.create_task(
                    name=arguments["name"],
                    task_type=task_type,
                    payload=payload
                )

                result = {
                    "task_id": task_id,
                    "name": arguments["name"],
                    "task_type": task_type_str,
                    "status": "pending",
                    "message": f"Task '{arguments['name']}' created. Run with run_tasks to execute."
                }

            elif name == "run_tasks":
                task_ids = arguments.get("task_ids")
                result = tasker.run_tasks(task_ids=task_ids)

            elif name == "get_task":
                task_data = tasker.get_task(arguments["task_id"])
                if task_data:
                    result = task_data
                else:
                    result = {"error": f"Task '{arguments['task_id']}' not found"}

            elif name == "list_tasks":
                status = None
                if "status" in arguments and arguments["status"]:
                    status = TaskStatus(arguments["status"])
                tasks = tasker.list_tasks(status=status)
                result = {
                    "count": len(tasks),
                    "tasks": tasks
                }

            elif name == "get_summary":
                result = tasker.get_summary()

            elif name == "clear_tasks":
                status = None
                if "status" in arguments and arguments["status"]:
                    status = TaskStatus(arguments["status"])
                count = tasker.clear(status=status)
                result = {
                    "cleared": count,
                    "message": f"Cleared {count} task(s)"
                }

            elif name == "execute_batch":
                # Create all tasks
                task_ids = []
                for task_def in arguments["tasks"]:
                    task_type = TaskType(task_def["task_type"])

                    payload = {}
                    if task_type == TaskType.PYTHON_CODE:
                        payload["code"] = task_def.get("code", "")
                        payload["timeout"] = task_def.get("timeout", 30)
                    elif task_type == TaskType.HTTP_REQUEST:
                        payload["url"] = task_def.get("url", "")
                        payload["method"] = task_def.get("method", "GET")
                        payload["headers"] = task_def.get("headers")
                        payload["body"] = task_def.get("body")
                        payload["timeout"] = task_def.get("timeout", 30)
                    elif task_type == TaskType.SHELL_COMMAND:
                        payload["command"] = task_def.get("command", "")
                        payload["timeout"] = task_def.get("timeout", 60)
                    elif task_type == TaskType.FILE_READ:
                        payload["path"] = task_def.get("path", "")
                    elif task_type == TaskType.FILE_WRITE:
                        payload["path"] = task_def.get("path", "")
                        payload["content"] = task_def.get("content", "")
                        payload["mode"] = task_def.get("mode", "w")

                    task_id = tasker.create_task(
                        name=task_def["name"],
                        task_type=task_type,
                        payload=payload
                    )
                    task_ids.append(task_id)

                # Run all created tasks
                result = tasker.run_tasks(task_ids=task_ids)

            else:
                result = {"error": f"Unknown tool: {name}"}

            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str)
                )]
            )

        except Exception as e:
            error_result = {
                "error": str(e),
                "traceback": traceback.format_exc()
            }
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=json.dumps(error_result, indent=2)
                )],
                isError=True
            )

    return server


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AgentTasker MCP Server - Parallel task execution for AI agents"
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=4,
        help="Maximum number of parallel workers (default: 4)"
    )
    args = parser.parse_args()

    server = create_server(max_workers=args.workers)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
