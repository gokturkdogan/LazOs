"""Pronunciation layer: speech-only rewriting, protection rules, user dictionary, end-to-end isolation."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.chat import TurnRequest
from app.speech.pronunciation import BUILTIN_RULES, Pronouncer, Rule
from app.speech.text import to_speakable
from tests.fakes import FakeLLM, FakePlayer, text


@pytest.fixture
def p() -> Pronouncer:
    return Pronouncer()


def test_backend_is_normalised_per_engine(p: Pronouncer) -> None:
    assert p.apply("LAZOS, backend yine patladı.", "piper") == "LAZOS, bek end yine patladı."
    assert p.apply("Backend ne demek?", "macos") == "bek end ne demek?"


def test_typescript_and_terms_inside_turkish_sentences(p: Pronouncer) -> None:
    out = p.apply("Backend'i FastAPI ile yazdım, TypeScript'i ise frontend'de kullanıyorum.", "piper")
    assert out == "bek end'i fast ey pi ay ile yazdım, tayp skript'i ise frontend'de kullanıyorum."
    assert p.apply("React ve Node.js projesinde npm ile JSON okuyorum.", "piper") == (
        "riekt ve nod cey es projesinde en pi em ile ceysın okuyorum."
    )


def test_engine_specific_rules(p: Pronouncer) -> None:
    # "framework" needs a respelling on Piper but Yelda reads it correctly (measured).
    assert p.apply("Bu framework iyi.", "piper") == "Bu freymvörk iyi."
    assert p.apply("Bu framework iyi.", "macos") == "Bu framework iyi."
    assert p.apply("server", "piper") == "sörvır" and p.apply("server", "macos") == "sörver"


def test_word_boundaries_and_case_sensitivity(p: Pronouncer) -> None:
    assert p.apply("API değil, apiye ya da kapı.", "piper") == "ey pi ay değil, apiye ya da kapı."
    assert p.apply("Mac ve mac", "piper") == "Mek ve mac"
    assert p.apply("Dockerfile ve docker", "piper") == "Dockerfile ve doker"  # no partial-word rewrites
    assert p.apply("macOS ve MacBook", "piper") == "mek o es ve mekbuk"


def test_urls_paths_and_commands_are_never_rewritten(p: Pronouncer) -> None:
    src = "Dosya src/backend/api.py içinde; adres https://github.com/x/backend ve config=backend; run() çağır."
    out = p.apply(src, "piper")
    for protected in ("src/backend/api.py", "https://github.com/x/backend", "config=backend", "run()"):
        assert protected in out


def test_code_blocks_never_reach_speech(p: Pronouncer) -> None:
    md = "Şunu çalıştır:\n```bash\nnpm install backend\n```\nSonra **backend** hazır [1]."
    spoken = p.apply(to_speakable(md), "piper")
    assert "npm install" not in spoken and "**" not in spoken and "[1]" not in spoken
    assert "bek end hazır" in spoken


def test_abbreviations_and_punctuation(p: Pronouncer) -> None:
    assert p.apply("Fiyat 50 TL, vb. masraflar.", "piper") == "Fiyat 50 lira, ve benzeri masraflar."
    assert p.apply("Gerçekten mi?! Harika!!!", "piper") == "Gerçekten mi? Harika!"


def test_custom_rules_override_disable_and_engine_scope() -> None:
    p = Pronouncer([*BUILTIN_RULES, Rule("backend", "bekend", "macos", id="u1")])
    assert p.apply("backend", "macos") == "bekend"  # overrides the built-in for Yelda only
    assert p.apply("backend", "piper") == "bek end"
    p.set_rules([*BUILTIN_RULES, Rule("Kubernetes", "kubernetis", id="u2"), Rule("LAZOS", "lazos", enabled=False, id="u3")])
    assert p.apply("Kubernetes ve LAZOS", "piper") == "kubernetis ve LAZOS"


def test_every_builtin_term_matches_itself(p: Pronouncer) -> None:
    for rule in BUILTIN_RULES:
        for engine in ("piper", "macos"):
            if rule.applies_to(engine):
                assert p.apply(f"Bu {rule.term} test.", engine) != f"Bu {rule.term} test.", (rule.term, engine)


# ── API ────────────────────────────────────────────────────────────────────


def test_dictionary_api_crud_and_test_endpoint(client: TestClient, auth: dict[str, str]) -> None:
    overview = client.get("/api/pronunciation", headers=auth).json()
    assert overview["enabled"] is True and len(overview["builtin"]) == len(BUILTIN_RULES) and overview["custom"] == []

    rule = client.post("/api/pronunciation/rules", json={"term": "Kubernetes", "replacement": "kubernetis"}, headers=auth).json()
    spoken = client.post("/api/pronunciation/test", json={"text": "Kubernetes ve backend"}, headers=auth).json()["spoken"]
    assert spoken == {"piper": "kubernetis ve bek end", "macos": "kubernetis ve bek end"}

    edited = client.patch(f"/api/pronunciation/rules/{rule['id']}", json={"replacement": "kübernetis", "engine": "piper"}, headers=auth).json()
    assert edited["replacement"] == "kübernetis" and edited["engine"] == "piper"
    spoken = client.post("/api/pronunciation/test", json={"text": "Kubernetes"}, headers=auth).json()["spoken"]
    assert spoken == {"piper": "kübernetis", "macos": "Kubernetes"}

    assert client.delete(f"/api/pronunciation/rules/{rule['id']}", headers=auth).status_code == 204
    assert client.delete(f"/api/pronunciation/rules/{rule['id']}", headers=auth).status_code == 404
    assert client.post("/api/pronunciation/test", json={"text": "Kubernetes"}, headers=auth).json()["spoken"]["piper"] == "Kubernetes"
    assert client.post("/api/pronunciation/rules", json={"term": "", "replacement": "x"}, headers=auth).status_code == 422


def test_corrections_can_be_disabled(client: TestClient, auth: dict[str, str]) -> None:
    client.put("/api/settings", json={"pronunciation_enabled": False}, headers=auth)
    spoken = client.post("/api/pronunciation/test", json={"text": "backend!!!"}, headers=auth).json()["spoken"]
    assert spoken["piper"] == "backend!"  # punctuation clean-up still applies


# ── End to end: speech changes, everything else stays identical ────────────


class RecordingTTS:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sample_rate = 22_050
        self.spoken: list[str] = []

    def available(self) -> bool:
        return True

    async def synthesize(self, text_: str, speed: float = 1.0) -> np.ndarray:
        self.spoken.append(text_)
        return np.zeros(220, dtype=np.float32)

    async def warm_up(self) -> None:
        return None


def test_display_history_and_memory_are_untouched(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str]) -> None:
    svc: Any = client.services  # type: ignore[attr-defined]
    piper = RecordingTTS("piper")
    svc.tts.engines = {"piper": piper, "macos": RecordingTTS("macos")}
    player = FakePlayer()
    svc.player = player
    svc.chat.player = player
    answer = "Backend'i FastAPI ile yazdım. TypeScript ile frontend de hazır."
    fake_llm.scripts = [text(answer)]
    client.post("/api/memory/items", json={"category": "preference", "content": "Kullanıcı backend için TypeScript kullanıyor."}, headers=auth)

    async def turn() -> None:
        task = await svc.chat.submit(TurnRequest(text="Backend durumu ne?", conversation_id=None, origin="voice", speak=True))
        await task

    client.portal.call(turn)
    spoken = " ".join(piper.spoken)
    assert "bek end'i fast ey pi ay" in spoken and "tayp skript" in spoken
    conv = client.get("/api/conversations", headers=auth).json()[0]
    stored = client.get(f"/api/conversations/{conv['id']}", headers=auth).json()["messages"]
    assert stored[-1]["content"] == answer  # history keeps the original text
    assert stored[0]["content"] == "Backend durumu ne?"
    memories = [m["content"] for m in client.get("/api/memory", headers=auth).json()["memories"]]
    assert memories == ["Kullanıcı backend için TypeScript kullanıyor."]


def test_normaliser_overhead_is_negligible(p: Pronouncer) -> None:
    sentence = "Backend'i FastAPI ile yazdım, TypeScript'i ise frontend'de kullanıyorum ve API ile JSON dönüyorum."
    t = time.perf_counter()
    for _ in range(1000):
        p.apply(sentence, "piper")
    per_call_ms = (time.perf_counter() - t)
    assert per_call_ms < 0.5, f"{per_call_ms:.3f} ms per sentence"  # 1000 calls in < 0.5 s
