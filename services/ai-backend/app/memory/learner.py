"""Learning: the local Qwen model proposes memory operations; deterministic rules decide what is applied.

The model is asked to call an `update_memory` tool (Ollama's MLX runner has no structured-output
mode, but native tool calls are parsed reliably). Every proposed operation is then validated:
deletes only on an explicit "forget" request, sensitive facts only with explicit permission, never
secrets, never facts about LAZOS's own identity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.conversations.store import ConversationStore, Message
from app.core.config import UserSettings
from app.core.events import EventHub
from app.inference.base import GenerationOptions, LLMBackend, LLMError
from app.memory.intent import contains_secret, is_hypothetical, is_identity_fact, is_sensitive
from app.memory.store import CATEGORIES, PROFILE_CATEGORIES, Memory, MemoryStore
from app.memory.text import fold

log = logging.getLogger("lazos.memory")

IDLE_DELAY_S = 1.5

UPDATE_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "update_memory",
        "description": "Apply operations to LAZOS's long-term memory about the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "description": "Memory operations; an empty array when there is nothing durable to store.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["add", "update", "delete"]},
                            "id": {"type": "string", "description": "existing memory id for update/delete"},
                            "category": {"type": "string", "enum": list(CATEGORIES)},
                            "content": {"type": "string"},
                            "sensitive": {"type": "boolean"},
                        },
                        "required": ["action"],
                    },
                }
            },
            "required": ["operations"],
        },
    },
}

EXTRACTION_PROMPT = """Sen LAZOS'un hafıza yöneticisisin. Görevin, kullanıcının SON MESAJINDAN uzun vadede faydalı, kalıcı \
bilgileri belirleyip update_memory aracını tam olarak bir kez çağırmak.

Kategoriler:
- user_identity: kullanıcının adı, mesleği, yaşadığı yer gibi kimlik bilgileri
- preference: genel tercihleri ve alışkanlıkları (kullandığı araçlar, sevdiği/sevmediği şeyler)
- communication: LAZOS'un onunla nasıl konuşmasını istediği (cevap uzunluğu, hitap, dil, üslup)
- project: üzerinde çalıştığı projeler
- interest: ilgi alanları ve hobileri
- decision: verdiği önemli kararlar
- knowledge: hatırlanması istenen diğer kalıcı bilgiler (tarihler, isimler, olgular)

Kurallar:
1. Her kayıt kısa, üçüncü şahıs, Türkçe tek bir cümle olsun ve "Kullanıcı" ile başlasın.
2. Yalnızca kullanıcının KENDİSİ hakkında söylediği kalıcı bilgileri kaydet. Soruları, varsayımsal ya da hayali \
ifadeleri ("diyelim ki", "mesela"), şakaları, geçici durumları (yorgunum, açım), genel bilgileri ve LAZOS'un \
söylediklerini kaydetme.
3. LAZOS'un kendi kimliği (adı, yaratıcısı, amacı) hafızaya ait değildir; asla kaydetme.
4. Mevcut hafızada aynı bilgi varsa ekleme. Bilgi değiştiyse ilgili kaydı action="update", id ve yeni content ile güncelle.
5. "delete" yalnızca kullanıcı açıkça unutmanı veya silmeni istediğinde kullanılır. "Bu", "o tercihim" gibi \
ifadelerde konuşmanın önceki kısmına bakarak doğru kaydı seç.
6. Sağlık, din, siyasi görüş, cinsel yönelim, finansal durum gibi hassas bilgileri sensitive=true işaretle.
7. Kalıcı bilgi yoksa operations boş dizi olsun.

Örnekler:
KULLANICI: Ben genellikle TypeScript kullanıyorum.
→ {"operations": [{"action": "add", "category": "preference", "content": "Kullanıcı genellikle TypeScript kullanıyor.", "sensitive": false}]}
KULLANICI: Bundan sonra bana kısa cevaplar ver.
→ {"operations": [{"action": "add", "category": "communication", "content": "Kullanıcı kısa cevaplar istiyor.", "sensitive": false}]}
KULLANICI: Diyelim ki Python'dan nefret ediyorum, ne önerirsin?
→ {"operations": []}
Mevcut: [m1] (preference) Kullanıcı genellikle TypeScript kullanıyor.
KULLANICI: Artık TypeScript yerine Rust kullanıyorum.
→ {"operations": [{"action": "update", "id": "m1", "category": "preference", "content": "Kullanıcı artık genellikle Rust kullanıyor."}]}
Mevcut: [m2] (communication) Kullanıcı kısa cevaplar istiyor.
KULLANICI: Bu tercihimi unut.
→ {"operations": [{"action": "delete", "id": "m2"}]}"""

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


_TEMPORARY = re.compile(r"\b(su anda|su an|simdi|bugun|bu aksam|bu sabah|su sira|currently|right now|today)\b")


class MemoryOp(BaseModel):
    action: Literal["add", "update", "delete"]
    id: str | None = None
    category: str | None = None
    content: str | None = Field(default=None, max_length=400)
    sensitive: bool = False


@dataclass
class MemoryOutcome:
    added: list[Memory] = field(default_factory=list)
    updated: list[Memory] = field(default_factory=list)
    deleted: list[Memory] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)  # reasons for operations that were not applied

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.deleted)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append("Hafızaya KAYDEDİLDİ: " + "; ".join(f"“{m.content}”" for m in self.added))
        if self.updated:
            parts.append("Hafızada GÜNCELLENDİ: " + "; ".join(f"“{m.content}”" for m in self.updated))
        if self.deleted:
            parts.append(
                "Hafızadan SİLİNDİ (bu bilgi artık geçerli değil; yanıtında bunu unuttuğunu söyle, tersini vaat etme): "
                + "; ".join(f"“{m.content}”" for m in self.deleted)
            )
        if "secret" in self.refused:
            parts.append("Şifre, kart numarası gibi gizli bilgiler güvenlik nedeniyle kaydedilmedi")
        if not parts:
            return "Uygun bir hafıza kaydı bulunamadı; hiçbir şey değiştirilmedi"
        return ". ".join(parts)


def parse_operations(tool_calls: list[Any], text: str) -> list[MemoryOp]:
    raw: list[Any] = []
    for call in tool_calls:
        if getattr(call, "name", "") == "update_memory":
            ops = call.arguments.get("operations", [])
            if isinstance(ops, str):
                try:
                    ops = json.loads(ops)
                except json.JSONDecodeError:
                    ops = []
            raw.extend(ops if isinstance(ops, list) else [])
    if not raw and text:
        match = _JSON_BLOCK.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
                raw = data.get("operations", []) if isinstance(data, dict) else []
            except json.JSONDecodeError:
                raw = []
    ops = []
    for item in raw:
        try:
            ops.append(MemoryOp.model_validate(item))
        except ValidationError:
            log.info("dropped malformed memory op", extra={"op": str(item)[:200]})
    return ops


class MemoryLearner:
    def __init__(
        self,
        *,
        store: MemoryStore,
        conversations: ConversationStore,
        llm: LLMBackend,
        hub: EventHub,
        settings: Callable[[], UserSettings],
        is_busy: Callable[[], bool],
        on_change: Callable[[], Any],
        after_background: Callable[[], Any] | None = None,
    ) -> None:
        self.after_background = after_background
        self.store = store
        self.conversations = conversations
        self.llm = llm
        self.hub = hub
        self.settings = settings
        self.is_busy = is_busy
        self.on_change = on_change
        self._pending: deque[tuple[str, str]] = deque()
        self._wakeup = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._inflight: asyncio.Task[MemoryOutcome] | None = None
        self._last_activity = time.monotonic()
        self.processed = 0

    # ── background learning ────────────────────────────────────────────────
    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="memory-learner")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None

    def enqueue(self, conversation_id: str, message_id: str) -> None:
        self._pending.append((conversation_id, message_id))
        self._last_activity = time.monotonic()
        self._wakeup.set()

    def pause(self) -> None:
        """A new turn is starting: never compete with it for the model."""
        self._last_activity = time.monotonic()
        if self._inflight and not self._inflight.done():
            self._inflight.cancel()

    @property
    def pending(self) -> int:
        return len(self._pending)

    async def wait_idle(self, timeout: float = 30.0) -> None:
        """Test helper: wait until the queue is drained."""
        deadline = time.monotonic() + timeout
        while (self._pending or (self._inflight and not self._inflight.done())) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    async def _run(self) -> None:
        while True:
            if not self._pending:
                self._wakeup.clear()
                await self._wakeup.wait()
            while self.is_busy() or time.monotonic() - self._last_activity < IDLE_DELAY_S:
                await asyncio.sleep(0.2)
            if not self._pending:
                continue
            conv_id, message_id = self._pending[0]
            self._inflight = asyncio.create_task(self._process_pending(conv_id, message_id))
            try:
                await self._inflight
                self._pending.popleft()
            except asyncio.CancelledError:
                if self._worker is not None and self._worker.cancelling():
                    raise
                log.info("memory learning postponed: a new turn started")  # stays queued
            except Exception:
                log.exception("memory learning failed")
                self._pending.popleft()

    async def _process_pending(self, conv_id: str, message_id: str) -> MemoryOutcome:
        if not self.settings().memory_auto_learn:
            return MemoryOutcome()
        messages = await self.conversations.list_messages(conv_id)
        idx = next((i for i, m in enumerate(messages) if m.id == message_id), None)
        if idx is None:
            return MemoryOutcome()
        outcome = await self.process(
            message=messages[idx], recent=messages[max(0, idx - 4) : idx], explicit=False, forget=False
        )
        self.processed += 1
        if self.after_background:
            # The extraction prompt may have displaced the chat prompt in Ollama's cache; re-warm it while idle.
            self.after_background()
        return outcome

    async def prime(self) -> None:
        """Cache the extraction prompt prefix so explicit "hatırla/unut" requests answer faster."""
        settings = self.settings()
        async for _ in self.llm.stream_chat(
            settings.llm_model,
            [{"role": "system", "content": EXTRACTION_PROMPT}, {"role": "user", "content": "Mevcut hafıza:\n(boş)"}],
            GenerationOptions(temperature=0.1, max_tokens=1, num_ctx=4096, keep_alive_minutes=settings.keep_alive_minutes),
            [UPDATE_MEMORY_TOOL],
        ):
            pass

    # ── extraction ─────────────────────────────────────────────────────────
    async def process(self, *, message: Message, recent: list[Message], explicit: bool, forget: bool) -> MemoryOutcome:
        text = message.content
        if not explicit and not forget and is_hypothetical(text):
            return MemoryOutcome()

        candidates = await self._candidates(text, forget)
        aliases = {f"m{i + 1}": mem for i, mem in enumerate(candidates)}
        listing = "\n".join(f"[{a}] ({m.category}) {m.content}" for a, m in aliases.items()) or "(boş)"
        convo = "\n".join(f"{'KULLANICI' if m.role == 'user' else 'LAZOS'}: {m.content[:500]}" for m in recent)
        notes = []
        if explicit:
            notes.append("Not: Kullanıcı bunu açıkça hatırlamanı istedi.")
        if forget:
            notes.append("Not: Kullanıcı bir şeyi unutmanı istiyor; ilgili kaydı sil.")
        prompt = (
            f"Mevcut hafıza:\n{listing}\n\nKonuşmanın önceki kısmı:\n{convo or '(yok)'}\n\n"
            f"KULLANICI (son mesaj): {text[:1500]}\n\n" + "\n".join(notes)
        ).strip()

        settings = self.settings()
        tool_calls: list[Any] = []
        content: list[str] = []
        async for chunk in self.llm.stream_chat(
            settings.llm_model,
            [{"role": "system", "content": EXTRACTION_PROMPT}, {"role": "user", "content": prompt}],
            GenerationOptions(temperature=0.1, max_tokens=400, num_ctx=4096, keep_alive_minutes=settings.keep_alive_minutes),
            [UPDATE_MEMORY_TOOL],
        ):
            if chunk.kind == "tool_calls":
                tool_calls.extend(chunk.tool_calls)
            elif chunk.kind == "text":
                content.append(chunk.text)

        ops = parse_operations(tool_calls, "".join(content))
        outcome = await self._apply(ops, aliases, message, explicit=explicit, forget=forget)
        if outcome.changed:
            self.on_change()
        log.info(
            "memory processed",
            extra={
                "explicit": explicit,
                "forget": forget,
                "added": len(outcome.added),
                "updated": len(outcome.updated),
                "deleted": len(outcome.deleted),
                "refused": ",".join(outcome.refused),
            },
        )
        return outcome

    async def _candidates(self, text: str, forget: bool) -> list[Memory]:
        found: list[Memory] = []
        for mem in await self.store.list_memories(PROFILE_CATEGORIES):
            found.append(mem)
        for mem in await self.store.search_memories(text, limit=8):
            if all(m.id != mem.id for m in found):
                found.append(mem)
        if forget:
            for mem in (await self.store.list_memories())[:8]:  # most recently updated first
                if all(m.id != mem.id for m in found):
                    found.append(mem)
        return found[:30]

    async def _apply(
        self, ops: list[MemoryOp], aliases: dict[str, Memory], message: Message, *, explicit: bool, forget: bool
    ) -> MemoryOutcome:
        outcome = MemoryOutcome()
        origin = "explicit" if (explicit or forget) else "auto"
        for op in ops:
            target = aliases.get(op.id or "") if op.id else None
            if op.action == "delete":
                if not forget:
                    outcome.refused.append("delete-without-request")
                    continue
                if target is None:
                    outcome.refused.append("unknown-id")
                    continue
                deleted = await self.store.delete_memory(target.id)
                if deleted:
                    outcome.deleted.append(deleted)
                    self._publish("deleted", deleted, origin)
                continue

            content = (op.content or "").strip()
            if len(content) < 6:
                outcome.refused.append("empty")
                continue
            if contains_secret(content):  # never stored, even on request
                outcome.refused.append("secret")
                continue
            if not explicit and _TEMPORARY.search(fold(content)):
                outcome.refused.append("temporary")  # "şu anda …" is a passing state, not a durable fact
                continue
            if is_identity_fact(content):
                outcome.refused.append("identity")
                continue
            sensitive = op.sensitive or is_sensitive(content)
            if sensitive and not explicit:
                outcome.refused.append("sensitive")
                continue
            category = op.category if op.category in CATEGORIES else (target.category if target else "knowledge")

            if op.action == "update" and target is not None:
                updated = await self.store.update_memory(
                    target.id,
                    content=content,
                    category=category,
                    source_conversation_id=message.conversation_id,
                    source_message_id=message.id,
                )
                if updated:
                    outcome.updated.append(updated)
                    self._publish("updated", updated, origin)
                continue

            mem, created = await self.store.add_memory(
                category,
                content,
                origin=origin,
                sensitive=sensitive,
                source_conversation_id=message.conversation_id,
                source_message_id=message.id,
            )
            if created:
                outcome.added.append(mem)
                self._publish("added", mem, origin)
        return outcome

    def _publish(self, change: str, mem: Memory, origin: str) -> None:
        self.hub.publish({"type": "memory.changed", "change": change, "origin": origin, "memory": mem.to_dict()})


async def run_with_timeout(coro: Any, timeout: float) -> MemoryOutcome:
    try:
        return await asyncio.wait_for(coro, timeout)
    except (TimeoutError, LLMError) as exc:
        log.warning("explicit memory operation failed: %s", exc)
        outcome = MemoryOutcome()
        outcome.refused.append("error")
        return outcome
