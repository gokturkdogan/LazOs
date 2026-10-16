"""Self-hosted SearXNG provider (preferred: aggregates multiple engines, no API key)."""

from __future__ import annotations

import httpx

from app.research.providers.base import SearchProviderError, SearchResult


class SearxngProvider:
    name = "searxng"

    def __init__(self, base_url: str, timeout: float = 8.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                r = await client.get(f"{self._base}/healthz")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, max_results: int, language: str | None, news: bool) -> list[SearchResult]:
        params = {"q": query, "format": "json", "safesearch": "0"}
        if language:
            params["language"] = language
        if news:
            params["categories"] = "news,general"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(f"{self._base}/search", params=params)
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchProviderError(f"SearXNG failed: {exc}") from exc

        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in data.get("results", []):
            url = item.get("url") or ""
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            results.append(
                SearchResult(
                    title=(item.get("title") or url).strip(),
                    url=url,
                    snippet=(item.get("content") or "").strip(),
                    provider=self.name,
                    published=item.get("publishedDate"),
                )
            )
            if len(results) >= max_results:
                break
        return results
