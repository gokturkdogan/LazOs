"""Phase-1 diagnostic: full chat turn with real SearXNG + real qwen3.5:9b; dumps the exact tool context the model saw."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.chat import TurnRequest
from app.core.config import BootConfig
from app.services import Services

MODELS = Path.home() / "Library" / "Application Support" / "LAZOS" / "models"


async def main(q: str, runs: int):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "logs").mkdir()
        (tmp / "models").symlink_to(MODELS)
        svc = Services(BootConfig(data_dir=tmp, warmup=False, enable_audio=False))
        await svc.start()
        svc.user_settings = svc.user_settings.model_copy(update={"memory_auto_learn": False})
        orig = svc.chat.llm.stream_chat
        seen = []

        def spy(model, messages, options, tools=None):
            seen.append([m for m in messages if m["role"] == "tool"])
            return orig(model, messages, options, tools)

        svc.chat.llm.stream_chat = spy
        for _ in range(runs):
            await (await svc.chat.submit(TurnRequest(text=q, conversation_id=None, origin="text", speak=False)))
            m = (await svc.store.list_messages(svc.chat.current_conversation_id))[-1]
            tool = seen[-1][-1]["content"] if seen[-1] else ""
            print("ANSWER:", m.content.replace("\n", " ")[:300])
            print(
                "  sources:",
                [s["domain"] for s in m.sources],
                "| research_ms",
                (m.metrics or {}).get("research_ms"),
                "| ctx has '16 puan' or row:",
                ("16 puan" in tool) or ("1, Galatasaray" in tool),
            )
        await svc.stop()


asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3))
