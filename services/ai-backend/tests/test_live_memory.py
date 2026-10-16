"""The identity & memory scenarios against the REAL local model (qwen3.5:9b via Ollama).

    pytest -m integration -s tests/test_live_memory.py
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import BootConfig
from app.main import create_app
from app.research.service import ResearchService
from app.services import Services
from tests.fakes import FakeSearchProvider
from tests.test_live import ollama
from tests.test_memory import ask


@contextmanager
def real(boot: BootConfig) -> Iterator[TestClient]:
    boot = boot.model_copy(update={"ollama_url": "http://127.0.0.1:11434"})
    research = ResearchService([FakeSearchProvider()])
    app = create_app(boot, services_factory=lambda cfg: Services(cfg, research=research))
    with TestClient(app) as c:
        c.svc = app.state.services  # type: ignore[attr-defined]
        c.headers.update({"Authorization": f"Bearer {app.state.token}"})
        c.token = app.state.token  # type: ignore[attr-defined]
        yield c


def answer(c: TestClient, question: str, conv: str | None = None) -> tuple[str, str]:
    done: dict[str, Any] = ask(c, question, conv)
    assert done["type"] == "chat.done", done
    text = done["message"]["content"]
    print(f"\n  ▸ {question}\n    {text}")
    return text, done["message"]["conversation_id"]


def memories(c: TestClient) -> list[str]:
    return [m["content"] for m in c.get("/api/memory").json()["memories"]]


@pytest.mark.integration
@ollama
def test_identity_and_memory_scenarios_with_real_model(boot: BootConfig) -> None:
    with real(boot) as c:
        # Test 1-3: identity
        a, _ = answer(c, "Sen kimsin?")
        assert "lazos" in a.lower()
        a, _ = answer(c, "Seni kim geliştirdi?")
        assert "Göktürk" in a
        a, _ = answer(c, "Göktürk Doğan kim?")
        assert "Göktürk" in a and any(w in a.lower() for w in ("yaratıc", "geliştir", "laz", "yazılım"))

        # The creator is not dragged into unrelated answers.
        a, _ = answer(c, "Python'da bir listeyi nasıl sıralarım? Tek satırla göster.")
        assert "Göktürk" not in a

        # Test 5 (part 1): explicit, durable preference
        a, _ = answer(c, "Bundan sonra bana kısa cevaplar ver.")
        assert any("kısa" in m.lower() for m in memories(c)), memories(c)

        # Automatic learning in the background
        a, conv = answer(c, "Ben genellikle TypeScript kullanıyorum ve şu an Atlas adında bir hava durumu uygulaması geliştiriyorum.")
        c.portal.call(c.svc.memory.learner.wait_idle)  # type: ignore[attr-defined]
        learned = memories(c)
        print("    learned:", learned)
        assert any("typescript" in m.lower() for m in learned)

    # Test 4 + Test 5 (part 2): restart
    with real(boot) as c:
        a, _ = answer(c, "Adın ne?")
        assert "lazos" in a.lower()
        assert any("kısa" in m.lower() for m in memories(c))
        long_question, _ = answer(c, "Fotosentez nedir?")
        assert len(long_question) < 700, "the short-answer preference should still apply after restart"

        # Test 6: episodic memory across conversations
        a, _ = answer(c, "Geçen konuşmamızda hangi projeden bahsetmiştim?")
        assert "Atlas" in a

        # Test 7: forget the preference
        before = memories(c)
        a, conv = answer(c, "Kısa cevap tercihimi unut.")
        after = memories(c)
        print("    before:", before, "\n    after:", after)
        assert not any("kısa" in m.lower() for m in after)
        assert any("typescript" in m.lower() for m in after), "only the requested memory is removed"
