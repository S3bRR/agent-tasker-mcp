"""Shared traditional MCP wire rules; no draft/newer protocol claims."""

import json

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_MESSAGE_BYTES = 8_000_000


def valid_id(value):
    return isinstance(value, (str, int)) and not isinstance(value, bool)


def loads(text):
    def reject_constant(value):
        raise ValueError(f"Non-JSON numeric constant: {value}")

    return json.loads(text, parse_constant=reject_constant)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def validate_initialize(params):
    if not isinstance(params.get("protocolVersion"), str) or not params["protocolVersion"]:
        raise ValueError("protocolVersion must be a non-empty string")
    if not isinstance(params.get("capabilities"), dict):
        raise ValueError("capabilities must be an object")
    info = params.get("clientInfo")
    if not isinstance(info, dict) or any(not isinstance(info.get(key), str) or not info[key] for key in ("name", "version")):
        raise ValueError("clientInfo needs string name and version")


def validate_tool_result(result):
    if not isinstance(result.get("content"), list) or not all(isinstance(item, dict) and isinstance(item.get("type"), str) for item in result["content"]):
        raise RuntimeError("Remote tools/call must return a content array")
    fields = {"text": ("text",), "image": ("data", "mimeType"), "audio": ("data", "mimeType"),
              "resource_link": ("uri", "name"), "resource": ()}
    for item in result["content"]:
        if item["type"] not in fields or any(not isinstance(item.get(key), str) for key in fields[item["type"]]):
            raise RuntimeError("Invalid remote MCP content block")
        if item["type"] == "resource":
            resource = item.get("resource")
            if not isinstance(resource, dict) or not isinstance(resource.get("uri"), str) or not any(isinstance(resource.get(key), str) for key in ("text", "blob")):
                raise RuntimeError("Invalid remote MCP embedded resource")
        if any(key in item and not isinstance(item[key], dict) for key in ("annotations", "_meta")):
            raise RuntimeError("Invalid remote MCP content metadata")
    if "isError" in result and not isinstance(result["isError"], bool):
        raise RuntimeError("Remote isError must be a boolean")
    if "structuredContent" in result and not isinstance(result["structuredContent"], dict):
        raise RuntimeError("Remote structuredContent must be an object")
    return result
