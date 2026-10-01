# Web retrieval implementation plan

## Goal and boundaries

Add curl-compatible search and website-reading patterns without adding a curl subprocess, a browser, another public tool, or third-party runtime dependencies. Preserve the five MCP tools, shared worker pool, traditional MCP protocols, background batches, cache controls, and remote-call safety. Implementation was initially local-only. Commit and push to `main` were subsequently authorized; no tagged/package release is part of this work.

## Implementation

1. **SearXNG search**
   - Add `--search-provider searxng` with `--searxng-url` (default `http://localhost:8080`).
   - Build a normal JSON provider for `/search?q={query_encoded}&format=json`.
   - Validate the instance URL before startup/config generation. Preserve deployments under a URL subpath.
   - Reuse search ranking, deduplication, cache/coalescing, throttling, and page-context retrieval. Explain that the instance must enable JSON and upstream engines can fail or throttle.
2. **Markdown-first website retrieval**
   - Prefer Markdown through HTTP content negotiation, then HTML/plain text.
   - Preserve Markdown and code verbatim, extract bounded headings/links, and indicate the extraction method.
   - Retain structured HTML metadata. Run regex recovery only for malformed/empty extraction rather than every page. Preserve decoded literal text such as `<T>`.
3. **Explicit reader fallback**
   - Add operator opt-in `--reader-fallback jina`; tasks may disable it with `reader_fallback: none`, but cannot enable it when the operator has not opted in.
   - Fall back for successful minimal-script pages or HTTP 403, not authentication failures, missing pages, quota responses, TLS failures, or arbitrary network errors.
   - POST only the public target URL to `https://r.jina.ai/`; never forward origin headers/cookies. Optional reader authentication uses `JINA_API_KEY`.
   - Reject URL credentials, local/internal hostnames, and non-public DNS/IP destinations before contacting the reader. This is a best-effort disclosure guard, not a general SSRF sandbox; enabling a hosted reader still shares URLs with that service.
   - Always validate reader TLS; apply conservative shared spacing and existing HTTP bounds/retries. The direct request and reader share the page timeout budget.
   - Validate the reader's JSON/content envelope. Preserve direct output and a reader warning if optional fallback fails; a failed HTTP 403 must remain an error.
4. **Integration and output**
   - Apply configured fallback defaults to both page tasks and search-result context.
   - Preserve source, extraction, warnings, and truncation in compact output. Failed reader context must not enter the healthy search cache; cache keys account for reader credential changes.
   - Propagate options into all generated client configs and the local installer. Do not edit harness settings.
5. **Documentation**
   - Keep the README short. Add SearXNG setup, Jina opt-in/privacy/quotas, examples, and limitations in linked guides.
   - Cite the studied repositories: [Jina Reader](https://github.com/jina-ai/reader), [SearXNG](https://github.com/searxng/searxng), and [agent-fetch](https://github.com/firede/agent-fetch).

## Verification and optimization

- Regression tests: schemas/defaults, CLI/config generation, URL validation, Markdown/plain text/code fidelity, malformed HTML, request counts, truncation, fallback opt-in and triggers, DNS/privacy checks, TLS/auth isolation, timeout sharing, reader failures, and credential-sensitive cache behavior.
- Local HTTP integration: SearXNG-shaped JSON, native Markdown negotiation, search page context, and ordered background batches. Test mocked Jina transport deterministically without paid credentials or external traffic.
- Official SDK: initialize/list tools, validate schemas/results, and call the new retrieval workflow over stdio.
- Measure extraction cost before/after and compact/full response size; assert one fetch for good pages and one provider request for cached duplicates. Do not sacrifice correctness for a LOC/performance target.
- Run the full suite on available Python versions, check shell syntax, verify a clean installed entrypoint outside the checkout, and review the final diff.

## Completion record

Implementation and verification are complete. The follow-up publication request authorizes committing these changes, pushing to `main`, and updating the GitHub description.

- SearXNG preset/URL validation, Markdown-first extraction, opt-in Jina fallback, privacy checks, CLI/config/installer propagation, context/cache integration, examples, and documentation are implemented.
- **Python 3.14: 91 tests passed**, including four official MCP SDK/schema integration tests. **Python 3.12: 87 passed, four SDK tests skipped** because that interpreter does not have the optional SDK dependencies.
- Real local HTTP tests verified SearXNG-shaped results, UTF-8 query encoding, Markdown context, cache hits, and ten concurrently active page requests using a ten-party barrier. Jina responses/DNS were mocked to test rendering fallback, authentication/TLS isolation, quota options, malformed/truncated responses, remaining timeout, and disclosure rejection without external requests.
- A fresh `./setup.sh --client cursor --search-provider searxng --searxng-url http://localhost:8080 --reader-fallback jina --quiet` installation succeeded. Its generated configuration was launched **outside the checkout** through the official SDK, confirming search/context/cache behavior and the private-URL reader guard. Installed metadata has no third-party runtime requirements; the reader module is included and the removed basic executor is absent.
- Extraction benchmark against committed pre-change code: a 27,056-byte HTML fixture with 200 sections/links, five alternating samples of 100 iterations each. Median page extraction fell from **0.2832s to 0.2219s (21.6%)**; link-disabled page context fell from **0.2721s to 0.2108s (22.5%)**. These are local CPU microbenchmarks, not end-to-end network speed claims.
- On the same fixture, full result JSON was **27,139 bytes**, versus **5,233 bytes** for the compact task envelope (about 81% smaller). Healthy HTML uses one parsing pass; disabled/capped links skip unnecessary URL work. Good pages need one HTTP request, and an identical cached search/context pair produces no additional HTTP requests.
- Shell syntax, diff whitespace, and local documentation links/anchors were checked. Final diff reviewed.

Unverified: live Jina service behavior/quotas, a production SearXNG deployment and its upstream engines, and interactive harness UIs. The implementation retains the five-tool surface and zero third-party runtime dependencies.
