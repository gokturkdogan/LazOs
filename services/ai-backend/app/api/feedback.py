"""Feedback, corrections, learning suggestions and local exports."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.http import require_token, services
from app.feedback.policy import REASONS
from app.feedback.service import FeedbackError
from app.services import Services

router = APIRouter(prefix="/api/feedback", dependencies=[Depends(require_token)])

Reason = Literal[
    "incorrect", "unnatural_turkish", "too_long", "too_short", "misunderstood", "lost_context", "wrong_tone", "unnecessary_tool", "other"
]
EXPORT_DIR = Path.home() / "Downloads" / "LAZOS"


class RatingBody(BaseModel):
    rating: Literal[-1, 1]
    reasons: list[Reason] = Field(default_factory=list, max_length=9)
    note: str | None = Field(default=None, max_length=500)


class CorrectionBody(BaseModel):
    preferred: str = Field(min_length=2, max_length=4000)
    use_as_example: bool = True
    reasons: list[Reason] = Field(default_factory=list, max_length=9)


class CorrectionPatch(BaseModel):
    preferred: str | None = Field(default=None, min_length=2, max_length=4000)
    kind: Literal["style", "local", "factual"] | None = None
    status: Literal["pending", "approved", "rejected"] | None = None


class TranscriptBody(BaseModel):
    corrected: str = Field(min_length=1, max_length=8000)


class SuggestionBody(BaseModel):
    proposal: str | None = Field(default=None, min_length=3, max_length=300)


class ExportBody(BaseModel):
    kind: Literal["training", "all"] = "training"
    skip_ids: list[str] = Field(default_factory=list, max_length=10_000)


async def _run(coro: Any) -> Any:
    try:
        return await coro
    except FeedbackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
async def overview(svc: Services = Depends(services)) -> dict[str, Any]:
    f = svc.feedback
    return {
        "enabled": svc.user_settings.feedback_examples_enabled,
        "reasons": REASONS,
        "stats": await f.stats(),
        "corrections": await f.list_corrections(),
        "suggestions": await f.list_suggestions(),
        "transcripts": await f.list_transcript_corrections(),
        "feedback": await f.list_feedback(limit=100),
    }


@router.post("/messages/{message_id}/rating")
async def rate(message_id: str, body: RatingBody, svc: Services = Depends(services)) -> dict[str, Any]:
    return await _run(svc.feedback.rate(message_id, body.rating, list(body.reasons), body.note))


@router.delete("/messages/{message_id}/rating", status_code=204)
async def withdraw(message_id: str, svc: Services = Depends(services)) -> None:
    if not await svc.feedback.withdraw(message_id):
        raise HTTPException(status_code=404, detail="no rating for this message")


@router.post("/messages/{message_id}/correction")
async def correct(message_id: str, body: CorrectionBody, svc: Services = Depends(services)) -> dict[str, Any]:
    return await _run(svc.feedback.correct(message_id, body.preferred, use_as_example=body.use_as_example, reasons=list(body.reasons)))


@router.post("/messages/{message_id}/transcript")
async def correct_transcript(message_id: str, body: TranscriptBody, svc: Services = Depends(services)) -> dict[str, Any]:
    return await _run(svc.feedback.correct_transcript(message_id, body.corrected))


@router.patch("/corrections/{correction_id}")
async def update_correction(correction_id: str, body: CorrectionPatch, svc: Services = Depends(services)) -> dict[str, Any]:
    return await _run(svc.feedback.update_correction(correction_id, preferred=body.preferred, kind=body.kind, status=body.status))


@router.delete("/corrections/{correction_id}", status_code=204)
async def delete_correction(correction_id: str, svc: Services = Depends(services)) -> None:
    if not await svc.feedback.delete_correction(correction_id):
        raise HTTPException(status_code=404, detail="correction not found")


@router.delete("/transcripts/{correction_id}", status_code=204)
async def delete_transcript(correction_id: str, svc: Services = Depends(services)) -> None:
    if not await svc.feedback.delete_transcript_correction(correction_id):
        raise HTTPException(status_code=404, detail="transcript correction not found")


@router.delete("/items/{feedback_id}", status_code=204)
async def delete_feedback(feedback_id: str, svc: Services = Depends(services)) -> None:
    if not await svc.feedback.delete_feedback(feedback_id):
        raise HTTPException(status_code=404, detail="feedback not found")


@router.post("/suggestions/{suggestion_id}/{action}")
async def decide(
    suggestion_id: str,
    action: Literal["approve", "reject", "revoke"],
    body: SuggestionBody | None = None,
    svc: Services = Depends(services),
) -> dict[str, Any]:
    f = svc.feedback
    if action == "approve":
        result = await _run(f.approve_suggestion(suggestion_id, body.proposal if body else None))
    elif action == "reject":
        result = await _run(f.reject_suggestion(suggestion_id))
    else:
        result = await _run(f.revoke_suggestion(suggestion_id))
    if result.get("kind") == "stt_term":
        await svc.refresh_learned_terms()
    return result


@router.patch("/suggestions/{suggestion_id}")
async def edit_suggestion(suggestion_id: str, body: SuggestionBody, svc: Services = Depends(services)) -> dict[str, Any]:
    if not body.proposal:
        raise HTTPException(status_code=422, detail="proposal required")
    result = await _run(svc.feedback.edit_suggestion(suggestion_id, body.proposal))
    if result.get("kind") == "stt_term":
        await svc.refresh_learned_terms()
    return result


@router.delete("")
async def clear(confirm: bool = False, svc: Services = Depends(services)) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="pass confirm=true to clear feedback history")
    counts = await svc.feedback.clear_all()
    await svc.refresh_learned_terms()
    return counts


@router.get("/export/preview")
async def export_preview(svc: Services = Depends(services)) -> dict[str, Any]:
    return await svc.feedback.training_records()


@router.post("/export")
async def export(body: ExportBody, svc: Services = Depends(services)) -> dict[str, Any]:
    """Writes a local file only (never uploaded). Training exports contain approved style examples only."""
    return await svc.feedback.export(body.kind, EXPORT_DIR, body.skip_ids)
