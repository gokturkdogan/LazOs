"""Typed contracts for client → server WebSocket messages and HTTP request bodies.

Server → client events are documented in docs/ARCHITECTURE.md and mirrored in
apps/desktop/src/types/protocol.ts.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class ChatSend(BaseModel):
    type: Literal["chat.send"]
    text: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class ChatCancel(BaseModel):
    type: Literal["chat.cancel"]


class SpeechStop(BaseModel):
    type: Literal["speech.stop"]


class VoicePTT(BaseModel):
    type: Literal["voice.ptt"]
    action: Literal["start", "stop"]
    conversation_id: str | None = None


class VoiceContinuous(BaseModel):
    type: Literal["voice.continuous"]
    enabled: bool
    conversation_id: str | None = None


class ConversationSelect(BaseModel):
    type: Literal["conversation.select"]
    conversation_id: str | None = None


class Ping(BaseModel):
    type: Literal["ping"]


ClientMessage = Annotated[
    ChatSend | ChatCancel | SpeechStop | VoicePTT | VoiceContinuous | ConversationSelect | Ping,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[Any] = TypeAdapter(ClientMessage)


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class InstallBody(BaseModel):
    ids: list[Literal["llm", "stt", "tts", "vad"]] = Field(min_length=1)


class PreviewBody(BaseModel):
    text: str = Field(default="Merhaba, ben LAZOS. Sistemler hazır.", max_length=400)
    engine: Literal["piper", "macos"] | None = None
