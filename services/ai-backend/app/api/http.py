"""HTTP endpoints: health, status, setup, conversations, settings, audio devices."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app import __version__
from app.api.schemas import InstallBody, PreviewBody, RenameBody
from app.core.security import token_matches
from app.services import Services
from app.speech import audio_io
from app.speech.tts import TTSError


def services(request: Request) -> Services:
    return request.app.state.services


def require_token(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    provided = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else request.headers.get("x-lazos-token")
    if not token_matches(request.app.state.token, provided):
        raise HTTPException(status_code=401, detail="invalid or missing token")


public = APIRouter()
api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


@public.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "app": "lazos", "version": __version__}


# ── status & setup ─────────────────────────────────────────────────────────
@api.get("/status")
async def status(svc: Services = Depends(services)) -> dict[str, Any]:
    return {**svc.system_snapshot(), "state": svc.state.state.value, "voice_mode": svc.voice.mode}


@api.get("/setup")
async def setup(svc: Services = Depends(services)) -> dict[str, Any]:
    await svc.models.refresh()
    llm_health = await svc.llm.health()
    providers = await svc.research.provider_status()
    try:
        devices = await asyncio.to_thread(audio_io.list_devices)
    except Exception as exc:  # PortAudio unavailable
        devices = {"inputs": [], "outputs": [], "error": str(exc)}
    return {
        "system": svc.system_info(),
        "runtime": {
            "ollama_installed": svc.ollama_binary() is not None,
            "ollama_running": llm_health.reachable,
            "ollama_version": llm_health.version,
        },
        "models": svc.models.describe(),
        "audio": {"inputs": len(devices["inputs"]), "outputs": len(devices["outputs"]), "error": devices.get("error")},
        "search": providers,
        "ready": svc.models.all_present() and llm_health.reachable,
    }


@api.post("/setup/install")
async def install(body: InstallBody, svc: Services = Depends(services)) -> dict[str, Any]:
    await svc.models.refresh()
    if "llm" in body.ids and not (await svc.llm.health()).reachable:
        raise HTTPException(status_code=409, detail="Ollama çalışmıyor")
    return {"started": svc.models.install(list(body.ids))}


@api.post("/setup/ollama/start")
async def start_ollama(svc: Services = Depends(services)) -> dict[str, Any]:
    return await svc.start_ollama()


@api.post("/setup/mic-test")
async def mic_test(svc: Services = Depends(services)) -> dict[str, Any]:
    """Records ~1.5 s to trigger the macOS permission prompt and verify that real audio arrives."""
    if svc.voice.mode != "off":
        raise HTTPException(status_code=409, detail="voice session active")
    try:
        svc.mic.start(svc.user_settings.input_device)
    except audio_io.AudioDeviceError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        await asyncio.sleep(1.5)
        frames = svc.mic.queue.qsize()
        peak = svc.mic.peak_since_start
    finally:
        svc.mic.stop()
    if frames == 0:
        return {"ok": False, "error": "Mikrofondan veri gelmedi."}
    if peak == 0.0:
        return {"ok": False, "error": "Mikrofon sessiz — macOS mikrofon izni verilmemiş olabilir.", "permission": "denied"}
    return {"ok": True, "peak": round(peak, 4)}


# ── conversations ──────────────────────────────────────────────────────────
@api.get("/conversations")
async def list_conversations(svc: Services = Depends(services)) -> list[dict[str, Any]]:
    return [c.to_dict() for c in await svc.store.list_conversations()]


@api.post("/conversations", status_code=201)
async def create_conversation(svc: Services = Depends(services)) -> dict[str, Any]:
    conv = await svc.store.create_conversation()
    return conv.to_dict()


@api.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, svc: Services = Depends(services)) -> dict[str, Any]:
    conv = await svc.store.get_conversation(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    messages = await svc.store.list_messages(conv_id)
    return {**conv.to_dict(), "messages": [m.to_dict() for m in messages]}


@api.patch("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, body: RenameBody, svc: Services = Depends(services)) -> dict[str, Any]:
    if not await svc.store.rename_conversation(conv_id, body.title):
        raise HTTPException(status_code=404, detail="conversation not found")
    conv = await svc.store.get_conversation(conv_id)
    return conv.to_dict() if conv else {}


@api.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str, svc: Services = Depends(services)) -> None:
    if svc.chat.busy and svc.chat.current_conversation_id == conv_id:
        await svc.chat.cancel()
    if not await svc.store.delete_conversation(conv_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    if svc.chat.current_conversation_id == conv_id:
        svc.chat.current_conversation_id = None


# ── settings ───────────────────────────────────────────────────────────────
@api.get("/settings")
async def get_settings(svc: Services = Depends(services)) -> dict[str, Any]:
    return svc.user_settings.model_dump()


@api.put("/settings")
async def put_settings(patch: dict[str, Any], svc: Services = Depends(services)) -> dict[str, Any]:
    try:
        updated = await svc.update_settings(patch)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    return updated.model_dump()


# ── audio ──────────────────────────────────────────────────────────────────
@api.get("/audio/devices")
async def devices(refresh: bool = False) -> dict[str, Any]:
    if refresh:
        await asyncio.to_thread(audio_io.refresh_devices)
    try:
        return await asyncio.to_thread(audio_io.list_devices)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"audio subsystem unavailable: {exc}") from exc


@api.post("/voice/preview")
async def preview_voice(body: PreviewBody, svc: Services = Depends(services)) -> dict[str, Any]:
    if svc.chat.busy or svc.voice.mode != "off":
        raise HTTPException(status_code=409, detail="busy")
    previous = svc.tts.preferred
    if body.engine:
        svc.tts.preferred = body.engine
    try:
        audio, rate, engine = await svc.tts.synthesize(body.text, svc.user_settings.tts_speed)
    except TTSError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        svc.tts.preferred = previous
    from app.core.state import AIState

    svc.state.set(AIState.SPEAKING, "preview", force=True)
    svc.player.enqueue(audio, rate, svc.user_settings.output_device)
    await svc.player.wait_idle()
    svc.state.set(AIState.IDLE, force=True)
    return {"engine": engine, "duration_s": round(len(audio) / rate, 2)}


@api.get("/metrics")
async def metrics(svc: Services = Depends(services)) -> dict[str, Any]:
    return {"recent": list(svc.metrics.recent)}
