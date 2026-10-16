"""Research pipeline: search (with provider fallback) → fetch top pages → extract relevant passages."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.research.fetch import USER_AGENT, UnsafeURL, extract_text, fetch_html, select_passages
from app.research.providers.base import SearchProvider, SearchProviderError, SearchResult

log = logging.getLogger("lazos.research")


class WebSearchArgs(BaseModel):
    """Validated arguments of the `web_search` tool call produced by the model."""

    query: str = Field(min_length=2, max_length=200)

    @field_validator("query")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip().strip("\"'")
        if len(v) < 2:
            raise ValueError("query too short")
        return v


def parse_tool_args(raw: dict) -> WebSearchArgs | None:
    try:
        return WebSearchArgs.model_validate(raw)
    except ValidationError:
        return None


@dataclass
class Source:
    id: int
    title: str
    url: str
    domain: str
    snippet: str
    excerpt: str = ""
    provider: str = ""
    published: str | None = None

    def public(self) -> dict:
        data = asdict(self)
        data.pop("excerpt")
        return data


@dataclass
class ResearchResult:
    query: str
    sources: list[Source] = field(default_factory=list)
    provider: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.sources)

    def as_prompt(self) -> str:
        blocks = []
        for s in self.sources:
            body = s.excerpt or s.snippet
            date = f" ({s.published})" if s.published else ""
            blocks.append(f"[{s.id}] {s.title}{date}\nURL: {s.url}\n<<<\n{body}\n>>>")
        return f"Arama sorgusu: {self.query}\n\n" + "\n\n".join(blocks)


# Pages that cannot be text-extracted (login walls, video/social feeds) make poor sources.
_UNEXTRACTABLE = ("instagram.com", "facebook.com", "tiktok.com", "pinterest.", "x.com", "twitter.com", "linkedin.com")

_NEWSY = re.compile(r"\b(haber|gündem|son dakika|bugün|news|latest|today)\w*", re.IGNORECASE)
_TURKISH_CHARS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")


class ResearchService:
    def __init__(
        self,
        providers: list[SearchProvider],
        *,
        fetch_timeout: float = 4.0,
        total_timeout: float = 14.0,
        excerpt_chars: int = 900,
    ) -> None:
        self.providers = providers
        self._fetch_timeout = fetch_timeout
        self._total_timeout = total_timeout
        self._excerpt_chars = excerpt_chars

    async def provider_status(self) -> list[dict]:
        return [{"name": p.name, "available": await p.available()} for p in self.providers]

    async def search(self, query: str, max_results: int) -> tuple[list[SearchResult], str | None, list[str]]:
        language = "tr" if _TURKISH_CHARS.search(query) else None
        news = bool(_NEWSY.search(query))
        errors: list[str] = []
        for provider in self.providers:
            try:
                if not await provider.available():
                    errors.append(f"{provider.name}: unavailable")
                    continue
                results = await provider.search(query, max_results=max_results, language=language, news=news)
                if results:
                    return results, provider.name, errors
                errors.append(f"{provider.name}: no results")
            except SearchProviderError as exc:
                log.warning("search provider failed", extra={"provider": provider.name, "error": str(exc)})
                errors.append(f"{provider.name}: {exc}")
        return [], None, errors

    async def research(self, query: str, *, max_sources: int = 4) -> ResearchResult:
        try:
            return await asyncio.wait_for(self._research(query, max_sources), timeout=self._total_timeout)
        except TimeoutError:
            return ResearchResult(query=query, error="araştırma zaman aşımına uğradı")

    async def _research(self, query: str, max_sources: int) -> ResearchResult:
        results, provider, errors = await self.search(query, max_results=max_sources * 2 + 2)
        if not results:
            return ResearchResult(query=query, error="; ".join(errors) or "sonuç bulunamadı")

        # Prefer domain diversity among top results.
        picked: list[SearchResult] = []
        domains: set[str] = set()
        for r in results:
            d = urlsplit(r.url).hostname or ""
            if d in domains or any(d == u or d.endswith("." + u) or d.startswith(u) for u in _UNEXTRACTABLE):
                continue
            domains.add(d)
            picked.append(r)
            if len(picked) >= max_sources:
                break

        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.8"}) as client:
            excerpts = await asyncio.gather(*(self._excerpt(client, r.url, query) for r in picked))

        sources = []
        for i, (r, excerpt) in enumerate(zip(picked, excerpts, strict=True), start=1):
            domain = (urlsplit(r.url).hostname or "").removeprefix("www.")
            sources.append(
                Source(
                    id=i,
                    title=r.title[:200],
                    url=r.url,
                    domain=domain,
                    snippet=r.snippet[:400],
                    excerpt=excerpt,
                    provider=r.provider,
                    published=r.published,
                )
            )
        return ResearchResult(query=query, sources=sources, provider=provider)

    async def _excerpt(self, client: httpx.AsyncClient, url: str, query: str) -> str:
        try:
            html = await asyncio.wait_for(fetch_html(client, url, self._fetch_timeout), timeout=self._fetch_timeout + 1)
            if not html:
                return ""
            text = await asyncio.to_thread(extract_text, html, url)
            return select_passages(text, query, self._excerpt_chars) if text else ""
        except (UnsafeURL, httpx.HTTPError, TimeoutError, UnicodeError) as exc:
            log.info("page fetch skipped", extra={"url": url, "reason": str(exc)[:120]})
            return ""
        except Exception:  # extraction libraries can fail on hostile HTML
            log.exception("page extraction failed", extra={"url": url})
            return ""
