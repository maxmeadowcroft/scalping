"""Minimal JSON HTTP client shared by API-style bots."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def http_json(
    method: str,
    url: str,
    *,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: float = 25.0,
) -> tuple[int, Any, dict[str, str]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            if not raw:
                return resp.status, None, hdrs
            try:
                return resp.status, json.loads(raw.decode("utf-8")), hdrs
            except json.JSONDecodeError:
                return resp.status, raw.decode("utf-8", errors="replace"), hdrs
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = raw.decode("utf-8", errors="replace")
        return exc.code, data, {}
