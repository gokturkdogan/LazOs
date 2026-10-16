"""Unit tests: routing, state machine, context management, speech text, VAD segmentation, safety."""

from __future__ import annotations

import asyncio
from datetime import datetime

import numpy as np
import pytest

from app.conversations.store import Message
from app.core.events import EventHub
from app.core.state import AIState, InvalidTransition, StateMachine
from app.inference.context import build_context, stamp
from app.research.fetch import UnsafeURL, assert_public_url, select_passages
from app.research.router import build_search_query, route
from app.research.service import parse_tool_args
from app.speech.text import SentenceSplitter, to_speakable
from app.speech.vad import FRAME, SegmenterConfig, UtteranceSegmenter

# ── routing ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("LAZOS, nasılsın?", "none"),
        ("Merhaba!", "none"),
        ("teşekkürler", "none"),
        ("12 * 7 kaç eder?", "none"),
        ("Bana kuantum bilgisayarları kısaca anlat.", "none"),
        ("Python'da liste nasıl ters çevrilir, açıkla", "none"),
        ("LAZOS, bugün yapay zeka dünyasında neler oldu?", "search"),
        ("Dolar kuru şu an ne kadar?", "search"),
        ("İnternette ara: en iyi mekanik klavyeler", "search"),
        ("What's the latest news about Apple?", "search"),
        ("Yarın İstanbul'da hava nasıl olacak?", "search"),
        ("Galatasaray maçı kim kazandı?", "search"),
        ("Qwen 3.5 modelini değerlendirir misin?", "auto"),
    ],
)
def test_route(text: str, action: str) -> None:
    assert route(text).action == action


def test_route_modes() -> None:
    assert route("Merhaba", "always").action == "search"
    assert route("Bugün haberler neler?", "off").action == "none"


def test_build_search_query() -> None:
    now = datetime(2026, 10, 16, 15, 0)
    assert build_search_query("LAZOS, bugün yapay zeka dünyasında neler oldu?", now) == "yapay zeka dünyasında neler oldu 16 Ekim 2026"
    assert build_search_query("İnternette ara: Galatasaray son maç skoru", now) == "Galatasaray son maç skoru 2026"
    assert build_search_query("Qwen 3.5 ne zaman çıktı?", now) == "Qwen 3.5 ne zaman çıktı"


def test_tool_args_validation() -> None:
    assert parse_tool_args({"query": "  dolar kuru  "}).query == "dolar kuru"  # type: ignore[union-attr]
    assert parse_tool_args({"query": ""}) is None
    assert parse_tool_args({"q": "x"}) is None
    assert parse_tool_args({"query": "x" * 500}) is None


# ── state machine ─────────────────────────────────────────────────────────


def test_state_transitions_publish_events() -> None:
    hub = EventHub()
    sm = StateMachine(hub)
    q = hub.subscribe()  # receives sticky current state
    assert q.get_nowait() == {"type": "state", "state": "idle", "detail": None}
    for dst in (AIState.LISTENING, AIState.TRANSCRIBING, AIState.THINKING, AIState.SPEAKING, AIState.LISTENING, AIState.IDLE):
        sm.set(dst)
        assert q.get_nowait()["state"] == dst.value


def test_invalid_transition_rejected() -> None:
    sm = StateMachine(EventHub())
    with pytest.raises(InvalidTransition):
        sm.set(AIState.TRANSCRIBING)  # idle → transcribing is not a valid path
    sm.set(AIState.TRANSCRIBING, force=True)
    assert sm.state == AIState.TRANSCRIBING


def test_error_state_recovers() -> None:
    sm = StateMachine(EventHub())
    sm.set(AIState.THINKING)
    sm.set(AIState.ERROR)
    sm.set(AIState.IDLE)
    assert sm.state == AIState.IDLE


def test_event_hub_drops_lossy_events_when_full() -> None:
    hub = EventHub(queue_size=2)
    q = hub.subscribe()
    for _ in range(5):
        hub.publish({"type": "audio.level", "level": 0.5})
    hub.publish({"type": "chat.delta", "text": "x"})
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert items[-1]["type"] == "chat.delta"


# ── context management ────────────────────────────────────────────────────


def _msg(i: int, role: str, content: str) -> Message:
    return Message(id=str(i), conversation_id="c", role=role, content=content, created_at=1_760_000_000 + i, seq=i)


def test_context_keeps_recent_messages_within_budget() -> None:
    history = [_msg(i, "user" if i % 2 == 0 else "assistant", "x" * 2000) for i in range(20)]
    plan = build_context(system_prompt="sys", summary=None, history=history, user_text="son soru", num_ctx=4096, reserve_for_answer=1024)
    assert plan.messages[0]["role"] == "system"
    assert plan.messages[-1] == {"role": "user", "content": "son soru"}
    kept = plan.messages[1:-1]
    assert 0 < len(kept) < 20
    assert kept[0]["role"] == "user", "window must not start with an orphaned assistant reply"
    assert plan.estimated_tokens <= 4096 - 1024
    assert plan.dropped and plan.dropped[0].seq == 0


def test_context_includes_summary_and_stamps_history() -> None:
    history = [_msg(1, "user", "Benim adım Ada."), _msg(2, "assistant", "Memnun oldum Ada.")]
    plan = build_context(system_prompt="sys", summary="Kullanıcı kahve sever.", history=history, user_text="?", num_ctx=8192, reserve_for_answer=1024)
    assert "Kullanıcı kahve sever." in plan.messages[0]["content"]
    first_user = plan.messages[1]["content"]
    assert first_user.startswith("[Şu an: ") and first_user.endswith("Benim adım Ada.")
    # Stamps are deterministic, so the rendered history is identical across requests (prompt cache).
    again = build_context(system_prompt="sys", summary="Kullanıcı kahve sever.", history=history, user_text="?", num_ctx=8192, reserve_for_answer=1024)
    assert again.messages[:-1] == plan.messages[:-1]
    assert stamp("a", datetime(2026, 10, 16, 9, 5)) == "[Şu an: 16 Ekim 2026, Cuma, saat 09:05]\na"


# ── speech text ───────────────────────────────────────────────────────────


def test_to_speakable_strips_markdown_and_citations() -> None:
    md = "## Başlık\n**Kuantum** bilgisayarlar [1][2] `qubit` kullanır. Bkz. https://example.com 🚀\n```python\nprint(1)\n```"
    out = to_speakable(md)
    assert "Kuantum bilgisayarlar qubit kullanır." in out
    for bad in ("**", "[1]", "https://", "print(1)", "##", "🚀"):
        assert bad not in out
    assert to_speakable("LAZOS hazır") == "Lazos hazır"
    assert "yüzde" in to_speakable("%50 indirim")


def test_sentence_splitter_streams_sentences() -> None:
    sp = SentenceSplitter()
    out: list[str] = []
    for chunk in ["Gayet iyi", "yim. Sistemler ", "hazır! Bugün ne ", "üzerine konuşmak istersin?", " Son"]:
        out += sp.feed(chunk)
    out += sp.flush()
    assert out[0] == "Gayet iyiyim. Sistemler hazır!" or out[0] == "Gayet iyiyim."
    assert "".join(out).replace(" ", "") == "Gayetiyiyim.Sistemlerhazır!Bugünneüzerinekonuşmakistersin?Son"


def test_sentence_splitter_keeps_decimals_and_skips_code() -> None:
    sp = SentenceSplitter(first_min_chars=5, min_chars=5)
    out = sp.feed("Sürüm 3.5 çıktı ve çok hızlı. Örnek:\n```python\nx = 1. \n```\nBitti burada. ")
    out += sp.flush()
    joined = " ".join(out)
    assert "3.5" in joined and "x = 1" not in joined and "Bitti burada." in joined


# ── VAD segmentation ──────────────────────────────────────────────────────


class ScriptedVAD:
    def __init__(self, probs: list[float]) -> None:
        self.probs = probs
        self.i = 0

    def __call__(self, frame: np.ndarray) -> float:
        p = self.probs[self.i] if self.i < len(self.probs) else 0.0
        self.i += 1
        return p

    def reset(self) -> None:
        pass


def test_segmenter_detects_utterance() -> None:
    frames_speech = 30  # ~1 s
    probs = [0.05] * 10 + [0.9] * frames_speech + [0.05] * 40
    seg = UtteranceSegmenter(ScriptedVAD(probs), SegmenterConfig(silence_ms=500, pre_roll_ms=200))
    events = [e for e in (seg.feed(np.zeros(FRAME, np.float32)) for _ in probs) if e]
    assert [e.kind for e in events] == ["start", "end"]
    audio = events[1].audio
    assert audio is not None
    assert len(audio) >= frames_speech * FRAME  # includes pre-roll


def test_segmenter_ignores_short_noise() -> None:
    probs = [0.9] * 3 + [0.0] * 40
    seg = UtteranceSegmenter(ScriptedVAD(probs), SegmenterConfig(silence_ms=300, min_speech_ms=250))
    events = [e for e in (seg.feed(np.zeros(FRAME, np.float32)) for _ in probs) if e]
    assert events[-1].kind == "end" and events[-1].audio is None


def test_segmenter_force_end() -> None:
    seg = UtteranceSegmenter(ScriptedVAD([0.9] * 20), SegmenterConfig())
    for _ in range(20):
        seg.feed(np.zeros(FRAME, np.float32))
    audio = seg.force_end()
    assert audio is not None and not seg.active


# ── web safety ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "http://localhost:47811/api", "http://192.168.1.1/", "file:///etc/passwd", "ftp://x.org"])
def test_private_urls_are_blocked(url: str) -> None:
    with pytest.raises(UnsafeURL):
        asyncio.run(assert_public_url(url))


def test_select_passages_prefers_relevant_text() -> None:
    text = "\n".join(
        [
            "Bu paragraf tamamen alakasız bir menü ve çerez bildirimi içeriyor, hiçbir bilgi yok.",
            "Merkez bankası bugün dolar kuru için yeni bir açıklama yaptı ve piyasalar tepki verdi.",
            "Başka bir alakasız paragraf: abonelik, reklam ve sosyal medya bağlantıları burada.",
        ]
    )
    out = select_passages(text, "dolar kuru açıklama", max_chars=120)
    assert "dolar kuru" in out
