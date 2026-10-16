"""Conversation persistence across restarts, model detection and verified downloads."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from app.conversations.store import ConversationStore
from app.core.events import EventHub
from app.models.manager import DownloadError, ModelManager, RemoteFile, download_file
from tests.fakes import FakeLLM


async def test_conversations_survive_restart(tmp_path: Path) -> None:
    db = tmp_path / "lazos.db"
    store = ConversationStore(db)
    await store.open()
    conv = await store.create_conversation("Test")
    await store.add_message(conv.id, "user", "Merhaba")
    await store.add_message(conv.id, "assistant", "Selam!", sources=[{"id": 1, "url": "https://a.b"}], metrics={"ttft_ms": 12})
    await store.save_settings({"tts_speed": 1.1})
    await store.close()

    reopened = ConversationStore(db)  # simulates an application restart
    await reopened.open()
    convs = await reopened.list_conversations()
    assert [c.title for c in convs] == ["Test"] and convs[0].message_count == 2
    msgs = await reopened.list_messages(conv.id)
    assert [m.content for m in msgs] == ["Merhaba", "Selam!"]
    assert msgs[1].sources == [{"id": 1, "url": "https://a.b"}] and msgs[1].metrics == {"ttft_ms": 12}
    assert await reopened.load_settings() == {"tts_speed": 1.1}

    await reopened.set_summary(conv.id, "özet", msgs[0].seq)
    assert (await reopened.list_messages(conv.id, after_seq=msgs[0].seq))[0].content == "Selam!"
    assert await reopened.delete_conversation(conv.id)
    assert await reopened.list_messages(conv.id) == []  # cascade
    await reopened.close()


def _manager(tmp_path: Path, llm: FakeLLM, checkpoint: Path | None = None) -> ModelManager:
    return ModelManager(
        models_dir=tmp_path / "models",
        whisper_checkpoint=checkpoint or tmp_path / "none.pt",
        llm=llm,
        hub=EventHub(),
        llm_model="qwen3.5:9b",
    )


async def test_model_detection(tmp_path: Path) -> None:
    mm = _manager(tmp_path, FakeLLM())
    await mm.refresh()
    status = {m["id"]: m for m in mm.describe()}
    assert status["llm"]["status"] == "present"
    assert status["stt"]["status"] == "missing" and status["stt"]["source"] == "download"
    assert status["vad"]["status"] == "missing"
    assert not mm.all_present()

    # Files with the exact expected size are detected as present.
    vad_spec = mm.specs["vad"].files[0]
    mm.vad_model.parent.mkdir(parents=True, exist_ok=True)
    mm.vad_model.write_bytes(b"\0" * vad_spec.size)
    await mm.refresh()
    assert mm.states["vad"].status == "present"


async def test_existing_whisper_checkpoint_is_converted_not_downloaded(tmp_path: Path) -> None:
    ckpt = tmp_path / "large-v3-turbo.pt"
    ckpt.write_bytes(b"fake")
    mm = _manager(tmp_path, FakeLLM(), ckpt)
    await mm.refresh()
    stt = next(m for m in mm.describe() if m["id"] == "stt")
    assert stt["source"] == "local-convert" and stt["download_bytes"] == 0


async def test_llm_unreachable_is_unknown(tmp_path: Path) -> None:
    mm = _manager(tmp_path, FakeLLM(reachable=False))
    await mm.refresh()
    assert mm.states["llm"].status == "unknown" and mm.states["llm"].error


def _transport(payload: bytes) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, content=payload))


async def test_download_verifies_checksum(tmp_path: Path) -> None:
    payload = b"lazos-model-bytes" * 100
    good = RemoteFile("https://example.invalid/m.onnx", "m.onnx", len(payload), hashlib.sha256(payload).hexdigest())
    seen: list[int] = []
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        await download_file(client, good, tmp_path / "m.onnx", seen.append)
    assert (tmp_path / "m.onnx").read_bytes() == payload and seen[-1] == len(payload)

    bad = RemoteFile("https://example.invalid/x.onnx", "x.onnx", len(payload), "0" * 64)
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        with pytest.raises(DownloadError, match="checksum"):
            await download_file(client, bad, tmp_path / "x.onnx", seen.append)
    assert not (tmp_path / "x.onnx").exists() and not (tmp_path / "x.onnx.part").exists()

    short = RemoteFile("https://example.invalid/y.onnx", "y.onnx", len(payload) + 1, None)
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        with pytest.raises(DownloadError, match="size"):
            await download_file(client, short, tmp_path / "y.onnx", seen.append)


def test_whisper_key_remap() -> None:
    import numpy as np

    from app.models.whisper_convert import _remap

    key, val = _remap("encoder.blocks.0.mlp.0.weight", np.zeros((4, 2)))
    assert key == "encoder.blocks.0.mlp1.weight"
    key, val = _remap("encoder.conv1.weight", np.zeros((8, 3, 5)))
    assert key == "encoder.conv1.weight" and val.shape == (8, 5, 3)
