"""Concurrent stdio MCP peer for real subprocess integration tests."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
from threading import Barrier, Event, Lock
import time

for stream in (sys.stdin, sys.stdout):
    stream.reconfigure(encoding="utf-8")

lock, barriers, cancelled, hang = Lock(), {}, [], Event()
pool = ThreadPoolExecutor(max_workers=10)


def send(message):
    with lock:
        print(json.dumps(message), flush=True)


def respond(message):
    method, params = message["method"], message.get("params", {})
    result = {}
    if method == "initialize":
        result = {"protocolVersion": os.getenv("PEER_PROTOCOL_VERSION", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
    elif method == "tools/list":
        names = ["fail", "hang", "inspect", "disconnect", "binary"] if params.get("cursor") else ["echo"]
        result = {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in names]}
        if not params.get("cursor"):
            result["nextCursor"] = "page-2"
    elif method == "tools/call":
        name, arguments = params["name"], params.get("arguments", {})
        if name == "disconnect":
            os._exit(0)
        if name == "hang":
            hang.wait(5)
        if arguments.get("parties"):
            with lock:
                barrier = barriers.setdefault(arguments["parties"], Barrier(arguments["parties"]))
            barrier.wait(5)
        time.sleep(arguments.get("delay", 0))
        data = {"cancelled": len(cancelled)} if name == "inspect" else arguments
        result = {"content": [{"type": "text", "text": json.dumps(data)}], "structuredContent": data, "isError": name == "fail"}
        if name == "binary":
            result["content"].append({"type": "image", "mimeType": "image/png", "data": "fixture"})
        send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}})
    if "id" in message:
        response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        send([response] if os.getenv("PEER_BATCH_RESPONSES") and method != "initialize" else response)


for line in sys.stdin:
    message = json.loads(line)
    if message["method"] == "notifications/cancelled":
        cancelled.append(message["params"]["requestId"])
        hang.set()
    elif message["method"] == "tools/call":
        pool.submit(respond, message)
    else:
        respond(message)
pool.shutdown()
