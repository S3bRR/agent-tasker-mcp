# AgentTasker reference

[Quick start](../README.md) · [Harness setup](setup.md)

A search-first, stdio MCP server for **parallel searches and repeated calls to other MCP tools**. Zero third-party runtime dependencies; Python 3.10+.

- Ten concurrent tasks by default; the calling agent chooses concurrency.
- Synchronous results or background batches with progress and cancellation.
- One execution pool: providers and page fetches are sequential within each query.
- Bounded search caching, in-flight deduplication, provider throttling, and `Retry-After` handling.
- Persistent, multiplexed connections to explicitly configured **stdio** MCP servers.

## Development

```bash
./setup.sh --quiet
.venv/bin/python -m pip install '.[test]'
.venv/bin/python -m unittest discover -s tests
```

The optional test extra installs the official Python MCP SDK and JSON Schema validator for interoperability checks. They are **not runtime dependencies**. Without this extra, only those integration tests are skipped. For installation and client configuration, see the [setup guide](setup.md).

## Task types and tools

Task types:

| Type | Required inputs | Purpose |
| --- | --- | --- |
| `discovery_search` | `query`, plus configured or explicit `providers` | Search JSON APIs, deduplicate URLs, rank results, optionally fetch page context |
| `web_scrape` | `url` | Fetch a page and extract visible text, title, headings, and links |
| `mcp_tool` | `server`, `tool` | Call a configured remote MCP tool with an `arguments` object |

Five public tools:

- `execute`: run one task and wait for its result.
- `execute_batch`: run tasks concurrently; `wait: false` returns a `batch_id` immediately.
- `get_batch`: retrieve progress and finished results; optionally wait 0–30 seconds.
- `cancel_batch`: stop queued tasks; in-flight operations finish normally.
- `list_remote_tools`: list configured server names, or specify `server` to retrieve its tool schemas.

### Parallel background searches

Call `execute_batch`:

```json
{
  "wait": false,
  "concurrency": 10,
  "tasks": [
    {"name": "asyncio", "task_type": "discovery_search", "query": "Python asyncio documentation"},
    {"name": "threads", "task_type": "discovery_search", "query": "Python ThreadPoolExecutor documentation"}
  ]
}
```

See [a ten-query example](../examples/ten-searches.json). The agent chooses queries and a concurrency between 1 and `--workers` (default 10). Multiple batches share the execution pool. Lower concurrency helps reduce load, but it is **not** a requests-per-second rate limit.

Collect with `get_batch`:

```json
{"batch_id": "<returned ID>", "wait_seconds": 5}
```

Use `wait_seconds: 0` to return immediately; avoid tight polling. Positive waits occupy that MCP request until completion or the wait expires. The stdio reader remains responsive to ping and cancellation even during synchronous tool calls. Normal MCP clients may send separate concurrent `tools/call` requests; `execute_batch` is a convenience tool, not JSON-RPC array batching.

Compact responses include `batch_id`, `status`, `progress: {"done": 2, "total": 10}`, and **only finished tasks**, in input order. Task results omit internal IDs, timing metadata, null fields, and redundant counts. Search results retain URLs, titles, snippets, source attribution, context, and provider errors.

Set `output_mode: "full"` to retrieve all task placeholders, counters, timestamps, ranking scores, and original remote MCP result envelopes. Compact page text is capped at 2,000 characters with an explicit `truncated` flag. Compact remote results drop only JSON text that duplicates `structuredContent`; additional text, images, and other content are preserved.

Terminal statuses: `completed`, `failed`, `cancelled`. A failed task sets MCP `isError` without discarding successful results. A task may use `depends_on: ["another-task-name"]`; failed dependencies block downstream work. The entire batch is validated before anything runs.

Cancel with `cancel_batch` using the same `batch_id`. Cancellation is cooperative: HTTP requests and remote tool calls already in progress are not forcibly interrupted. Status remains `cancelling` until they finish. Shutdown cancels pending batches, drains in-flight work, and closes remote processes.

## HTTP search providers

Use `--search-provider brave` or `--search-provider searxng --searxng-url http://localhost:8080` for built-in setup ([details](web-retrieval.md)), or configure a JSON array with `--providers-file` or `AGENT_TASKER_PROVIDERS_FILE`. Task-level `providers` override defaults. See [the Brave configuration](../examples/brave-providers.json):

```json
[
  {
    "name": "brave",
    "url_template": "https://api.search.brave.com/res/v1/web/search?q={query_encoded}&count={limit}",
    "headers_env": {"X-Subscription-Token": "BRAVE_SEARCH_API_KEY"},
    "items_path": "web.results",
    "title_path": "title",
    "url_path": "url",
    "snippet_path": "description",
    "requests_per_second": 1
  }
]
```

Provide your API key through the server environment, not tool arguments. API keys, pricing, availability, and quotas are your responsibility. Choose `requests_per_second` for your plan; the example uses conservative one-request-per-second spacing. Ten searches can be submitted together, but throttling deliberately spaces their outbound requests.

Required provider fields are shown above except `headers_env`, `snippet_path`, and `requests_per_second`, which are optional. Also supported: `headers`, `method`, `body_template`, `result_limit`, and `errors_path` (upstream errors turn otherwise valid results into uncached partial results). Paths use dot notation, including numeric indexes; an empty `items_path` selects a root array.

Templates accept `{query_encoded}` for URLs, `{query_json}` for JSON-escaped text, `{query}` for raw text, and `{limit}`. Double literal braces in JSON bodies: `{{"query": {query_json}, "limit": {limit}}}`.

`max_results` defaults to 10. Set `fetch_top_results` to fetch page context and `fetch_max_chars` to bound text per page. Providers and context fetches run sequentially **within** a query; independent queries run in parallel. Ranking is lightweight lexical matching, not semantic reranking. HTTP failures are not reported as successful empty searches. Website context uses the [Markdown-first pipeline](web-retrieval.md#website-analysis); optional `reader_fallback` inherits the operator's setting and may be disabled per task. Hosted Jina fallback requires `--reader-fallback jina` at startup and is restricted to public URLs.

### Cache and throttling behavior

- Identical searches share one in-flight operation and reuse healthy results for 60 seconds.
- Cache is bounded by both entries (128) and serialized result bytes (8 MB); expired entries are removed lazily and oldest entries are evicted first.
- Failures and partial-provider/page-context failures are not retained. Credential changes create different cache keys.
- Set `cache: false` on a search to bypass both cached results and in-flight coalescing. Set any global cache limit to zero to disable caching entirely.
- Provider spacing and server-directed cooldowns are shared across searches/batches in this process. Configure each provider consistently so calls share the same limiter.
- Retryable HTTP errors honor numeric or HTTP-date `Retry-After`. If the required delay cannot fit the request timeout, the request fails instead of retrying early. Requests already in flight cannot be recalled.
- GET/HEAD/OPTIONS default to two retries; other methods default to none. `timeout` (default 30 seconds) budgets rate-limit waiting, backoff, and network attempts for each HTTP request; socket timeouts are not a hard whole-search deadline.

## Other MCP tools

AgentTasker cannot access another agent client's existing connections. Configure the servers it should connect to in a standard-shaped JSON file:

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

See [examples/fetch-mcp.json](../examples/fetch-mcp.json). The external server has its own installation requirements (`uvx` in this example). Configuration supports `command`, `args`, `env`, `cwd`, and optional `allowed_tools`. Commands use argument arrays, **not a shell**; the child inherits AgentTasker's environment plus explicit overrides. An omitted allowlist exposes all that server's tools; an empty allowlist permits none.

First call `list_remote_tools` with `{}` to list names, then `{"server": "fetch"}` to discover schemas. Run the same tool five times with `execute_batch`:

```json
{
  "wait": false,
  "concurrency": 5,
  "tasks": [
    {"name": "asyncio", "task_type": "mcp_tool", "server": "fetch", "tool": "fetch", "arguments": {"url": "https://docs.python.org/3/library/asyncio.html"}},
    {"name": "threads", "task_type": "mcp_tool", "server": "fetch", "tool": "fetch", "arguments": {"url": "https://docs.python.org/3/library/threading.html"}}
  ]
}
```

See [the complete five-call batch](../examples/five-mcp-calls.json). Calls use one persistent server process with separate request IDs, so replies may arrive out of order without getting mixed up. The remote server determines whether calls actually execute concurrently. A serial server will remain serial; AgentTasker does not spawn five copies automatically.

**Remote calls are never cached, coalesced, or retried**, even when arguments are identical: generic tools may have side effects. Timeouts send an MCP cancellation notification, but the remote operation may already have executed. Calls do not automatically restart disconnected servers; restart AgentTasker to reconnect safely.

This is a tools-only client supporting traditional stdio MCP versions **2025-11-25**, 2025-06-18, 2025-03-26, and 2024-11-05. It does not proxy resource/prompt endpoints, roots, sampling, or elicitation, and does not implement Streamable HTTP/SSE transport. Resource/image/audio content returned by tools is preserved and exposed as native outer MCP content blocks. Incoming remote messages are capped at 2 MB. Initialization/discovery use a 30-second response timeout; `mcp_tool.timeout` applies to the tool response after initialization. Initialization timeouts close the connection; they never send a forbidden initialization-cancellation notification.

## MCP compatibility

The implementation follows the traditional [`initialize` lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle), [tool/result contract](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), and [stdio framing rules](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports). Tests connect the official Python MCP client to AgentTasker, and AgentTasker to an official SDK server.

- Initialization validates `protocolVersion`, `capabilities`, and `clientInfo`; tools are unavailable until `notifications/initialized`. Unsupported versions negotiate a supported version rather than pretending to support newer revisions.
- For 2025-06-18 and newer supported versions, each UTF-8 newline-delimited message is one JSON-RPC object. [JSON-RPC array batching was removed in 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/changelog); use `execute_batch` instead. Negotiated older versions still accept legacy JSON-RPC arrays as their specifications require. Request IDs must be strings or integers, not null, booleans, arrays, or floats. Clients must use fresh IDs per session.
- Tool schemas use JSON Schema 2020-12 (the default MCP dialect), state task-specific required fields, and reject unknown options. Input mistakes and API failures return actionable `isError` tool results; malformed protocol requests and unknown tool names return JSON-RPC errors.
- Newer clients receive `structuredContent`, a matching serialized text block for compatibility, and output schemas. Older clients receive text results without unsupported structured-output fields.
- Tool titles and conservative annotations aid discovery. Generic execution tools are **not** marked read-only or safely repeatable because configured remote tools may mutate state. Annotations are hints, never authorization.
- The catalog is static and returned in one page; `listChanged` is false. Arbitrary pagination cursors are rejected rather than silently ignored.
- Standard `notifications/cancelled` suppresses the cancelled request's response and stops its queued work. Already-running HTTP/remote operations finish cooperatively. Cancelling a `get_batch` wait does not cancel the underlying batch; use `cancel_batch` for that.
- Background batches are application-level tools, **not native MCP Tasks**. No Tasks capability is advertised, and native task-augmented calls are rejected explicitly. Progress is collected with `get_batch`; push progress notifications are not currently emitted.

Only the explicitly listed protocol versions/features are claimed. The proxy delegates remote tool argument/output-schema semantics to the configured server; it validates the basic MCP result envelope/content shapes, not arbitrary external JSON Schemas. It does not advertise newer protocol revisions or unsupported capabilities.

## Limits and security

Environment variables:

| Variable | Default |
| --- | --- |
| `AGENT_TASKER_MAX_TASKS` | 1000 per batch |
| `AGENT_TASKER_MAX_PAYLOAD_BYTES` | 1,000,000 UTF-8 JSON bytes per task |
| `AGENT_TASKER_MAX_MEMORY_MB` | 0 (optional soft high-water RSS guard; unavailable on Windows) |
| `AGENT_TASKER_MAX_BATCHES` | 32 retained background batches |
| `AGENT_TASKER_BATCH_TTL_SECONDS` | 3600 completed-batch retention seconds |
| `AGENT_TASKER_CACHE_TTL_SECONDS` | 60 |
| `AGENT_TASKER_CACHE_MAX_ENTRIES` | 128 |
| `AGENT_TASKER_CACHE_MAX_BYTES` | 8,000,000 serialized result bytes |
| `AGENT_TASKER_PROVIDERS_FILE` | unset |
| `AGENT_TASKER_MCP_CONFIG` | unset |

Background results are process-local and disappear on restart. Completed batches may be evicted early to make room; active batches are never evicted. Full capacity of active batches rejects new submissions. Execution uses one worker pool plus bounded request/background coordination threads and one reader thread per connected remote server. At most twice `--workers` tool requests can be in flight; excess requests fail without blocking control messages. Incoming AgentTasker messages are capped at 8 MB, separate from the per-task payload limit.

Use only trusted local clients and configured servers. Remote commands execute software with your permissions; remote tools can read/write files or have other side effects even though AgentTasker's own local execution tools were removed. Prefer narrow allowlists. The proxy does not provide an approval UI or inherit the calling client's approval policy. Web content and remote results are untrusted data, not instructions. Do not expose this server to untrusted users.

## Migration and release

The search-first 2.x interface removes `python_code`, `shell_command`, `file_read`, `file_write`, and public `http_request` tasks. Use your agent's native tools or an explicitly configured MCP server instead. Compact output is intentionally smaller; use full mode if you need previous timing/count metadata. Search attribution is now `sources`, not duplicate `source_records`/`source_ids` arrays.

Releases remain tag-driven: keep `pyproject.toml` and [server.json](../server.json) versions aligned, push a matching `vX.Y.Z` tag, and GitHub Actions tests/builds/publishes to PyPI and the MCP Registry. No release is created by local setup.

MIT licensed. Repository: https://github.com/S3bRR/agent-tasker-mcp
