"""HTTP and scraping executors."""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any, Dict, Optional

from ..common import HTMLContentExtractor, fallback_html_extract
from ..models import DEFAULT_MAX_BODY_BYTES, RETRYABLE_HTTP_STATUSES
from ..version import package_version


def default_retries(method: str, retries: Optional[int]) -> int:
    if retries is not None:
        return retries
    return 2 if method.upper() in {"GET", "HEAD", "OPTIONS"} else 0


def request_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    merged = {"User-Agent": f"agent-tasker-mcp-server/{package_version()}"}
    if headers:
        merged.update(headers)
    return merged


def decode_json_response(response: Dict[str, Any], context: str) -> Dict[str, Any]:
    if response.get("status_code", 200) >= 400:
        raise RuntimeError(f"{context} returned HTTP {response['status_code']}")
    if response.get("body_truncated"):
        raise RuntimeError(f"{context} response exceeded max_body_bytes")
    body = response.get("body") or ""
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{context} returned invalid JSON: {exc}")


def _read_limited_body(stream: Any, max_body_bytes: int) -> tuple[bytes, bool]:
    body = stream.read(max_body_bytes + 1)
    if len(body) <= max_body_bytes:
        return body, False
    return body[:max_body_bytes], True


def _decode_body(body_bytes: bytes, headers: Any) -> str:
    charset = None
    if headers is not None and hasattr(headers, "get_content_charset"):
        charset = headers.get_content_charset()
    if not charset:
        charset = "utf-8"
    return body_bytes.decode(charset, errors="replace")


def _retryable_network_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, socket.timeout)) or isinstance(exc.reason, OSError)
    return False


class RateLimiter:
    """Shared provider spacing/cooldowns; waits release the lock."""

    def __init__(self):
        self._next = {}
        self._lock = Lock()

    def wait(self, key, requests_per_second, deadline):
        interval = 1 / requests_per_second if requests_per_second else 0
        while True:
            with self._lock:
                now = time.monotonic()
                delay = max(0, self._next.get(key, 0) - now)
                if not delay:
                    if interval:
                        self._reserve(key, now + interval)
                    return
            if delay >= deadline - time.monotonic():
                raise RuntimeError("Provider rate-limit wait exceeds request timeout")
            time.sleep(delay)

    def _reserve(self, key, until):
        if key not in self._next and len(self._next) >= 256:
            self._next = {key: value for key, value in self._next.items() if value > time.monotonic()}
            if len(self._next) >= 256:
                raise RuntimeError("Too many active provider rate limits")
        self._next[key] = until

    def pause(self, key, seconds):
        with self._lock:
            self._reserve(key, max(self._next.get(key, 0), time.monotonic() + seconds))


def retry_after_seconds(value):
    if not value:
        return None
    try:
        return max(0, float(value)) if value.strip().isdigit() else max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def execute_http_request(payload: Dict[str, Any], *, limiter=None, rate_key=None, requests_per_second=None) -> Dict[str, Any]:
    """Bounded HTTP fetch, retry/backoff, and optional shared provider throttling."""
    url, method = payload["url"], payload.get("method", "GET").upper()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only HTTP(S) URLs are supported")
    body = payload.get("body")
    req = urllib.request.Request(url, data=body.encode("utf-8") if body else None, headers=request_headers(payload.get("headers")), method=method)
    context = None
    if not payload.get("verify_ssl", True):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    retries = default_retries(method, payload.get("retries"))
    deadline = time.monotonic() + (payload.get("timeout") or 30)
    max_bytes = payload.get("max_body_bytes", DEFAULT_MAX_BODY_BYTES)
    for attempt in range(retries + 1):
        delay = payload.get("retry_backoff_seconds", 1) * (2**attempt)
        if limiter is not None:
            limiter.wait(rate_key, requests_per_second, deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("HTTP request timed out")
        try:
            with urllib.request.urlopen(req, timeout=remaining, context=context) as response:
                data, truncated = _read_limited_body(response, max_bytes)
                return {"status_code": response.status, "headers": dict(response.headers), "body": _decode_body(data, response.headers),
                        "body_bytes": len(data), "body_truncated": truncated, "url": response.url, "attempts": attempt + 1}
        except urllib.error.HTTPError as exc:
            try:
                data, truncated = _read_limited_body(exc, max_bytes)
                retry_after = retry_after_seconds(exc.headers.get("Retry-After")) if exc.headers else None
                if exc.code in RETRYABLE_HTTP_STATUSES:
                    delay = max(delay, retry_after or 0)
                    if limiter is not None and (retry_after is not None or exc.code in {429, 503}):
                        limiter.pause(rate_key, delay)
                if exc.code not in RETRYABLE_HTTP_STATUSES or attempt == retries:
                    return {"status_code": exc.code, "headers": dict(exc.headers or {}), "body": _decode_body(data, exc.headers),
                            "body_bytes": len(data), "body_truncated": truncated, "error": str(exc), "url": url, "attempts": attempt + 1}
            finally:
                exc.close()
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if attempt == retries or not _retryable_network_error(exc):
                raise RuntimeError(f"HTTP request failed after {attempt + 1} attempt(s): {exc}") from exc
        if delay >= deadline - time.monotonic():
            raise RuntimeError("HTTP retry/backoff exceeds request timeout")
        time.sleep(delay)


def execute_web_scrape(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch a webpage and extract lightweight visible content."""
    response = execute_http_request(
        {
            "url": payload["url"],
            "method": "GET",
            "timeout": payload.get("timeout") or 30,
            "verify_ssl": payload.get("verify_ssl", True),
            "max_body_bytes": payload.get("max_body_bytes", DEFAULT_MAX_BODY_BYTES),
            "retries": payload.get("retries"),
            "retry_backoff_seconds": payload.get("retry_backoff_seconds", 1),
            "headers": {
                "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
            },
        }
    )

    if response.get("status_code", 200) >= 400:
        raise RuntimeError(f"Web scrape returned HTTP {response['status_code']}: {payload['url']}")
    body = response.get("body", "")
    content_type = response.get("headers", {}).get("Content-Type") or response.get("headers", {}).get("content-type") or ""
    parser = HTMLContentExtractor(
        response.get("url", payload["url"]),
        max_links=payload.get("max_links", 50),
        link_include_pattern=payload.get("link_include_pattern"),
    )
    parser.feed(body)
    parser.close()
    extracted = parser.extract(max_text_chars=payload.get("max_text_chars", 20000))
    fallback = fallback_html_extract(
        response.get("url", payload["url"]),
        body,
        max_links=payload.get("max_links", 50),
        link_include_pattern=payload.get("link_include_pattern"),
    )
    extracted["title"] = fallback["title"] or extracted["title"]
    if not extracted["text"]:
        extracted["text"] = fallback["text"][: payload.get("max_text_chars", 20000)].rstrip()
        extracted["text_truncated"] = len(fallback["text"]) > payload.get("max_text_chars", 20000)
    if not extracted["links"]:
        extracted["links"] = fallback["links"]
    links = extracted["links"] if payload.get("extract_links", True) else []
    headings = extracted["headings"] if payload.get("extract_headings", True) else []

    result: Dict[str, Any] = {
        "url": payload["url"],
        "final_url": response.get("url", payload["url"]),
        "status_code": response.get("status_code"),
        "content_type": content_type,
        "body_truncated": response.get("body_truncated", False),
        "title": extracted["title"],
        "meta_description": extracted["meta_description"],
        "text": extracted["text"],
        "text_truncated": extracted["text_truncated"],
        "headings": headings,
        "links": links,
        "link_count": len(links),
    }
    if extracted.get("js_rendered_warning"):
        result["js_rendered_warning"] = extracted["js_rendered_warning"]
    if payload.get("include_html", False):
        max_text_chars = payload.get("max_text_chars", 20000)
        result["html"] = body[:max_text_chars]
        result["html_truncated"] = len(body) > max_text_chars
    return result
