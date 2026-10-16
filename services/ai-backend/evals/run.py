"""LAZOS conversation evaluation against the REAL local model.

    python -m evals.run benchmark [--split dev|heldout|all]   # behavioural benchmark (scenarios.json)
    python -m evals.run feedback                               # approved-example retrieval A/B experiment

Every scenario runs in a fresh temporary data directory (no user data). Web search is a recording fake:
we measure *whether* LAZOS decides to search, not what the web says. Results go to evals/results/.
Checks are behavioural (content any-of, forbidden phrases, length limits, routing, thinking), never
exact-answer matching; "pass" therefore means "met these documented checks", not "perfect answer".
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
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.chat import TurnRequest  # noqa: E402
from app.core.config import BootConfig  # noqa: E402
from app.feedback.policy import new_terms  # noqa: E402
from app.research.providers.base import SearchResult  # noqa: E402
from app.research.service import ResearchService  # noqa: E402
from app.services import Services  # noqa: E402

HERE = Path(__file__).parent
MODELS = Path.home() / "Library" / "Application Support" / "LAZOS" / "models"
CLICHE = re.compile(
    r"nasıl yardımcı olabilirim|yardımcı olmaktan|her zaman buradayım|harika (bir )?(soru|fikir)|elbette|tabii ki|"
    r"bir yapay zek[aâ] (asistanı )?olarak|memnuniyetle",
    re.IGNORECASE,
)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


class RecordingSearch:
    name = "recording"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def available(self) -> bool:
        return True

    async def search(self, query: str, *, max_results: int, language: str | None, news: bool) -> list[SearchResult]:
        self.queries.append(query)
        return [SearchResult("Örnek kaynak", "https://example.com/a", "Bu bir test sonucudur; güncel veri içermez.", "recording")]


async def start(tmp: Path, search: RecordingSearch) -> Services:
    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    if not (tmp / "models").exists():
        (tmp / "models").symlink_to(MODELS)
    svc = Services(BootConfig(data_dir=tmp, warmup=False), research=ResearchService([search]))
    await svc.start()
    svc.user_settings = svc.user_settings.model_copy(update={"memory_auto_learn": False})
    return svc


async def converse(svc: Services, turns: list[str]) -> tuple[str, dict[str, Any]]:
    conv = None
    for text in turns:
        task = await svc.chat.submit(TurnRequest(text=text, conversation_id=conv, origin="text", speak=False))
        await task
        conv = svc.chat.current_conversation_id
    msgs = await svc.store.list_messages(conv)  # type: ignore[arg-type]
    last = msgs[-1]
    return (last.content if last.role == "assistant" else ""), (last.metrics or {})


def check(answer: str, metrics: dict[str, Any], checks: dict[str, Any], searched: bool) -> list[str]:
    fails = []
    if not answer.strip():
        return ["empty answer"]
    if CLICHE.search(answer):
        fails.append(f"cliché: {CLICHE.search(answer).group(0)!r}")  # type: ignore[union-attr]
    if EMOJI.search(answer):
        fails.append("emoji")
    if (anys := checks.get("any_of")) and not any(a.lower() in answer.lower() for a in anys):
        fails.append(f"missing any of {anys}")
    for bad in checks.get("none_of", []):
        if bad.lower() in answer.lower():
            fails.append(f"contains {bad!r}")
    if (mx := checks.get("max_chars")) and len(answer) > mx:
        fails.append(f"too long ({len(answer)} > {mx})")
    if "research" in checks and checks["research"] != searched:
        fails.append(f"research expected {checks['research']}, got {searched}")
    if "think" in checks and bool(metrics.get("reasoning")) != checks["think"]:
        fails.append(f"thinking expected {checks['think']}")
    return fails


async def benchmark(split: str) -> None:
    scenarios = [s for s in json.loads((HERE / "scenarios.json").read_text()) if split == "all" or s["split"] == split]
    rows = []
    for sc in scenarios:
        t0 = time.perf_counter()
        if sc["kind"] == "stt":
            got = new_terms(sc["turns"][0], sc["checks"]["corrected"])
            fails = [] if got == sc["checks"]["terms"] else [f"terms {got} != {sc['checks']['terms']}"]
            rows.append(
                {
                    "id": sc["id"],
                    "category": sc["category"],
                    "split": sc["split"],
                    "pass": not fails,
                    "fails": fails,
                    "turns": sc["turns"],
                    "answer": f"→ {sc['checks']['corrected']} (terms {got})",
                    "ttft_ms": None,
                    "total_ms": 0,
                }
            )
            continue
        search = RecordingSearch()
        with tempfile.TemporaryDirectory() as tmp:
            svc = await start(Path(tmp), search)
            for category, content in sc["setup"].get("memories", []):
                await svc.memory.store.add_memory(category, content, origin="manual")
            await svc.memory.refresh_profile()
            answer, metrics = await converse(svc, sc["turns"])
            await svc.stop()
        fails = check(answer, metrics, sc["checks"], bool(search.queries))
        rows.append(
            {
                "id": sc["id"],
                "category": sc["category"],
                "split": sc["split"],
                "pass": not fails,
                "fails": fails,
                "turns": sc["turns"],
                "answer": answer,
                "ttft_ms": metrics.get("ttft_ms"),
                "total_ms": round((time.perf_counter() - t0) * 1000),
            }
        )
        print(f"{'✓' if not fails else '✗'} {sc['id']:22} {sc['turns'][-1][:44]:46} {answer[:70]!r} {fails or ''}", flush=True)
    summary = {}
    for key in sorted({r["category"] for r in rows}):
        sel = [r for r in rows if r["category"] == key]
        summary[key] = f"{sum(r['pass'] for r in sel)}/{len(sel)}"
    for sp in ("dev", "heldout"):
        sel = [r for r in rows if r["split"] == sp]
        if sel:
            summary[f"[{sp}]"] = f"{sum(r['pass'] for r in sel)}/{len(sel)}"
    ttfts = [r["ttft_ms"] for r in rows if r["ttft_ms"]]
    summary["median_ttft_ms"] = round(statistics.median(ttfts)) if ttfts else None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (HERE / "results" / f"benchmark-{split}-{stamp}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1)
    )
    md = [f"# Benchmark ({split}) {stamp}", "", "| category | pass |", "|---|---|", *[f"| {k} | {v} |" for k, v in summary.items()], ""]
    for r in rows:
        md += [
            f"### {'✓' if r['pass'] else '✗'} {r['id']} ({r['split']})",
            *[f"> **Sen:** {t}" for t in r["turns"]],
            f"> **LAZOS:** {r['answer']}",
            "",
        ]
        if r["fails"]:
            md += [f"*Fails:* {'; '.join(r['fails'])}", ""]
    (HERE / "results" / f"benchmark-{split}-{stamp}.md").write_text("\n".join(md))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


async def _seed_pool(svc: Services, pool: list[dict[str, str]]) -> None:
    """Seed approved corrections exactly as the UI would (answer → correction, 'use as example' ticked)."""
    for item in pool:
        conv = await svc.store.create_conversation("pool")
        await svc.store.add_message(conv.id, "user", item["prompt"])
        ans = await svc.store.add_message(
            conv.id,
            "assistant",
            "Merhaba! Bu konuyu açıklamak için önce birkaç şeyi anlamam gerekiyor. "
            "Hangi teknolojileri kullanıyorsun ve tam olarak neyi öğrenmek istiyorsun?",
        )
        await svc.feedback.correct(ans.id, item["preferred"], use_as_example=True)
    async with svc.store.db.execute("SELECT COUNT(*) FROM response_corrections WHERE status='approved' AND kind='style'") as cur:
        approved = (await cur.fetchone())[0]
    assert approved == len(pool), f"only {approved}/{len(pool)} pool items became approved style examples"


async def feedback_experiment(reps: int = 2) -> None:
    """A/B: identical probes with approved-example retrieval OFF vs ON. 'unrelated' probes measure noise
    (they must never receive examples, so any OFF/ON difference there is run-to-run variance)."""
    data = json.loads((HERE / "feedback_experiment.json").read_text())
    scenario_texts = {t for s in json.loads((HERE / "scenarios.json").read_text()) for t in s["turns"]}
    results: dict[str, Any] = {}
    for exp in data["experiments"]:
        pool_prompts = {p["prompt"] for p in exp["pool"]}
        probes = exp["probes"]["similar"] + exp["probes"]["unrelated"]
        assert not pool_prompts & set(probes), "pool and probes must not overlap"
        assert not pool_prompts & scenario_texts, "pool must not overlap the benchmark"
        marker = re.compile(exp["marker"], re.IGNORECASE)
        with tempfile.TemporaryDirectory() as tmp:
            svc = await start(Path(tmp), RecordingSearch())
            await _seed_pool(svc, exp["pool"])
            await svc._prime_llm()
            for enabled in (False, True):
                svc.user_settings = svc.user_settings.model_copy(update={"feedback_examples_enabled": enabled})
                for group, texts in exp["probes"].items():
                    rows = []
                    for _ in range(reps):
                        for probe in texts:
                            answer, m = await converse(svc, [probe])
                            sentences = len([x for x in re.split(r"(?<=[.!?])\s+", answer.strip()) if x])
                            rows.append(
                                {
                                    "probe": probe,
                                    "answer": answer,
                                    "chars": len(answer),
                                    "sentences": sentences,
                                    "marker": bool(marker.search(answer)),
                                    "verbatim_copy": any(p["preferred"] in answer for p in exp["pool"]),
                                    "examples": m.get("examples") or 0,
                                    "ttft_ms": m.get("ttft_ms"),
                                    "prompt_tokens": m.get("prompt_tokens"),
                                }
                            )
                    key = f"{exp['name']} | {group} | examples {'ON' if enabled else 'OFF'}"
                    results[key] = {
                        "n": len(rows),
                        "median_chars": statistics.median(r["chars"] for r in rows),
                        "mean_sentences": round(statistics.mean(r["sentences"] for r in rows), 2),
                        exp["marker_label"]: sum(r["marker"] for r in rows),
                        "verbatim_copies": sum(r["verbatim_copy"] for r in rows),
                        "turns_with_examples": sum(1 for r in rows if r["examples"]),
                        "median_ttft_ms": statistics.median(r["ttft_ms"] for r in rows if r["ttft_ms"]),
                        "median_prompt_tokens": statistics.median(r["prompt_tokens"] for r in rows if r["prompt_tokens"]),
                        "rows": rows,
                    }
                    print(key, json.dumps({k: v for k, v in results[key].items() if k != "rows"}, ensure_ascii=False), flush=True)
            t = time.perf_counter()
            for _ in range(200):
                await svc.feedback.examples_for(exp["probes"]["similar"][0])
            results[f"{exp['name']} | retrieval_ms_per_call"] = round((time.perf_counter() - t) / 200 * 1000, 3)
            await svc.stop()
    print({k: v for k, v in results.items() if "retrieval_ms" in k})
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (HERE / "results" / f"feedback-{stamp}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=["benchmark", "feedback"])
    parser.add_argument("--split", default="all", choices=["dev", "heldout", "all"])
    args = parser.parse_args()
    asyncio.run(benchmark(args.split) if args.what == "benchmark" else feedback_experiment())


if __name__ == "__main__":
    main()
