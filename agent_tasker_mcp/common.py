"""Shared helpers for parsing, extraction, and output shaping."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urldefrag, urljoin, urlparse


class HTMLContentExtractor(HTMLParser):
    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    _SKIP_LINK_PREFIXES = ("javascript:", "mailto:", "tel:")

    def __init__(self, base_url: str, max_links: int = 50, link_include_pattern: Optional[str] = None):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.max_links = max_links
        self._link_include_re = re.compile(link_include_pattern) if link_include_pattern else None
        self.in_title = self.in_script = self.in_style = False
        self.script_tag_count = 0
        self.current_link_href: Optional[str] = None
        self.current_heading_tag: Optional[str] = None
        self.current_link_text: List[str] = []
        self.current_heading_text: List[str] = []
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []
        self.headings: List[Dict[str, str]] = []
        self.links: List[Dict[str, str]] = []
        self.seen_links = set()
        self.meta_description: Optional[str] = None

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(value.split()).strip()

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attr_map = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "script":
            self.in_script = True
            self.script_tag_count += 1
        elif tag == "style":
            self.in_style = True
        elif tag == "meta" and not self.meta_description:
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if name in {"description", "og:description"}:
                content = self._clean_text(attr_map.get("content") or "")
                if content:
                    self.meta_description = content
        elif tag == "a" and attr_map.get("href"):
            self.current_link_href = attr_map["href"]
            self.current_link_text = []
        elif tag in self._HEADING_TAGS:
            self.current_heading_tag = tag
            self.current_heading_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        elif tag == "script":
            self.in_script = False
        elif tag == "style":
            self.in_style = False
        elif tag == "a" and self.current_link_href:
            self._close_link()
        elif tag == self.current_heading_tag and self.current_heading_tag:
            heading_text = self._clean_text(" ".join(self.current_heading_text))
            if heading_text:
                self.headings.append({"level": self.current_heading_tag, "text": heading_text})
            self.current_heading_tag = None
            self.current_heading_text = []

    def _close_link(self) -> None:
        link_url = urljoin(self.base_url, self.current_link_href or "")
        link_url, _fragment = urldefrag(link_url)
        link_text = self._clean_text(" ".join(self.current_link_text))
        if (
            link_url
            and not link_url.lower().startswith(self._SKIP_LINK_PREFIXES)
            and link_url not in self.seen_links
            and (self._link_include_re is None or self._link_include_re.search(link_url))
            and len(self.links) < self.max_links
        ):
            self.links.append({"url": link_url, "text": link_text or link_url})
        if link_url:
            self.seen_links.add(link_url)
        self.current_link_href = None
        self.current_link_text = []

    def handle_data(self, data: str) -> None:
        if self.in_script or self.in_style:
            return
        cleaned = self._clean_text(data)
        if not cleaned:
            return
        if self.in_title:
            self.title_parts.append(cleaned)
        self.text_parts.append(cleaned)
        if self.current_link_href:
            self.current_link_text.append(cleaned)
        if self.current_heading_tag:
            self.current_heading_text.append(cleaned)

    def extract(self, max_text_chars: int = 20000) -> Dict[str, Any]:
        title = _strip_tags(" ".join(self.title_parts)) or None
        text = _strip_tags(" ".join(self.text_parts))
        truncated = len(text) > max_text_chars
        if truncated:
            text = text[:max_text_chars].rstrip()
        result = {
            "title": title,
            "meta_description": self.meta_description,
            "text": text,
            "text_truncated": truncated,
            "headings": self.headings,
            "links": self.links,
        }
        if self.script_tag_count > 5 and len(text) < 200:
            result["js_rendered_warning"] = (
                f"Page returned minimal text ({len(text)} chars) with {self.script_tag_count} "
                "script tags — likely requires JavaScript rendering. Consider using an API "
                "endpoint or a JS-capable scraper instead."
            )
        return result


def normalize_text(value: Optional[str]) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split())


def tokenize_text(value: Optional[str]) -> List[str]:
    normalized = normalize_text(value)
    return normalized.split() if normalized else []


def extract_domain(url: Optional[str]) -> str:
    try:
        return (urlparse(url or "").netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def get_nested_value(data: Any, path: str) -> Any:
    current = data
    for part in path.split(".") if path else []:
        if isinstance(current, list):
            if not part.isdigit() or not 0 <= int(part) < len(current):
                return None
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)(?:</title>|<)", re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(r"<a\b[^>]*href\s*=\s*(?:['\"]([^'\"]+)['\"]|([^'\" >]+))[^>]*>(.*?)(?:</a>|$)", re.IGNORECASE | re.DOTALL)


def _strip_tags(value: str) -> str:
    return HTMLContentExtractor._clean_text(_TAG_RE.sub(" ", value))


def fallback_html_extract(base_url: str, html: str, *, max_links: int = 50, link_include_pattern: Optional[str] = None) -> Dict[str, Any]:
    link_re = re.compile(link_include_pattern) if link_include_pattern else None
    title_match = _TITLE_RE.search(html)
    links: List[Dict[str, str]] = []
    seen = set()
    for match in _LINK_RE.finditer(html):
        if len(links) >= max_links:
            break
        href = (match.group(1) or match.group(2) or "").strip()
        if not href:
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if (
            not url
            or url.lower().startswith(HTMLContentExtractor._SKIP_LINK_PREFIXES)
            or url in seen
            or (link_re is not None and not link_re.search(url))
        ):
            continue
        links.append({"url": url, "text": _strip_tags(match.group(3) or "") or url})
        seen.add(url)
    text = _strip_tags(_SCRIPT_STYLE_RE.sub(" ", html))
    return {
        "title": _strip_tags(title_match.group(1)) if title_match else None,
        "text": text,
        "links": links,
    }


def _duplicate_text(item: Dict[str, Any], structured: Any) -> bool:
    if item.get("type") != "text":
        return False
    try:
        return json.loads(item.get("text", "")) == structured
    except (ValueError, TypeError):
        return False


def compact_task_result(task: Dict[str, Any]) -> Dict[str, Any]:
    compact = {key: task[key] for key in ("name", "status", "error") if task.get(key) is not None}
    result = task.get("result")
    if isinstance(result, dict) and task["task_type"] == "discovery_search":
        compact["result"] = {"results": [{key: value for key, value in item.items() if key not in {"score", "domain"} and value is not None} for item in result["results"]]}
        failures = [status for status in result["provider_statuses"] if status["status"] != "ok"]
        if failures:
            compact["result"]["provider_errors"] = failures
    elif isinstance(result, dict) and task["task_type"] == "web_scrape":
        compact["result"] = {key: result[key] for key in ("final_url", "title", "text", "links", "js_rendered_warning") if result.get(key)}
        # Preserve useful text, but signal any truncation explicitly.
        if len(result.get("text", "")) > 2000:
            compact["result"]["text"] = result["text"][:2000]
        if len(result.get("text", "")) > 2000 or result.get("text_truncated") or result.get("body_truncated"):
            compact["result"]["truncated"] = True
    elif isinstance(result, dict) and "structuredContent" in result:
        compact["result"] = {"structuredContent": result["structuredContent"]}
        extra_content = [item for item in result.get("content", []) if not _duplicate_text(item, result["structuredContent"])]
        if extra_content:
            compact["result"]["content"] = extra_content
        if result.get("isError"):
            compact["result"]["isError"] = True
    elif result is not None:
        compact["result"] = result
    return compact


def apply_output_mode(raw: Dict[str, Any], output_mode: str) -> Dict[str, Any]:
    if output_mode == "full":
        return raw
    compact = {key: raw[key] for key in ("batch_id", "status", "error") if raw.get(key) is not None}
    if "batch_id" in raw:
        compact["progress"] = {"done": raw["completed"] + raw["failed"] + raw["cancelled"], "total": raw["total"]}
    compact["results"] = [compact_task_result(task) for task in raw["results"] if task["status"] not in {"queued", "running"}]
    return compact
