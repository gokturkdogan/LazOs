"""Owner-facing memory management: core identity, learned memories and conversation memory search.

The core identity has no write path from conversations; it can only be changed here.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.http import require_token, services
from app.memory.identity import DEFAULT_IDENTITY, CoreIdentity
from app.memory.store import CATEGORIES, PROFILE_CATEGORIES
from app.services import Services

router = APIRouter(prefix="/api/memory", dependencies=[Depends(require_token)])

CategoryName = Literal["user_identity", "preference", "communication", "project", "interest", "decision", "knowledge"]


class MemoryCreate(BaseModel):
    category: CategoryName
    content: str = Field(min_length=3, max_length=400)


class MemoryPatch(BaseModel):
    category: CategoryName | None = None
    content: str | None = Field(default=None, min_length=3, max_length=400)


def _publish(svc: Services, change: str, memory: dict[str, Any]) -> None:
    svc.hub.publish({"type": "memory.changed", "change": change, "origin": "manual", "memory": memory})


@router.get("")
async def overview(svc: Services = Depends(services)) -> dict[str, Any]:
    memories = await svc.memory.store.list_memories()
    return {
        "identity": svc.memory.identity.model_dump(),
        "default_identity": DEFAULT_IDENTITY.model_dump(),
        "categories": CATEGORIES,
        "profile_categories": list(PROFILE_CATEGORIES),
        "memories": [m.to_dict() for m in memories],
        "stats": await svc.memory.store.stats(),
        "pending_learning": svc.memory.learner.pending,
    }


@router.put("/identity")
async def update_identity(body: CoreIdentity, svc: Services = Depends(services)) -> dict[str, Any]:
    identity = await svc.memory.set_identity(body)
    return identity.model_dump()


@router.post("/identity/reset")
async def reset_identity(svc: Services = Depends(services)) -> dict[str, Any]:
    identity = await svc.memory.set_identity(DEFAULT_IDENTITY)
    return identity.model_dump()


@router.post("/items", status_code=201)
async def add_item(body: MemoryCreate, svc: Services = Depends(services)) -> dict[str, Any]:
    mem, created = await svc.memory.store.add_memory(body.category, body.content, origin="manual")
    await svc.memory.refresh_profile()
    if created:
        _publish(svc, "added", mem.to_dict())
    return {**mem.to_dict(), "created": created}


@router.patch("/items/{memory_id}")
async def update_item(memory_id: str, body: MemoryPatch, svc: Services = Depends(services)) -> dict[str, Any]:
    mem = await svc.memory.store.update_memory(memory_id, content=body.content, category=body.category)
    if mem is None:
        raise HTTPException(status_code=404, detail="memory not found")
    await svc.memory.refresh_profile()
    _publish(svc, "updated", mem.to_dict())
    return mem.to_dict()


@router.delete("/items/{memory_id}", status_code=204)
async def delete_item(memory_id: str, svc: Services = Depends(services)) -> None:
    mem = await svc.memory.store.delete_memory(memory_id)
    if mem is None:
        raise HTTPException(status_code=404, detail="memory not found")
    await svc.memory.refresh_profile()
    _publish(svc, "deleted", mem.to_dict())


@router.delete("/items")
async def clear_items(confirm: bool = False, svc: Services = Depends(services)) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="pass confirm=true to clear all learned memories")
    count = await svc.memory.store.clear_memories()
    await svc.memory.refresh_profile()
    svc.hub.publish({"type": "memory.changed", "change": "cleared", "origin": "manual", "memory": None})
    return {"deleted": count}


@router.get("/episodes")
async def search_episodes(q: str, svc: Services = Depends(services)) -> dict[str, Any]:
    hits = await svc.memory.store.search_messages(q, limit=20)
    return {
        "query": q,
        "hits": [
            {
                "conversation_id": h.conversation_id,
                "conversation_title": h.conversation_title,
                "message_id": h.message_id,
                "role": h.role,
                "content": h.content[:500],
                "created_at": h.created_at,
            }
            for h in hits
        ],
    }
