"""Lightweight per-turn latency metrics."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field

log = logging.getLogger("lazos.metrics")


def now_ms() -> float:
    return time.perf_counter() * 1000.0


@dataclass
class TurnMetrics:
    origin: str = "text"
    started_ms: float = field(default_factory=now_ms)
    stt_ms: float | None = None
    research_ms: float | None = None
    ttft_ms: float | None = None
    tokens: int = 0
    tokens_per_s: float | None = None
    prompt_tokens: int | None = None
    tts_first_audio_ms: float | None = None
    total_ms: float | None = None

    def mark_first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = round(now_ms() - self.started_ms, 1)

    def mark_first_audio(self) -> None:
        if self.tts_first_audio_ms is None:
            self.tts_first_audio_ms = round(now_ms() - self.started_ms, 1)

    def finish(self) -> dict:
        self.total_ms = round(now_ms() - self.started_ms, 1)
        data = {k: v for k, v in asdict(self).items() if k != "started_ms" and v is not None}
        log.info("turn metrics", extra=data)
        return data


class MetricsLog:
    """Keeps the most recent turns for the status endpoint / HUD."""

    def __init__(self, size: int = 50) -> None:
        self.recent: deque[dict] = deque(maxlen=size)

    def add(self, metrics: dict) -> None:
        self.recent.append(metrics)

    def last(self) -> dict | None:
        return self.recent[-1] if self.recent else None
