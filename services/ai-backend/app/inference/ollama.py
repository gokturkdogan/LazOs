"""Ollama implementation of `LLMBackend` (local HTTP API on 127.0.0.1:11434)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.inference.base import (
    BackendHealth,
    GenerationOptions,
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    ModelMissing,
    StreamChunk,
    ToolCall,
)

log = logging.getLogger("lazos.ollama")


class OllamaBackend:
    name = "ollama"

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        # Generous read timeout: the first request may load several GB into memory.
        self._client = httpx.AsyncClient(base_url=self._base, timeout=httpx.Timeout(connect=3.0, read=180.0, write=10.0, pool=5.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── status ─────────────────────────────────────────────────────────────
    async def health(self) -> BackendHealth:
        try:
            r = await self._client.get("/api/version", timeout=2.0)
            r.raise_for_status()
            return BackendHealth(reachable=True, version=r.json().get("version"))
        except Exception as exc:
            return BackendHealth(reachable=False, error=str(exc) or exc.__class__.__name__)

    async def list_models(self) -> list[str]:
        try:
            r = await self._client.get("/api/tags", timeout=5.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama is not reachable: {exc}") from exc
        return [m["name"] for m in r.json().get("models", [])]

    async def has_model(self, model: str) -> bool:
        names = await self.list_models()
        wanted = model if ":" in model else f"{model}:latest"
        return wanted in names

    async def loaded_models(self) -> list[dict[str, Any]]:
        try:
            r = await self._client.get("/api/ps", timeout=3.0)
            r.raise_for_status()
            return r.json().get("models", [])
        except httpx.HTTPError:
            return []

    # ── generation ─────────────────────────────────────────────────────────
    @staticmethod
    def _payload(
        model: str,
        messages: list[dict[str, Any]],
        options: GenerationOptions,
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "think": options.think,
            "keep_alive": f"{options.keep_alive_minutes}m",
            "options": {
                "temperature": options.temperature,
                "top_p": options.top_p,
                "top_k": options.top_k,
                "num_ctx": options.num_ctx,
                "num_predict": options.max_tokens,
                **({"repeat_penalty": options.repeat_penalty} if options.repeat_penalty is not None else {}),
                **({"presence_penalty": options.presence_penalty} if options.presence_penalty is not None else {}),
            },
        }
        if tools:
            payload["tools"] = tools
        return payload

    async def stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: GenerationOptions,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._payload(model, messages, options, tools, stream=True)
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code == 404:
                    body = (await resp.aread()).decode(errors="replace")
                    raise ModelMissing(f"Model '{model}' is not installed in Ollama ({body.strip()})")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise LLMError(f"Ollama error {resp.status_code}: {body.strip()[:300]}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise LLMError(str(data["error"]))
                    msg = data.get("message") or {}
                    if msg.get("tool_calls"):
                        calls = []
                        for call in msg["tool_calls"]:
                            fn = call.get("function") or {}
                            args = fn.get("arguments") or {}
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {"_raw": args}
                            calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
                        yield StreamChunk(kind="tool_calls", tool_calls=calls)
                    if msg.get("content"):
                        yield StreamChunk(kind="text", text=msg["content"])
                    if data.get("done"):
                        yield StreamChunk(
                            kind="done",
                            eval_count=data.get("eval_count"),
                            eval_duration_ms=(data.get("eval_duration") or 0) / 1e6 or None,
                            prompt_eval_count=data.get("prompt_eval_count"),
                        )
                        return
        except httpx.ConnectError as exc:
            raise LLMUnavailable("Ollama is not running") from exc
        except httpx.ReadTimeout as exc:
            raise LLMTimeout("The local model stopped responding") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Connection to Ollama failed: {exc}") from exc

    async def complete(self, model: str, messages: list[dict[str, Any]], options: GenerationOptions) -> str:
        parts: list[str] = []
        async for chunk in self.stream_chat(model, messages, options):
            if chunk.kind == "text":
                parts.append(chunk.text)
        return "".join(parts).strip()

    async def warm_up(self, model: str, keep_alive_minutes: int) -> None:
        """Loading with an empty message list makes Ollama load the weights without generating."""
        try:
            r = await self._client.post("/api/chat", json={"model": model, "messages": [], "keep_alive": f"{keep_alive_minutes}m"})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("warm-up failed: %s", exc)

    async def unload(self, model: str) -> None:
        try:
            await self._client.post("/api/chat", json={"model": model, "messages": [], "keep_alive": 0})
        except httpx.HTTPError:
            pass

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        try:
            async with self._client.stream(
                "POST", "/api/pull", json={"model": model, "stream": True}, timeout=httpx.Timeout(10.0, read=600.0)
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.strip():
                        data = json.loads(line)
                        if "error" in data:
                            raise LLMError(data["error"])
                        yield data
        except httpx.ConnectError as exc:
            raise LLMUnavailable("Ollama is not running") from exc
