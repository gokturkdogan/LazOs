"""Microphone capture and speaker playback (PortAudio via sounddevice).

Audio levels reported to the UI are computed from the exact samples being captured / handed to the
output device, so the AI core reacts to real sound — never simulated values.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

log = logging.getLogger("lazos.audio")

MIC_RATE = 16_000
MIC_BLOCK = 512


class AudioDeviceError(RuntimeError):
    pass


def _sd():  # type: ignore[no-untyped-def]
    import sounddevice as sd

    return sd


def refresh_devices() -> None:
    """Re-initialise PortAudio so hot-plugged devices (headsets, AirPods) become visible."""
    sd = _sd()
    try:
        sd._terminate()
        sd._initialize()
    except Exception:  # pragma: no cover - best effort
        log.warning("PortAudio re-initialisation failed", exc_info=True)


def list_devices() -> dict:
    sd = _sd()
    devices = sd.query_devices()
    default_in, default_out = sd.default.device
    inputs, outputs = [], []
    for idx, dev in enumerate(devices):
        entry = {"index": idx, "name": dev["name"], "default": False}
        if dev["max_input_channels"] > 0:
            inputs.append({**entry, "default": idx == default_in})
        if dev["max_output_channels"] > 0:
            outputs.append({**entry, "default": idx == default_out})
    return {"inputs": inputs, "outputs": outputs}


def resolve_device(name: str | None, kind: str) -> int | None:
    """Map a stored device name to the current PortAudio index (indices change when devices change)."""
    if not name:
        return None
    sd = _sd()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for idx, dev in enumerate(sd.query_devices()):
        if dev["name"] == name and dev[key] > 0:
            return idx
    log.warning("configured %s device %r not found; using system default", kind, name)
    return None


def rms_level(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float32))))
    # Map roughly -60 dBFS..-10 dBFS to 0..1 for a perceptually useful meter.
    db = 20 * np.log10(max(rms, 1e-6))
    return float(np.clip((db + 60) / 50, 0.0, 1.0))


class MicCapture:
    """Streams 32 ms float32 frames at 16 kHz into an asyncio queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_level: Callable[[float], None]) -> None:
        self._loop = loop
        self._on_level = on_level
        self._stream = None
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=400)
        self.last_frame_at = 0.0
        self.peak_since_start = 0.0
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._stream is not None and self._stream.active

    def start(self, device_name: str | None = None) -> None:
        if self.running:
            return
        sd = _sd()
        self.error = None
        self.peak_since_start = 0.0

        def callback(indata, frames, _time, status):  # type: ignore[no-untyped-def]
            if status.input_overflow:
                log.debug("mic overflow")
            frame = indata[:, 0].copy()
            self.last_frame_at = time.monotonic()
            self._loop.call_soon_threadsafe(self._push, frame)

        def finished() -> None:
            self._loop.call_soon_threadsafe(self._on_finished)

        try:
            self._stream = sd.InputStream(
                samplerate=MIC_RATE,
                blocksize=MIC_BLOCK,
                channels=1,
                dtype="float32",
                device=resolve_device(device_name, "input"),
                callback=callback,
                finished_callback=finished,
                latency="low",
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioDeviceError(f"Mikrofon açılamadı: {exc}") from exc
        log.info("microphone started")

    def _push(self, frame: np.ndarray) -> None:
        level = rms_level(frame)
        self.peak_since_start = max(self.peak_since_start, float(np.max(np.abs(frame))) if frame.size else 0.0)
        self._on_level(level)
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(frame)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def _on_finished(self) -> None:
        if self._stream is not None and not self._stream.active and not self._stopping:
            self.error = "Mikrofon bağlantısı kesildi"
            log.warning("microphone stream ended unexpectedly")

    _stopping = False

    def drain(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()

    def stop(self) -> None:
        self._stopping = True
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            log.warning("error closing microphone", exc_info=True)
        finally:
            self._stream = None
            self._stopping = False
            self.drain()
            self._on_level(0.0)


class AudioPlayer:
    """Callback-driven output stream fed from a sample queue; reports the level of what is playing."""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_level: Callable[[float], None]) -> None:
        self._loop = loop
        self._on_level = on_level
        self._stream = None
        self._rate = 0
        self._device: str | None = None
        self._chunks: deque[np.ndarray] = deque()
        self._offset = 0
        self._lock = threading.Lock()
        self._idle = asyncio.Event()
        self._idle.set()
        self._started = asyncio.Event()
        self._last_level_at = 0.0
        self._close_handle: asyncio.TimerHandle | None = None

    @property
    def playing(self) -> bool:
        return not self._idle.is_set()

    def _ensure_stream(self, rate: int, device_name: str | None) -> None:
        if self._stream is not None and self._rate == rate and self._device == device_name and self._stream.active:
            return
        self._close_stream()
        sd = _sd()

        def callback(outdata, frames, _time, status):  # type: ignore[no-untyped-def]
            out = outdata[:, 0]
            filled = 0
            with self._lock:
                while filled < frames and self._chunks:
                    chunk = self._chunks[0]
                    take = min(frames - filled, len(chunk) - self._offset)
                    out[filled : filled + take] = chunk[self._offset : self._offset + take]
                    filled += take
                    self._offset += take
                    if self._offset >= len(chunk):
                        self._chunks.popleft()
                        self._offset = 0
                empty = not self._chunks
            if filled < frames:
                out[filled:] = 0.0
            level = rms_level(out[:filled]) if filled else 0.0
            self._loop.call_soon_threadsafe(self._report, level, filled > 0, empty)

        try:
            self._stream = sd.OutputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                blocksize=1024,
                device=resolve_device(device_name, "output"),
                callback=callback,
                latency="low",
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioDeviceError(f"Ses çıkışı açılamadı: {exc}") from exc
        self._rate = rate
        self._device = device_name

    def _report(self, level: float, had_audio: bool, empty: bool) -> None:
        if had_audio and not self._started.is_set():
            self._started.set()
        now = time.monotonic()
        if now - self._last_level_at >= 1 / 40 or (empty and not had_audio):
            self._last_level_at = now
            self._on_level(level)
        if empty and not had_audio and not self._idle.is_set():
            self._idle.set()
            self._on_level(0.0)
            self._schedule_close()

    def _schedule_close(self) -> None:
        if self._close_handle:
            self._close_handle.cancel()
        # Release the device after a pause so device changes are picked up and CPU stays idle.
        self._close_handle = self._loop.call_later(3.0, self._close_if_idle)

    def _close_if_idle(self) -> None:
        if self._idle.is_set():
            self._close_stream()

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.warning("error closing output stream", exc_info=True)
            self._stream = None

    def enqueue(self, audio: np.ndarray, rate: int, device_name: str | None = None) -> None:
        if audio.size == 0:
            return
        if self._close_handle:
            self._close_handle.cancel()
            self._close_handle = None
        if self._stream is not None and self._rate != rate and self.playing:
            # Different engine sample rate mid-utterance: resample instead of reopening the device.
            audio = np.interp(
                np.linspace(0, len(audio), int(len(audio) * self._rate / rate), endpoint=False),
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)
            rate = self._rate
        self._ensure_stream(rate, device_name)
        with self._lock:
            self._chunks.append(np.ascontiguousarray(audio, dtype=np.float32))
        self._idle.clear()

    async def wait_started(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._started.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def reset_started(self) -> None:
        self._started.clear()

    def stop(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._offset = 0
        self._idle.set()
        self._on_level(0.0)
        self._schedule_close()

    def close(self) -> None:
        self.stop()
        self._close_stream()
