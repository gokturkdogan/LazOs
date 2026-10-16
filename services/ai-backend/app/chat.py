"""Conversation turn orchestration: routing → (research) → streaming LLM → (speech) → persistence."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.conversations.store import ConversationStore, Message
from app.core.config import UserSettings
from app.core.events import EventHub
from app.core.metrics import MetricsLog, TurnMetrics, now_ms
from app.core.state import AIState, StateMachine
from app.feedback.service import FeedbackService
from app.inference.base import GenerationOptions, LLMBackend, LLMError, LLMUnavailable, ModelMissing, ToolCall
from app.inference.context import build_context, estimate_tokens, stamp
from app.inference.prompts import (
    RESEARCH_FAILED,
    RESEARCH_RULES,
    SUMMARIZE,
    WEB_SEARCH_TOOL,
    build_system_prompt,
)
from app.inference.reasoning import needs_deep_reasoning
from app.memory.intent import is_episodic, is_forget, is_remember
from app.memory.service import SEARCH_MEMORY_TOOL, MemoryService, TurnMemory
from app.research.router import RouteDecision, build_search_query, route
from app.research.service import ResearchResult, ResearchService, parse_tool_args
from app.speech.audio_io import AudioPlayer
from app.speech.speaker import SpeechSession
from app.speech.tts import TTSManager

log = logging.getLogger("lazos.chat")

MAX_SEARCHES_PER_TURN = 2
MAX_TOOL_ROUNDS = 3
_CITE = re.compile(r"\[(\d+)\]")

FRIENDLY_ERRORS = {
    "llm_unavailable": "Yerel yapay zekâ motoruna (Ollama) ulaşılamıyor. Ollama'nın çalıştığından emin ol.",
    "model_missing": "Dil modeli yüklü değil. Kurulum ekranından modeli indirebilirsin.",
    "llm_timeout": "Yerel model zamanında yanıt vermedi. Tekrar dener misin?",
    "llm_error": "Yanıt üretilirken bir hata oluştu.",
    "empty_response": "Model boş bir yanıt döndürdü. Tekrar dener misin?",
}


def make_title(text: str, limit: int = 48) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(lazos[,!.\s]+)", "", text, flags=re.IGNORECASE)
    if len(text) <= limit:
        return text[:1].upper() + text[1:] if text else "Yeni konuşma"
    cut = text.rfind(" ", 0, limit)
    return (text[: cut if cut > 20 else limit]).rstrip(",.;:") + "…"


@dataclass
class TurnRequest:
    text: str
    conversation_id: str | None
    origin: str = "text"  # "text" | "voice"
    speak: bool = False
    stt_ms: float | None = None


class ChatService:
    def __init__(
        self,
        *,
        store: ConversationStore,
        llm: LLMBackend,
        research: ResearchService,
        hub: EventHub,
        state: StateMachine,
        tts: TTSManager,
        player: AudioPlayer,
        metrics: MetricsLog,
        settings: Callable[[], UserSettings],
        memory: MemoryService | None = None,
        feedback: FeedbackService | None = None,
    ) -> None:
        self.store = store
        self.memory = memory
        self.feedback = feedback
        self.llm = llm
        self.research = research
        self.hub = hub
        self.state = state
        self.tts = tts
        self.player = player
        self.metrics = metrics
        self.settings = settings
        self.current_conversation_id: str | None = None
        self.idle_state: Callable[[], AIState] = lambda: AIState.IDLE
        self._turn: asyncio.Task[None] | None = None
        self._speech: SpeechSession | None = None
        self._summarizer: asyncio.Task[None] | None = None
        self._error_reset: asyncio.TimerHandle | None = None

    # ── public API ─────────────────────────────────────────────────────────
    @property
    def busy(self) -> bool:
        return self._turn is not None and not self._turn.done()

    def tools_for(self, settings: UserSettings) -> list[dict[str, Any]] | None:
        """Tool set offered to the model. Kept identical across turns so the prompt prefix stays cached."""
        tools: list[dict[str, Any]] = []
        if settings.research_mode != "off":
            tools.append(WEB_SEARCH_TOOL)
        if self.memory and settings.memory_enabled:
            tools.append(SEARCH_MEMORY_TOOL)
        return tools or None

    def system_prompt(self, *, has_tools: bool) -> str:
        """Identical for text and voice turns (the mode travels in the turn tag), so the prompt cache survives."""
        if self.memory:
            return self.memory.system_prompt(has_tools=has_tools)
        s = self.settings()
        return build_system_prompt(
            has_tools=has_tools, llm_model=s.llm_model, conversation_style=s.conversation_style, voice_style=s.voice_style
        )

    async def submit(self, req: TurnRequest) -> asyncio.Task[None]:
        if self.memory:
            self.memory.on_turn_start()
        await self.cancel()
        if self._summarizer and not self._summarizer.done():
            self._summarizer.cancel()
        self._turn = asyncio.create_task(self._run(req), name="chat-turn")
        return self._turn

    async def cancel(self) -> None:
        if self._speech:
            self._speech.cancel()
        if self.busy and self._turn:
            self._turn.cancel()
            try:
                await self._turn
            except (asyncio.CancelledError, Exception):
                pass

    def stop_speaking(self) -> None:
        if self._speech:
            self._speech.cancel()

    # ── turn ───────────────────────────────────────────────────────────────
    async def _run(self, req: TurnRequest) -> None:
        settings = self.settings()
        metrics = TurnMetrics(origin=req.origin, stt_ms=req.stt_ms)
        if self._error_reset:
            self._error_reset.cancel()

        conv = await self.store.get_conversation(req.conversation_id) if req.conversation_id else None
        created = conv is None
        if conv is None:
            conv = await self.store.create_conversation(make_title(req.text))
        self.current_conversation_id = conv.id
        history = await self.store.list_messages(conv.id, after_seq=conv.summary_upto)
        user_msg = await self.store.add_message(conv.id, "user", req.text, origin=req.origin)
        if created:
            conv = await self.store.get_conversation(conv.id) or conv

        assistant_id = uuid.uuid4().hex
        self.hub.publish({"type": "chat.user", "conversation": conv.to_dict(), "message": user_msg.to_dict()})
        self.hub.publish({"type": "chat.start", "conversation_id": conv.id, "message_id": assistant_id})
        self.state.set(AIState.THINKING, force=True)

        speech: SpeechSession | None = None
        if req.speak:
            speech = SpeechSession(
                self.tts,
                self.player,
                speed=settings.tts_speed,
                device=settings.output_device,
                on_first_audio=lambda: self._on_first_audio(metrics),
                on_error=self._on_speech_error,
            )
            self._speech = speech

        parts: list[str] = []
        research: ResearchResult | None = None
        status = "complete"
        turn_memory: TurnMemory | None = None
        try:
            if self.memory:
                if is_remember(req.text) or is_forget(req.text):
                    self.state.set(AIState.THINKING, "memory", force=True)
                turn_memory = await self.memory.prepare_turn(
                    text=req.text, conversation_id=conv.id, message=user_msg, recent=history[-4:]
                )
                self.state.set(AIState.THINKING, force=True)

            decision = route(req.text, settings.research_mode)
            if self.memory and (is_episodic(req.text) or self.memory.is_identity_question(req.text)):
                decision = RouteDecision("none", "answered from memory/identity")
            tools = self.tools_for(settings)
            log.info("turn routed", extra={"action": decision.action, "reason": decision.reason})

            system = self.system_prompt(has_tools=settings.research_mode != "off")
            stamped = stamp(req.text, datetime.fromtimestamp(user_msg.created_at).astimezone(), voice=req.origin == "voice")
            plan = build_context(
                system_prompt=system,
                summary=conv.summary,
                history=history,
                user_text=stamped,
                num_ctx=settings.num_ctx,
                reserve_for_answer=settings.max_response_tokens + 1800,  # room for research results
            )
            messages = plan.messages
            think = settings.reasoning_mode == "deep" or (
                settings.reasoning_mode == "auto" and needs_deep_reasoning(req.text)
            )
            if think:
                # Qwen3.5 thinking profile; the hidden reasoning needs a larger token budget.
                options = GenerationOptions(
                    temperature=0.6,
                    top_p=0.95,
                    num_ctx=settings.num_ctx,
                    max_tokens=max(settings.max_response_tokens, 6144),
                    keep_alive_minutes=settings.keep_alive_minutes,
                    think=True,
                    repeat_penalty=1.0,
                    presence_penalty=1.5,
                )
                self.state.set(AIState.THINKING, "reasoning", force=True)
                metrics.reasoning = True
            else:
                # Qwen3.5 non-thinking profile (chosen after a controlled comparison, see docs/CONVERSATION.md).
                options = GenerationOptions(
                    temperature=settings.temperature,
                    top_p=0.8,
                    top_k=20,
                    num_ctx=settings.num_ctx,
                    max_tokens=settings.max_response_tokens,
                    keep_alive_minutes=settings.keep_alive_minutes,
                    repeat_penalty=1.0,
                    presence_penalty=1.5,
                )
            log.info("generation profile", extra={"think": think})

            # Lowest priority context: approved style examples from the owner's corrections (data, not instructions).
            examples = await self.feedback.examples_for(req.text) if self.feedback else []
            if examples:
                metrics.examples = len(examples)
            memory_block = turn_memory.tool_content if turn_memory else None
            if examples:
                rendered = self.feedback.render_examples(examples)  # type: ignore[union-attr]
                memory_block = f"{memory_block}\n\n{rendered}" if memory_block else rendered
            if memory_block:
                messages += self._exchange("search_memory", {"query": req.text[:200]}, memory_block)

            searches = 0
            tool_rounds = 0
            if decision.action == "search":
                query = build_search_query(req.text)
                research = await self._do_research(query, assistant_id, settings, metrics)
                messages += self._tool_exchange(query, research)
                searches += 1

            while True:
                tool_calls: list[ToolCall] = []
                async for chunk in self.llm.stream_chat(settings.llm_model, messages, options, tools):
                    if chunk.kind == "text":
                        if not parts:
                            metrics.mark_first_token()
                        parts.append(chunk.text)
                        self.hub.publish({"type": "chat.delta", "message_id": assistant_id, "text": chunk.text})
                        if speech:
                            speech.feed(chunk.text)
                    elif chunk.kind == "tool_calls":
                        tool_calls.extend(chunk.tool_calls)
                    elif chunk.kind == "done":
                        metrics.tokens += chunk.eval_count or 0
                        metrics.prompt_tokens = chunk.prompt_eval_count
                        if chunk.eval_count and chunk.eval_duration_ms:
                            metrics.tokens_per_s = round(chunk.eval_count / (chunk.eval_duration_ms / 1000), 1)

                call = next((c for c in tool_calls if c.name in ("web_search", "search_memory")), None)
                answered = bool("".join(parts).strip())
                if call and not answered and tool_rounds < MAX_TOOL_ROUNDS:
                    tool_rounds += 1
                    args = parse_tool_args(call.arguments)
                    query = args.query if args else req.text[:200]
                    if call.name == "search_memory" and self.memory:
                        found = await self.memory.search_tool(query, conv.id)
                        messages += self._exchange("search_memory", {"query": query}, found)
                        continue
                    if call.name == "web_search" and searches < MAX_SEARCHES_PER_TURN:
                        research = await self._do_research(query, assistant_id, settings, metrics)
                        messages += self._tool_exchange(query, research)
                        searches += 1
                        continue
                if call and not answered:
                    # Tool budget exhausted: answer with what we have, without tools.
                    tools = None
                    continue
                break

            content = "".join(parts).strip()
            if not content:
                raise LLMError("empty_response")
        except asyncio.CancelledError:
            status = "interrupted"
            if speech:
                speech.cancel()
            await self._finalize(conv.id, assistant_id, parts, research, metrics, req, status)
            self.state.set(self.idle_state(), force=True)
            raise
        except LLMError as exc:
            if speech:
                speech.cancel()
            code = "empty_response" if str(exc) == "empty_response" else exc.code
            log.error("turn failed", extra={"code": code, "error": str(exc)})
            if parts:
                await self._finalize(conv.id, assistant_id, parts, research, metrics, req, "interrupted")
            self._fail(assistant_id, code, FRIENDLY_ERRORS.get(code, str(exc)))
            return
        except Exception:
            if speech:
                speech.cancel()
            log.exception("unexpected turn failure")
            self._fail(assistant_id, "internal", "Beklenmeyen bir hata oluştu; ayrıntılar günlük dosyasında.")
            return

        if speech:
            speech.finish()
            # Short answers can finish generating before the first sentence plays; wait briefly so
            # the turn metrics include real time-to-first-audio.
            await speech.wait_first_audio(timeout=5.0)
        await self._finalize(conv.id, assistant_id, parts, research, metrics, req, status)
        if self.memory:
            self.memory.after_turn(
                conversation_id=conv.id, message=user_msg, handled=bool(turn_memory and turn_memory.handled_explicitly)
            )

        if speech:
            try:
                await speech.wait()
            except asyncio.CancelledError:
                speech.cancel()
                raise
            finally:
                self._speech = None
        self.state.set(self.idle_state(), force=True)

        if plan.dropped:
            self._summarizer = asyncio.create_task(self._summarize(conv.id, plan.dropped, history, settings))

    # ── helpers ────────────────────────────────────────────────────────────
    def _on_first_audio(self, metrics: TurnMetrics) -> None:
        metrics.mark_first_audio()
        self.state.set(AIState.SPEAKING, force=True)

    def _on_speech_error(self, message: str) -> None:
        self.hub.publish({"type": "error", "code": "tts_failed", "message": f"Sesli yanıt verilemedi: {message}"})

    def _fail(self, message_id: str, code: str, message: str) -> None:
        self.hub.publish({"type": "chat.error", "message_id": message_id, "code": code, "message": message})
        self.state.set(AIState.ERROR, code, force=True)
        loop = asyncio.get_running_loop()
        self._error_reset = loop.call_later(3.0, lambda: self.state.set(self.idle_state(), force=True))

    async def _finalize(
        self,
        conv_id: str,
        message_id: str,
        parts: list[str],
        research: ResearchResult | None,
        metrics: TurnMetrics,
        req: TurnRequest,
        status: str,
    ) -> Message | None:
        content = "".join(parts).strip()
        if not content:
            self.hub.publish({"type": "chat.done", "message_id": message_id, "status": status, "message": None})
            return None
        sources = []
        if research and research.ok:
            cited = {int(n) for n in _CITE.findall(content)}
            sources = [{**s.public(), "cited": s.id in cited} for s in research.sources]
        data = metrics.finish()
        self.metrics.add(data)
        msg = await self.store.add_message(
            conv_id,
            "assistant",
            content,
            origin=req.origin,
            status=status,
            sources=sources,
            metrics=data,
            message_id=message_id,
        )
        self.hub.publish({"type": "chat.done", "message_id": message_id, "status": status, "message": msg.to_dict()})
        self.hub.publish({"type": "metrics", "turn": data})
        return msg

    @staticmethod
    def _exchange(name: str, arguments: dict[str, Any], content: str) -> list[dict[str, Any]]:
        return [
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]},
            {"role": "tool", "tool_name": name, "content": content},
        ]

    @staticmethod
    def _tool_exchange(query: str, research: ResearchResult) -> list[dict[str, Any]]:
        if research.ok:
            content = f"{RESEARCH_RULES}\n\n{research.as_prompt()}"
        else:
            content = RESEARCH_FAILED.format(reason=research.error or "bilinmeyen hata")
        return [
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": query}}}]},
            {"role": "tool", "tool_name": "web_search", "content": content},
        ]

    async def _do_research(self, query: str, message_id: str, settings: UserSettings, metrics: TurnMetrics) -> ResearchResult:
        started = now_ms()
        self.state.set(AIState.THINKING, "research", force=True)
        self.hub.publish({"type": "chat.research", "message_id": message_id, "status": "searching", "query": query})
        result = await self.research.research(query, max_sources=settings.max_sources)
        metrics.research_ms = round((metrics.research_ms or 0) + now_ms() - started, 1)
        self.hub.publish(
            {
                "type": "chat.research",
                "message_id": message_id,
                "status": "done" if result.ok else "failed",
                "query": query,
                "provider": result.provider,
                "error": result.error,
                "sources": [s.public() for s in result.sources],
            }
        )
        self.state.set(AIState.THINKING, force=True)
        return result

    async def _summarize(self, conv_id: str, dropped: list[Message], history: list[Message], settings: UserSettings) -> None:
        """Fold old messages into the rolling summary, leaving ~half the budget free afterwards."""
        try:
            conv = await self.store.get_conversation(conv_id)
            if conv is None:
                return
            to_fold = list(dropped)
            remaining = history[len(dropped) :]
            budget = (settings.num_ctx - settings.max_response_tokens - 1800) // 2
            while remaining and sum(estimate_tokens(m.content) for m in remaining) > budget:
                to_fold.append(remaining.pop(0))
            transcript = "\n".join(f"{'Kullanıcı' if m.role == 'user' else 'LAZOS'}: {m.content[:1500]}" for m in to_fold)
            prior = f"Mevcut özet:\n{conv.summary}\n\n" if conv.summary else ""
            summary = await self.llm.complete(
                settings.llm_model,
                [{"role": "system", "content": SUMMARIZE}, {"role": "user", "content": prior + transcript}],
                GenerationOptions(
                    temperature=0.3, max_tokens=320, num_ctx=settings.num_ctx, keep_alive_minutes=settings.keep_alive_minutes
                ),
            )
            if summary:
                await self.store.set_summary(conv_id, summary, to_fold[-1].seq)
                log.info("conversation summarized", extra={"conversation": conv_id, "folded": len(to_fold)})
        except asyncio.CancelledError:
            raise
        except (LLMUnavailable, ModelMissing):
            pass
        except Exception:
            log.exception("summarization failed")
