"""Keenable (keenable.ai): an independent web search and page-fetch API. Plain HTTP, no SDK.

With KEENABLE_API_KEY (`keen_...`, header X-API-Key) it uses /v1/search and /v1/fetch. Without a key it uses the keyless
/public twins, which Keenable's docs allow (a shared pool of 1,000 requests an hour per IP, and a required X-Keenable-Title
header naming the app). Checked live on 2026-10-23: search answers in about 0.3 s, and fetching caltrain.com's weekday timetable
returns the same markdown tables `chief/caltrain.py` already reads.

Everything it returns is untrusted web text. Errors raise KeenableError (the caller decides about a fallback).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

BASE = "https://api.keenable.ai"
APP_NAME = "Chief"
SNIPPET_CHARS = 1500


class KeenableError(RuntimeError):
    pass


class Keenable:
    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None, timeout: float = 15.0):
        self.api_key = (api_key or "").strip() or None
        self.http = http or httpx.Client(timeout=timeout)

    @classmethod
    def from_env(cls) -> "Keenable":
        return cls(os.getenv("KEENABLE_API_KEY"))

    @property
    def keyed(self) -> bool:
        return self.api_key is not None

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {"X-Keenable-Title": APP_NAME}

    def _path(self, name: str) -> str:
        return f"{BASE}/v1/{name}" if self.keyed else f"{BASE}/v1/{name}/public"

    def _ok(self, r: httpx.Response) -> Any:
        if r.status_code == 429:
            raise KeenableError(f"Keenable rate limit reached (retry after {r.headers.get('Retry-After', '?')} s)")
        if r.status_code == 402:
            raise KeenableError("Keenable credits are used up")
        if r.status_code in (400, 401, 403):
            raise KeenableError(f"Keenable refused the request (HTTP {r.status_code}); check KEENABLE_API_KEY")
        if r.status_code >= 400:
            raise KeenableError(f"Keenable error (HTTP {r.status_code})")
        try:
            return r.json()
        except ValueError as exc:
            raise KeenableError("Keenable returned something that is not JSON") from exc

    def search(self, query: str, max_results: int = 5, site: str | None = None, mode: str = "pro") -> list[dict]:
        """[{"title", "url", "excerpts": [str]}] like Chief's other search results."""
        body: dict[str, Any] = {"query": query, "max_results": max(1, min(int(max_results), 50)), "mode": mode}
        if site:
            body["site"] = site
        try:
            data = self._ok(self.http.post(self._path("search"), headers=self._headers(), json=body))
        except httpx.HTTPError as exc:
            raise KeenableError(f"Keenable search failed: {type(exc).__name__}") from exc
        out = []
        for item in data.get("results") or []:
            url = item.get("url")
            if not url:
                continue
            text = str(item.get("snippet") or item.get("description") or "")
            out.append({"title": item.get("title") or url, "url": url, "excerpts": [text[:SNIPPET_CHARS]] if text else []})
        return out

    def fetch(self, url: str, max_chars: int = 120_000) -> dict:
        """The whole page as markdown: {"title", "url", "excerpts": [text]}. live=true so a page that is not in the index
        is fetched from its site (the timetable is)."""
        params = {"url": url, "live": "true", "max_chars": max_chars}
        try:
            data = self._ok(self.http.get(self._path("fetch"), headers=self._headers(), params=params))
        except httpx.HTTPError as exc:
            raise KeenableError(f"Keenable fetch failed: {type(exc).__name__}") from exc
        content = str(data.get("content") or "")
        if not content:
            raise KeenableError("Keenable returned an empty page")
        # The page we asked for, not the address Keenable indexed it under (the timetable comes back as a different tab URL).
        return {"title": data.get("title") or url, "url": url, "excerpts": [content]}
