"""SQLite persistence for conversations, messages and user settings."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    summary       TEXT,
    summary_upto  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    id              TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    origin          TEXT NOT NULL DEFAULT 'text',
    status          TEXT NOT NULL DEFAULT 'complete',
    sources         TEXT,
    metrics         TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Message:
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: float
    seq: int = 0
    origin: str = "text"
    status: str = "complete"
    sources: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "origin": self.origin,
            "status": self.status,
            "sources": self.sources,
            "metrics": self.metrics,
        }


@dataclass
class Conversation:
    id: str
    title: str
    created_at: float
    updated_at: float
    summary: str | None = None
    summary_upto: int = 0
    message_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
        }


class StoreError(RuntimeError):
    pass


class ConversationStore:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        try:
            self._db = await aiosqlite.connect(self._path)
            self._db.row_factory = aiosqlite.Row
            await self._db.executescript(SCHEMA)
            await self._db.commit()
        except Exception as exc:  # pragma: no cover - surfaced via status
            raise StoreError(f"cannot open database: {exc}") from exc

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise StoreError("database not open")
        return self._db

    # ── conversations ──────────────────────────────────────────────────────
    async def create_conversation(self, title: str = "Yeni konuşma") -> Conversation:
        now = time.time()
        conv = Conversation(id=uuid.uuid4().hex, title=title, created_at=now, updated_at=now)
        await self.db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conv.id, conv.title, conv.created_at, conv.updated_at),
        )
        await self.db.commit()
        return conv

    async def get_conversation(self, conv_id: str) -> Conversation | None:
        async with self.db.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n
               FROM conversations c WHERE c.id = ?""",
            (conv_id,),
        ) as cur:
            row = await cur.fetchone()
        return self._conv(row) if row else None

    async def list_conversations(self, limit: int = 200) -> list[Conversation]:
        async with self.db.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n
               FROM conversations c ORDER BY c.updated_at DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._conv(r) for r in rows]

    async def rename_conversation(self, conv_id: str, title: str) -> bool:
        cur = await self.db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title.strip()[:120], time.time(), conv_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def delete_conversation(self, conv_id: str) -> bool:
        cur = await self.db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        await self.db.commit()
        return cur.rowcount > 0

    async def set_summary(self, conv_id: str, summary: str, upto_seq: int) -> None:
        await self.db.execute("UPDATE conversations SET summary = ?, summary_upto = ? WHERE id = ?", (summary, upto_seq, conv_id))
        await self.db.commit()

    # ── messages ───────────────────────────────────────────────────────────
    async def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        *,
        origin: str = "text",
        status: str = "complete",
        sources: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> Message:
        msg = Message(
            id=message_id or uuid.uuid4().hex,
            conversation_id=conv_id,
            role=role,
            content=content,
            created_at=time.time(),
            origin=origin,
            status=status,
            sources=sources or [],
            metrics=metrics,
        )
        cur = await self.db.execute(
            """INSERT INTO messages (id, conversation_id, role, content, origin, status, sources, metrics, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg.id,
                conv_id,
                role,
                content,
                origin,
                status,
                json.dumps(msg.sources, ensure_ascii=False) if msg.sources else None,
                json.dumps(metrics) if metrics else None,
                msg.created_at,
            ),
        )
        msg.seq = cur.lastrowid or 0
        await self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (msg.created_at, conv_id))
        await self.db.commit()
        return msg

    async def list_messages(self, conv_id: str, *, after_seq: int = 0) -> list[Message]:
        async with self.db.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND seq > ? ORDER BY seq", (conv_id, after_seq)
        ) as cur:
            rows = await cur.fetchall()
        return [self._msg(r) for r in rows]

    # ── settings ───────────────────────────────────────────────────────────
    async def load_settings(self) -> dict[str, Any]:
        async with self.db.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    async def save_settings(self, values: dict[str, Any]) -> None:
        await self.db.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v)) for k, v in values.items()],
        )
        await self.db.commit()

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _conv(row: aiosqlite.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            summary=row["summary"],
            summary_upto=row["summary_upto"],
            message_count=row["n"],
        )

    @staticmethod
    def _msg(row: aiosqlite.Row) -> Message:
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
            seq=row["seq"],
            origin=row["origin"],
            status=row["status"],
            sources=json.loads(row["sources"]) if row["sources"] else [],
            metrics=json.loads(row["metrics"]) if row["metrics"] else None,
        )
