"""Live checks against the real local stack. Skipped automatically when a dependency is missing.

    pytest -m integration   # real Ollama model
    pytest -m network       # live web search
    pytest -m models        # real speech models / audio device
"""

from __future__ import annotations

import asyncio
import time

import httpx
import numpy as np
import pytest

from app.core.config import UserSettings
from app.inference.base import GenerationOptions
from app.inference.ollama import OllamaBackend
from app.inference.prompts import WEB_SEARCH_TOOL, build_system_prompt
from app.models.manager import PIPER_VOICE
from app.research.providers.ddgs_provider import DdgsProvider
from app.research.providers.searxng import SearxngProvider
from app.research.service import ResearchService
from tests.conftest import REAL_MODELS_DIR

MODEL = UserSettings().llm_model


def _ollama_has_model() -> bool:
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1).json()
        return any(m["name"] == MODEL for m in tags.get("models", []))
    except Exception:
        return False


ollama = pytest.mark.skipif(not _ollama_has_model(), reason=f"Ollama with {MODEL} not available")


@pytest.mark.integration
@ollama
async def test_real_turkish_streaming_answer() -> None:
    llm = OllamaBackend("http://127.0.0.1:11434")
    messages = [
        {"role": "system", "content": build_system_prompt(voice=False, has_tools=True)},
        {"role": "user", "content": "LAZOS, nasılsın?"},
    ]
    t0 = time.perf_counter()
    first = None
    parts = []
    async for chunk in llm.stream_chat(MODEL, messages, GenerationOptions(max_tokens=120), [WEB_SEARCH_TOOL]):
        if chunk.kind == "text":
            first = first or time.perf_counter() - t0
            parts.append(chunk.text)
    await llm.aclose()
    answer = "".join(parts)
    assert answer.strip()
    assert "<think>" not in answer
    assert any(ch in answer.lower() for ch in "ıiöüşçğ"), f"expected Turkish, got: {answer}"
    assert len(answer) < 600, "small talk should get a short answer"
    print(f"\nTTFT {first * 1000:.0f} ms → {answer!r}")


@pytest.mark.integration
@ollama
async def test_real_cancellation_stops_stream_quickly() -> None:
    llm = OllamaBackend("http://127.0.0.1:11434")
    messages = [{"role": "user", "content": "1'den 500'e kadar tüm sayıları tek tek yaz."}]

    async def consume() -> int:
        n = 0
        async for chunk in llm.stream_chat(MODEL, messages, GenerationOptions(max_tokens=2000)):
            if chunk.kind == "text":
                n += 1
        return n

    task = asyncio.create_task(consume())
    await asyncio.sleep(1.5)
    task.cancel()
    t0 = time.perf_counter()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.perf_counter() - t0 < 0.5
    await llm.aclose()


@pytest.mark.network
async def test_live_research_returns_real_sources() -> None:
    service = ResearchService([SearxngProvider("http://127.0.0.1:8888"), DdgsProvider()])
    result = await service.research("yapay zeka haberleri", max_sources=3)
    if not result.ok:
        pytest.skip(f"no search provider reachable: {result.error}")
    assert 1 <= len(result.sources) <= 3
    for s in result.sources:
        assert s.url.startswith("http") and s.domain and s.title
    assert any(s.excerpt for s in result.sources), "at least one page should be fetched and extracted"


@pytest.mark.models
@pytest.mark.skipif(not (REAL_MODELS_DIR / "piper" / f"{PIPER_VOICE}.onnx").exists(), reason="Piper voice missing")
async def test_piper_turkish_synthesis() -> None:
    from app.speech.tts import PiperTTS

    tts = PiperTTS(REAL_MODELS_DIR / "piper" / f"{PIPER_VOICE}.onnx")
    t0 = time.perf_counter()
    audio = await tts.synthesize("Merhaba, ben Lazos. Sistemler hazır.")
    elapsed = time.perf_counter() - t0
    duration = len(audio) / tts.sample_rate
    assert duration > 1.0 and float(np.max(np.abs(audio))) > 0.05
    assert elapsed < duration, "synthesis must be faster than real time"


@pytest.mark.models
async def test_macos_turkish_voice() -> None:
    from app.speech.tts import MacSayTTS

    tts = MacSayTTS()
    if not tts.available():
        pytest.skip("macOS say not available")
    audio = await tts.synthesize("Bugün hava çok güzel.")
    assert len(audio) / tts.sample_rate > 0.8


@pytest.mark.models
async def test_output_device_lifecycle_with_silence() -> None:
    """Opens the real output device and plays digital silence (inaudible); verifies idle detection."""
    from app.speech.audio_io import AudioDeviceError, AudioPlayer

    levels: list[float] = []
    player = AudioPlayer(asyncio.get_running_loop(), levels.append)
    try:
        player.enqueue(np.zeros(int(22050 * 0.3), np.float32), 22050)
    except AudioDeviceError as exc:
        pytest.skip(f"no output device: {exc}")
    assert player.playing
    assert await player.wait_started(2.0)
    await asyncio.wait_for(player.wait_idle(), 3.0)
    assert not player.playing and levels and levels[-1] == 0.0
    player.close()
