"""Opt-in hosted reading; only public URLs are disclosed, never origin credentials."""

import ipaddress
import json
import os
import socket
import time
from urllib.parse import urlsplit

from .http import decode_json_response

READER_URL = "https://r.jina.ai/"


def require_public_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None
            or ("." not in host and ":" not in host) or host == "localhost"
            or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))):
        raise ValueError("Hosted reader only accepts public HTTP(S) URLs without URL credentials")
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        if not addresses or not all(ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("Hosted reader cannot receive private/non-public destinations")
    except OSError as exc:
        raise ValueError("Cannot verify a public destination for hosted reader") from exc


def read_page(url, payload, *, fetch, limiter=None, deadline):
    # Check both the original URL and any origin redirect before disclosing either.
    for target in {payload["url"], url}:
        require_public_url(target)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("No time left for hosted reader fallback")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "X-Respond-With": "content"}
    key = os.getenv("JINA_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = fetch({
        **payload, "url": READER_URL, "method": "POST", "body": json.dumps({"url": url}),
        "headers": headers, "verify_ssl": True, "timeout": remaining, "retries": payload.get("retries", 2),
    }, limiter=limiter, rate_key="jina-reader", requests_per_second=1 / 3)
    envelope = decode_json_response(response, "Jina reader")
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if (not isinstance(data, dict) or not isinstance(data.get("content"), str) or not data["content"].strip()
            or envelope.get("code", 200) != 200):
        raise RuntimeError("Jina reader returned an invalid/empty content envelope")
    return data, response
