"""Web-research benchmark against REAL SearXNG, real web pages and the real qwen3.5:9b.

    python -m evals.research.bench [--label before|after] [--runs N]

Ground truth is fetched fresh at run time by this harness with its own small parser (never hard-coded):
Süper Lig standings from tr.wikipedia's standings template and Mackolik, software versions from
endoflife.date. A standings answer is correct when its rank/points/leader match *either* truth source
(they can differ by one match while a round is in progress). Questions without a checkable truth
are scored only for support: every number in the answer must appear in what the model was shown.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import lxml.html

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.chat import TurnRequest  # noqa: E402
from app.core.config import BootConfig  # noqa: E402
from app.services import Services  # noqa: E402

HERE = Path(__file__).parent
MODELS = Path.home() / "Library" / "Application Support" / "LAZOS" / "models"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) LAZOS-eval/1.0", "Accept-Language": "tr"}


def fold(s: str) -> str:
    return s.translate(str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")).lower()


def season(now: datetime, offset: int = 0) -> str:
    start = (now.year if now.month >= 7 else now.year - 1) + offset
    return f"{start}-{(start + 1) % 100:02d}"


# ── independent ground truth ───────────────────────────────────────────────
def _cells(row: Any) -> list[str]:
    out = []
    for c in row.xpath("./th|./td"):
        t = re.sub(r"\s+", " ", c.text_content()).strip()
        half = len(t) // 2
        if len(t) % 2 == 1 and t[half] == " " and t[:half] == t[half + 1 :]:  # responsive duplicates "Galatasaray Galatasaray", not "77"
            t = t[:half]
        out.append(t)
    return out


def _table_rows(html: str, min_rows: int = 16, max_rows: int = 20) -> list[dict[str, Any]]:
    doc = lxml.html.fromstring(html)
    for table in doc.iter("table"):
        rows = [_cells(r) for r in table.xpath(".//tr")]
        rows = [r for r in rows if r]
        if not min_rows <= len(rows) - 1 <= max_rows:
            continue
        head = [fold(h) for h in rows[0]]
        try:
            i_team = next(i for i, h in enumerate(head) if h.startswith("takim"))
            i_pts = len(head) - 1 - next(i for i, h in enumerate(reversed(head)) if h in ("p", "puan"))
            i_played = head.index("o")
        except (StopIteration, ValueError):
            continue
        out = []
        for pos, r in enumerate(rows[1:], start=1):
            if len(r) <= max(i_team, i_pts):
                continue
            team = re.sub(r"\s*\(.*?\)|\.mw-parser.*", "", r[i_team]).strip()
            out.append(
                {
                    "rank": pos,
                    "team": team,
                    "played": int(re.sub(r"\D", "", r[i_played]) or 0),
                    "points": int(re.sub(r"[^\d-]", "", r[i_pts]) or 0),
                }
            )
        if out:
            return out  # first table that looks like a league table (Mackolik lists Süper Lig before the CL table)
    return []


async def standings_truth(client: httpx.AsyncClient, now: datetime, offset: int = 0) -> list[list[dict[str, Any]]]:
    tables = []
    wiki = f"https://tr.wikipedia.org/wiki/{quote('Şablon:' + season(now, offset) + '_Süper_Lig_puan_durumu')}"
    urls = [wiki] + (["https://www.mackolik.com/takim/galatasaray/puan-durumu/esa748l653sss1wurz5ps3228"] if offset == 0 else [])
    for url in urls:
        try:
            r = await client.get(url, timeout=10)
            rows = _table_rows(r.text)
            if rows:
                tables.append(rows)
        except httpx.HTTPError:
            pass
    return tables


async def eol_latest(client: httpx.AsyncClient, product: str, lts: bool = False) -> set[str]:
    r = await client.get(f"https://endoflife.date/api/{product}.json", timeout=10)
    cycles = r.json()
    if lts:  # "lts" is the date a line becomes LTS; a future date means it is not LTS yet
        today = datetime.now().date().isoformat()
        cycles = [c for c in cycles if c.get("lts") is True or (isinstance(c.get("lts"), str) and c["lts"] <= today)]
    top = cycles[0]
    return {str(top["cycle"]), str(top.get("latest", ""))}


# ── questions ───────────────────────────────────────────────────────────────
QUESTIONS: list[dict[str, Any]] = [
    {"id": "gs_rank", "turns": ["Şu an Galatasaray ligde kaçıncı sırada?"], "kind": "rank", "team": "Galatasaray"},
    {"id": "fb_rank", "turns": ["Fenerbahçe ligde kaçıncı sırada?"], "kind": "rank", "team": "Fenerbahçe"},
    {"id": "ts_rank", "turns": ["Trabzonspor puan durumunda kaçıncı?"], "kind": "rank", "team": "Trabzonspor"},
    {"id": "leader", "turns": ["Süper Lig'de şu an lider kim?"], "kind": "leader"},
    {"id": "bjk_points", "turns": ["Beşiktaş'ın ligde kaç puanı var?"], "kind": "points", "team": "Beşiktaş"},
    {"id": "followup", "turns": ["Galatasaray ligde kaçıncı sırada?", "Peki Fenerbahçe?"], "kind": "rank", "team": "Fenerbahçe"},
    {"id": "last_season", "turns": ["Geçen sezon Süper Lig'i kim kazandı?"], "kind": "leader", "offset": -1},
    {"id": "usd", "turns": ["Bugün dolar kuru ne kadar?"], "kind": "supported_number"},
    {"id": "eur", "turns": ["Euro şu an kaç TL?"], "kind": "supported_number"},
    {"id": "weather", "turns": ["İstanbul'da bugün hava nasıl?"], "kind": "supported_number"},
    {"id": "python_ver", "turns": ["Python'un en son sürümü hangisi?"], "kind": "version", "product": "python"},
    {"id": "node_lts", "turns": ["Node.js'in güncel LTS sürümü kaç?"], "kind": "version", "product": "nodejs", "lts": True},
    {"id": "tech_news", "turns": ["Yapay zeka dünyasında bu hafta neler oldu?"], "kind": "sourced"},
    {"id": "company", "turns": ["Apple'ın şu anki CEO'su kim?"], "kind": "sourced"},
    {"id": "general", "turns": ["Python nedir?"], "kind": "no_search"},
    {"id": "casual", "turns": ["Bugün kod yazasım yok."], "kind": "no_search"},
    {"id": "general2", "turns": ["Fotosentez nasıl çalışır?"], "kind": "no_search"},
]

_NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?")


def numbers(text: str) -> set[str]:
    return {n.replace(",", ".").rstrip(".0") or "0" for n in _NUM.findall(text)}


def unsupported_numbers(answer: str, context: str, question: str) -> set[str]:
    allowed = numbers(context) | numbers(question) | {str(i) for i in range(0, 11)} | {str(datetime.now().year)}
    return {n for n in numbers(answer) if n not in allowed}


def rank_in_answer(answer: str) -> set[int]:
    a = fold(answer)
    words = {
        "birinci": 1,
        "ikinci": 2,
        "ucuncu": 3,
        "dorduncu": 4,
        "besinci": 5,
        "altinci": 6,
        "yedinci": 7,
        "sekizinci": 8,
        "dokuzuncu": 9,
        "onuncu": 10,
    }
    found = {int(m) for m in re.findall(r"(\d+)\s*\.?\s*(?:sira|sirada|sirayi|konum|basamak)", a)}
    found |= {int(m) for m in re.findall(r"(\d+)\.(?!\d)", a)}
    found |= {v for k, v in words.items() if k in a}
    if re.search(r"\blider", a):
        found.add(1)
    return found


def score(q: dict[str, Any], answer: str, context: str, searched: bool, truth: dict[str, Any]) -> dict[str, Any]:
    a = fold(answer)
    out: dict[str, Any] = {"correct": None}
    if q["kind"] == "no_search":
        out["correct"] = not searched
        return out
    unsupported = unsupported_numbers(answer, context, " ".join(q["turns"]))
    out["unsupported"] = sorted(unsupported)
    out["refused"] = bool(re.search(r"bulamadim|bilgi yok|ulasamadim|net degil|belirtilmemis|erisemedim|soyleyemiyorum|bulunamadi", a))
    tables = truth.get("tables", [])
    if q["kind"] in ("rank", "points"):
        team = fold(q["team"])
        rows = [next((r for r in t if fold(r["team"]).startswith(team)), None) for t in tables]
        rows = [r for r in rows if r]
        out["truth"] = rows
        if not rows:
            return out
        if q["kind"] == "rank":
            out["correct"] = any(r["rank"] in rank_in_answer(answer) for r in rows)
        else:
            out["correct"] = any(str(r["points"]) in numbers(answer) for r in rows)
    elif q["kind"] == "leader":
        leaders = {fold(t[0]["team"]) for t in tables if t}
        out["truth"] = sorted(leaders)
        out["correct"] = any(lead.split()[0] in a for lead in leaders) if leaders else None
        if q.get("offset") == -1 and re.search(r"bitmedi|tamamlanmadi|devam ediyor|henuz bit", a):
            out["correct"] = False  # named the right club but called the finished season unfinished
    elif q["kind"] == "version":
        out["truth"] = sorted(truth.get("versions", []))
        out["correct"] = any(v and re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", answer) for v in truth.get("versions", []))
    elif q["kind"] == "supported_number":
        out["correct"] = bool(numbers(answer) - {str(i) for i in range(0, 11)}) and not unsupported and searched
    elif q["kind"] == "sourced":
        out["correct"] = searched and bool(re.search(r"\[\d\]", answer)) and not out["refused"]
    return out


async def run(label: str, runs: int) -> None:
    now = datetime.now()
    async with httpx.AsyncClient(headers=UA, follow_redirects=True) as client:
        truth_now = {"tables": await standings_truth(client, now)}
        truth_last = {"tables": await standings_truth(client, now, -1)}
        versions = {"python": await eol_latest(client, "python"), "nodejs": await eol_latest(client, "nodejs", lts=True)}
    print(
        "truth tables:",
        [len(t) for t in truth_now["tables"]],
        "leader:",
        [t[0] for t in truth_now["tables"]],
        "last season:",
        [t[0] for t in truth_last["tables"]],
    )
    print("versions:", versions)
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        (tmpd / "logs").mkdir()
        (tmpd / "models").symlink_to(MODELS)
        svc = Services(BootConfig(data_dir=tmpd, warmup=False, enable_audio=False))
        await svc.start()
        svc.user_settings = svc.user_settings.model_copy(update={"memory_auto_learn": False})
        await svc._prime_llm()
        orig = svc.chat.llm.stream_chat
        seen: list[list[dict[str, Any]]] = []

        def spy(model: str, messages: list[dict[str, Any]], options: Any, tools: Any = None) -> Any:
            seen.append(messages)
            return orig(model, messages, options, tools)

        svc.chat.llm.stream_chat = spy  # type: ignore[method-assign]
        for _ in range(runs):
            for q in QUESTIONS:
                conv = None
                t0 = time.perf_counter()
                for text in q["turns"]:
                    seen.clear()
                    await (await svc.chat.submit(TurnRequest(text=text, conversation_id=conv, origin="text", speak=False)))
                    conv = svc.chat.current_conversation_id
                total_ms = (time.perf_counter() - t0) * 1000
                msg = (await svc.store.list_messages(conv))[-1]  # type: ignore[arg-type]
                m = msg.metrics or {}
                web = [x for x in (seen[-1] if seen else []) if x.get("role") == "tool" and x.get("tool_name") == "web_search"]
                context = "\n".join(x["content"] for x in web)
                searched = bool(web) or bool(m.get("research_ms"))
                truth = truth_last if q.get("offset") == -1 else truth_now
                if q["kind"] == "version":
                    truth = {"versions": versions[q["product"]]}
                s = score(q, msg.content, context, searched, truth)
                row = {
                    "id": q["id"],
                    "answer": msg.content,
                    "searched": searched,
                    "research_ms": m.get("research_ms"),
                    "ttft_ms": m.get("ttft_ms"),
                    "total_ms": round(total_ms),
                    "prompt_tokens": m.get("prompt_tokens"),
                    "context_chars": len(context),
                    "context": context,
                    "sources": [x["domain"] for x in msg.sources],
                    **s,
                }
                rows.append(row)
                mark = {True: "✓", False: "✗", None: "·"}[s["correct"]]
                print(
                    f"{mark} {q['id']:12} search={searched!s:5} r={m.get('research_ms')} ctx={len(context):5} unsup={s.get('unsupported')} | {msg.content[:150]!r}",
                    flush=True,
                )
        await svc.stop()

    scored = [r for r in rows if r["correct"] is not None]
    research_rows = [r for r in rows if r["research_ms"]]
    rt = sorted(r["research_ms"] for r in research_rows)
    summary = {
        "label": label,
        "date": now.isoformat(timespec="minutes"),
        "correct": f"{sum(bool(r['correct']) for r in scored)}/{len(scored)}",
        "standings_correct": f"{sum(bool(r['correct']) for r in scored if r['id'] in ('gs_rank', 'fb_rank', 'ts_rank', 'leader', 'bjk_points', 'followup', 'last_season'))}"
        f"/{sum(1 for r in scored if r['id'] in ('gs_rank', 'fb_rank', 'ts_rank', 'leader', 'bjk_points', 'followup', 'last_season'))}",
        "refusals_on_research": sum(bool(r.get("refused")) for r in rows if r["searched"]),
        "answers_with_unsupported_numbers": sum(bool(r.get("unsupported")) for r in rows if r["searched"]),
        "searched_answers": sum(r["searched"] for r in rows),
        "unnecessary_searches": sum(r["searched"] for r in rows if r["id"] in ("general", "casual", "general2")),
        "research_ms_mean": round(statistics.mean(rt)) if rt else None,
        "research_ms_p95": round(rt[min(len(rt) - 1, int(len(rt) * 0.95))]) if rt else None,
        "total_ms_median_research_questions": round(statistics.median(r["total_ms"] for r in research_rows)) if research_rows else None,
        "ttft_ms_median_research": round(statistics.median(r["ttft_ms"] for r in research_rows if r["ttft_ms"])) if research_rows else None,
        "context_chars_median": round(statistics.median(r["context_chars"] for r in research_rows)) if research_rows else None,
        "prompt_tokens_median_research": round(statistics.median(r["prompt_tokens"] for r in research_rows if r["prompt_tokens"]))
        if research_rows
        else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"research-{label}-{stamp}.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "truth": {"now": truth_now, "last": truth_last, "versions": {k: sorted(v) for k, v in versions.items()}},
                "rows": rows,
            },
            ensure_ascii=False,
            indent=1,
        )
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--label", default="run")
    p.add_argument("--runs", type=int, default=1)
    a = p.parse_args()
    asyncio.run(run(a.label, a.runs))


if __name__ == "__main__":
    main()


def rescore(path: str) -> None:
    """Re-apply the (fixed) scorer to a saved run; uses the saved context and truth."""
    data = json.loads(Path(path).read_text())
    truth = data["truth"]
    for r in data["rows"]:
        q = next(q for q in QUESTIONS if q["id"] == r["id"])
        t = {"tables": truth["last"]["tables"] if q.get("offset") == -1 else truth["now"]["tables"]}
        if q["kind"] == "version":
            t = {"versions": set(truth["versions"][q["product"]])}
        if "context" not in r and q["kind"] not in ("rank", "points", "leader", "version"):
            continue  # support checks need the saved model context
        new = score(q, r["answer"], r.get("context", ""), r["searched"], t)
        if "context" not in r:
            new.pop("unsupported", None)
        r.update(new)
    scored = [r for r in data["rows"] if r["correct"] is not None]
    data["summary"]["correct"] = f"{sum(bool(r['correct']) for r in scored)}/{len(scored)}"
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(
        data["summary"]["correct"], [(r["id"], r["correct"]) for r in data["rows"] if r["id"] in ("node_lts", "python_ver", "last_season")]
    )
