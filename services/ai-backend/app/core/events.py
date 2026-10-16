"""Event hub: fan-out of typed server events to connected WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("lazos.events")

# High-frequency events that may be dropped when a client falls behind.
_LOSSY = {"audio.level"}


class EventHub:
    def __init__(self, queue_size: int = 512) -> None:
        self._clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self._queue_size = queue_size
        self._last: dict[str, dict[str, Any]] = {}

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self._queue_size)
        self._clients.add(q)
        # Replay sticky events so a (re)connecting client gets the current picture immediately.
        for event in self._last.values():
            q.put_nowait(event)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._clients.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def publish(self, event: dict[str, Any], *, sticky: bool = False) -> None:
        """Non-blocking publish. Safe to call from the event loop thread only."""
        if sticky:
            self._last[event["type"]] = event
        for q in list(self._clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                if event["type"] in _LOSSY:
                    continue
                # Drop the oldest event to make room for a meaningful one.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    log.warning("client queue saturated; dropping event", extra={"event": event["type"]})

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(self.publish, event)
