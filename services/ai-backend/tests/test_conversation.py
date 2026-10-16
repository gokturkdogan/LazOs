"""Conversation intelligence: prompt assembly, voice/text style, continuity fixes, inference profiles."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.chat import TurnRequest
from app.inference.context import stamp
from app.inference.prompts import build_system_prompt
from app.inference.reasoning import needs_deep_reasoning
from app.memory.identity import CoreIdentity, render_identity, tr_copula, tr_genitive
from app.memory.intent import is_episodic, is_identity_fact
from app.research.router import route
from tests.fakes import FakeLLM, text


def run_turn(client: TestClient, message: str, *, origin: str = "text", conv: str | None = None) -> Any:
    svc: Any = client.services  # type: ignore[attr-defined]

    async def go() -> None:
        task = await svc.chat.submit(TurnRequest(text=message, conversation_id=conv, origin=origin, speak=False))
        await task

    client.portal.call(go)
    return svc.chat.current_conversation_id


# ── prompt assembly ────────────────────────────────────────────────────────


def test_voice_tag_is_deterministic_and_carried_by_history(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.scripts = [text("Her şey yolunda."), text("Tamam.")]
    conv = run_turn(client, "Selam, naber?", origin="voice")
    run_turn(client, "Peki.", origin="text", conv=conv)
    first, second = fake_llm.calls[0]["messages"], fake_llm.calls[1]["messages"]
    assert " · sesli]" in first[-1]["content"]
    assert second[1]["content"] == first[-1]["content"], "history re-renders the voice turn identically (prompt cache)"
    assert " · sesli]" not in second[-1]["content"]
    assert first[0]["content"] == second[0]["content"], "same system prompt for voice and text turns"


def test_stamp_format() -> None:
    when = datetime(2026, 10, 16, 9, 5)
    assert stamp("a", when) == "[Şu an: 16 Ekim 2026, Cuma, saat 09:05]\na"
    assert stamp("a", when, voice=True) == "[Şu an: 16 Ekim 2026, Cuma, saat 09:05 · sesli]\na"


def test_system_prompt_contents_and_styles() -> None:
    base = build_system_prompt(has_tools=True, llm_model="qwen3.5:9b")
    assert "qwen3.5:9b" in base and "SESLİ YANITLAR" in base and "ÖRNEK TARZ" in base
    assert "Sunucu yine çöktü" not in base, "examples must not mirror the evaluation prompts"
    for banned in ("Bu Mac", "bu Mac onun"):
        assert banned not in base  # "Mac" mentions were read as "Maç" by the Turkish voice
    assert "Profesyonel" in build_system_prompt(has_tools=True, conversation_style="professional")
    assert "Olabildiğince kısa" in build_system_prompt(has_tools=True, conversation_style="concise")
    assert "beş sekiz cümle" in build_system_prompt(has_tools=True, voice_style="detailed")


def test_settings_flow_into_the_system_prompt(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str]) -> None:
    client.put("/api/settings", json={"conversation_style": "professional", "voice_style": "detailed"}, headers=auth)
    fake_llm.scripts = [text("Tamam.")]
    run_turn(client, "Merhaba")
    system = fake_llm.calls[0]["messages"][0]["content"]
    assert "ÜSLUP: Profesyonel" in system and "beş sekiz cümle" in system


def test_turkish_suffixes_for_identity_examples() -> None:
    assert [tr_copula(n) for n in ("Göktürk", "Ali", "Murat", "Umut", "Ayşe")] == ["Göktürk'sün", "Ali'sin", "Murat'sın", "Umut'sun", "Ayşe'sin"]
    assert [tr_genitive(n) for n in ("Göktürk Doğan", "Ali", "Murat", "Umut", "Ayşe")] == [
        "Göktürk Doğan'ın", "Ali'nin", "Murat'ın", "Umut'un", "Ayşe'nin"
    ]
    block = render_identity(CoreIdentity())
    assert "Sen Göktürk'sün; beni geliştiren kişi." in block
    assert "Göktürk Doğan'ın geliştirdiği kişisel bir yapay zekâ asistanıyım" in block
    renamed = render_identity(CoreIdentity(creator_name="Murat Kaya"))
    assert "Sen Murat'sın" in renamed and "Murat Kaya'nın geliştirdiği" in renamed


# ── continuity & retrieval fixes found in the audit ───────────────────────


def test_today_alone_does_not_trigger_research() -> None:
    assert route("Bugün hiç kod yazasım yok.").action != "search"
    assert route("Bugün çok yoruldum").action != "search"
    assert route("LAZOS, bugün yapay zeka dünyasında neler oldu?").action == "search"
    assert route("Dolar kuru şu an ne kadar?").action == "search"


def test_personal_future_questions_do_not_trigger_research() -> None:
    # Found by the V1.2.1 benchmark (dev split): a year in a question about the user's own plans forced a search.
    assert route("2031'de hangi telefonu alacağım?").action != "search"
    assert route("Yarın neyim vardı?").action != "search"
    assert route("2026'da çıkan en son iPhone hangisi?").action == "search"
    assert route("Bugün hangi filmler vizyonda?").action == "search"


def test_current_conversation_references_are_not_episodic() -> None:
    assert not is_episodic("Az önce hangi projeden bahsediyorduk?")
    assert not is_episodic("Demin ne demiştim?")
    assert is_episodic("Geçen hafta hangi projeden bahsetmiştik?")


def test_current_conversation_question_gets_no_contradicting_memory_note(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.scripts = [text("Ne tür bir proje?"), text("Güzel."), text("Futbol uygulamasından.")]
    conv = run_turn(client, "React ile bir proje geliştiriyorum.")
    run_turn(client, "Bir futbol uygulaması.", conv=conv)
    run_turn(client, "Az önce hangi projeden bahsediyorduk?", conv=conv)
    last = fake_llm.calls[-1]["messages"]
    assert not [m for m in last if m["role"] == "tool"], "no episodic 'not found' note for the current conversation"
    assert any("futbol uygulaması" in m["content"].lower() for m in last if m["role"] == "user")


def test_short_answers_do_not_pull_unrelated_project_memories(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str]) -> None:
    client.post("/api/memory/items", json={"category": "project", "content": "Kullanıcı Atlas adında bir hava durumu uygulaması geliştiriyor."}, headers=auth)
    fake_llm.scripts = [text("Ne tür?"), text("Anladım.")]
    conv = run_turn(client, "React ile bir proje geliştiriyorum.")
    run_turn(client, "Bir futbol uygulaması.", conv=conv)
    assert not any(m["role"] == "tool" for m in fake_llm.calls[-1]["messages"])
    fake_llm.scripts = [text("Atlas.")]
    run_turn(client, "Projelerim neler?")
    assert any("Atlas" in m["content"] for m in fake_llm.calls[-1]["messages"] if m["role"] == "tool")


def test_identity_facts_in_memory_are_kept_but_not_used(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str]) -> None:
    client.post("/api/memory/items", json={"category": "user_identity", "content": "Kullanıcı LAZOS'un yaratıcısıdır."}, headers=auth)
    client.post("/api/memory/items", json={"category": "user_identity", "content": "Kullanıcı Göktürk."}, headers=auth)
    fake_llm.scripts = [text("Selam.")]
    run_turn(client, "Selam")
    system = fake_llm.calls[0]["messages"][0]["content"]
    assert "- Kullanıcı Göktürk." in system and "LAZOS'un yaratıcısıdır" not in system
    assert len(client.get("/api/memory", headers=auth).json()["memories"]) == 2  # nothing deleted
    assert is_identity_fact("Kullanıcı LAZOS'un yaratıcısıdır.") and not is_identity_fact("Kullanıcı LAZOS adında bir asistan geliştiriyor.")


def test_explicit_memory_acknowledgement_is_requested_briefly(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str]) -> None:
    from tests.fakes import memory_ops

    fake_llm.scripts = [memory_ops({"action": "add", "category": "communication", "content": "Kullanıcı kısa cevaplar istiyor."}), text("Tamam.")]
    run_turn(client, "Bundan sonra kısa cevap ver.")
    note = next(m["content"] for m in fake_llm.calls[-1]["messages"] if m["role"] == "tool")
    assert "tek kısa ve doğal cümleyle onayla" in note


# ── inference profiles ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "deep"),
    [
        ("Bir çiftlikte tavuk ve tavşanlar var. Toplam 35 baş ve 94 ayak var. Kaç tavşan var?", True),
        ("17 × 23 + 89 kaç eder?", True),
        ("Bunu adım adım düşün: hangisi daha iyi?", True),
        ("Bu kod neden çalışmıyor?\n```py\nprint(x)\n```", True),
        ("Bir sınıfta 30 öğrenci var ve üçte biri gözlüklü. Kaç öğrenci gözlüksüz?", True),
        ("2 artı 2 kaç eder?", False),
        ("Python mı TypeScript mi?", False),
        ("Backend neden yavaş olabilir?", False),
        ("Saat 15:30'da 2 toplantım var", False),
    ],
)
def test_deep_reasoning_detection(message: str, deep: bool) -> None:
    assert needs_deep_reasoning(message) is deep


@pytest.mark.parametrize(("mode", "message", "think"), [
    ("auto", "17 × 23 + 89 kaç eder?", True), ("auto", "Selam", False), ("fast", "17 × 23 + 89 kaç eder?", False), ("deep", "Selam", True),
])
def test_generation_profile(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str], mode: str, message: str, think: bool) -> None:
    client.put("/api/settings", json={"reasoning_mode": mode}, headers=auth)
    fake_llm.scripts = [text("Tamam.")]
    run_turn(client, message)
    opts = fake_llm.calls[-1]["options"]
    assert opts.think is think
    if think:
        assert opts.temperature == 0.6 and opts.top_p == 0.95 and opts.max_tokens >= 6144
    else:
        assert opts.repeat_penalty == 1.0 and opts.presence_penalty == 1.5 and opts.top_p == 0.8


# ── speech recognition vocabulary ──────────────────────────────────────────


def test_stt_vocabulary(client: TestClient, auth: dict[str, str]) -> None:
    svc: Any = client.services  # type: ignore[attr-defined]
    vocab = svc.stt_vocabulary()
    assert vocab.startswith("LAZOS, Göktürk Doğan.") and "TypeScript" in vocab and "backend" in vocab and "vb." not in vocab
    client.post("/api/pronunciation/rules", json={"term": "Kubernetes", "replacement": "kubernetis"}, headers=auth)
    assert "Kubernetes" in svc.stt_vocabulary()
    client.put("/api/settings", json={"stt_vocabulary": False}, headers=auth)
    assert svc.stt_vocabulary() is None


def test_punctuation_only_transcripts_are_discarded(tmp_path: Path) -> None:
    import numpy as np

    from app.speech.stt import WhisperSTT

    (tmp_path / "weights.safetensors").write_bytes(b"x")
    stt = WhisperSTT(tmp_path)
    prompts: list[Any] = []

    def fake(audio, language, prompt=None):  # type: ignore[no-untyped-def]
        prompts.append(prompt)
        return {"text": " ! ", "language": "tr"}

    stt._transcribe_sync = fake  # type: ignore[method-assign]
    result = asyncio.run(stt.transcribe(np.zeros(16000, np.float32), "tr", prompt="LAZOS, backend."))
    assert result.text == "" and prompts == ["LAZOS, backend."]
