"""Test doubles for subsystems that need hardware, models or the network."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import numpy as np

from app.inference.base import BackendHealth, GenerationOptions, LLMUnavailable, StreamChunk, ToolCall
from app.research.providers.base import SearchResult

Script = list[StreamChunk] | Exception


def text(*parts: str) -> list[StreamChunk]:
    return [StreamChunk(kind="text", text=p) for p in parts] + [
        StreamChunk(kind="done", eval_count=len(parts), eval_duration_ms=10.0, prompt_eval_count=42)
    ]


def tool_call(query: Any, name: str = "web_search") -> list[StreamChunk]:
    return [
        StreamChunk(kind="tool_calls", tool_calls=[ToolCall(name=name, arguments={"query": query})]),
        StreamChunk(kind="done", eval_count=1, eval_duration_ms=1.0, prompt_eval_count=10),
    ]


def memory_ops(*operations: dict[str, Any]) -> list[StreamChunk]:
    """What the local model returns for a memory-extraction request (an update_memory tool call)."""
    return [
        StreamChunk(kind="tool_calls", tool_calls=[ToolCall(name="update_memory", arguments={"operations": list(operations)})]),
        StreamChunk(kind="done", eval_count=1, eval_duration_ms=1.0, prompt_eval_count=10),
    ]


class FakeLLM:
    """Scripted LLM: each stream_chat call consumes the next script (chunks or an exception)."""

    name = "fake"

    def __init__(self, scripts: list[Script] | None = None, *, delay: float = 0.0, reachable: bool = True) -> None:
        self.scripts: list[Script] = list(scripts or [])
        self.delay = delay
        self.reachable = reachable
        self.calls: list[dict[str, Any]] = []
        self.default: Script = text("Varsayılan yanıt.")

    async def health(self) -> BackendHealth:
        return BackendHealth(reachable=self.reachable, version="test")

    async def list_models(self) -> list[str]:
        if not self.reachable:
            raise LLMUnavailable("down")
        return ["qwen3.5:9b"]

    async def has_model(self, model: str) -> bool:
        return model in await self.list_models()

    async def loaded_models(self) -> list[dict[str, Any]]:
        return [{"name": "qwen3.5:9b"}]

    async def stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: GenerationOptions,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append({"model": model, "messages": messages, "tools": tools, "options": options})
        script = self.scripts.pop(0) if self.scripts else self.default
        if isinstance(script, Exception):
            raise script
        for chunk in script:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def complete(self, model: str, messages: list[dict[str, Any]], options: GenerationOptions) -> str:
        parts = []
        async for chunk in self.stream_chat(model, messages, options):
            if chunk.kind == "text":
                parts.append(chunk.text)
        return "".join(parts)

    async def warm_up(self, model: str, keep_alive_minutes: int) -> None:
        return None

    async def unload(self, model: str) -> None:
        return None

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        yield {"status": "success"}


class FakeSearchProvider:
    name = "fake-search"

    def __init__(self, results: list[SearchResult] | None = None, *, fail: bool = False) -> None:
        self.results = results or []
        self.fail = fail
        self.queries: list[str] = []

    async def available(self) -> bool:
        return True

    async def search(self, query: str, *, max_results: int, language: str | None, news: bool) -> list[SearchResult]:
        from app.research.providers.base import SearchProviderError

        self.queries.append(query)
        if self.fail:
            raise SearchProviderError("boom")
        return self.results[:max_results]


class FakeMic:
    """Stands in for MicCapture: tests push 16 kHz float32 frames into `queue`."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue()
        self.error: str | None = None
        self.peak_since_start = 0.5
        self.running = False

    def start(self, device_name: str | None = None) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def drain(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()


class FakePlayer:
    """Stands in for AudioPlayer: records what would be played and simulates playback time."""

    def __init__(self, seconds_per_chunk: float = 0.01) -> None:
        self.played: list[tuple[int, int]] = []
        self._started = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._spc = seconds_per_chunk
        self._pending = 0

    def reset_started(self) -> None:
        self._started.clear()

    def enqueue(self, audio: np.ndarray, rate: int, device_name: str | None = None) -> None:
        self.played.append((len(audio), rate))
        self._started.set()
        self._idle.clear()
        self._pending += 1
        asyncio.get_running_loop().call_later(self._spc * self._pending, self._done_one)

    def _done_one(self) -> None:
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self._idle.set()

    async def wait_started(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._started.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def stop(self) -> None:
        self._pending = 0
        self._idle.set()

    def close(self) -> None:
        self.stop()


class FakeTTS:
    name = "fake-tts"
    sample_rate = 22_050

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def available(self) -> bool:
        return True

    async def synthesize(self, text_: str, speed: float = 1.0) -> np.ndarray:
        self.spoken.append(text_)
        return np.zeros(2205, dtype=np.float32)

    async def warm_up(self) -> None:
        return None
