"""V1.2.2 research engine: routing, ranking, structured extraction, evidence, verification, diagnostics.

Hermetic: a fake search provider and an in-process mock web (httpx.MockTransport) with small synthetic
pages. No real site's values are hard-coded anywhere in the engine; the numbers below exist only in
these fixtures. Live checks against real SearXNG and the real model are in test_live_research.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.research.evidence import Observation, StandingRow, parse_standings, render, snippet_rows, verify
from app.research.extract import clean_cell, parse_page
from app.research.fetch import _decode
from app.research.intent import classify, followup_intent
from app.research.providers.base import SearchResult
from app.research.router import build_search_query, route
from app.research.service import ResearchService
from tests.fakes import FakeLLM, FakeSearchProvider, text

NOW = datetime(2026, 10, 16, 21, 0)

# ── synthetic pages ──────────────────────────────────────────────────────
SUPER_LIG = [  # rank, team, played, won, drawn, lost, gd, points
    (1, "Galatasaray", 7, 5, 1, 1, 5, 16),
    (2, "Beşiktaş", 7, 5, 0, 2, 8, 15),
    (3, "Amed SK", 7, 4, 2, 1, 8, 14),
    (4, "Fenerbahçe", 7, 4, 1, 2, 13, 13),
    (5, "Trabzonspor", 7, 4, 1, 2, 11, 13),
    (6, "Alanyaspor", 7, 3, 3, 1, 2, 12),
]
CHAMPIONS = [
    (1, "PSG", 2, 2, 0, 0, 7, 6),
    (2, "Barcelona", 2, 2, 0, 0, 7, 6),
    (3, "Aston Villa", 2, 2, 0, 0, 7, 6),
    (34, "Galatasaray", 2, 0, 0, 2, -5, 0),
]
NAV = "<nav><a>Ana sayfa</a><a>Canlı skor</a><a>İddaa</a></nav><div class='cookie'>Çerezleri kabul ediyor musunuz? Tümünü kabul et</div>"


def table(rows: list[tuple[Any, ...]], duplicate_cells: bool = False) -> str:
    def cell(v: Any) -> str:
        return f"<td><span>{v}</span><span class='mobile'> {v}</span></td>" if duplicate_cells else f"<td>{v}</td>"

    head = "<tr><th>#</th><th>Takım</th><th>O</th><th>G</th><th>B</th><th>M</th><th>Av</th><th>P</th></tr>"
    body = "".join("<tr>" + "".join(cell(v) for v in r) + "</tr>" for r in rows)
    return f"<table>{head}{body}</table>"


def page(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body>{NAV}{body}<footer>Tüm hakları saklıdır.</footer></body></html>"


TEAM_PAGE = page(  # the Champions League table comes FIRST: it must be recognised and skipped
    "Galatasaray Puan Durumu",
    f"<div><div class='title'>Şampiyonlar Ligi</div><div>{table(CHAMPIONS, duplicate_cells=True)}</div></div>"
    f"<div><div class='title'>Trendyol Süper Lig</div><div>{table(SUPER_LIG, duplicate_cells=True)}</div></div>",
)
LEAGUE_PAGE = page("Trendyol Süper Lig 2026-27 Puan Durumu", f"<h2>Puan Durumu</h2>{table(SUPER_LIG)}")
JSON_PAGE = (
    "<html><head><title>Trendyol Süper Lig Puan Durumu - 2026/2027</title></head><body><div id='root'></div>"
    "<script id='__NEXT_DATA__' type='application/json'>"
    + json.dumps(
        {
            "props": {
                "pageProps": {
                    "data": {
                        "home": [{"position": i + 1, "point": 30 - i, "played": 4, "team": {"name": f"Ev{i}"}} for i in range(6)],
                        "away": [{"position": i + 1, "point": 20 - i, "played": 3, "team": {"name": f"Dep{i}"}} for i in range(6)],
                        "general": [
                            {
                                "position": r[0],
                                "team": {"name": r[1]},
                                "played": r[2],
                                "won": r[3],
                                "draw": r[4],
                                "lost": r[5],
                                "goals_average": r[6],
                                "point": r[7],
                            }
                            for r in SUPER_LIG
                        ],
                    }
                }
            }
        }
    )
    + "</script></body></html>"
)
CONFLICT_PAGE = page(
    "Süper Lig puan tablosu", table([(1, "Beşiktaş", 7, 5, 1, 1, 9, 16), (2, "Galatasaray", 7, 5, 0, 2, 5, 15), *SUPER_LIG[2:]])
)
OLD_SEASON_PAGE = page(
    "Süper Lig 2024-25 puan durumu",
    f"<h2>2024-25 Süper Lig</h2>{table([(3, 'Galatasaray', 34, 20, 6, 8, 30, 66), (1, 'X', 34, 25, 5, 4, 50, 80)])}",
)
OLDER_ROUND_PAGE = page(
    "Süper Lig puan durumu",
    table(
        [
            (1, "Beşiktaş", 6, 5, 0, 1, 9, 15),
            (2, "Galatasaray", 6, 4, 1, 1, 4, 13),
            *[(r[0], r[1], 6, 0, 0, 0, 0, 10 - r[0]) for r in SUPER_LIG[2:]],
        ]
    ),
)
EXCHANGE_PAGE = page(
    "Döviz kurları",
    "<p>Serbest piyasada döviz kurları gün içinde dalgalı seyretti ve yatırımcılar merkez bankası açıklamasını bekliyor.</p>"
    "<h2>Döviz kurları</h2><table><tr><th>Döviz</th><th>Alış</th><th>Satış</th></tr>"
    "<tr><td>Dolar (USD)</td><td>41,1234</td><td>41,2345</td></tr><tr><td>Euro (EUR)</td><td>47,5000</td><td>47,6000</td></tr>"
    "<tr><td>Sterlin (GBP)</td><td>55,0100</td><td>55,2000</td></tr></table>",
)
ARTICLE_PAGE = page(
    "Galatasaray kampa girdi",
    "<article><p>"
    + "Galatasaray, hafta sonu oynanacak maçın hazırlıklarını tamamladı ve kafileye on dokuz oyuncu dahil edildi. " * 3
    + "</p></article>",
)


class MockWeb:
    """In-process web: url → (status, html, delay seconds)."""

    def __init__(self, pages: dict[str, tuple[int, str, float]]) -> None:
        self.pages = pages
        self.hits: list[str] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits.append(url)
        status, html, delay = self.pages.get(url, (404, "not found", 0.0))
        if delay:
            await asyncio.sleep(delay)
        return httpx.Response(status, text=html, headers={"content-type": "text/html; charset=utf-8"})


async def _no_guard(url: str) -> None:
    return None


def engine(
    results: list[SearchResult], pages: dict[str, tuple[int, str, float]], **kw: Any
) -> tuple[ResearchService, MockWeb, FakeSearchProvider]:
    web = MockWeb(pages)
    search = FakeSearchProvider(results)
    svc = ResearchService(
        [search],
        transport=httpx.MockTransport(web.handler),
        url_guard=_no_guard,
        clock=lambda: NOW,
        fetch_timeout=kw.pop("fetch_timeout", 1.0),
        **kw,
    )
    return svc, web, search


def r(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title, url, snippet, "fake")


def ask(svc: ResearchService, text_: str, **kw: Any) -> Any:
    intent = classify(text_, NOW)
    query = build_search_query(text_, NOW, intent)
    return asyncio.run(svc.research(query, max_sources=3, intent=intent, request=text_, **kw))


# ── 1-4: standings (rank, other team, leader, points) ───────────────────
def test_galatasaray_rank_from_table_not_champions_league() -> None:
    svc, _, _ = engine(
        [r("Galatasaray Puan Durumu", "https://a.test/gs"), r("Süper Lig puan durumu", "https://b.test/lig")],
        {"https://a.test/gs": (200, TEAM_PAGE, 0), "https://b.test/lig": (200, LEAGUE_PAGE, 0)},
    )
    res = ask(svc, "Şu an Galatasaray ligde kaçıncı sırada?")
    ev = res.evidence
    assert ev.status == "verified" and ev.row.rank == 1 and ev.row.points == 16 and ev.row.played == 7
    assert "Şampiyonlar" in json.dumps(res.trace.fetches, ensure_ascii=False)  # the CL table was seen and rejected
    prompt = res.as_prompt()
    assert "ÇIKARILAN KANIT" in prompt and "Galatasaray: 1. sıra, 16 puan, 7 maç (5G 1B 1M)" in prompt
    assert prompt.index("ÇIKARILAN KANIT") < prompt.index("[1] ")  # evidence comes before raw sources


def test_other_team_rank_and_leader_and_points() -> None:
    pages = {"https://a.test/gs": (200, TEAM_PAGE, 0), "https://b.test/lig": (200, LEAGUE_PAGE, 0)}
    results = [r("Galatasaray Puan Durumu", "https://a.test/gs"), r("Süper Lig puan durumu", "https://b.test/lig")]
    svc, _, _ = engine(results, pages)
    assert ask(svc, "Fenerbahçe ligde kaçıncı sırada?").evidence.row.rank == 4
    leader = ask(svc, "Süper Lig'de şu an lider kim?").evidence
    assert leader.want == "leader" and leader.row.team == "Galatasaray"
    points = ask(svc, "Beşiktaş'ın ligde kaç puanı var?").evidence
    assert points.want == "points" and points.row.points == 15 and points.row.rank == 2


def test_embedded_json_overall_table_beats_home_and_away() -> None:
    svc, _, _ = engine([r("Trendyol Süper Lig Puan Durumu", "https://c.test/json")], {"https://c.test/json": (200, JSON_PAGE, 0)})
    ev = ask(svc, "Trabzonspor puan durumunda kaçıncı?").evidence
    assert ev.row.rank == 5 and ev.row.points == 13 and ev.status == "single_source"


# ── 5-11: routing and query generation ───────────────────────────────────
@pytest.mark.parametrize(
    ("text_", "kind", "action"),
    [
        ("Bugün dolar kuru ne kadar?", "exchange", "search"),
        ("İstanbul'da bugün hava nasıl?", "weather", "search"),
        ("Yapay zeka dünyasında bu hafta neler oldu?", "news", "search"),
        ("Python'un en son sürümü hangisi?", "version", "search"),
        ("Apple'ın şu anki CEO'su kim?", "current", "search"),
        ("Galatasaray kaçıncı sırada?", "standings", "search"),
        ("Python nedir?", "general", "none"),
        ("Bugün kod yazasım yok.", "general", "auto"),  # V1.2 rule: must not search; the model may chat
        ("Şimdi biraz dinleneceğim.", "general", "auto"),
        ("Yeni bir fikrim var, dinler misin?", "general", "auto"),
    ],
)
def test_research_routing_by_meaning(text_: str, kind: str, action: str) -> None:
    decision = route(text_, now=NOW)
    assert decision.action == action, decision
    assert action == "search" or decision.action != "search"
    if action == "search":
        assert decision.intent and decision.intent.kind == kind


def test_targeted_queries() -> None:
    assert build_search_query("Şu an Galatasaray ligde kaçıncı sırada?", NOW, classify("Şu an Galatasaray ligde kaçıncı sırada?", NOW)) == (
        "Trendyol Süper Lig 2026-27 puan durumu"
    )
    # Forecast pages, not dated news articles (measured: dated queries returned no temperatures).
    assert build_search_query("İstanbul'da hava nasıl?", NOW, classify("İstanbul'da hava nasıl?", NOW)) == "İstanbul hava durumu"
    q = "Node.js'in güncel LTS sürümü kaç?"
    assert build_search_query(q, NOW, classify(q, NOW)) == "Node.js LTS latest version"
    # Unchanged V1.2 behaviour for everything else.
    assert build_search_query("LAZOS, bugün yapay zeka dünyasında neler oldu?", NOW) == "yapay zeka dünyasında neler oldu 16 Ekim 2026"


def test_exchange_table_reaches_the_model_with_rows_intact() -> None:
    svc, _, _ = engine(
        [r("Döviz kurları", "https://fx.test/kur", "Dolar ve euro kurları.")], {"https://fx.test/kur": (200, EXCHANGE_PAGE, 0)}
    )
    res = ask(svc, "Bugün dolar kuru ne kadar?")
    prompt = res.as_prompt()
    assert "| Dolar (USD) | 41,1234 | 41,2345 |" in prompt  # value stays next to its label and column
    assert "Çerez" not in prompt and "Tümünü kabul" not in prompt  # navigation / cookie boilerplate removed


# ── 12-13: outdated and conflicting sources ──────────────────────────────
def test_outdated_season_results_and_tables_are_rejected() -> None:
    svc, _, _ = engine(
        [r("Süper Lig 2024-25 puan durumu", "https://old.test/2425"), r("Süper Lig puan durumu", "https://b.test/lig")],
        {"https://old.test/2425": (200, OLD_SEASON_PAGE, 0), "https://b.test/lig": (200, LEAGUE_PAGE, 0)},
    )
    res = ask(svc, "Galatasaray ligde kaçıncı sırada?")
    old = next(c for c in res.trace.candidates if "old.test" in c["url"])
    assert old["rejected"] and "sezon" in old["rejected"]
    assert res.evidence.row.rank == 1 and all("old.test" not in u for u in res.evidence.agreeing)


def test_conflicting_sources_are_reported_and_majority_wins() -> None:
    svc, _, _ = engine(
        [
            r("Galatasaray Puan Durumu", "https://a.test/gs"),
            r("Süper Lig puan durumu", "https://b.test/lig"),
            r("Süper Lig puan tablosu", "https://x.test/conf"),
        ],
        {
            "https://a.test/gs": (200, TEAM_PAGE, 0),
            "https://b.test/lig": (200, LEAGUE_PAGE, 0),
            "https://x.test/conf": (200, CONFLICT_PAGE, 0),
        },
    )
    # Disable early stop by asking for all pages: verify() sees all three observations.
    obs = []
    for url, html in (("https://a.test/gs", TEAM_PAGE), ("https://b.test/lig", LEAGUE_PAGE), ("https://x.test/conf", CONFLICT_PAGE)):
        p = parse_page(html, url)
        t = next(t for t in p.tables if "Şampiyonlar" not in t.label)
        obs.append(Observation(url, url.split("/")[2], "table", t.label, parse_standings(t)))
    ev = verify(classify("Galatasaray ligde kaçıncı sırada?", NOW), obs)
    assert ev.status == "conflict" and ev.row.rank == 1 and ev.row.points == 16
    assert any("çelişiyor" in n and "x.test" in n for n in ev.notes)
    assert svc  # engine constructed with the same pages for symmetry with the other tests


def test_older_snapshot_loses_to_fresher_table() -> None:
    p_new, p_old = parse_page(LEAGUE_PAGE, "https://b.test/lig"), parse_page(OLDER_ROUND_PAGE, "https://o.test/x")
    obs = [
        Observation("https://o.test/x", "o.test", "table", "", parse_standings(p_old.tables[0]), authority=3),
        Observation("https://b.test/lig", "b.test", "table", "", parse_standings(p_new.tables[0])),
    ]
    ev = verify(classify("Galatasaray ligde kaçıncı sırada?", NOW), obs)
    assert ev.row.played == 7 and ev.row.rank == 1
    assert any("daha eski veri" in n for n in ev.notes)


# ── 14-15: table preservation and noisy pages ────────────────────────────
def test_html_table_rows_and_columns_are_preserved() -> None:
    p = parse_page(TEAM_PAGE, "https://a.test/gs")
    lig = next(t for t in p.tables if t.label == "Trendyol Süper Lig")
    assert lig.headers == ["#", "Takım", "O", "G", "B", "M", "Av", "P"]
    assert lig.rows[0] == ["1", "Galatasaray", "7", "5", "1", "1", "5", "16"]  # responsive duplicate cells undone
    assert "| 1 | Galatasaray | 7 | 5 | 1 | 1 | 5 | 16 |" in lig.to_markdown()
    assert clean_cell("77") == "77" and clean_cell("Galatasaray Galatasaray") == "Galatasaray" and clean_cell("1 1") == "1"


def test_navigation_and_cookie_text_is_not_passed_as_content() -> None:
    p = parse_page(ARTICLE_PAGE, "https://n.test/a")
    assert "hazırlıklarını" in p.text and "Çerez" not in p.text and "Canlı skor" not in p.text


# ── 16-18: failed, slow and insufficient sources ─────────────────────────
def test_failed_fetch_is_recorded_and_other_sources_are_used() -> None:
    svc, _, _ = engine(
        [r("Galatasaray Puan Durumu", "https://down.test/gs"), r("Süper Lig puan durumu", "https://b.test/lig")],
        {"https://down.test/gs": (503, "busy", 0), "https://b.test/lig": (200, LEAGUE_PAGE, 0)},
    )
    res = ask(svc, "Galatasaray ligde kaçıncı sırada?")
    assert res.evidence.row.rank == 1
    failed = next(f for f in res.trace.fetches if "down.test" in f["url"])
    assert failed["status"] != "ok"


def test_slow_website_times_out_without_blocking_the_answer() -> None:
    svc, _, _ = engine(
        [r("Galatasaray Puan Durumu", "https://slow.test/gs"), r("Süper Lig puan durumu", "https://b.test/lig")],
        {"https://slow.test/gs": (200, TEAM_PAGE, 5.0), "https://b.test/lig": (200, LEAGUE_PAGE, 0)},
        fetch_timeout=0.3,
        total_timeout=4.0,
    )
    t = time.perf_counter()
    res = ask(svc, "Galatasaray ligde kaçıncı sırada?")
    assert time.perf_counter() - t < 3.5
    assert res.evidence.row.rank == 1
    slow = [f for f in res.trace.fetches if "slow.test" in f["url"]]
    assert not slow or slow[0]["status"] != "ok"


def test_insufficient_evidence_is_explicit_and_invents_nothing() -> None:
    svc, _, _ = engine(
        [r("Galatasaray kampa girdi", "https://n.test/a", "Galatasaray hazırlıklarını tamamladı.")],
        {"https://n.test/a": (200, ARTICLE_PAGE, 0)},
    )
    res = ask(svc, "Galatasaray ligde kaçıncı sırada?")
    assert res.evidence.status == "insufficient" and res.evidence.row is None
    assert "ÇIKARILAN KANIT: yok" in res.as_prompt() and "Sayı uydurma" in res.as_prompt()
    assert any("ikinci sorgu" in f for f in res.trace.fallbacks)  # one bounded retry, no loop
    assert len(res.trace.queries) == 2


# ── 19: two seasons ──────────────────────────────────────────────────────
def test_last_season_question_does_not_use_current_table() -> None:
    intent = classify("Geçen sezon Süper Lig'i kim kazandı?", NOW)
    assert intent.season == "2025-26" and intent.want == "leader"
    svc, _, _ = engine([r("Trendyol Süper Lig 2026-27 Puan Durumu", "https://b.test/lig")], {"https://b.test/lig": (200, LEAGUE_PAGE, 0)})
    res = asyncio.run(svc.research("Trendyol Süper Lig 2025-26 puan durumu", intent=intent, request="Geçen sezon Süper Lig'i kim kazandı?"))
    assert not res.evidence.found  # the 2026-27 page is rejected for a 2025-26 question
    assert any("sezon" in (c["rejected"] or "") for c in res.trace.candidates)


# ── 20: multi-turn follow-up ─────────────────────────────────────────────
def test_followup_inherits_research_intent() -> None:
    prev = classify("Galatasaray ligde kaçıncı sırada?", NOW)
    assert followup_intent("Peki Fenerbahçe?", prev, NOW).entity == "Fenerbahçe"
    assert followup_intent("Ya lider kim?", prev, NOW).want == "leader"
    assert followup_intent("Teşekkürler, sağ ol.", prev, NOW) is None


def test_followup_turn_searches_again(client: TestClient, fake_llm: FakeLLM, fake_search: FakeSearchProvider) -> None:
    fake_llm.scripts = [text("Galatasaray 1. sırada [1]."), text("Fenerbahçe 4. sırada [1].")]
    with client.websocket_connect(f"/ws?token={client.token}") as ws:  # type: ignore[attr-defined]
        ws.receive_json()
        conv = None
        for q in ("Galatasaray ligde kaçıncı sırada?", "Peki Fenerbahçe?"):
            ws.send_json({"type": "chat.send", "text": q, "conversation_id": conv})
            while True:
                e = ws.receive_json()
                if e["type"] == "chat.done":
                    conv = e["message"]["conversation_id"]
                    break
    assert len(fake_search.queries) == 2  # the elliptical follow-up was researched too
    assert fake_search.queries[1] == "Trendyol Süper Lig 2026-27 puan durumu" or "puan durumu" in fake_search.queries[1]


# ── staged strategy, snippets, diagnostics, privacy ──────────────────────
def test_snippets_alone_answer_when_two_sources_agree() -> None:
    svc, web, _ = engine(
        [
            r(
                "Şablon:2026-27 Süper Lig puan durumu",
                "https://w.test/t",
                "Sıra, Takım, O, G, B, M, AG, YG, GF, P. 1, Galatasaray, 7, 5, 1, 1, 16, 11, +5, 16, 2027-28",
            ),
            r(
                "Galatasaray puan durumu",
                "https://h.test/gs",
                "Galatasaray, Trendyol Süper Lig puan durumunda 1. sırada ve 16 puanı var (7 maç: 5 galibiyet).",
            ),
        ],
        {},
    )
    res = ask(svc, "Galatasaray ligde kaçıncı sırada?")
    assert res.evidence.status == "verified" and res.evidence.row.rank == 1
    assert web.hits == []  # no page was fetched
    assert any("snippet" in f for f in res.trace.fallbacks)


def test_snippet_parsers() -> None:
    assert snippet_rows("1, Galatasaray, 7, 5, 1, 1, 16, 11, +5, 16, 2027-28 UEFA", None)[0] == StandingRow(
        1, "Galatasaray", 7, 5, 1, 1, 5, 16
    )
    assert snippet_rows("Fenerbahçe 13 puanla 4. sırada yer alıyor.", "Fenerbahçe")[0].rank == 4


def test_early_stop_skips_remaining_pages_once_verified() -> None:
    svc, web, _ = engine(
        [
            r("Galatasaray Puan Durumu", "https://a.test/gs"),
            r("Süper Lig puan durumu", "https://b.test/lig"),
            r("Süper Lig", "https://c.test/json"),
        ],
        {
            "https://a.test/gs": (200, TEAM_PAGE, 0),
            "https://b.test/lig": (200, LEAGUE_PAGE, 0),
            "https://c.test/json": (200, JSON_PAGE, 0.8),
        },
        max_concurrent_fetches=3,
    )
    res = ask(svc, "Galatasaray ligde kaçıncı sırada?")
    assert res.evidence.status == "verified"
    assert any("durduruldu" in f for f in res.trace.fallbacks)
    assert res.trace.timings_ms["fetch"] < 700


def test_turkish_legacy_encoding_is_decoded() -> None:
    raw = "<html><meta charset='windows-1254'><p>Beşiktaş ağır çekim</p></html>".encode("cp1254")
    assert "Beşiktaş ağır" in _decode(raw, "text/html")


def test_diagnostics_off_by_default_and_on_records_trace(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str]) -> None:
    fake_llm.scripts = [text("Galatasaray 1. sırada [1]."), text("Yine 1. sırada [1].")]

    def turn() -> str:
        with client.websocket_connect(f"/ws?token={client.token}") as ws:  # type: ignore[attr-defined]
            ws.receive_json()
            ws.send_json(
                {
                    "type": "chat.send",
                    "text": "Galatasaray ligde kaçıncı sırada? Bana ali@example.com adresinden yaz.",
                    "conversation_id": None,
                }
            )
            while True:
                e = ws.receive_json()
                if e["type"] == "chat.done":
                    return e["message"]["id"]

    first = turn()
    assert client.get("/api/research/traces", headers=auth).json() == {"enabled": False, "traces": []}
    assert client.get(f"/api/research/traces/{first}", headers=auth).status_code == 404
    assert client.put("/api/settings", headers=auth, json={"research_diagnostics": True}).status_code == 200
    second = turn()
    listing = client.get("/api/research/traces", headers=auth).json()
    assert listing["enabled"] and listing["traces"][0]["message_id"] == second
    trace = client.get(f"/api/research/traces/{second}", headers=auth).json()[0]
    assert trace["answer"] == "Yine 1. sırada [1]." and trace["queries"] and trace["candidates"]
    assert "ali@example.com" not in json.dumps(trace) and "[gizlendi]" in trace["request"]
    assert set(trace) >= {
        "request",
        "intent",
        "queries",
        "candidates",
        "fetches",
        "evidence",
        "context",
        "answer",
        "timings_ms",
        "fallbacks",
        "errors",
    }
    assert client.get("/api/research/traces").status_code == 401  # owner-only


def test_past_season_evidence_says_it_is_finished() -> None:
    final = [StandingRow(1, "Galatasaray", 34, 24, 5, 5, 47, 77), *[StandingRow(i, f"T{i}", 34, points=70 - i) for i in range(2, 6)]]
    intent = classify("Geçen sezon Süper Lig'i kim kazandı?", NOW)
    ev = verify(intent, [Observation("https://t.test/x", "t.test", "table", "", final)], current_season="2026-27")
    block = render(ev, {"https://t.test/x": 1}, {"https://t.test/x": "t.test"})
    assert "2025-26 GEÇMİŞ sezondur" in block and "şampiyonu Galatasaray" in block


def test_answer_bearing_snippet_beats_famous_homepage() -> None:
    svc, _, _ = engine(
        [
            r("Welcome to Python.org", "https://www.python.org/", "Python is a programming language that lets you work quickly."),
            r("Python Release Python 3.99.0", "https://www.python.org/downloads/latest/", "Python 3.99.0 is the newest major release."),
            r("Some blog", "https://blog.test/p", "Python tips and tricks."),
        ],
        {},
    )
    res = ask(svc, "Python'un en son sürümü hangisi?")
    chosen = [c for c in res.trace.candidates if c["selected"]]
    assert chosen[0]["url"].endswith("/downloads/latest/") or "/downloads/latest/" in res.sources[0].url
    assert any("cevap içeren" in reason for c in res.trace.candidates for reason in c["reasons"])
