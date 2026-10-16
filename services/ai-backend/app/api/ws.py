"""The real-time WebSocket channel: streamed answers, AI state, audio levels, voice control."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api import schemas
from app.chat import TurnRequest
from app.core.security import origin_allowed, token_matches
from app.services import Services

log = logging.getLogger("lazos.ws")
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if not origin_allowed(ws.headers.get("origin")) or not token_matches(ws.app.state.token, ws.query_params.get("token")):
        await ws.close(code=4401)
        return
    await ws.accept()
    svc: Services = ws.app.state.services
    queue = svc.hub.subscribe()
    svc.publish_system()
    await ws.send_json({"type": "hello", "conversation_id": svc.chat.current_conversation_id, "settings": svc.user_settings.model_dump()})
    sender = asyncio.create_task(_sender(ws, queue))
    try:
        while True:
            raw = await ws.receive_json()
            await _dispatch(ws, svc, raw)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket receive loop failed")
    finally:
        svc.hub.unsubscribe(queue)
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender


async def _sender(ws: WebSocket, queue: asyncio.Queue[dict[str, Any]]) -> None:
    while True:
        event = await queue.get()
        await ws.send_json(event)


async def _dispatch(ws: WebSocket, svc: Services, raw: Any) -> None:
    try:
        msg = schemas.client_message_adapter.validate_python(raw)
    except ValidationError as exc:
        await ws.send_json({"type": "error", "code": "bad_request", "message": str(exc.errors(include_url=False)[:1])})
        return

    match msg:
        case schemas.Ping():
            await ws.send_json({"type": "pong"})
        case schemas.ChatSend(text=text, conversation_id=conv_id):
            if svc.models.states["llm"].status != "present":
                await ws.send_json(
                    {"type": "chat.error", "code": "model_missing", "message": "Dil modeli henüz hazır değil. Kurulumu tamamla."}
                )
                return
            speak = svc.user_settings.speak_text_replies or svc.voice.mode == "continuous"
            await svc.chat.submit(TurnRequest(text=text.strip(), conversation_id=conv_id, origin="text", speak=speak))
        case schemas.ChatCancel():
            await svc.chat.cancel()
        case schemas.SpeechStop():
            svc.chat.stop_speaking()
        case schemas.VoicePTT(action="start", conversation_id=conv_id):
            await svc.voice.start_ptt(conv_id)
        case schemas.VoicePTT(action="stop"):
            svc.voice.stop_ptt()
        case schemas.VoiceContinuous(enabled=enabled, conversation_id=conv_id):
            await svc.voice.set_continuous(enabled, conv_id)
        case schemas.ConversationSelect(conversation_id=conv_id):
            if svc.chat.busy:
                await svc.chat.cancel()
            svc.chat.current_conversation_id = conv_id
