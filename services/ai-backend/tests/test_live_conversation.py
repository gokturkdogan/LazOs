"""The V1.2 conversation scenarios against the REAL local model, plus real-voice pronunciation checks.

    pytest -m integration -s tests/test_live_conversation.py   # prints a readable transcript
    pytest -m models tests/test_live_conversation.py            # real Piper/Yelda + Whisper round trip

Conversation quality is judged on behaviour (content, length, no clichés, no needless self-introduction),
never on exact strings.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.chat import TurnRequest
from app.core.config import BootConfig
from app.main import create_app
from app.research.service import ResearchService
from app.services import Services
from tests.conftest import REAL_MODELS_DIR
from tests.fakes import FakePlayer, FakeSearchProvider
from tests.test_live import ollama

CLICHE = re.compile(
    r"nasıl yardımcı olabilirim|yardımcı olmaktan|her zaman buradayım|harika (bir )?(soru|fikir)|elbette|tabii ki|"
    r"bir yapay zek[aâ] (asistanı )?olarak|memnuniyetle",
    re.IGNORECASE,
)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


class SpeechRecorder:
    """Stands in for both voices: records exactly what would be spoken, produces silence."""

    def __init__(self, name: str) -> None:
        self.name, self.sample_rate, self.spoken = name, 22_050, []

    def available(self) -> bool:
        return True

    async def synthesize(self, text: str, speed: float = 1.0) -> np.ndarray:
        self.spoken.append(text)
        return np.zeros(2205, dtype=np.float32)

    async def warm_up(self) -> None:
        return None


@contextmanager
def real(boot: BootConfig) -> Iterator[TestClient]:
    boot = boot.model_copy(update={"ollama_url": "http://127.0.0.1:11434"})
    search = FakeSearchProvider()
    app = create_app(boot, services_factory=lambda cfg: Services(cfg, research=ResearchService([search])))
    with TestClient(app) as c:
        svc: Any = app.state.services
        svc.tts.engines = {"piper": SpeechRecorder("piper"), "macos": SpeechRecorder("macos")}
        svc.player = svc.chat.player = FakePlayer()
        c.svc, c.search = svc, search  # type: ignore[attr-defined]
        yield c


def say(c: TestClient, text: str, *, conv: str | None = None, voice: bool = False) -> tuple[str, str]:
    svc: Any = c.svc  # type: ignore[attr-defined]
    svc.tts.engines["piper"].spoken.clear()

    async def go() -> None:
        task = await svc.chat.submit(TurnRequest(text=text, conversation_id=conv, origin="voice" if voice else "text", speak=voice))
        await task

    c.portal.call(go)
    conv_id = svc.chat.current_conversation_id
    answer = c.get(f"/api/conversations/{conv_id}", headers={"Authorization": f"Bearer {c.app.state.token}"}).json()["messages"][-1]["content"]  # type: ignore[attr-defined]
    print(f"\n  {'🎙' if voice else '⌨'} SEN: {text}\n     LAZOS: {answer}")
    if voice:
        print(f"     (sese giden: {' '.join(svc.tts.engines['piper'].spoken)})")
    assert not CLICHE.search(answer), f"cliché in: {answer}"
    assert not EMOJI.search(answer), f"emoji in: {answer}"
    return answer, conv_id


@pytest.mark.integration
@ollama
def test_v12_conversation_scenarios(boot: BootConfig) -> None:
    with real(boot) as c:
        # 1. casual technical complaint → short, natural
        a, _ = say(c, "LAZOS, backend yine patladı.")
        assert len(a) < 260 and "Ben LAZOS" not in a
        # 2. low motivation → natural, no web research, no lecture
        a, _ = say(c, "Bugün hiç kod yazasım yok.")
        assert len(a) < 320 and c.search.queries == []  # type: ignore[attr-defined]
        # 3. concise comparison
        a, _ = say(c, "Python mı TypeScript mi?")
        assert "Python" in a and "TypeScript" in a and len(a) < 700
        # 4–5. identity
        a, _ = say(c, "Sen kimsin?")
        assert "LAZOS" in a.upper() and len(a) < 260
        a, _ = say(c, "Seni kim geliştirdi?")
        # The speaker is the creator, so "Beni sen geliştirdin." is as correct as naming him.
        assert ("Göktürk" in a or "sen geliştirdin" in a.lower()) and len(a) < 200
        # 7. continuity inside one conversation
        _, conv = say(c, "React ile bir proje geliştiriyorum.")
        say(c, "Bir futbol uygulaması.", conv=conv)
        a, _ = say(c, "Az önce hangi projeden bahsediyorduk?", conv=conv)
        assert "futbol" in a.lower() and "hafızamda" not in a.lower()
        # 8. explanation, spoken: correct content and corrected pronunciation in speech only
        a, _ = say(c, "Backend ne demek?", voice=True)
        spoken = " ".join(c.svc.tts.engines["piper"].spoken)  # type: ignore[attr-defined]
        assert any(w in a.lower() for w in ("sunucu", "server", "arka"))
        if "backend" in a.lower():
            assert "bek end" in spoken and "backend" not in spoken.lower()
        assert "**" not in spoken and "#" not in spoken
        # 9. style change without identity drift
        a, _ = say(c, "LAZOS, biraz daha ciddi konuş.", conv=conv)
        assert "Ben LAZOS" not in a and len(a) < 400
        # 10. thanks
        a, _ = say(c, "Teşekkürler.")
        assert len(a) < 80
        # 6. explicit preference, persisted and briefly acknowledged
        a, _ = say(c, "Bundan sonra kısa cevap ver.")
        assert len(a) < 90
        memories = [m.content for m in c.portal.call(c.svc.memory.store.list_memories)]  # type: ignore[attr-defined]
        assert any("kısa" in m.lower() for m in memories), memories

    with real(boot) as c:  # restart: the preference survives
        system = c.svc.chat.system_prompt(has_tools=True)  # type: ignore[attr-defined]
        assert "kısa" in system.split("KULLANICI HAKKINDA BİLDİKLERİN")[1].lower()


@pytest.mark.integration
@ollama
def test_auto_reasoning_solves_a_word_problem(boot: BootConfig) -> None:
    with real(boot) as c:
        a, _ = say(c, "Bir çiftlikte tavuk ve tavşanlar var. Toplam 35 baş ve 94 ayak var. Kaç tavşan var?")
        assert re.search(r"\b12\b", a), a


@pytest.mark.integration
@ollama
def test_voice_answers_are_spoken_style(boot: BootConfig) -> None:
    with real(boot) as c:
        a, _ = say(c, "Backend neden yavaş olabilir?", voice=True)
        assert not re.search(r"^\s*([-*•]|\d+\.)\s", a, re.MULTILINE), "no list formatting in spoken answers"
        assert "**" not in a and len(a) < 700


# ── real voices: the respelled terms are heard as the intended English words ─


@pytest.mark.models
@pytest.mark.skipif(not (REAL_MODELS_DIR / "whisper-large-v3-turbo").exists(), reason="Whisper missing")
@pytest.mark.parametrize("engine", ["piper", "macos"])
async def test_real_voice_round_trip_with_pronunciation(engine: str) -> None:
    from app.speech.pronunciation import Pronouncer
    from app.speech.stt import WhisperSTT
    from app.speech.tts import MacSayTTS, PiperTTS, TTSManager

    piper_path = REAL_MODELS_DIR / "piper" / "tr_TR-dfki-medium.onnx"
    if engine == "piper" and not piper_path.exists():
        pytest.skip("Piper voice missing")
    pronouncer = Pronouncer()
    tts = TTSManager(PiperTTS(piper_path), MacSayTTS(), normalizer=pronouncer.apply)
    tts.preferred = engine
    stt = WhisperSTT(REAL_MODELS_DIR / "whisper-large-v3-turbo")
    recognised = 0
    terms = ["backend", "TypeScript", "JavaScript", "React", "API", "localhost", "database"]
    for term in terms:
        audio, rate, used = await tts.synthesize(f"Bugün {term} hakkında konuştuk.")
        assert used == engine
        a16 = np.interp(np.linspace(0, len(audio), int(len(audio) * 16000 / rate), endpoint=False), np.arange(len(audio)), audio)
        heard = (await stt.transcribe(a16.astype(np.float32), "tr")).text
        ok = re.sub(r"[^a-z]", "", term.lower()) in re.sub(r"[^a-z]", "", heard.lower())
        recognised += ok
        print(f"\n{engine}: {term:11} → {heard}")
    # Synthesis is stochastic, so Whisper's spelling of a correctly spoken term varies between runs
    # ("lokal host" vs "localhost"); Yelda's "backend" is heard as the Turkish spelling "bekend". Allow one.
    assert recognised >= len(terms) - 1
