"""Small field manifest used for both schemas and validation (not a JSON Schema engine)."""

from copy import deepcopy
import math
import re
from typing import Any

from .executors.discovery import render_provider_template
from .models import ALLOWED_HTTP_METHODS, TaskType


def field(kind: str, **constraints: Any) -> dict:
    return {"type": kind, **constraints}


STRING_MAP = field("object", additionalProperties=field("string"))
PROVIDER_FIELDS = {
    "name": field("string", minLength=1),
    "url_template": field("string", minLength=1, pattern=r"^https?://", description="HTTP(S) URL with {query_encoded} and {limit}"),
    "items_path": field("string", description="Dot path to results; empty for root array"),
    "title_path": field("string", minLength=1),
    "url_path": field("string", minLength=1),
    "snippet_path": field("string"),
    "errors_path": field("string", description="Optional dot path to upstream errors; partial results are returned but not cached"),
    "method": field("string", enum=sorted(ALLOWED_HTTP_METHODS), default="GET"),
    "headers": STRING_MAP,
    "headers_env": field("object", additionalProperties=field("string", minLength=1), description="Header names mapped to credential environment variables"),
    "body_template": field("string", description="Use {query_json}; double literal JSON braces"),
    "result_limit": field("integer", minimum=1),
    "requests_per_second": field("number", minimum=0.001, description="Shared provider rate limit; omit for no fixed rate"),
}
PROVIDER_REQUIRED = ["name", "url_template", "items_path", "title_path", "url_path"]
FIELDS = {
    "query": field("string", minLength=1),
    "providers": field("array", minItems=1, items=field("object", properties=PROVIDER_FIELDS, required=PROVIDER_REQUIRED, additionalProperties=False), description="Omit to use configured providers"),
    "max_results": field("integer", minimum=1, default=10),
    "fetch_top_results": field("integer", minimum=0, default=0),
    "fetch_max_chars": field("integer", minimum=1, default=4000),
    "cache": field("boolean", default=True, description="False bypasses both search caching and duplicate coalescing"),
    "reader_fallback": field("string", enum=["none", "jina"], default="none", description="Optional hosted reader for minimal-script pages/HTTP 403; jina requires operator opt-in and shares public URLs with Jina"),
    "timeout": field("integer", minimum=1, default=30),
    "retries": field("integer", minimum=0),
    "retry_backoff_seconds": field("number", minimum=0, default=1),
    "verify_ssl": field("boolean", default=True),
    "max_body_bytes": field("integer", minimum=1, default=2_000_000),
    "url": field("string", minLength=1, pattern=r"^https?://"),
    "max_links": field("integer", minimum=0, default=50),
    "max_text_chars": field("integer", minimum=1, default=20000),
    "include_html": field("boolean", default=False),
    "extract_links": field("boolean", default=True),
    "extract_headings": field("boolean", default=True),
    "link_include_pattern": field("string"),
    "server": field("string", minLength=1, description="Configured remote MCP server name"),
    "tool": field("string", minLength=1, description="Remote MCP tool name"),
    "arguments": field("object", default={}),
}
NETWORK_FIELDS = ("timeout", "retries", "retry_backoff_seconds", "verify_ssl", "max_body_bytes")
# (fields, required fields): execution routes remain instance-owned, not duplicated here.
TASK_SPECS = {
    TaskType.DISCOVERY_SEARCH: (("query", "providers", "max_results", "fetch_top_results", "fetch_max_chars", "cache", "reader_fallback", *NETWORK_FIELDS), ("query", "providers")),
    TaskType.WEB_SCRAPE: (("url", "max_links", "max_text_chars", "include_html", "extract_links", "extract_headings", "link_include_pattern", "reader_fallback", *NETWORK_FIELDS), ("url",)),
    TaskType.MCP_TOOL: (("server", "tool", "arguments", "timeout"), ("server", "tool")),
}


def validate_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a non-empty name")
    return value.strip()


def _validate(value: Any, spec: dict, path: str) -> None:
    kind = spec["type"]
    expected = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list, "object": dict}[kind]
    if not isinstance(value, expected) or (kind in {"integer", "number"} and isinstance(value, bool)):
        raise ValueError(f"'{path}' must be a {kind}")
    if kind == "number" and isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"'{path}' must be finite")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError(f"'{path}' must be one of {spec['enum']}")
    if "minimum" in spec and value < spec["minimum"]:
        raise ValueError(f"'{path}' must be >= {spec['minimum']}")
    if "maximum" in spec and value > spec["maximum"]:
        raise ValueError(f"'{path}' must be <= {spec['maximum']}")
    if "pattern" in spec and not re.match(spec["pattern"], value):
        raise ValueError(f"'{path}' must use HTTP(S)")
    if spec.get("minLength") and not value.strip():
        raise ValueError(f"'{path}' must be non-empty")
    if kind == "array":
        if len(value) < spec.get("minItems", 0):
            raise ValueError(f"'{path}' must be a non-empty array")
        for index, item in enumerate(value):
            _validate(item, spec["items"], f"{path}[{index}]")
    if kind == "object":
        for key in spec.get("required", ()):
            if key not in value or value[key] is None:
                raise ValueError(f"'{path}.{key}' is required")
        for key, item in value.items():
            child = spec.get("properties", {}).get(key, spec.get("additionalProperties"))
            if child is False:
                raise ValueError(f"Unknown field: {path}.{key}")
            if isinstance(child, dict):
                _validate(item, child, f"{path}.{key}")


def build_payload(task_type: TaskType, source: dict) -> dict:
    return {name: deepcopy(source[name] if name in source else FIELDS[name]["default"])
            for name in TASK_SPECS[task_type][0] if name in source or "default" in FIELDS[name]}


def validate_payload(task_type: TaskType, source: dict) -> dict:
    payload = build_payload(task_type, source)
    for name in TASK_SPECS[task_type][1]:
        if name not in payload:
            raise ValueError(f"'{name}' is required for {task_type.value}")
    for name, value in payload.items():
        _validate(value, FIELDS[name], name)
    if task_type == TaskType.DISCOVERY_SEARCH:
        for provider in payload["providers"]:
            for key in ("url_template", "body_template"):
                if key in provider:
                    try:
                        render_provider_template(provider[key], "test", 1)
                    except (KeyError, ValueError, IndexError, AttributeError) as exc:
                        raise ValueError(f"Invalid provider '{key}': {exc}") from exc
    if "link_include_pattern" in payload:
        try:
            re.compile(payload["link_include_pattern"])
        except re.error as exc:
            raise ValueError(f"Invalid link_include_pattern: {exc}") from exc
    return payload


CONTROL_FIELDS = {
    "output_mode": field("string", enum=["compact", "full"], default="compact"),
    "concurrency": field("integer", minimum=1, description="Parallel tasks, up to server worker limit"),
    "wait": field("boolean", default=True, description="False returns a background batch_id immediately"),
    "wait_seconds": field("integer", minimum=0, maximum=30, default=0),
}


def option(arguments: dict, name: str, **bounds: Any) -> Any:
    spec = {**CONTROL_FIELDS[name], **bounds}
    value = arguments.get(name, spec.get("default"))
    _validate(value, spec, name)
    return value


TASK_PROPERTIES = {
    "name": field("string", minLength=1),
    "task_type": field("string", enum=[item.value for item in TaskType]),
    **FIELDS,
    "depends_on": field("array", items=field("string", minLength=1)),
}


def check_keys(arguments: dict, properties: dict) -> None:
    unknown = arguments.keys() - properties.keys()
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")


def task_definition_schema() -> dict:
    return field("object", properties=deepcopy(TASK_PROPERTIES), required=["task_type"], additionalProperties=False,
        allOf=[{"if": {"properties": {"task_type": {"const": kind.value}}}, "then": {"required": [name for name in required if name != "providers"]}}
               for kind, (_, required) in TASK_SPECS.items()])


def execute_schema() -> dict:
    schema = task_definition_schema()
    schema["properties"]["output_mode"] = CONTROL_FIELDS["output_mode"]
    return schema


def execute_batch_schema() -> dict:
    return field("object", properties={
        "tasks": field("array", minItems=1, items=task_definition_schema()),
        **{name: deepcopy(CONTROL_FIELDS[name]) for name in ("concurrency", "wait", "output_mode")},
    }, required=["tasks"], additionalProperties=False)


def get_batch_schema() -> dict:
    return field("object", properties={
        "batch_id": field("string", minLength=1),
        **{name: CONTROL_FIELDS[name] for name in ("wait_seconds", "output_mode")},
    }, required=["batch_id"], additionalProperties=False)


def cancel_batch_schema() -> dict:
    return field("object", properties={"batch_id": field("string", minLength=1)}, required=["batch_id"], additionalProperties=False)


def remote_tools_schema() -> dict:
    return field("object", properties={"server": FIELDS["server"]}, additionalProperties=False,
                 description="Omit server to list configured server names; specify one to discover its tool schemas")


def output_schema(name: str) -> dict:
    properties = {
        "task": field("object"), "results": field("array", items=field("object")),
        "batch_id": field("string"), "status": field("string"), "error": field("string"),
        "servers": field("array", items=field("string")), "server": field("string"), "tools": field("array", items=field("object")),
    }
    required = {"execute": ["task"], "execute_batch": ["results"], "get_batch": ["batch_id", "results"],
                "cancel_batch": ["batch_id", "results"], "list_remote_tools": ["servers"]}[name]
    alternatives = [{"required": required}, {"required": ["error"]}]
    if name == "list_remote_tools":
        alternatives.append({"required": ["server", "tools"]})
    return field("object", properties=properties, anyOf=alternatives)
