"""Web search through a self-hosted SearXNG instance (JSON API). No API keys, no third-party search SDK."""

from __future__ import annotations

from typing import Any

import httpx

from .config import VerifierConfig


class SearchError(RuntimeError):
    pass


async def searxng(query: str, cfg: VerifierConfig, k: int | None = None, timeout_s: float = 12) -> list[dict[str, Any]]:
    """GET {searxng_url}/search?format=json. SearXNG must allow the json format (settings.yml → search.formats)."""
    base = cfg.searxng_url.rstrip("/")
    params = {"q": query, "format": "json", "safesearch": 0}
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(f"{base}/search", params=params, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise SearchError(f"SearXNG at {base} is unreachable: {type(exc).__name__}: {exc}") from exc
    if resp.status_code == 403:
        raise SearchError("SearXNG refused the JSON format: add `json` to search.formats in settings.yml")
    if resp.status_code >= 400:
        raise SearchError(f"SearXNG HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise SearchError("SearXNG returned HTML, not JSON: enable `json` in search.formats") from exc
    out = []
    for r in data.get("results") or []:
        if not isinstance(r, dict) or not r.get("url"):
            continue
        out.append({"title": str(r.get("title") or "")[:300], "url": str(r["url"])[:500],
                    "snippet": str(r.get("content") or "")[:800], "engine": r.get("engine")})
        if len(out) >= (k or cfg.search_results):
            break
    return out
