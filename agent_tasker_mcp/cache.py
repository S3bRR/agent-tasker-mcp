"""Bounded TTL/LRU cache and in-flight coalescing for searches only."""

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from hashlib import sha256
import json
import os
from threading import Lock
import time


class SearchCache:
    def __init__(self, ttl_seconds=60, max_entries=128, max_bytes=8_000_000):
        if min(ttl_seconds, max_entries, max_bytes) < 0:
            raise ValueError("Search cache limits must be non-negative")
        self.ttl, self.max_entries, self.max_bytes = ttl_seconds, max_entries, max_bytes
        self._cached, self._pending = OrderedDict(), {}
        self._bytes = 0
        self._lock = Lock()

    def run(self, payload, search):
        if not payload.get("cache", True) or not self.ttl or not self.max_entries or not self.max_bytes:
            return search(payload)
        # Include credential changes without retaining plaintext credential values in keys.
        credentials = {name: os.getenv(name) for provider in payload["providers"] for name in provider.get("headers_env", {}).values()}
        if payload.get("reader_fallback") == "jina" and payload.get("fetch_top_results"):
            credentials["JINA_API_KEY"] = os.getenv("JINA_API_KEY")
        key = sha256(json.dumps([payload, credentials], sort_keys=True).encode()).digest()
        with self._lock:
            for expired in [key for key, (until, _, _) in self._cached.items() if until <= time.monotonic()]:
                self._bytes -= self._cached.pop(expired)[2]
            if key in self._cached:
                self._cached.move_to_end(key)
                return deepcopy(self._cached[key][1])
            owner = key not in self._pending
            future = self._pending.setdefault(key, Future())
        if not owner:
            return deepcopy(future.result())
        try:
            result = search(payload)
            size = len(json.dumps(result, ensure_ascii=False).encode())
            healthy = all(s["status"] == "ok" for s in result["provider_statuses"]) and not any("page_context_error" in r or "reader_error" in r.get("page_context", {}) for r in result["results"])
            with self._lock:
                if healthy and size <= self.max_bytes:
                    while self._cached and (len(self._cached) >= self.max_entries or self._bytes + size > self.max_bytes):
                        self._bytes -= self._cached.popitem(last=False)[1][2]
                    self._cached[key] = (time.monotonic() + self.ttl, deepcopy(result), size)
                    self._bytes += size
                future.set_result(result)
            return deepcopy(result)
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)
