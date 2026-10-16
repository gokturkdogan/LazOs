"""Inference abstraction. The rest of LAZOS only talks to `LLMBackend`, never to a runtime directly."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


class LLMError(RuntimeError):
    code = "llm_error"


class LLMUnavailable(LLMError):
    code = "llm_unavailable"


class ModelMissing(LLMError):
    code = "model_missing"


class LLMTimeout(LLMError):
    code = "llm_timeout"


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class StreamChunk:
    kind: Literal["text", "tool_calls", "done"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    eval_count: int | None = None
    eval_duration_ms: float | None = None
    prompt_eval_count: int | None = None


@dataclass
class GenerationOptions:
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    num_ctx: int = 8192
    max_tokens: int = 1536
    keep_alive_minutes: int = 30
    think: bool = False


@dataclass
class BackendHealth:
    reachable: bool
    version: str | None = None
    error: str | None = None


class LLMBackend(Protocol):
    name: str

    async def health(self) -> BackendHealth: ...

    async def list_models(self) -> list[str]: ...

    async def has_model(self, model: str) -> bool: ...

    def stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: GenerationOptions,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamChunk]: ...

    async def complete(self, model: str, messages: list[dict[str, Any]], options: GenerationOptions) -> str: ...

    async def warm_up(self, model: str, keep_alive_minutes: int) -> None: ...

    async def unload(self, model: str) -> None: ...

    def pull(self, model: str) -> AsyncIterator[dict[str, Any]]: ...

    async def loaded_models(self) -> list[dict[str, Any]]: ...
