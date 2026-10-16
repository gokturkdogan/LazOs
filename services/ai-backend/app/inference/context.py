"""Context window management: fit system prompt + summary + recent history into the token budget."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.conversations.store import Message
from app.inference.prompts import turkish_datetime

# Turkish is agglutinative and tokenizes denser than English; ~3.2 chars/token is a safe estimate
# for Qwen's tokenizer on mixed Turkish/English text.
CHARS_PER_TOKEN = 3.2
MESSAGE_OVERHEAD_TOKENS = 6


def stamp(text: str, when: datetime | None = None, *, voice: bool = False) -> str:
    """Prefix a user turn with the time it was sent and, for spoken turns, a "sesli" tag that switches the
    model to its spoken style. The same tag is reproduced for history messages, so earlier turns render
    identically on every request and stay in Ollama's prompt cache."""
    mode = " · sesli" if voice else ""
    return f"[Şu an: {turkish_datetime(when)}{mode}]\n{text}"


def render_history(msg: Message) -> str:
    if msg.role == "user":
        return stamp(msg.content, datetime.fromtimestamp(msg.created_at).astimezone(), voice=msg.origin == "voice")
    return msg.content


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + MESSAGE_OVERHEAD_TOKENS


@dataclass
class ContextPlan:
    messages: list[dict[str, Any]]
    dropped: list[Message]  # older messages that did not fit (candidates for summarization)
    estimated_tokens: int


def build_context(
    *,
    system_prompt: str,
    summary: str | None,
    history: list[Message],
    user_text: str,
    num_ctx: int,
    reserve_for_answer: int,
    extra_system: str | None = None,
) -> ContextPlan:
    """Newest messages are kept first; older ones are dropped once the budget is exhausted."""
    system = system_prompt
    if summary:
        system += f"\n\nÖnceki konuşmanın özeti:\n{summary}"
    budget = num_ctx - reserve_for_answer
    used = estimate_tokens(system) + estimate_tokens(user_text)
    if extra_system:
        used += estimate_tokens(extra_system)

    kept: list[Message] = []
    dropped: list[Message] = []
    for msg in reversed(history):
        cost = estimate_tokens(msg.content)
        if not dropped and used + cost <= budget:
            kept.append(msg)
            used += cost
        else:
            dropped.append(msg)
    kept.reverse()
    dropped.reverse()

    # Never start the window with an orphaned assistant reply.
    while kept and kept[0].role == "assistant":
        dropped.append(kept.pop(0))

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages += [{"role": m.role, "content": render_history(m)} for m in kept if m.content.strip()]
    if extra_system:
        messages.append({"role": "system", "content": extra_system})
    messages.append({"role": "user", "content": user_text})
    return ContextPlan(messages=messages, dropped=dropped, estimated_tokens=used)
