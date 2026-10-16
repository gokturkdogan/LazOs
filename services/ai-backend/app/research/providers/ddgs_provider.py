"""DuckDuckGo (via the `ddgs` metasearch library) — fallback that needs no local service."""

from __future__ import annotations

import asyncio

from app.research.providers.base import SearchProviderError, SearchResult


class DdgsProvider:
    name = "duckduckgo"

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def available(self) -> bool:
        try:
            import ddgs  # noqa: F401
        except ImportError:
            return False
        return True

    async def search(self, query: str, *, max_results: int, language: str | None, news: bool) -> list[SearchResult]:
        region = "tr-tr" if (language or "").startswith("tr") else "wt-wt"

        def _run() -> list[dict]:
            from ddgs import DDGS

            with DDGS(timeout=int(self._timeout)) as client:
                if news:
                    hits = list(client.news(query, region=region, max_results=max_results))
                    if hits:
                        return hits
                return list(client.text(query, region=region, max_results=max_results))

        try:
            raw = await asyncio.wait_for(asyncio.to_thread(_run), timeout=self._timeout + 2)
        except Exception as exc:
            raise SearchProviderError(f"DuckDuckGo failed: {exc}") from exc

        results = []
        for item in raw:
            url = item.get("href") or item.get("url") or ""
            if not url.startswith(("http://", "https://")):
                continue
            results.append(
                SearchResult(
                    title=(item.get("title") or url).strip(),
                    url=url,
                    snippet=(item.get("body") or "").strip(),
                    provider=self.name,
                    published=item.get("date"),
                )
            )
        return results
