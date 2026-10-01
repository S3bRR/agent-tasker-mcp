"""Sequential provider/page work; concurrency belongs across queries, not within them."""

import json
import os
from urllib.parse import urlencode

from ..common import extract_domain, get_nested_value, tokenize_text
from .http import decode_json_response, execute_http_request, execute_web_scrape


def render_provider_template(template: str, query: str, limit: int) -> str:
    return template.format(query=query, query_encoded=urlencode({"q": query})[2:], query_json=json.dumps(query), limit=limit)


def _network_options(payload: dict) -> dict:
    return {key: payload[key] for key in ("timeout", "retries", "retry_backoff_seconds", "verify_ssl", "max_body_bytes", "reader_fallback") if key in payload}


def execute_discovery_search(payload: dict, *, limiter=None) -> dict:
    query, limit = payload["query"], payload["max_results"]
    statuses, merged = [], {}
    options = _network_options(payload)
    for provider in payload["providers"]:
        name = provider["name"]
        try:
            headers = dict(provider.get("headers", {}))
            for header, env_name in provider.get("headers_env", {}).items():
                value = os.getenv(env_name)
                if not value:
                    raise RuntimeError(f"Missing environment variable: {env_name}")
                headers[header] = value
            rate_key = json.dumps(provider, sort_keys=True)
            response = execute_http_request({
                **options,
                "url": render_provider_template(provider["url_template"], query, limit),
                "method": provider.get("method", "GET"), "headers": headers,
                "body": render_provider_template(provider["body_template"], query, limit) if "body_template" in provider else None,
            }, limiter=limiter, rate_key=rate_key, requests_per_second=provider.get("requests_per_second"))
            data = decode_json_response(response, name)
            items = get_nested_value(data, provider["items_path"])
            if not isinstance(items, list):
                raise RuntimeError("items_path did not resolve to a list")
            count = 0
            for item in items[:provider.get("result_limit", limit)]:
                title, url = get_nested_value(item, provider["title_path"]), get_nested_value(item, provider["url_path"])
                if not isinstance(title, str) or not title.strip() or not isinstance(url, str) or not url.strip():
                    continue
                count += 1
                snippet = get_nested_value(item, provider.get("snippet_path", ""))
                candidate = merged.setdefault(url.strip(), {"title": title.strip(), "url": url.strip(),
                    "snippet": snippet if isinstance(snippet, str) else None, "domain": extract_domain(url), "sources": []})
                if name not in candidate["sources"]:
                    candidate["sources"].append(name)
                if not candidate["snippet"] and isinstance(snippet, str):
                    candidate["snippet"] = snippet
            errors = get_nested_value(data, provider["errors_path"]) if "errors_path" in provider else None
            statuses.append({"provider": name, "status": "partial" if errors else "ok", "candidates": count,
                             **({"error": json.dumps(errors, ensure_ascii=False)} if errors else {})})
        except Exception as exc:
            statuses.append({"provider": name, "status": "failed", "error": str(exc)})
    if not merged and all(status["status"] != "ok" for status in statuses):
        raise RuntimeError("Search failed across all providers: " + "; ".join(f"{s['provider']}: {s['error']}" for s in statuses))
    tokens = set(tokenize_text(query))
    for candidate in merged.values():
        title = set(tokenize_text(candidate["title"]))
        snippet = set(tokenize_text(candidate["snippet"]))
        candidate["score"] = round((70 * len(tokens & title) + 25 * len(tokens & snippet)) / max(1, len(tokens)) + 3 * len(candidate["sources"]), 3)
    results = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:limit]
    for candidate in results[:payload["fetch_top_results"]]:
        try:
            page = execute_web_scrape({**options, "url": candidate["url"], "max_text_chars": payload["fetch_max_chars"],
                                      "max_links": 0, "extract_links": False, "extract_headings": False}, limiter=limiter)
            candidate["page_context"] = {key: page[key] for key in ("title", "meta_description", "text", "final_url", "status_code",
                "source", "extraction", "text_truncated", "body_truncated", "reader_error", "fallback_reason", "js_rendered_warning") if key in page}
        except Exception as exc:
            candidate["page_context_error"] = str(exc)
    return {"query": query, "provider_statuses": statuses, "results": results}
