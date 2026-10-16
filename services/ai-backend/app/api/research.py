"""Developer research diagnostics (owner-only, in memory, disabled unless `research_diagnostics` is on)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.http import require_token, services
from app.services import Services

router = APIRouter(prefix="/api/research", dependencies=[Depends(require_token)])


def _summary(t: dict[str, Any]) -> dict[str, Any]:
    ev = t.get("evidence") or {}
    return {
        "message_id": t["message_id"],
        "request": t["request"],
        "intent": t["intent"],
        "queries": t["queries"],
        "evidence_status": ev.get("status"),
        "total_ms": t["timings_ms"].get("total"),
        "created_at": t["created_at"],
    }


@router.get("/traces")
async def list_traces(svc: Services = Depends(services)) -> dict[str, Any]:
    if not svc.user_settings.research_diagnostics:
        svc.chat.research_traces.clear()  # turning the setting off forgets collected traces
        return {"enabled": False, "traces": []}
    return {"enabled": True, "traces": [_summary(t.to_dict()) for t in reversed(svc.chat.research_traces)]}


@router.get("/traces/{message_id}")
async def get_traces(message_id: str, svc: Services = Depends(services)) -> list[dict[str, Any]]:
    """Every research call made for one answer (a turn can search twice). Web data and the request only."""
    found = [t.to_dict() for t in svc.chat.research_traces if t.message_id == message_id]
    if not found:
        raise HTTPException(status_code=404, detail="no diagnostics for this message (is research diagnostics on?)")
    return found


@router.delete("/traces", status_code=204)
async def clear_traces(svc: Services = Depends(services)) -> None:
    svc.chat.research_traces.clear()
