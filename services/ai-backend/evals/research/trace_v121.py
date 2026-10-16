"""Phase-1 diagnostic: trace the V1.2.1 research pipeline stage by stage for one query (real SearXNG, real pages)."""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx

from app.research.fetch import USER_AGENT, extract_text, fetch_html, select_passages
from app.research.providers.searxng import SearxngProvider
from app.research.router import build_search_query, route
from app.research.service import ResearchService

Q = sys.argv[1] if len(sys.argv) > 1 else "Şu an Galatasaray ligde kaçıncı sırada?"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/trace")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    print("1. route:", route(Q))
    query = build_search_query(Q)
    print("2. query:", repr(query))
    svc = ResearchService([SearxngProvider("http://127.0.0.1:8888")])
    t = time.perf_counter()
    results, prov, errs = await svc.search(query, max_results=8)
    print(f"3. searxng {prov} {len(results)} results in {(time.perf_counter() - t) * 1000:.0f} ms errs={errs}")
    for r in results:
        print("   -", r.url, "|", r.title[:70], "|", r.published, "\n      snippet:", r.snippet[:200].replace("\n", " "))
    t = time.perf_counter()
    res = await svc.research(query, max_sources=3)
    print(f"4. research() {(time.perf_counter() - t) * 1000:.0f} ms; picked:", [s.url for s in res.sources], res.error)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.8"}) as client:
        for i, s in enumerate(res.sources, 1):
            try:
                html = await fetch_html(client, s.url, 6)
            except Exception as exc:
                print(f"   [{i}] fetch error {exc!r}")
                continue
            if not html:
                print(f"   [{i}] fetch returned nothing")
                continue
            (OUT / f"page{i}.html").write_text(html)
            text = extract_text(html, s.url)
            (OUT / f"page{i}.txt").write_text(text)
            has_table = "<table" in html.lower()
            gs_html = html.count("Galatasaray")
            gs_text = text.count("Galatasaray")
            excerpt = select_passages(text, query, 900) if text else ""
            print(
                f"   [{i}] html={len(html)} chars, <table>={has_table}, 'Galatasaray' in html={gs_html}, in extracted text={gs_text}, text={len(text)} chars, excerpt has GS={'Galatasaray' in excerpt}"
            )
    prompt = res.as_prompt()
    (OUT / "model_context.txt").write_text(prompt)
    print("5. model context chars:", len(prompt))
    print(prompt[:3000])


asyncio.run(main())
