"""Phase-1 control: the same turn, but the research context contains the real standings (as a preserved table)."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.chat import TurnRequest
from app.core.config import BootConfig
from app.research.providers.base import SearchResult
from app.research.service import ResearchService
from app.services import Services

MODELS = Path.home() / "Library" / "Application Support" / "LAZOS" / "models"
TABLE = (
    "Trendyol Süper Lig 2026-27 puan durumu\n| Sıra | Takım | O | G | B | M | AG | YG | Av | P |\n|---|---|---|---|---|---|---|---|---|---|\n"
    "| 1 | Galatasaray | 7 | 5 | 1 | 1 | 16 | 11 | +5 | 16 |\n| 2 | Beşiktaş | 7 | 5 | 0 | 2 | 16 | 8 | +8 | 15 |\n| 3 | Amed SK | 7 | 4 | 2 | 1 | 17 | 9 | +8 | 14 |"
)


class Fake:
    name = "fake"

    async def available(self):
        return True

    async def search(self, query, **kw):
        return [SearchResult("Süper Lig puan durumu", "https://invalid.invalid/a", TABLE, "fake")]


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "logs").mkdir()
        (tmp / "models").symlink_to(MODELS)
        svc = Services(BootConfig(data_dir=tmp, warmup=False, enable_audio=False), research=ResearchService([Fake()]))
        await svc.start()
        for _ in range(3):
            await (
                await svc.chat.submit(
                    TurnRequest(text="Şu an Galatasaray ligde kaçıncı sırada?", conversation_id=None, origin="text", speak=False)
                )
            )
            print("ANSWER:", (await svc.store.list_messages(svc.chat.current_conversation_id))[-1].content.replace("\n", " ")[:250])
        await svc.stop()


asyncio.run(main())
