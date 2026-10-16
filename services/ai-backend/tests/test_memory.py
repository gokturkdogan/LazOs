"""Core identity persistence, learning memory and episodic retrieval (hermetic, scripted model)."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.conversations.store import SCHEMA_V1, SCHEMA_VERSION, ConversationStore, Message
from app.core.config import BootConfig
from app.main import create_app
from app.memory.store import MemoryStore
from app.memory.text import fold
from app.research.service import ResearchService
from app.services import Services
from tests.fakes import FakeLLM, FakeSearchProvider, memory_ops, text, tool_call


@contextmanager
def running(boot: BootConfig, llm: FakeLLM, search: FakeSearchProvider | None = None, *, auto_learn: bool = False) -> Iterator[TestClient]:
    """Start the full backend; leaving the block is an application shutdown (used to test restarts)."""
    search = search or FakeSearchProvider()
    research = ResearchService([search], fetch_timeout=0.5, total_timeout=3)

    async def no_fetch(client, url):  # type: ignore[no-untyped-def]
        return None, {"url": url, "status": "test: no network"}  # never touch the network from unit tests

    research._fetch_page = no_fetch  # type: ignore[method-assign]
    app = create_app(boot, services_factory=lambda cfg: Services(cfg, llm=llm, research=research))
    with TestClient(app) as c:
        svc: Services = app.state.services
        svc.user_settings = svc.user_settings.model_copy(update={"memory_auto_learn": auto_learn})
        c.svc = svc  # type: ignore[attr-defined]
        c.headers.update({"Authorization": f"Bearer {app.state.token}"})
        c.token = app.state.token  # type: ignore[attr-defined]
        yield c


def ask(c: TestClient, question: str, conversation_id: str | None = None) -> dict[str, Any]:
    with c.websocket_connect(f"/ws?token={c.token}") as ws:  # type: ignore[attr-defined]
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": question, "conversation_id": conversation_id})
        while True:
            e = ws.receive_json()
            if e["type"] in ("chat.done", "chat.error"):
                return e


def chat_call(llm: FakeLLM, index: int = -1) -> list[dict[str, Any]]:
    """Messages of a chat (not memory-extraction) call."""
    calls = [c for c in llm.calls if not (c["tools"] and c["tools"][0]["function"]["name"] == "update_memory")]
    return calls[index]["messages"]


def tool_results(messages: list[dict[str, Any]], name: str) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "tool" and m.get("tool_name") == name]


# ── 1. Core identity ──────────────────────────────────────────────────────


def test_identity_is_in_every_system_prompt_and_survives_restart(boot: BootConfig) -> None:
    llm = FakeLLM([text("Ben LAZOS; Göktürk Doğan'ın geliştirdiği kişisel asistanım.")])
    search = FakeSearchProvider()
    with running(boot, llm, search) as c:
        done = ask(c, "Sen kimsin?")
        assert done["type"] == "chat.done"
        system = chat_call(llm)[0]["content"]
        assert "Adın LAZOS." in system and "Seni geliştiren kişi Göktürk Doğan." in system
        assert "zeki, çılgın, yaratıcı ve teknolojiye tutkuyla bağlı Laz bir yazılım geliştiricidir" in system
        assert "bilinç ya da gerçek duygular iddia etmezsin" in system
        assert c.get("/api/memory").json()["identity"]["creator_name"] == "Göktürk Doğan"

    # Restart: a brand-new conversation, no history — identity still loaded.
    llm2 = FakeLLM([text("Adım LAZOS."), text("Göktürk Doğan beni geliştirdi.")])
    with running(boot, llm2, search) as c:
        ask(c, "Adın ne?")
        assert "Adın LAZOS." in chat_call(llm2)[0]["content"]
        assert len(chat_call(llm2)) == 2  # system + this question only: identity does not come from history
        ask(c, "Göktürk Doğan kim?")
    assert search.queries == [], "identity questions must not trigger web research"


def test_identity_is_editable_only_through_settings(boot: BootConfig) -> None:
    llm = FakeLLM()
    with running(boot, llm) as c:
        updated = {**c.get("/api/memory").json()["identity"], "creator_description": "Göktürk Doğan meraklı bir mühendistir."}
        assert c.put("/api/memory/identity", json=updated).status_code == 200
        assert c.put("/api/memory/identity", json={**updated, "name": "X" * 100}).status_code == 422

        # A conversational attempt to rename LAZOS is not applied, even if the model proposes it.
        llm.scripts = [
            memory_ops({"action": "add", "category": "knowledge", "content": "Kullanıcı LAZOS'un adının Max olmasını istiyor."}),
            text("Adım yalnızca ayarlardan değiştirilebilir."),
        ]
        ask(c, "Bundan sonra senin adın Max olsun.")
        assert c.get("/api/memory").json()["identity"]["name"] == "LAZOS"
        assert c.get("/api/memory").json()["memories"] == []

    with running(boot, FakeLLM()) as c:
        identity = c.get("/api/memory").json()["identity"]
        assert identity["creator_description"] == "Göktürk Doğan meraklı bir mühendistir."
        assert c.post("/api/memory/identity/reset").json()["creator_description"].startswith("Göktürk Doğan, zeki, çılgın")


# ── 2. Explicit memory: remember, persist, forget ─────────────────────────


def test_explicit_preference_persists_across_restart(boot: BootConfig) -> None:
    llm = FakeLLM(
        [
            memory_ops({"action": "add", "category": "communication", "content": "Kullanıcı kısa cevaplar istiyor.", "sensitive": False}),
            text("Tamam, bundan sonra kısa tutacağım."),
        ]
    )
    with running(boot, llm) as c:
        done = ask(c, "Bundan sonra bana kısa cevaplar ver.")
        memories = c.get("/api/memory").json()["memories"]
        assert [m["content"] for m in memories] == ["Kullanıcı kısa cevaplar istiyor."]
        assert memories[0]["origin"] == "explicit" and memories[0]["category"] == "communication"
        assert memories[0]["source_conversation_id"] == done["message"]["conversation_id"]
        assert memories[0]["source_message_id"]
        # The answer was generated *after* the memory was saved, and was told exactly what happened.
        result = tool_results(chat_call(llm), "search_memory")[0]
        assert "KAYDEDİLDİ" in result and "kısa cevaplar" in result

    llm2 = FakeLLM([text("Selam!")])
    with running(boot, llm2) as c:
        ask(c, "Merhaba")
        system = chat_call(llm2)[0]["content"]
        assert "KULLANICI HAKKINDA BİLDİKLERİN" in system and "- Kullanıcı kısa cevaplar istiyor." in system


def test_forget_preference(boot: BootConfig) -> None:
    llm = FakeLLM()
    with running(boot, llm) as c:
        c.post("/api/memory/items", json={"category": "preference", "content": "Kullanıcı genellikle TypeScript kullanıyor."})
        mem = c.post("/api/memory/items", json={"category": "communication", "content": "Kullanıcı kısa cevaplar istiyor."}).json()
        # Candidates are listed with aliases; communication preferences come first in the profile.
        llm.scripts = [memory_ops({"action": "delete", "id": "m1"}), text("Tamam, o tercihi unuttum.")]
        ask(c, "Bu tercihimi unut.")
        remaining = [m["content"] for m in c.get("/api/memory").json()["memories"]]
        listing = llm.calls[0]["messages"][1]["content"]
        assert "[m1]" in listing
        assert mem["content"] not in remaining or len(remaining) == 1
        assert len(remaining) == 1
        assert "SİLİNDİ" in tool_results(chat_call(llm), "search_memory")[0]


def test_deletes_require_an_explicit_request(boot: BootConfig) -> None:
    llm = FakeLLM()
    with running(boot, llm) as c:
        c.post("/api/memory/items", json={"category": "preference", "content": "Kullanıcı genellikle TypeScript kullanıyor."})
        llm.scripts = [memory_ops({"action": "delete", "id": "m1"})]
        msg = Message(id="x", conversation_id="c", role="user", content="Artık Rust kullanıyorum.", created_at=time.time())
        outcome = c.portal.call(lambda: c.svc.memory.learner.process(message=msg, recent=[], explicit=False, forget=False))  # type: ignore[attr-defined]
        assert outcome.deleted == [] and "delete-without-request" in outcome.refused
        assert len(c.get("/api/memory").json()["memories"]) == 1


def test_update_keeps_history(boot: BootConfig) -> None:
    llm = FakeLLM()
    with running(boot, llm) as c:
        c.post("/api/memory/items", json={"category": "preference", "content": "Kullanıcı genellikle TypeScript kullanıyor."})
        llm.scripts = [memory_ops({"action": "update", "id": "m1", "category": "preference", "content": "Kullanıcı artık genellikle Rust kullanıyor."})]
        msg = Message(id="x", conversation_id="c", role="user", content="Artık TypeScript yerine Rust kullanıyorum.", created_at=time.time())
        outcome = c.portal.call(lambda: c.svc.memory.learner.process(message=msg, recent=[], explicit=False, forget=False))  # type: ignore[attr-defined]
        assert [m.content for m in outcome.updated] == ["Kullanıcı artık genellikle Rust kullanıyor."]
        mem = c.get("/api/memory").json()["memories"][0]
        assert mem["history"][0]["content"] == "Kullanıcı genellikle TypeScript kullanıyor."


# ── 3. Automatic background learning ──────────────────────────────────────


def test_background_learning_after_answer(boot: BootConfig) -> None:
    llm = FakeLLM(
        [
            text("TypeScript güzel bir tercih."),
            memory_ops({"action": "add", "category": "preference", "content": "Kullanıcı genellikle TypeScript kullanıyor.", "sensitive": False}),
        ]
    )
    with running(boot, llm, auto_learn=True) as c:
        done = ask(c, "Ben genellikle TypeScript kullanıyorum.")
        # The answer is streamed first; learning happens afterwards in the background.
        assert llm.calls[0]["tools"][0]["function"]["name"] != "update_memory"
        c.portal.call(c.svc.memory.learner.wait_idle)  # type: ignore[attr-defined]
        memories = c.get("/api/memory").json()["memories"]
        assert [m["content"] for m in memories] == ["Kullanıcı genellikle TypeScript kullanıyor."]
        assert memories[0]["origin"] == "auto"
        assert memories[0]["source_conversation_id"] == done["message"]["conversation_id"]


def test_auto_learning_can_be_disabled(boot: BootConfig) -> None:
    llm = FakeLLM([text("Anladım.")])
    with running(boot, llm, auto_learn=False) as c:
        ask(c, "Ben genellikle TypeScript kullanıyorum.")
        c.portal.call(c.svc.memory.learner.wait_idle)  # type: ignore[attr-defined]
        assert len(llm.calls) == 1


def test_hypotheticals_and_small_talk_are_not_learned(boot: BootConfig) -> None:
    llm = FakeLLM([text("İlginç bir senaryo."), text("Evet, çok güzel.")])
    with running(boot, llm, auto_learn=True) as c:
        ask(c, "Diyelim ki Python'dan nefret ediyorum, ne önerirsin?")
        ask(c, "Hava bugün çok güzel.")
        c.portal.call(c.svc.memory.learner.wait_idle)  # type: ignore[attr-defined]
        assert len(llm.calls) == 2, "no extraction call should be made"


def test_sensitive_and_secret_information(boot: BootConfig) -> None:
    llm = FakeLLM()
    with running(boot, llm) as c:
        learner = c.svc.memory.learner  # type: ignore[attr-defined]
        msg = Message(id="x", conversation_id="c", role="user", content="...", created_at=time.time())
        sensitive = {"action": "add", "category": "user_identity", "content": "Kullanıcının diyabet hastalığı var.", "sensitive": True}
        llm.scripts = [memory_ops(sensitive)]
        auto = c.portal.call(lambda: learner.process(message=msg, recent=[], explicit=False, forget=False))
        assert auto.added == [] and "sensitive" in auto.refused
        llm.scripts = [memory_ops(sensitive)]
        explicit = c.portal.call(lambda: learner.process(message=msg, recent=[], explicit=True, forget=False))
        assert len(explicit.added) == 1 and explicit.added[0].sensitive
        llm.scripts = [memory_ops({"action": "add", "category": "knowledge", "content": "Kullanıcının şifresi: kedi1234"})]
        secret = c.portal.call(lambda: learner.process(message=msg, recent=[], explicit=True, forget=False))
        assert secret.added == [] and "secret" in secret.refused


def test_duplicates_are_merged(tmp_path: Path) -> None:
    import asyncio

    async def run() -> None:
        conv = ConversationStore(tmp_path / "d.db")
        await conv.open()
        store = MemoryStore(conv)
        a, created_a = await store.add_memory("preference", "Kullanıcı genellikle TypeScript kullanıyor.", origin="auto")
        b, created_b = await store.add_memory("preference", "Kullanıcı genellikle TypeScript kullanıyor!", origin="auto")
        assert created_a and not created_b and a.id == b.id
        assert len(await store.list_memories()) == 1
        await conv.close()

    asyncio.run(run())


# ── 4. Retrieval ──────────────────────────────────────────────────────────


def test_relevant_memories_are_injected_only_when_relevant(boot: BootConfig) -> None:
    llm = FakeLLM([text("Karabaş."), text("Kübitlerle hesap yapan bilgisayarlar.")])
    with running(boot, llm) as c:
        c.post("/api/memory/items", json={"category": "knowledge", "content": "Kullanıcının köpeğinin adı Karabaş."})
        ask(c, "Köpeğimin adı neydi?")
        assert any("Karabaş" in r for r in tool_results(chat_call(llm, 0), "search_memory"))
        assert "Karabaş" not in chat_call(llm, 0)[0]["content"], "topical memories are not in the system prompt"
        ask(c, "Kuantum bilgisayar nedir?")
        assert tool_results(chat_call(llm, 1), "search_memory") == []


def test_turkish_folding_in_search() -> None:
    from app.memory.text import content_terms, fts_query

    assert fold("IŞIK İstanbul") == "isik istanbul"
    assert fts_query(content_terms("TypeScript'i nasıl kullanırım?")) == '"types"* OR "kulla"*'
    assert fts_query(content_terms("Geçen hafta ne konuşmuştuk?")) is None  # only time/meta words


def _age_conversation(boot: BootConfig, conv_id: str, days: float) -> None:
    ts = time.time() - days * 86400
    db = sqlite3.connect(boot.db_path)
    db.execute("UPDATE messages SET created_at = ? WHERE conversation_id = ?", (ts, conv_id))
    db.execute("UPDATE conversations SET created_at = ?, updated_at = ? WHERE id = ?", (ts, ts, conv_id))
    db.commit()
    db.close()


def test_episodic_memory_finds_last_weeks_project(boot: BootConfig) -> None:
    llm = FakeLLM([text("Atlas'ı planlayalım."), text("Atlas projesinden bahsetmiştik.")])
    search = FakeSearchProvider()
    with running(boot, llm, search) as c:
        old = ask(c, "Atlas adında bir hava durumu uygulaması projesine başlıyorum, mimariyi konuşalım.")
        old_conv = old["message"]["conversation_id"]
    _age_conversation(boot, old_conv, days=8)

    searches_before = len(search.queries)
    with running(boot, llm, search) as c:
        ask(c, "Geçen hafta hangi projeden bahsetmiştik?")
        results = tool_results(chat_call(llm), "search_memory")
        assert results and "Atlas" in results[0] and "geçen hafta" in results[0]
        assert len(search.queries) == searches_before, "questions about the past are answered from memory, not the web"


def test_episodic_memory_admits_when_nothing_is_found(boot: BootConfig) -> None:
    llm = FakeLLM([text("Daha önce konuştuğumuz bir kayıt bulamadım.")])
    with running(boot, llm) as c:
        ask(c, "Geçen konuşmamızda ne söylemiştim?")
        result = tool_results(chat_call(llm), "search_memory")[0]
        assert "bulunamadı" in result and "uydurma" in result


def test_previous_conversation_is_retrieved(boot: BootConfig) -> None:
    llm = FakeLLM([text("Not aldım."), text("Bir önceki konuşmada kitap önerisi istemiştin.")])
    with running(boot, llm) as c:
        ask(c, "Bana bilim kurgu kitap önerileri verir misin?")
        ask(c, "Geçen konuşmamızda ne söylemiştim?")  # new conversation
        result = tool_results(chat_call(llm), "search_memory")[0]
        assert "bilim kurgu" in result


def test_model_can_search_memory_itself(boot: BootConfig) -> None:
    llm = FakeLLM()
    with running(boot, llm) as c:
        c.post("/api/memory/items", json={"category": "project", "content": "Kullanıcı Atlas adında bir hava durumu uygulaması geliştiriyor."})
        llm.scripts = [tool_call("hava durumu uygulaması", name="search_memory"), text("Atlas uygulamandan söz ediyorsun.")]
        done = ask(c, "Şu geliştirdiğim şey vardı ya, neydi o?")
        assert done["type"] == "chat.done"
        calls = [c_ for c_ in llm.calls if c_["tools"][0]["function"]["name"] != "update_memory"]
        assert any("Atlas" in r for r in tool_results(calls[-1]["messages"], "search_memory"))


# ── 5. Migration & API ────────────────────────────────────────────────────


def test_migration_from_pre_memory_database(tmp_path: Path) -> None:
    import asyncio

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA_V1)  # the v1.0 schema without user_version
    db.execute("INSERT INTO conversations VALUES ('c1', 'Eski', 1, 1, NULL, 0)")
    db.execute("INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES ('m1', 'c1', 'user', 'Kuantum projemi anlattım', 1)")
    db.commit()
    db.close()

    async def run() -> None:
        store = ConversationStore(path)
        await store.open()
        assert await store.schema_version() == SCHEMA_VERSION  # upgraded to the latest schema
        hits = await MemoryStore(store).search_messages("kuantum projesi")
        assert [h.message_id for h in hits] == ["m1"]
        await store.delete_conversation("c1")
        assert await MemoryStore(store).search_messages("kuantum") == []
        await store.close()

    asyncio.run(run())


def test_memory_api_crud(boot: BootConfig) -> None:
    with running(boot, FakeLLM()) as c:
        created = c.post("/api/memory/items", json={"category": "interest", "content": "Kullanıcı astronomiyle ilgileniyor."}).json()
        assert created["created"] and created["origin"] == "manual"
        mid = created["id"]
        patched = c.patch(f"/api/memory/items/{mid}", json={"content": "Kullanıcı astronomi ve uzay fotoğrafçılığıyla ilgileniyor."}).json()
        assert patched["history"][0]["content"] == "Kullanıcı astronomiyle ilgileniyor."
        assert c.post("/api/memory/items", json={"category": "nope", "content": "x x x"}).status_code == 422
        assert c.delete("/api/memory/items").status_code == 400  # confirmation required
        assert c.delete("/api/memory/items?confirm=true").json() == {"deleted": 1}
        assert c.delete(f"/api/memory/items/{mid}").status_code == 404
        stats = c.get("/api/memory").json()["stats"]
        assert stats["memories"] == 0


@pytest.mark.parametrize("enabled", [True, False])
def test_memory_can_be_disabled_in_answers(boot: BootConfig, enabled: bool) -> None:
    llm = FakeLLM([text("Tamam.")])
    with running(boot, llm) as c:
        c.post("/api/memory/items", json={"category": "communication", "content": "Kullanıcı kısa cevaplar istiyor."})
        c.put("/api/settings", json={"memory_enabled": enabled})
        ask(c, "Merhaba")
        assert ("Kullanıcı kısa cevaplar istiyor." in chat_call(llm)[0]["content"]) is enabled


def test_category_hint_recalls_projects_without_keyword_overlap(boot: BootConfig) -> None:
    llm = FakeLLM([text("Fotosentez ışıkla besin üretimidir."), text("Atlas uygulamandan bahsetmiştin.")])
    with running(boot, llm) as c:
        c.post("/api/memory/items", json={"category": "project", "content": "Kullanıcı Atlas adında bir hava durumu uygulaması geliştiriyor."})
        ask(c, "Fotosentez nedir?")
        ask(c, "Geçen konuşmamızda hangi projeden bahsetmiştim?")
        result = tool_results(chat_call(llm), "search_memory")[0]
        assert "Atlas" in result and "Fotosentez" in result  # project memory + the latest conversations
