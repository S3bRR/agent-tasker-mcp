# Search and website reading

[Setup](setup.md) · [Technical reference](reference.md)

AgentTasker uses the same HTTP endpoints you can call with curl, but performs requests through Python's standard library. There is no curl subprocess, browser installation, extra public tool, or third-party runtime dependency.

## SearXNG search

Use an existing instance you control, or follow [SearXNG's installation guide](https://docs.searxng.org/admin/installation.html). Enable JSON output in the instance's `settings.yml`, merging into its existing `search` section:

```yaml
search:
  formats:
    - html
    - json
```

Start AgentTasker with:

```bash
agent-tasker-mcp-server --search-provider searxng --searxng-url http://localhost:8080
```

Or generate client configuration without starting it:

```bash
.venv/bin/agent-tasker-mcp-server --print-config cursor --search-provider searxng --searxng-url https://search.example.org
```

`--searxng-url` defaults to `http://localhost:8080`; subpath deployments such as `https://search.example.org/searxng` are supported. No provider API key is required by this preset, but an instance may require authentication. For custom headers/authentication/rate limits, use a [provider file](../examples/searxng-providers.json) instead of the preset.

The equivalent curl request is:

```bash
curl -fsS --max-time 30 --get http://localhost:8080/search \
  --data-urlencode 'q=Python asyncio documentation' \
  --data-urlencode 'format=json'
```

Many public instances disable JSON (HTTP 403), restrict automation, or throttle requests. Self-hosting does not guarantee upstream engine availability. Engine errors are surfaced; partial results are retained but not cached, and all-failed empty searches are errors, not successful empty responses. The preset spaces requests at one/second; configure your actual quota in a provider file.

## Website analysis

Agents continue to use `web_scrape` tasks:

```json
{
  "task_type": "web_scrape",
  "url": "https://example.com/article",
  "output_mode": "full"
}
```

The retrieval pipeline is:

1. Request Markdown with `Accept: text/markdown`, while accepting HTML/plain text too.
2. Preserve Markdown/plain text and code verbatim; HTML is parsed locally into readable text, metadata, headings, and links.
3. If explicitly enabled, try Jina for a minimal-script page or an origin HTTP 403.

Results identify `source` (`direct` or `jina`) and `extraction` (`html`, `markdown`, or `text`). Compact page output keeps these fields and caps text at 2,000 characters with `truncated: true`. Full output respects `max_text_chars` (default 20,000), reports body/text truncation, and includes headings/metadata. `include_html` applies only to direct HTML responses, not Markdown or plain text. Markdown heading/link recognition is intentionally basic, not a full CommonMark parser; code fences are ignored when collecting headings and links. Markdown headings are bounded to 100 and links to `max_links`.

The agent performs the analysis; retrieval itself does not run an LLM. Visual design, interactions, login flows, and screenshots still need a browser-capable tool or configured remote MCP server.

## Optional hosted reader

Opt in at server startup:

```bash
agent-tasker-mcp-server --reader-fallback jina
```

Combine it with search and generated harness configuration:

```bash
./setup.sh --client codex --search-provider searxng --searxng-url http://localhost:8080 --reader-fallback jina
```

Jina's hosted service can render pages remotely and return extracted Markdown. Its quotas/authentication/pricing are external to AgentTasker. Supply `JINA_API_KEY` through the server environment if your plan/features require it; anonymous access may be limited. Reader requests share conservative process-wide spacing of one per three seconds, and HTTP retries honor `Retry-After` within the remaining page timeout. A reader POST is a read operation, so it may be retried (default two retries); `retries: 0` disables that. Direct fetch and reader fallback share the task's timeout; DNS/socket timeouts are still not a hard wall-clock guarantee.

Without operator opt-in, a task cannot turn on hosted fallback. When enabled, a task can disable it:

```json
{"task_type": "web_scrape", "url": "https://example.com", "reader_fallback": "none"}
```

Searches with `fetch_top_results` use the same page pipeline and configured default. Page context preserves provenance, warnings, and truncation. Unsuccessful reader context is not retained in the healthy search cache; changing reader credentials changes affected cache keys.

### Privacy and failure behavior

- **Off by default.** Enabling Jina discloses requested public URLs and their content to a third party. URLs may contain sensitive query strings; do not use hosted fallback for confidential pages.
- URL credentials, local/internal hostnames, and non-public IP/DNS destinations are rejected before relay. Both original and origin-redirect URLs are checked. These are best-effort disclosure checks, **not** a complete SSRF sandbox or a guarantee about the reader's own DNS/redirect behavior.
- Only the target URL is sent. Origin cookies/authentication headers are not forwarded; an optional Jina key goes only to Jina. Reader TLS verification is always enabled, even if origin verification was disabled.
- HTTP 401/404/429/5xx, TLS errors, and arbitrary direct network failures do not trigger fallback. Fallback is not a general CAPTCHA/access-control bypass.
- A failed fallback after successful static retrieval returns direct output with `reader_error` and the original warning. A failed HTTP 403 remains a failed task when fallback cannot recover it.
- Static extraction heuristics can miss dynamic pages or flag short script-bearing pages unnecessarily. Opt out per task when appropriate.
- Web content is untrusted data, not instructions. Respect site access rules and rate limits.

## Sources studied

- [Jina Reader](https://github.com/jina-ai/reader): HTTP URL reading, remote rendering, readable content, bounded responses.
- [SearXNG search API](https://docs.searxng.org/dev/search_api.html): JSON search transport and instance configuration.
- [agent-fetch](https://github.com/firede/agent-fetch): Markdown-first → static extraction → rendering fallback.
- [Implementation plan and verification record](web-retrieval-plan.md).
