"""MemoryService: the single entry point the chat pipeline uses for identity and memory.

Per turn:
  1. The system prompt always carries the Core Identity and the user's profile memories
     (identity, communication and personal preferences). It only changes when they change, so
     Ollama's prompt cache stays warm.
  2. Explicit "remember"/"forget" requests are applied *before* answering, so LAZOS can confirm
     exactly what happened.
  3. Relevant topical memories and, for questions about the past, matching previous conversations
     are retrieved locally (FTS5, milliseconds) and injected as a `search_memory` tool result.
  4. After answering, durable-looking statements are queued for background learning.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.conversations.store import ConversationStore, Message
from app.core.config import UserSettings
from app.core.events import EventHub
from app.inference.base import LLMBackend
from app.inference.prompts import build_system_prompt, turkish_datetime
from app.memory.identity import CoreIdentity
from app.memory.intent import (
    asks_about_own_things,
    is_episodic,
    is_forget,
    is_identity_fact,
    is_learnable,
    is_remember,
    time_window,
    wants_latest_conversation,
)
from app.memory.learner import MemoryLearner, MemoryOutcome, run_with_timeout
from app.memory.store import PROFILE_CATEGORIES, Memory, MemoryStore
from app.memory.text import distinctive_overlap, fold

log = logging.getLogger("lazos.memory")

PROFILE_LIMIT = 24
PROFILE_CHARS = 1600

SEARCH_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": (
            "Search LAZOS's long-term memory: facts the user shared earlier and previous conversations. Use it when the "
            "user refers to the past ('geçen sefer', 'daha önce konuşmuştuk', 'hatırlıyor musun') or asks what you know "
            "about them. Never invent past conversations."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look for (topic, project, name, preference)."}},
            "required": ["query"],
        },
    },
}

_IDENTITY_Q = re.compile(
    r"\b(sen kimsin|kimsin|adin ne|ismin ne|seni kim|yaraticin|kim (yapti|yaratti|gelistirdi|olusturdu|programladi)|"
    r"neden (gelistirildin|yapildin|yaratildin)|ne ise yar|amacin ne|kendini tanit|"
    r"who (are you|made you|created you|built you)|your (name|creator))"
)


# Words in a question that point at a memory category ("hangi projeden…" → projects).
_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "proje": ("project",),
    "uygul": ("project",),
    "proj": ("project",),
    "tercih": ("preference", "communication"),
    "ilgi": ("interest",),
    "hobi": ("interest",),
    "karar": ("decision",),
    "project": ("project",),
    "hobby": ("interest",),
    "decision": ("decision",),
}


def hinted_categories(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for tok in fold(text).split():
        for prefix, cats in _CATEGORY_HINTS.items():
            if tok.startswith(prefix):
                found += [c for c in cats if c not in found]
    return tuple(found)


@dataclass
class TurnMemory:
    tool_content: str | None = None
    outcome: MemoryOutcome | None = None
    used: list[Memory] = field(default_factory=list)

    @property
    def handled_explicitly(self) -> bool:
        return self.outcome is not None


def _date(ts: float) -> str:
    return turkish_datetime(datetime.fromtimestamp(ts).astimezone()).rsplit(",", 1)[0]


class MemoryService:
    def __init__(
        self,
        *,
        store: MemoryStore,
        conversations: ConversationStore,
        llm: LLMBackend,
        hub: EventHub,
        settings: Callable[[], UserSettings],
        is_busy: Callable[[], bool],
        after_background: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self.hub = hub
        self.settings = settings
        self.identity = CoreIdentity()
        self.profile: list[Memory] = []
        self.learner = MemoryLearner(
            store=store,
            conversations=conversations,
            llm=llm,
            hub=hub,
            settings=settings,
            is_busy=is_busy,
            on_change=self._schedule_profile_refresh,
            after_background=after_background,
        )

    async def start(self) -> None:
        self.identity = await self.store.get_identity()
        await self.refresh_profile()
        self.learner.start()

    async def stop(self) -> None:
        await self.learner.stop()

    # ── identity & profile ─────────────────────────────────────────────────
    async def set_identity(self, identity: CoreIdentity) -> CoreIdentity:
        self.identity = await self.store.set_identity(identity)
        self.hub.publish({"type": "identity.changed", "identity": self.identity.model_dump()})
        return self.identity

    async def refresh_profile(self) -> None:
        self.profile = (await self.store.list_memories(PROFILE_CATEGORIES))[:PROFILE_LIMIT]

    def _schedule_profile_refresh(self) -> None:
        asyncio.get_running_loop().create_task(self.refresh_profile())

    def profile_lines(self) -> list[str]:
        if not self.settings().memory_enabled:
            return []
        lines, total = [], 0
        # Communication preferences first: they shape every answer.
        ordered = sorted(self.profile, key=lambda m: PROFILE_CATEGORIES.index(m.category))
        for mem in ordered:
            if is_identity_fact(mem.content):
                continue  # identity comes from the Core Identity, never from learned memories
            if total + len(mem.content) > PROFILE_CHARS:
                break
            lines.append(mem.content)
            total += len(mem.content)
        return lines

    def system_prompt(self, *, has_tools: bool, voice: bool = False) -> str:
        s = self.settings()
        return build_system_prompt(
            has_tools=has_tools,
            identity=self.identity,
            profile=self.profile_lines(),
            llm_model=s.llm_model,
            conversation_style=s.conversation_style,
            voice_style=s.voice_style,
        )

    def is_identity_question(self, text: str) -> bool:
        folded = fold(text)
        return bool(_IDENTITY_Q.search(folded)) or fold(self.identity.creator_name) in folded

    # ── per-turn ───────────────────────────────────────────────────────────
    async def prepare_turn(self, *, text: str, conversation_id: str, message: Message, recent: list[Message]) -> TurnMemory:
        turn = TurnMemory()
        settings = self.settings()
        remember, forget = is_remember(text), is_forget(text)
        if remember or forget:
            turn.outcome = await run_with_timeout(
                self.learner.process(message=message, recent=recent, explicit=remember or forget, forget=forget), timeout=15.0
            )
            if turn.outcome.changed:
                await self.refresh_profile()

        sections: list[str] = []
        if settings.memory_enabled:
            turn.used = await self._relevant(text, exclude=PROFILE_CATEGORIES)
            if turn.used:
                sections.append("İlgili hafıza notları:\n" + "\n".join(f"- {m.content}" for m in turn.used))
                await self.store.mark_used([m.id for m in turn.used])
            if is_episodic(text):
                sections.append(await self.episodic_context(text, conversation_id))
        if turn.outcome is not None:
            sections.append(
                f"Hafıza işlemi tamamlandı: {turn.outcome.summary()}.\nKullanıcıya bunu tek kısa ve doğal cümleyle onayla "
                "(örneğin \"Tamam.\" ya da \"Unuttum.\"); hafıza veya kayıt ayrıntısına girme."
            )
        if sections:
            turn.tool_content = (
                "Uzun süreli hafıza sonuçları. Geçmiş hakkında YALNIZCA bunlara dayan; burada olmayan bir konuşmayı veya "
                "bilgiyi asla uydurma.\n\n" + "\n\n".join(sections)
            )
        return turn

    async def search_tool(self, query: str, conversation_id: str | None) -> str:
        """Result for a model-initiated `search_memory` call."""
        if not self.settings().memory_enabled:
            return "Hafıza kullanımı ayarlardan kapatılmış."
        memories = await self._relevant(query, exclude=(), limit=6)
        sections = []
        if memories:
            sections.append("Hafıza notları:\n" + "\n".join(f"- {m.content}" for m in memories))
        sections.append(await self.episodic_context(query, conversation_id, fallback_recent=False))
        return "Hafıza arama sonuçları (yalnızca bunlara dayan, uydurma):\n\n" + "\n\n".join(sections)

    async def _relevant(self, text: str, *, exclude: tuple[str, ...], limit: int = 4) -> list[Memory]:
        found = [
            m
            for m in await self.store.search_memories(text, limit=limit * 2, exclude_categories=exclude)
            if distinctive_overlap(text, m.content)
        ][:limit]
        # Category hints only for questions about the user's own things or the past — otherwise a message like
        # "Bir futbol uygulaması." would drag unrelated project memories into the answer.
        use_hints = asks_about_own_things(text) or is_episodic(text)
        hinted = [c for c in hinted_categories(text) if c not in exclude] if use_hints else []
        if hinted:
            for mem in (await self.store.list_memories(tuple(hinted)))[:6]:
                if all(m.id != mem.id for m in found):
                    found.append(mem)
        return found

    async def episodic_context(self, text: str, conversation_id: str | None, *, fallback_recent: bool = True) -> str:
        window = time_window(text)
        since, until = (window.since, window.until) if window else (None, None)
        scope = f" ({window.label})" if window else ""
        hits = await self.store.search_messages(
            text, since=since, until=until, exclude_conversation=conversation_id, limit=6
        )
        if hits:
            blocks = []
            for hit in hits:
                who = "Kullanıcı" if hit.role == "user" else "LAZOS"
                block = f"[{_date(hit.created_at)} · “{hit.conversation_title}”]\n{who}: {hit.content[:400]}"
                if hit.role == "user":
                    for nxt in await self.store.neighbours(hit.conversation_id, hit.seq):
                        block += f"\nLAZOS: {nxt['content'][:300]}"
                blocks.append(block)
            return f"Geçmiş konuşmalardan bulunanlar{scope}:\n\n" + "\n\n".join(blocks)

        if window or wants_latest_conversation(text) or fallback_recent:
            convs = await self.store.recent_conversations(
                since=since, until=until, exclude_conversation=conversation_id, limit=3
            )
            if convs:
                blocks = []
                for conv in convs:
                    lines = [f"[{_date(conv['updated_at'])} · “{conv['title']}”]"]
                    if conv.get("summary"):
                        lines.append(f"Özet: {conv['summary']}")
                    for m in conv["messages"]:
                        who = "Kullanıcı" if m["role"] == "user" else "LAZOS"
                        lines.append(f"{who}: {m['content'][:300]}")
                    blocks.append("\n".join(lines))
                return f"İlgili zaman aralığındaki son konuşmalar{scope}:\n\n" + "\n\n".join(blocks)
        return (
            f"Önceki konuşmalarda{scope} bununla ilgili bir kayıt bulunamadı. Bu konuşmadaki mesajlar zaten önünde; cevap "
            "oradaysa onu kullan. Değilse hatırlamadığını dürüstçe söyle; tahmin yürütme."
        )

    def after_turn(self, *, conversation_id: str, message: Message, handled: bool) -> None:
        settings = self.settings()
        if handled or not settings.memory_auto_learn:
            return
        if is_learnable(message.content):
            self.learner.enqueue(conversation_id, message.id)

    def on_turn_start(self) -> None:
        self.learner.pause()
