"""Research pipeline (V1.2.2), staged so simple questions stay fast:

  1. search (provider fallback)        → candidates with title, URL, date, snippet
  2. rank: authority, relevance, duplicates, wrong competition/season, stale dates (each with a reason)
  3. snippets: for structured questions, are two independent snippets already enough?
  4. fetch only the selected pages (bounded concurrency, timeouts), stopping early once verified
  5. extract text + tables (HTML and embedded JSON) without flattening rows/columns
  6. evidence: deterministic extraction + cross-source verification for structured questions
  7. one fallback query if a structured question is still unanswered
  8. compact context for the model: evidence first, then each source's snippet and relevant excerpt

Every stage is recorded in a `ResearchTrace` for the developer diagnostics panel.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.research.evidence import Evidence, Observation, check_context, parse_standings, render, snippet_rows, verify
from app.research.extract import Page, Table, parse_page, select_passages
from app.research.fetch import USER_AGENT, UnsafeURL, assert_public_url, fetch_html
from app.research.intent import ResearchIntent, classify, competition_in, fold, football_season, seasons_in
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
class ResearchTrace:
    """Developer diagnostics for one research call. Holds web data and the request only (no history)."""

    request: str
    intent: dict[str, Any]
    queries: list[str] = field(default_factory=list)
    provider: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    fetches: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] | None = None
    context: str = ""
    answer: str = ""
    message_id: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchResult:
    query: str
    sources: list[Source] = field(default_factory=list)
    provider: str | None = None
    error: str | None = None
    evidence: Evidence | None = None
    evidence_block: str = ""
    trace: ResearchTrace | None = None

    @property
    def ok(self) -> bool:
        return bool(self.sources)

    def as_prompt(self) -> str:
        blocks = [f"Arama sorgusu: {self.query}"]
        if self.evidence_block:
            blocks.append(self.evidence_block)
        for s in self.sources:
            date = f" ({s.published[:10]})" if s.published else ""
            block = f"[{s.id}] {s.title}{date}\nURL: {s.url}"
            if s.snippet:
                block += f"\nÖzet: {s.snippet}"
            if s.excerpt:
                block += f"\n<<<\n{s.excerpt}\n>>>"
            blocks.append(block)
        return "\n\n".join(blocks)


# Pages that cannot be text-extracted (login walls, video/social feeds) make poor sources.
_UNEXTRACTABLE = ("instagram.com", "facebook.com", "tiktok.com", "pinterest.", "x.com", "twitter.com", "linkedin.com", "youtube.com")

# Preferred sources per question type (higher = more authoritative). Never used to invent answers.
AUTHORITY: dict[str, dict[str, int]] = {
    "standings": {
        "tff.org": 3,
        "uefa.com": 3,
        "premierleague.com": 3,
        "trtspor.com.tr": 2,
        "tr.wikipedia.org": 2,
        "en.wikipedia.org": 2,
        "mackolik.com": 2,
        "beinsports.com.tr": 2,
        "sporx.com": 1,
        "ntvspor.net": 1,
        "fanatik.com.tr": 1,
        "aspor.com.tr": 1,
        "sofascore.com": 1,
        "flashscore.com.tr": 1,
        "fotmob.com": 1,
        "transfermarkt.com.tr": 1,
    },
    "exchange": {"tcmb.gov.tr": 3, "doviz.com": 2, "bloomberght.com": 2, "tr.investing.com": 1, "investing.com": 1, "paraanaliz.com": 1},
    "weather": {"mgm.gov.tr": 3, "accuweather.com": 1, "weather.com": 1, "meteoblue.com": 1, "yandex.com.tr": 1},
    "version": {"python.org": 3, "nodejs.org": 3, "endoflife.date": 2, "github.com": 2, "docs.python.org": 2, "wikipedia.org": 1},
    "current": {"wikipedia.org": 2, "reuters.com": 2, "bloomberg.com": 2, "aa.com.tr": 2},
    "news": {"aa.com.tr": 2, "trthaber.com": 2, "reuters.com": 2, "bbc.com": 2, "theverge.com": 1, "techcrunch.com": 1, "webrazzi.com": 1},
}
_NEWSY = re.compile(r"\b(haber|gündem|son dakika|bugün|news|latest|today)\w*", re.IGNORECASE)
_TURKISH_CHARS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_STANDINGS_PAGE = re.compile(r"puan durumu|puan tablosu|standings|league table|sıralama", re.IGNORECASE)
_VALID_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}", re.IGNORECASE)
# A snippet that already contains the kind of value asked for is worth more than a famous homepage.
_ANSWER_SIGNAL = {
    "version": re.compile(r"\b\d+\.\d+(\.\d+)?\b"),
    "exchange": re.compile(r"\b\d{1,3}[.,]\d{2,4}\b"),
    "weather": re.compile(r"-?\d{1,2}\s*(°|derece)", re.IGNORECASE),
}
_STALE_DAYS = {"news": 30, "exchange": 7, "weather": 3, "current": 400, "version": 400}


def authority(domain: str, kind: str) -> int:
    for d, score in AUTHORITY.get(kind, {}).items():
        if domain == d or domain.endswith("." + d):
            return score
    return 0


def _normal_url(url: str) -> str:
    p = urlsplit(url)
    host = (p.hostname or "").removeprefix("www.").removeprefix("m.")
    path = re.sub(r"/(amp)?/?$", "", p.path)
    return urlunsplit(("https", host, path, "", ""))


def _age_days(published: str | None, now: datetime) -> float | None:
    if not published:
        return None
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (now.astimezone(UTC) - dt).total_seconds() / 86400


@dataclass
class _Candidate:
    result: SearchResult
    domain: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    rejected: str | None = None
    selected: bool = False
    snippet_rows: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.result.title[:160],
            "url": self.result.url,
            "domain": self.domain,
            "published": self.result.published,
            "snippet": self.result.snippet[:300],
            "score": round(self.score, 1),
            "selected": self.selected,
            "reasons": self.reasons,
            "rejected": self.rejected,
        }


class ResearchService:
    def __init__(
        self,
        providers: list[SearchProvider],
        *,
        fetch_timeout: float = 4.0,
        total_timeout: float = 14.0,
        excerpt_chars: int = 900,
        max_concurrent_fetches: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        url_guard: Callable[[str], Awaitable[None]] = assert_public_url,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.providers = providers
        self._fetch_timeout = fetch_timeout
        self._total_timeout = total_timeout
        self._excerpt_chars = excerpt_chars
        self._max_concurrent = max_concurrent_fetches
        self._transport = transport
        self._guard = url_guard
        self._clock = clock

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

    async def research(
        self,
        query: str,
        *,
        max_sources: int = 4,
        intent: ResearchIntent | None = None,
        request: str | None = None,
    ) -> ResearchResult:
        intent = intent or classify(request or query, self._clock())
        trace = ResearchTrace(request=_redact(request or query), intent=intent.to_dict())
        started = time.perf_counter()
        deadline = started + self._total_timeout
        try:
            result = await self._research(query, max_sources, intent, request or query, trace, deadline)
        except Exception as exc:  # never let research take the whole turn down
            log.exception("research failed")
            trace.errors.append(f"beklenmeyen hata: {exc}")
            result = ResearchResult(query=query, error="araştırma başarısız oldu")
        trace.timings_ms["total"] = round((time.perf_counter() - started) * 1000, 1)
        trace.context = result.as_prompt() if result.ok else ""
        result.trace = trace
        log.info(
            "research done",
            extra={
                "intent": intent.kind,
                "sources": len(result.sources),
                "evidence": result.evidence.status if result.evidence else None,
                "ms": trace.timings_ms["total"],
            },
        )
        return result

    # ── pipeline ───────────────────────────────────────────────────────────
    async def _research(
        self, query: str, max_sources: int, intent: ResearchIntent, request: str, trace: ResearchTrace, deadline: float
    ) -> ResearchResult:
        now = self._clock()
        candidates, provider, error = await self._search_and_rank(query, intent, max_sources, trace, now)
        if not candidates:
            return ResearchResult(query=query, error=error or "sonuç bulunamadı")
        pages: dict[str, Page] = {}
        observations: list[Observation] = []
        evidence: Evidence | None = None

        if intent.structured:
            observations = self._snippet_observations(candidates, intent)
            evidence = verify(intent, observations, football_season(self._clock()))
            enough = evidence.status == "verified" and evidence.row is not None and evidence.row.played is not None
            if enough:
                trace.fallbacks.append("snippet'ler yeterli: iki bağımsız özet aynı değeri verdi, sayfa açılmadı")
            else:
                evidence = await self._fetch_until_verified(candidates, intent, query, observations, pages, trace, deadline)
            if not evidence.found and time.perf_counter() < deadline - 3:
                alt = _fallback_query(intent)
                trace.fallbacks.append(f"kanıt bulunamadı; ikinci sorgu: {alt}")
                more, _, _ = await self._search_and_rank(alt, intent, max_sources, trace, now)
                known = {c.result.url for c in candidates}
                more = [c for c in more if c.result.url not in known]
                if more:
                    candidates += more
                    observations += self._snippet_observations(more, intent)
                    evidence = await self._fetch_until_verified(more, intent, query, observations, pages, trace, deadline)
            trace.evidence = evidence.to_dict()
        else:
            await self._fetch_pages([c for c in candidates if c.selected], pages, trace, deadline)

        return self._build_result(query, request, candidates, pages, intent, evidence, provider)

    async def _search_and_rank(
        self, query: str, intent: ResearchIntent, max_sources: int, trace: ResearchTrace, now: datetime
    ) -> tuple[list[_Candidate], str | None, str | None]:
        t = time.perf_counter()
        results, provider, errors = await self.search(query, max_results=max_sources * 2 + 4)
        trace.timings_ms["search"] = round(trace.timings_ms.get("search", 0) + (time.perf_counter() - t) * 1000, 1)
        trace.queries.append(query)
        trace.provider = trace.provider or provider
        trace.errors += errors
        if not results:
            return [], provider, "; ".join(errors) or "sonuç bulunamadı"
        candidates = self._rank(results, intent, now)
        picked_domains: set[str] = set()
        for c in sorted((c for c in candidates if not c.rejected), key=lambda c: -c.score):
            if c.domain in picked_domains:
                c.rejected = "aynı alan adından daha iyi bir sonuç seçildi"
                continue
            if len(picked_domains) >= max_sources:
                c.reasons.append("kaynak sınırı doldu")
                continue
            c.selected = True
            picked_domains.add(c.domain)
        trace.candidates += [c.to_dict() for c in candidates]
        return candidates, provider, None

    def _rank(self, results: list[SearchResult], intent: ResearchIntent, now: datetime) -> list[_Candidate]:
        out: list[_Candidate] = []
        seen: set[str] = set()
        entity = fold(intent.entity) if intent.entity else None
        for order, r in enumerate(results):
            domain = (urlsplit(r.url).hostname or "").removeprefix("www.")
            c = _Candidate(r, domain, score=max(0.0, 2 - order * 0.25))  # the engine's own ranking still counts a little
            out.append(c)
            norm = _normal_url(r.url)
            if norm in seen:
                c.rejected = "tekrarlanan sonuç"
                continue
            seen.add(norm)
            if not _VALID_HOST.fullmatch(domain) or "www." in domain:
                c.rejected = "geçersiz adres"
                continue
            if any(domain == u or domain.endswith("." + u) or domain.startswith(u) for u in _UNEXTRACTABLE):
                c.rejected = "metni okunamayan sosyal medya/video sayfası"
                continue
            text = f"{r.title} {r.snippet} {r.url}"
            if a := authority(domain, intent.kind):
                c.score += a * (1.5 if intent.kind == "standings" else 1.0)
                c.reasons.append(f"güvenilir kaynak (+{a})")
            if (signal := _ANSWER_SIGNAL.get(intent.kind)) and signal.search(f"{r.title} {r.snippet}"):
                c.score += 3
                c.reasons.append("özette cevap içeren değer var")
            if entity and entity in fold(text):
                c.score += 1
                c.reasons.append("aranan varlık geçiyor")
            if intent.kind == "standings":
                head = f"{r.title} {r.snippet[:160]} {r.url}"
                comp = competition_in(head)
                if comp and intent.competition and comp.key != intent.competition.key and not competition_in(head.replace(comp.name, "")):
                    c.score -= 4
                    c.reasons.append(f"farklı turnuva: {comp.name}")
                seasons = seasons_in(text)
                if intent.season and seasons and intent.season not in seasons:
                    c.rejected = f"farklı/eski sezon ({', '.join(sorted(seasons))})"
                    continue
                if _STANDINGS_PAGE.search(text):
                    c.score += 2
                    c.reasons.append("puan durumu sayfası")
                c.snippet_rows = snippet_rows(r.snippet, intent.entity)
                if c.snippet_rows:
                    c.score += 2
                    c.reasons.append("özette tablo satırı var")
            elif (limit := _STALE_DAYS.get(intent.kind)) is not None:
                age = _age_days(r.published, now)
                if age is not None and age > limit:
                    c.score -= 3
                    c.reasons.append(f"eski tarihli ({int(age)} gün önce)")
        return out

    def _snippet_observations(self, candidates: list[_Candidate], intent: ResearchIntent) -> list[Observation]:
        obs = []
        for c in candidates:
            if c.rejected or not c.snippet_rows:
                continue
            if check_context(intent, "", c.result.title):
                continue
            obs.append(Observation(c.result.url, c.domain, "snippet", c.result.title, c.snippet_rows, authority(c.domain, intent.kind)))
        return obs

    async def _fetch_until_verified(
        self,
        candidates: list[_Candidate],
        intent: ResearchIntent,
        query: str,
        observations: list[Observation],
        pages: dict[str, Page],
        trace: ResearchTrace,
        deadline: float,
    ) -> Evidence:
        """Fetch the selected pages; stop as soon as two independent sources agree."""
        selected = [c for c in candidates if c.selected and c.result.url not in pages]
        state = {"evidence": verify(intent, observations, football_season(self._clock()))}

        def on_page(c: _Candidate, page: Page, rec: dict[str, Any]) -> bool:
            t = time.perf_counter()
            for table in page.tables:
                reason = check_context(intent, table.label, page.title)
                rows = parse_standings(table)
                if not rows:
                    continue
                if reason:
                    rec.setdefault("rejected_tables", []).append(f"{table.label or table.origin}: {reason}")
                    continue
                observations.append(
                    Observation(
                        c.result.url,
                        c.domain,
                        "json" if table.origin == "json" else "table",
                        table.label,
                        rows,
                        authority(c.domain, intent.kind),
                    )
                )
                rec["standings_rows"] = len(rows)
                break  # one standings table per page
            state["evidence"] = verify(intent, observations, football_season(self._clock()))
            trace.timings_ms["evidence"] = round(trace.timings_ms.get("evidence", 0) + (time.perf_counter() - t) * 1000, 1)
            ev = state["evidence"]
            return ev.status == "verified" and len({o.domain for o in observations if o.url in ev.agreeing and o.origin != "snippet"}) >= 1

        await self._fetch_pages(selected, pages, trace, deadline, on_page=on_page)
        return state["evidence"]

    async def _fetch_pages(
        self,
        candidates: list[_Candidate],
        pages: dict[str, Page],
        trace: ResearchTrace,
        deadline: float,
        on_page: Callable[[_Candidate, Page, dict[str, Any]], bool] | None = None,
    ) -> None:
        if not candidates:
            return
        sem = asyncio.Semaphore(self._max_concurrent)
        t0 = time.perf_counter()
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.8"}
        async with httpx.AsyncClient(headers=headers, transport=self._transport) as client:

            async def one(c: _Candidate) -> tuple[_Candidate, Page | None, dict[str, Any]]:
                async with sem:
                    return (c, *await self._fetch_page(client, c.result.url))

            tasks = [asyncio.create_task(one(c)) for c in candidates]
            try:
                for fut in asyncio.as_completed(tasks, timeout=max(0.5, deadline - time.perf_counter())):
                    c, page, rec = await fut
                    trace.fetches.append(rec)
                    if page is None:
                        continue
                    pages[c.result.url] = page
                    if on_page and on_page(c, page, rec):
                        pending = [t for t in tasks if not t.done()]
                        if pending:
                            trace.fallbacks.append(f"doğrulandı; {len(pending)} sayfa daha açılmadan durduruldu")
                        break
            except TimeoutError:
                trace.errors.append("sayfa okuma süresi doldu; eldeki sonuçlarla devam edildi")
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        trace.timings_ms["fetch"] = round(trace.timings_ms.get("fetch", 0) + (time.perf_counter() - t0) * 1000, 1)

    async def _fetch_page(self, client: httpx.AsyncClient, url: str) -> tuple[Page | None, dict[str, Any]]:
        rec: dict[str, Any] = {"url": url, "status": "ok"}
        t = time.perf_counter()
        try:
            html = await asyncio.wait_for(fetch_html(client, url, self._fetch_timeout, guard=self._guard), timeout=self._fetch_timeout + 1)
            rec["fetch_ms"] = round((time.perf_counter() - t) * 1000, 1)
            if not html:
                rec["status"] = "boş/HTML olmayan yanıt veya HTTP hatası"
                return None, rec
            rec["bytes"] = len(html)
            t2 = time.perf_counter()
            page = await asyncio.to_thread(parse_page, html, url)
            rec["extract_ms"] = round((time.perf_counter() - t2) * 1000, 1)
            rec["tables"] = len(page.tables)
            rec["json_tables"] = sum(t.origin == "json" for t in page.tables)
            rec["text_chars"] = len(page.text)
            rec["published"] = page.published
            return page, rec
        except UnsafeURL as exc:
            rec["status"] = f"engellendi: {exc}"
        except (httpx.HTTPError, TimeoutError, UnicodeError) as exc:
            rec["status"] = f"okunamadı: {type(exc).__name__}"
        except Exception as exc:  # extraction libraries can fail on hostile HTML
            log.exception("page extraction failed", extra={"url": url})
            rec["status"] = f"ayrıştırılamadı: {type(exc).__name__}"
        rec["fetch_ms"] = round((time.perf_counter() - t) * 1000, 1)
        return None, rec

    def _build_result(
        self,
        query: str,
        request: str,
        candidates: list[_Candidate],
        pages: dict[str, Page],
        intent: ResearchIntent,
        evidence: Evidence | None,
        provider: str | None,
    ) -> ResearchResult:
        chosen = [c for c in candidates if c.selected]
        if evidence and evidence.found:
            # Make sure the sources the evidence came from are listed (they may come from the fallback query).
            for url in evidence.agreeing:
                c = next((c for c in candidates if c.result.url == url), None)
                if c and not c.selected:
                    c.selected = True
                    chosen.append(c)
            chosen.sort(key=lambda c: (c.result.url not in evidence.agreeing, -c.score))
        sources: list[Source] = []
        focus = f"{request} {query}"
        budget = 3200
        for i, c in enumerate(chosen[:6], start=1):
            r = c.result
            page = pages.get(r.url)
            excerpt = ""
            if page and not (evidence and evidence.found):
                excerpt = select_passages(page.text, focus, self._excerpt_chars)
                table = _relevant_table(page.tables, focus)
                if table:
                    excerpt = f"{excerpt}\n{table}".strip()
                excerpt = excerpt[: min(len(excerpt), max(0, budget))]
                budget -= len(excerpt)
            sources.append(
                Source(
                    id=i,
                    title=(page.title if page and page.title and not r.title else r.title)[:200],
                    url=r.url,
                    domain=c.domain,
                    snippet=r.snippet[:300],
                    excerpt=excerpt,
                    provider=r.provider,
                    published=r.published or (page.published if page else None),
                )
            )
        block = ""
        if evidence is not None:
            ids = {s.url: s.id for s in sources}
            block = render(evidence, ids, {s.url: s.domain for s in sources})
        return ResearchResult(query=query, sources=sources, provider=provider, evidence=evidence, evidence_block=block)


def _relevant_table(tables: list[Table], focus: str, max_rows: int = 12) -> str:
    """The table (if any) whose cells mention the request; rendered with rows and columns intact."""
    terms = {fold(w)[:6] for w in re.findall(r"\w{3,}", focus)}
    best, best_hits = None, 0
    for t in tables:
        cells = " ".join([t.label, *t.headers, *(" ".join(r) for r in t.rows[:40])])
        hits = len(terms & {fold(w)[:6] for w in re.findall(r"\w{3,}", cells)})
        if hits > best_hits:
            best, best_hits = t, hits
    if not best or best_hits < 1:
        return ""
    rows = best.rows
    hit_rows = [r for r in rows if terms & {fold(w)[:6] for w in re.findall(r"\w{3,}", " ".join(r))}]
    shown = (hit_rows or rows)[:max_rows]
    return best.to_markdown(shown)[:1400]


def _fallback_query(intent: ResearchIntent) -> str:
    comp = intent.competition.name if intent.competition else ""
    if intent.entity:
        return f"{intent.entity} {comp} {intent.season or ''} puan durumu".replace("  ", " ").strip()
    return f"{comp} puan durumu {intent.season or ''} TFF".strip()


_SECRETISH = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b(?:sk|pk|ghp|xox[bp])-?[A-Za-z0-9_-]{12,}\b|\b\d{11,}\b")


def _redact(text: str) -> str:
    return _SECRETISH.sub("[gizlendi]", text)[:500]
