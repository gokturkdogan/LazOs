"""Sentence-level streaming speech: LLM deltas → sentences → TTS → playback, pipelined.

While sentence N plays, sentence N+1 is already being synthesized, so speech starts after the first
sentence instead of after the whole answer.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.speech.audio_io import AudioPlayer
from app.speech.text import SentenceSplitter
from app.speech.tts import TTSError, TTSManager

log = logging.getLogger("lazos.speaker")

_END = object()


class SpeechSession:
    """One spoken answer. Create per turn; call feed() with deltas, finish() at the end, then wait()."""

    def __init__(
        self,
        tts: TTSManager,
        player: AudioPlayer,
        *,
        speed: float,
        device: str | None,
        on_first_audio: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._tts = tts
        self._player = player
        self._speed = speed
        self._device = device
        self._on_first_audio = on_first_audio
        self._on_error = on_error
        self._splitter = SentenceSplitter()
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._cancelled = False
        self.spoke_anything = False
        self._player.reset_started()
        self._worker = asyncio.create_task(self._run(), name="speech-session")
        self._watch = asyncio.create_task(self._watch_first_audio(), name="speech-first-audio")

    def feed(self, delta: str) -> None:
        if self._cancelled:
            return
        for sentence in self._splitter.feed(delta):
            self._queue.put_nowait(sentence)

    def say(self, text: str) -> None:
        self.feed(text)
        self.finish()

    def finish(self) -> None:
        if self._cancelled:
            return
        for sentence in self._splitter.flush():
            self._queue.put_nowait(sentence)
        self._queue.put_nowait(_END)

    async def _run(self) -> None:
        failures = 0
        while True:
            item = await self._queue.get()
            if item is _END or self._cancelled:
                break
            text = str(item)
            try:
                audio, rate, _engine = await self._tts.synthesize(text, self._speed)
            except TTSError as exc:
                failures += 1
                log.error("tts failed", extra={"error": str(exc)})
                if failures >= 2:
                    self._on_error(str(exc))
                    break
                continue
            if self._cancelled:
                break
            self._player.enqueue(audio, rate, self._device)
            self.spoke_anything = True
        if not self._cancelled and self.spoke_anything:
            await self._player.wait_idle()

    async def _watch_first_audio(self) -> None:
        while not self._cancelled:
            if await self._player.wait_started(0.5):
                self._on_first_audio()
                return
            if self._worker.done():
                return

    async def wait_first_audio(self, timeout: float) -> None:
        """Wait until playback has started (or nothing will be spoken)."""
        try:
            await asyncio.wait_for(asyncio.shield(self._watch), timeout)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            # Only swallow the watcher's own cancellation, never the caller's.
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise

    async def wait(self) -> None:
        try:
            await self._worker
        finally:
            self._watch.cancel()

    def cancel(self) -> None:
        self._cancelled = True
        self._player.stop()
        self._worker.cancel()
        self._watch.cancel()
