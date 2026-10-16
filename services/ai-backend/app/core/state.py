"""The LAZOS AI state machine. Every state maps to a colour of the AI core in the UI."""

from __future__ import annotations

import logging
from enum import StrEnum

from app.core.events import EventHub

log = logging.getLogger("lazos.state")


class AIState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


_ALLOWED: dict[AIState, set[AIState]] = {
    AIState.IDLE: {AIState.LISTENING, AIState.THINKING, AIState.SPEAKING, AIState.ERROR},
    AIState.LISTENING: {AIState.TRANSCRIBING, AIState.THINKING, AIState.IDLE, AIState.ERROR},
    AIState.TRANSCRIBING: {AIState.THINKING, AIState.LISTENING, AIState.IDLE, AIState.ERROR},
    AIState.THINKING: {AIState.SPEAKING, AIState.LISTENING, AIState.IDLE, AIState.ERROR},
    AIState.SPEAKING: {AIState.THINKING, AIState.LISTENING, AIState.IDLE, AIState.ERROR},
    AIState.ERROR: {AIState.IDLE, AIState.LISTENING, AIState.THINKING},
}


class InvalidTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self, hub: EventHub) -> None:
        self._hub = hub
        self._state = AIState.IDLE
        self._detail: str | None = None
        self._publish()

    @property
    def state(self) -> AIState:
        return self._state

    @staticmethod
    def can_transition(src: AIState, dst: AIState) -> bool:
        return src == dst or dst in _ALLOWED[src]

    def set(self, dst: AIState, detail: str | None = None, *, force: bool = False) -> None:
        if dst == self._state and detail == self._detail:
            return
        if not force and not self.can_transition(self._state, dst):
            raise InvalidTransition(f"{self._state} -> {dst}")
        log.debug("state %s -> %s (%s)", self._state, dst, detail)
        self._state = dst
        self._detail = detail
        self._publish()

    def _publish(self) -> None:
        self._hub.publish({"type": "state", "state": self._state.value, "detail": self._detail}, sticky=True)
