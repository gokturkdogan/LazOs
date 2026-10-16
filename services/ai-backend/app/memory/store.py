"""Persistence for the core identity, learned memories and episodic (conversation) search."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import aiosqlite

from app.conversations.store import ConversationStore
from app.memory.identity import DEFAULT_IDENTITY, CoreIdentity
from app.memory.text import content_terms, fts_query, index_body, similarity

Category = Literal["user_identity", "preference", "communication", "project", "interest", "decision", "knowledge"]
CATEGORIES: dict[str, str] = {
    "user_identity": "Kimlik",
    "preference": "Kişisel tercih",
    "communication": "İletişim tercihi",
    "project": "Proje",
    "interest": "İlgi alanı",
    "decision": "Önemli karar",
    "knowledge": "Uzun vadeli bilgi",
}
# Loaded into every conversation (the "profile"); the others are retrieved when relevant.
PROFILE_CATEGORIES = ("user_identity", "communication", "preference")
DUPLICATE_THRESHOLD = 0.75


@dataclass
class Memory:
    id: str
    category: str
    content: str
    origin: str
    created_at: float
    updated_at: float
    sensitive: bool = False
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    use_count: int = 0
    last_used_at: float | None = None
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "category_label": CATEGORIES.get(self.category, self.category),
            "content": self.content,
            "origin": self.origin,
            "sensitive": self.sensitive,
            "source_conversation_id": self.source_conversation_id,
            "source_message_id": self.source_message_id,
            "history": self.history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "use_count": self.use_count,
            "last_used_at": self.last_used_at,
        }


@dataclass
class EpisodeHit:
    conversation_id: str
    conversation_title: str
    message_id: str
    role: str
    content: str
    created_at: float
    seq: int


class MemoryStore:
    def __init__(self, conversations: ConversationStore) -> None:
        self._conv = conversations

    @property
    def db(self) -> aiosqlite.Connection:
        return self._conv.db

    # ── core identity ──────────────────────────────────────────────────────
    async def get_identity(self) -> CoreIdentity:
        async with self.db.execute("SELECT data FROM core_identity WHERE id = 1") as cur:
            row = await cur.fetchone()
        if row is None:
            await self.set_identity(DEFAULT_IDENTITY)
            return DEFAULT_IDENTITY
        return CoreIdentity.model_validate_json(row["data"])

    async def set_identity(self, identity: CoreIdentity) -> CoreIdentity:
        await self.db.execute(
            """INSERT INTO core_identity (id, data, updated_at) VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
            (identity.model_dump_json(), time.time()),
        )
        await self.db.commit()
        return identity

    # ── learned memories ───────────────────────────────────────────────────
    async def list_memories(self, categories: tuple[str, ...] | None = None) -> list[Memory]:
        sql = "SELECT * FROM memories"
        params: tuple[Any, ...] = ()
        if categories:
            sql += f" WHERE category IN ({','.join('?' * len(categories))})"
            params = categories
        async with self.db.execute(sql + " ORDER BY updated_at DESC", params) as cur:
            return [self._row(r) for r in await cur.fetchall()]

    async def get_memory(self, memory_id: str) -> Memory | None:
        async with self.db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)) as cur:
            row = await cur.fetchone()
        return self._row(row) if row else None

    async def find_duplicate(self, content: str) -> Memory | None:
        best: tuple[float, Memory] | None = None
        for mem in await self.list_memories():
            score = similarity(content, mem.content)
            if score >= DUPLICATE_THRESHOLD and (best is None or score > best[0]):
                best = (score, mem)
        return best[1] if best else None

    async def add_memory(
        self,
        category: str,
        content: str,
        *,
        origin: str,
        sensitive: bool = False,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
    ) -> tuple[Memory, bool]:
        """Returns (memory, created). Near-duplicates refresh the existing memory instead."""
        content = content.strip()
        if category not in CATEGORIES:
            category = "knowledge"
        dup = await self.find_duplicate(content)
        if dup is not None:
            await self.db.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (time.time(), dup.id))
            await self.db.commit()
            return dup, False
        now = time.time()
        mem = Memory(
            id=uuid.uuid4().hex[:12],
            category=category,
            content=content,
            origin=origin,
            created_at=now,
            updated_at=now,
            sensitive=sensitive,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
        )
        cur = await self.db.execute(
            """INSERT INTO memories (id, category, content, origin, sensitive, source_conversation_id, source_message_id,
                                     history, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)""",
            (mem.id, category, content, origin, int(sensitive), source_conversation_id, source_message_id, now, now),
        )
        mem.seq = cur.lastrowid or 0
        await self.db.execute("INSERT INTO memories_fts(rowid, body) VALUES (?, ?)", (mem.seq, index_body(content)))
        await self.db.commit()
        return mem, True

    async def update_memory(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
    ) -> Memory | None:
        mem = await self.get_memory(memory_id)
        if mem is None:
            return None
        new_content = (content or mem.content).strip()
        new_category = category if category in CATEGORIES else mem.category
        history = mem.history
        if new_content != mem.content:
            history = [*history, {"content": mem.content, "until": time.time()}][-10:]
        now = time.time()
        await self.db.execute(
            """UPDATE memories SET content = ?, category = ?, history = ?, updated_at = ?,
                   source_conversation_id = COALESCE(?, source_conversation_id),
                   source_message_id = COALESCE(?, source_message_id)
               WHERE id = ?""",
            (new_content, new_category, json.dumps(history, ensure_ascii=False), now, source_conversation_id, source_message_id, memory_id),
        )
        await self.db.execute("UPDATE memories_fts SET body = ? WHERE rowid = ?", (index_body(new_content), mem.seq))
        await self.db.commit()
        return await self.get_memory(memory_id)

    async def delete_memory(self, memory_id: str) -> Memory | None:
        mem = await self.get_memory(memory_id)
        if mem is None:
            return None
        await self.db.execute("DELETE FROM memories_fts WHERE rowid = ?", (mem.seq,))
        await self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await self.db.commit()
        return mem

    async def clear_memories(self) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM memories") as cur:
            count = (await cur.fetchone())[0]
        await self.db.execute("DELETE FROM memories")
        await self.db.execute("DELETE FROM memories_fts")
        await self.db.commit()
        return count

    async def search_memories(
        self, text: str, *, limit: int = 5, exclude_categories: tuple[str, ...] = ()
    ) -> list[Memory]:
        query = fts_query(content_terms(text))
        if not query:
            return []
        async with self.db.execute(
            """SELECT m.* FROM memories_fts f JOIN memories m ON m.seq = f.rowid
               WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?""",
            (query, limit * 3),
        ) as cur:
            rows = await cur.fetchall()
        found = [self._row(r) for r in rows if r["category"] not in exclude_categories]
        return found[:limit]

    async def mark_used(self, ids: list[str]) -> None:
        if not ids:
            return
        await self.db.executemany(
            "UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id = ?", [(time.time(), i) for i in ids]
        )
        await self.db.commit()

    async def stats(self) -> dict[str, Any]:
        async with self.db.execute("SELECT category, COUNT(*) AS n FROM memories GROUP BY category") as cur:
            by_cat = {r["category"]: r["n"] for r in await cur.fetchall()}
        async with self.db.execute("SELECT COUNT(*) FROM conversations") as cur:
            convs = (await cur.fetchone())[0]
        async with self.db.execute("SELECT COUNT(*) FROM messages_fts") as cur:
            indexed = (await cur.fetchone())[0]
        return {"memories": sum(by_cat.values()), "by_category": by_cat, "conversations": convs, "indexed_messages": indexed}

    # ── episodic memory (previous conversations) ───────────────────────────
    async def search_messages(
        self,
        text: str,
        *,
        since: float | None = None,
        until: float | None = None,
        exclude_conversation: str | None = None,
        limit: int = 8,
    ) -> list[EpisodeHit]:
        query = fts_query(content_terms(text))
        if not query:
            return []
        sql = """SELECT m.seq, m.id, m.role, m.content, m.created_at, m.conversation_id, c.title
                 FROM messages_fts f JOIN messages m ON m.seq = f.rowid JOIN conversations c ON c.id = m.conversation_id
                 WHERE messages_fts MATCH ?"""
        params: list[Any] = [query]
        if since is not None:
            sql += " AND m.created_at >= ?"
            params.append(since)
        if until is not None:
            sql += " AND m.created_at < ?"
            params.append(until)
        if exclude_conversation:
            sql += " AND m.conversation_id != ?"
            params.append(exclude_conversation)
        sql += " ORDER BY bm25(messages_fts) LIMIT ?"
        params.append(limit)
        async with self.db.execute(sql, params) as cur:
            return [self._hit(r) for r in await cur.fetchall()]

    async def recent_conversations(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        exclude_conversation: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        sql = "SELECT id, title, summary, created_at, updated_at FROM conversations WHERE 1 = 1"
        params: list[Any] = []
        if since is not None:
            sql += " AND updated_at >= ?"
            params.append(since)
        if until is not None:
            sql += " AND created_at < ?"
            params.append(until)
        if exclude_conversation:
            sql += " AND id != ?"
            params.append(exclude_conversation)
        sql += " AND EXISTS (SELECT 1 FROM messages WHERE conversation_id = conversations.id) ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        async with self.db.execute(sql, params) as cur:
            convs = [dict(r) for r in await cur.fetchall()]
        for conv in convs:
            async with self.db.execute(
                "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY seq LIMIT 6", (conv["id"],)
            ) as cur:
                conv["messages"] = [dict(r) for r in await cur.fetchall()]
        return convs

    async def neighbours(self, conversation_id: str, seq: int) -> list[dict[str, Any]]:
        """The message right after a hit (usually LAZOS's reply) for context."""
        async with self.db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? AND seq > ? ORDER BY seq LIMIT 1",
            (conversation_id, seq),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _row(row: aiosqlite.Row) -> Memory:
        return Memory(
            id=row["id"],
            category=row["category"],
            content=row["content"],
            origin=row["origin"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            sensitive=bool(row["sensitive"]),
            source_conversation_id=row["source_conversation_id"],
            source_message_id=row["source_message_id"],
            history=json.loads(row["history"] or "[]"),
            use_count=row["use_count"],
            last_used_at=row["last_used_at"],
            seq=row["seq"],
        )

    @staticmethod
    def _hit(row: aiosqlite.Row) -> EpisodeHit:
        return EpisodeHit(
            conversation_id=row["conversation_id"],
            conversation_title=row["title"],
            message_id=row["id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
            seq=row["seq"],
        )
