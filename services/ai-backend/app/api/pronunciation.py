"""Pronunciation dictionary management. Rules only ever change the text sent to the speech engine."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.http import require_token, services
from app.core.state import AIState
from app.services import Services
from app.speech.pronunciation import BUILTIN_RULES, ENGINES
from app.speech.text import to_speakable
from app.speech.tts import TTSError

router = APIRouter(prefix="/api/pronunciation", dependencies=[Depends(require_token)])

Engine = Literal["all", "piper", "macos"]


class RuleCreate(BaseModel):
    term: str = Field(min_length=1, max_length=60)
    replacement: str = Field(min_length=1, max_length=120)
    engine: Engine = "all"
    case_sensitive: bool = False


class RulePatch(BaseModel):
    term: str | None = Field(default=None, min_length=1, max_length=60)
    replacement: str | None = Field(default=None, min_length=1, max_length=120)
    engine: Engine | None = None
    case_sensitive: bool | None = None
    enabled: bool | None = None


class TestBody(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    engine: Literal["piper", "macos"] | None = None
    speak: bool = False


def _svc(svc: Services) -> Any:
    if svc.pronunciation is None:
        raise HTTPException(status_code=503, detail="pronunciation service not ready")
    return svc.pronunciation


@router.get("")
async def overview(svc: Services = Depends(services)) -> dict[str, Any]:
    p = _svc(svc)
    return {
        "enabled": svc.user_settings.pronunciation_enabled,
        "engines": list(ENGINES),
        "builtin": [r.to_dict() for r in BUILTIN_RULES],
        "custom": [r.to_dict() for r in p.custom],
    }


@router.post("/rules", status_code=201)
async def add_rule(body: RuleCreate, svc: Services = Depends(services)) -> dict[str, Any]:
    p = _svc(svc)
    rule = await p.store.add(body.term, body.replacement, body.engine, body.case_sensitive)
    await p.reload()
    return rule.to_dict()


@router.patch("/rules/{rule_id}")
async def update_rule(rule_id: str, body: RulePatch, svc: Services = Depends(services)) -> dict[str, Any]:
    p = _svc(svc)
    rule = await p.store.update(rule_id, **body.model_dump(exclude_none=True))
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    await p.reload()
    return rule.to_dict()


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, svc: Services = Depends(services)) -> None:
    p = _svc(svc)
    if not await p.store.delete(rule_id):
        raise HTTPException(status_code=404, detail="rule not found")
    await p.reload()


@router.post("/test")
async def test_pronunciation(body: TestBody, svc: Services = Depends(services)) -> dict[str, Any]:
    """Show exactly what each voice will be given; optionally speak it."""
    p = _svc(svc)
    speakable = to_speakable(body.text)
    result: dict[str, Any] = {"original": body.text, "spoken": {e: p.normalize(speakable, e) for e in ENGINES}}
    if body.speak:
        if svc.chat.busy or svc.voice.mode != "off":
            raise HTTPException(status_code=409, detail="busy")
        engine = body.engine or svc.tts.preferred
        previous = svc.tts.preferred
        svc.tts.preferred = engine
        try:
            audio, rate, used = await svc.tts.synthesize(speakable, svc.user_settings.tts_speed)
        except TTSError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            svc.tts.preferred = previous
        svc.state.set(AIState.SPEAKING, "preview", force=True)
        svc.player.enqueue(audio, rate, svc.user_settings.output_device)
        await svc.player.wait_idle()
        svc.state.set(AIState.IDLE, force=True)
        result["played_with"] = used
    return result
