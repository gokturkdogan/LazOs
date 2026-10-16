"""Live research checks: real SearXNG, real web pages, real qwen3.5:9b.

Truth is never hard-coded: standings are fetched fresh by the benchmark harness's own independent
parser (tr.wikipedia standings template + Mackolik) and the answer must match one of them.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
import pytest

from app.research.intent import classify
from app.research.providers.searxng import SearxngProvider
from app.research.router import build_search_query
from app.research.service import ResearchService
from evals.research.bench import UA, numbers, rank_in_answer, standings_truth
from tests.test_live import ollama
from tests.test_live_conversation import real, say

pytestmark = [pytest.mark.network]


def _searxng() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8888/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


searxng = pytest.mark.skipif(not _searxng(), reason="SearXNG not running")


@pytest.fixture(scope="module")
def truth() -> list[list[dict[str, Any]]]:
    import asyncio

    async def go() -> list[list[dict[str, Any]]]:
        async with httpx.AsyncClient(headers=UA, follow_redirects=True) as c:
            return await standings_truth(c, datetime.now())

    tables = asyncio.run(go())
    if not tables:
        pytest.skip("could not fetch independent standings truth")
    return tables


def _row(tables: list[list[dict[str, Any]]], team: str) -> list[dict[str, Any]]:
    return [r for t in tables for r in t if r["team"].lower().startswith(team.lower()[:6])]


@searxng
async def test_live_engine_extracts_verified_standings(truth: list[list[dict[str, Any]]]) -> None:
    text = "Şu an Galatasaray ligde kaçıncı sırada?"
    intent = classify(text)
    res = await ResearchService([SearxngProvider("http://127.0.0.1:8888")]).research(
        build_search_query(text, intent=intent), max_sources=3, intent=intent, request=text
    )
    assert res.evidence and res.evidence.found, res.trace
    expected = _row(truth, "Galatasaray")
    assert any(res.evidence.row.rank == r["rank"] and res.evidence.row.points == r["points"] for r in expected)
    assert res.evidence.status in ("verified", "single_source")
    assert res.trace.timings_ms["total"] < 14_000


@ollama
@searxng
@pytest.mark.integration
@pytest.mark.parametrize("team", ["Galatasaray", "Fenerbahçe"])
def test_live_answer_team_rank(boot, team: str, truth: list[list[dict[str, Any]]]) -> None:  # type: ignore[no-untyped-def]
    with real(boot) as c:
        c.svc.chat.research = ResearchService([SearxngProvider("http://127.0.0.1:8888")])
        answer, _ = say(c, f"Şu an {team} ligde kaçıncı sırada?")
    expected = {r["rank"] for r in _row(truth, team)}
    assert rank_in_answer(answer) & expected, answer
    assert not re.search(r"bulamadım|bilgi yok|net değil", answer, re.IGNORECASE), answer
    assert len(answer) < 300, answer


@ollama
@searxng
@pytest.mark.integration
def test_live_answer_leader_and_followup(boot, truth: list[list[dict[str, Any]]]) -> None:  # type: ignore[no-untyped-def]
    with real(boot) as c:
        c.svc.chat.research = ResearchService([SearxngProvider("http://127.0.0.1:8888")])
        leader, conv = say(c, "Süper Lig'de şu an lider kim?")
        follow, _ = say(c, "Peki Beşiktaş kaç puanda?", conv=conv)
    leaders = {t[0]["team"].split()[0].lower() for t in truth}
    assert any(name in leader.lower() for name in leaders), leader
    assert {str(r["points"]) for r in _row(truth, "Beşiktaş")} & numbers(follow), follow


@ollama
@searxng
@pytest.mark.integration
def test_live_exchange_rate_is_supported_by_sources(boot) -> None:  # type: ignore[no-untyped-def]
    with real(boot) as c:
        c.svc.chat.research = ResearchService([SearxngProvider("http://127.0.0.1:8888")])
        seen: list[str] = []
        orig = c.svc.chat.llm.stream_chat

        def spy(model, messages, options, tools=None):  # type: ignore[no-untyped-def]
            seen.append("\n".join(m.get("content") or "" for m in messages if m.get("role") == "tool"))
            return orig(model, messages, options, tools)

        c.svc.chat.llm.stream_chat = spy
        answer, _ = say(c, "Bugün dolar kuru ne kadar?")
    rates = [n for n in numbers(answer) if re.fullmatch(r"\d{2}\.\d+", n)]
    assert rates, answer
    context = seen[-1].replace(",", ".")
    assert any(rate in context for rate in rates), (answer, context[:500])
