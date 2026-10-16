"""Voice pipeline integration: real Silero VAD + real Whisper, simulated microphone and speaker.

Turkish speech is synthesized with the on-device macOS voice, fed into the voice controller as
32 ms microphone frames, and must flow through VAD → STT → LLM → sentence TTS → playback with the
correct state sequence. Requires the locally installed models (marker: `models`).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
import pytest

from app.chat import ChatService
from app.conversations.store import ConversationStore
from app.core.config import UserSettings
from app.core.events import EventHub
from app.core.metrics import MetricsLog
from app.core.state import StateMachine
from app.research.service import ResearchService
from app.speech.stt import WhisperSTT
from app.speech.tts import TTSManager
from app.speech.vad import SileroVAD
from app.speech.voice import VoiceController
from tests.conftest import REAL_MODELS_DIR
from tests.fakes import FakeLLM, FakeMic, FakePlayer, FakeSearchProvider, FakeTTS, text

WHISPER_DIR = REAL_MODELS_DIR / "whisper-large-v3-turbo"
VAD_MODEL = REAL_MODELS_DIR / "silero_vad.onnx"

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(
        not (WHISPER_DIR / "weights.safetensors").exists() or not VAD_MODEL.exists() or not shutil.which("say"),
        reason="local speech models or macOS `say` not available",
    ),
]


def say_16k(sentence: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.wav"
        subprocess.run(
            ["say", "-v", "Yelda", "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@16000", sentence],
            check=True,
        )
        with wave.open(str(out)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


async def feed(mic: FakeMic, audio: np.ndarray, *, lead_silence_s: float = 0.5, tail_silence_s: float = 1.2) -> None:
    rng = np.random.default_rng(0)
    signal = np.concatenate(
        [
            rng.normal(0, 0.002, int(16000 * lead_silence_s)).astype(np.float32),
            audio,
            rng.normal(0, 0.002, int(16000 * tail_silence_s)).astype(np.float32),
        ]
    )
    for i in range(0, len(signal) - 511, 512):
        await mic.queue.put(signal[i : i + 512])
        await asyncio.sleep(0.002)


@pytest.fixture(scope="module")
def stt() -> WhisperSTT:
    return WhisperSTT(WHISPER_DIR)


async def build(tmp_path: Path, stt: WhisperSTT, llm: FakeLLM):  # type: ignore[no-untyped-def]
    hub = EventHub()
    events = hub.subscribe()
    state = StateMachine(hub)
    store = ConversationStore(tmp_path / "v.db")
    await store.open()
    tts_engine = FakeTTS()
    tts = TTSManager(tts_engine, tts_engine)  # type: ignore[arg-type]
    player = FakePlayer()
    settings = UserSettings()
    chat = ChatService(
        store=store,
        llm=llm,
        research=ResearchService([FakeSearchProvider()]),
        hub=hub,
        state=state,
        tts=tts,
        player=player,  # type: ignore[arg-type]
        metrics=MetricsLog(),
        settings=lambda: settings,
    )
    mic = FakeMic()
    voice = VoiceController(
        mic=mic,  # type: ignore[arg-type]
        vad_factory=lambda: SileroVAD(VAD_MODEL),
        stt=stt,
        chat=chat,
        state=state,
        hub=hub,
        settings=lambda: settings,
    )
    return voice, mic, events, tts_engine, player, store


def drain(q: asyncio.Queue) -> list[dict]:  # type: ignore[type-arg]
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def wait_for_state(voice: VoiceController, value: str, timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while voice.state.state.value != value:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"state stuck at {voice.state.state.value}, expected {value}")
        await asyncio.sleep(0.02)


async def test_continuous_voice_conversation(tmp_path: Path, stt: WhisperSTT) -> None:
    llm = FakeLLM([text("Kuantum bilgisayarlar kübit kullanır. ", "Bu sayede bazı problemleri hızlı çözerler.")])
    voice, mic, events, tts_engine, player, store = await build(tmp_path, stt, llm)
    try:
        await voice.set_continuous(True, None)
        await wait_for_state(voice, "listening")
        await feed(mic, say_16k("Bana kuantum bilgisayarları kısaca anlatır mısın?"))
        # After answering, LAZOS must return to listening on its own (continuous mode).
        await asyncio.sleep(0.3)
        await wait_for_state(voice, "listening", timeout=60)

        evs = drain(events)
        transcript = next(e for e in evs if e["type"] == "voice.transcript")
        assert "kuantum" in transcript["text"].lower()
        assert transcript["language"] == "tr"

        states = [e["state"] for e in evs if e["type"] == "state"]
        order = ["listening", "transcribing", "thinking", "speaking", "listening"]
        idx = 0
        for s in states:
            if idx < len(order) and s == order[idx]:
                idx += 1
        assert idx == len(order), f"unexpected state sequence: {states}"

        # The LLM received the transcript; the answer was spoken sentence by sentence.
        assert "kuantum" in llm.calls[0]["messages"][-1]["content"].lower()
        assert len(tts_engine.spoken) == 2 and tts_engine.spoken[0].startswith("Kuantum bilgisayarlar")
        assert len(player.played) == 2

        done = next(e for e in evs if e["type"] == "chat.done")
        assert done["message"]["origin"] == "voice"
        assert done["message"]["metrics"]["stt_ms"] > 0
        assert done["message"]["metrics"]["tts_first_audio_ms"] > 0
    finally:
        await voice.stop()
        await store.close()
    assert voice.mode == "off" and not mic.running


async def test_push_to_talk_returns_to_idle(tmp_path: Path, stt: WhisperSTT) -> None:
    llm = FakeLLM([text("İyiyim, teşekkürler.")])
    voice, mic, events, tts_engine, player, store = await build(tmp_path, stt, llm)
    try:
        await voice.start_ptt(None)
        await wait_for_state(voice, "listening")
        await feed(mic, say_16k("Merhaba, nasılsın?"))
        await asyncio.sleep(0.3)
        await wait_for_state(voice, "idle", timeout=60)
        assert voice.mode == "off"
        assert tts_engine.spoken == ["İyiyim, teşekkürler."]
    finally:
        await voice.stop()
        await store.close()


async def test_silence_only_produces_no_turn(tmp_path: Path, stt: WhisperSTT) -> None:
    llm = FakeLLM()
    voice, mic, events, tts_engine, player, store = await build(tmp_path, stt, llm)
    try:
        await voice.start_ptt(None)
        await wait_for_state(voice, "listening")
        await feed(mic, np.zeros(0, np.float32), lead_silence_s=1.0, tail_silence_s=0.5)
        voice.stop_ptt()  # user releases without speaking
        await wait_for_state(voice, "idle", timeout=10)
        assert llm.calls == []
        assert any(e["type"] == "voice.notice" for e in drain(events))
    finally:
        await voice.stop()
        await store.close()


async def test_muted_microphone_reports_permission_problem(tmp_path: Path, stt: WhisperSTT) -> None:
    voice, mic, events, *_rest, store = await build(tmp_path, stt, FakeLLM())
    mic.peak_since_start = 0.0  # macOS delivers pure zeros when microphone access is denied
    try:
        await voice.start_ptt(None)
        await wait_for_state(voice, "listening")
        for _ in range(110):  # ~3.5 s of digital silence, delivered in real time
            await mic.queue.put(np.zeros(512, np.float32))
            await asyncio.sleep(0.032)
        await wait_for_state(voice, "error", timeout=10)
        notice = next(e for e in drain(events) if e["type"] == "voice.notice")
        assert "izin" in notice["message"]
        await wait_for_state(voice, "idle", timeout=10)  # recovers on its own
    finally:
        await voice.stop()
        await store.close()
