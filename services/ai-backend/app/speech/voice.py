"""Voice conversation controller (half-duplex).

Modes:
  ptt         — one utterance: listen → (VAD end or manual stop) → transcribe → answer → idle
  continuous  — loop: listen → transcribe → think → speak → listen …

Feedback-loop prevention: microphone frames are discarded whenever LAZOS is not LISTENING, and the
capture queue is flushed after playback ends (plus a short tail guard) so LAZOS never hears itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Literal

import numpy as np

from app.chat import ChatService, TurnRequest
from app.core.config import UserSettings
from app.core.events import EventHub
from app.core.state import AIState, StateMachine
from app.speech.audio_io import AudioDeviceError, MicCapture
from app.speech.stt import STTError, WhisperSTT
from app.speech.vad import SegmenterConfig, UtteranceSegmenter

log = logging.getLogger("lazos.voice")

Mode = Literal["off", "ptt", "continuous"]
ECHO_TAIL_S = 0.35
PTT_NO_SPEECH_TIMEOUT_S = 8.0
MIC_STALL_S = 2.5


class VoiceController:
    def __init__(
        self,
        *,
        mic: MicCapture,
        vad_factory: Callable[[], object],
        stt: WhisperSTT,
        chat: ChatService,
        state: StateMachine,
        hub: EventHub,
        settings: Callable[[], UserSettings],
    ) -> None:
        self.mic = mic
        self.vad_factory = vad_factory
        self.stt = stt
        self.chat = chat
        self.state = state
        self.hub = hub
        self.settings = settings
        self.mode: Mode = "off"
        self._task: asyncio.Task[None] | None = None
        self._stop_utterance = asyncio.Event()
        self._vad = None
        chat.idle_state = self._idle_state

    def _idle_state(self) -> AIState:
        return AIState.LISTENING if self.mode == "continuous" else AIState.IDLE

    @property
    def listening(self) -> bool:
        return self.state.state == AIState.LISTENING

    def _publish_mode(self) -> None:
        self.hub.publish({"type": "voice.mode", "mode": self.mode}, sticky=True)

    def _notice(self, message: str, level: str = "info") -> None:
        self.hub.publish({"type": "voice.notice", "level": level, "message": message})

    # ── commands ───────────────────────────────────────────────────────────
    async def start_ptt(self, conversation_id: str | None) -> None:
        if self.mode == "ptt" and self.listening:
            return
        if self.mode == "continuous":
            # Mic button during continuous mode = interrupt and listen now.
            await self.chat.cancel()
            return
        await self.chat.cancel()
        self._set_conversation(conversation_id)
        await self._start("ptt")

    def stop_ptt(self) -> None:
        """Manual end of utterance (second press)."""
        self._stop_utterance.set()

    async def set_continuous(self, enabled: bool, conversation_id: str | None) -> None:
        self._set_conversation(conversation_id)
        if enabled:
            if self.mode == "continuous":
                return
            await self.stop()
            await self._start("continuous")
        else:
            await self.stop()

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self.mode = "off"
        self._publish_mode()

    def _set_conversation(self, conversation_id: str | None) -> None:
        if conversation_id is not None:
            self.chat.current_conversation_id = conversation_id or None

    async def _start(self, mode: Mode) -> None:
        self.mode = mode
        self._publish_mode()
        self._task = asyncio.create_task(self._session(), name=f"voice-{mode}")

    # ── session ────────────────────────────────────────────────────────────
    async def _session(self) -> None:
        settings = self.settings()
        try:
            if not self.stt.available():
                raise STTError("Konuşma tanıma modeli yüklü değil. Kurulum ekranından yükleyebilirsin.")
            if self._vad is None:
                self._vad = await asyncio.to_thread(self.vad_factory)
            self.mic.start(settings.input_device)
            while self.mode != "off":
                audio = await self._capture_utterance()
                if audio is None:
                    if self.mode == "ptt":
                        break
                    continue
                self.state.set(AIState.TRANSCRIBING, force=True)
                transcript = await self.stt.transcribe(audio, self.settings().stt_language)
                if not transcript.text:
                    self._notice("Söylediğini anlayamadım, tekrar eder misin?")
                    if self.mode == "ptt":
                        break
                    continue
                self.hub.publish(
                    {"type": "voice.transcript", "text": transcript.text, "language": transcript.language, "stt_ms": transcript.duration_ms}
                )
                turn = await self.chat.submit(
                    TurnRequest(
                        text=transcript.text,
                        conversation_id=self.chat.current_conversation_id,
                        origin="voice",
                        speak=True,
                        stt_ms=transcript.duration_ms,
                    )
                )
                try:
                    await turn
                except asyncio.CancelledError:
                    if self._task is not None and self._task.cancelling():
                        raise
                except Exception:
                    log.exception("voice turn failed")
                if self.mode == "ptt":
                    break
                await asyncio.sleep(ECHO_TAIL_S)
        except asyncio.CancelledError:
            raise
        except (AudioDeviceError, STTError) as exc:
            log.error("voice session failed", extra={"error": str(exc)})
            self._notice(str(exc), "error")
            self.state.set(AIState.ERROR, "voice", force=True)
            await asyncio.sleep(2.5)
        except Exception:
            log.exception("voice session crashed")
            self._notice("Ses sisteminde beklenmeyen bir hata oluştu.", "error")
            self.state.set(AIState.ERROR, "voice", force=True)
            await asyncio.sleep(2.5)
        finally:
            self.mic.stop()
            if self.mode != "off":
                self.mode = "off"
                self._publish_mode()
            if not self.chat.busy:
                self.state.set(AIState.IDLE, force=True)

    async def _capture_utterance(self) -> np.ndarray | None:
        # Wait until any ongoing answer (e.g. a typed message) has finished speaking.
        while self.chat.busy:
            await asyncio.sleep(0.1)
        settings = self.settings()
        segmenter = UtteranceSegmenter(
            self._vad,
            SegmenterConfig(
                threshold=settings.vad_threshold, neg_threshold=max(0.15, settings.vad_threshold - 0.15), silence_ms=settings.vad_silence_ms
            ),
        )
        self._stop_utterance.clear()
        self.mic.drain()
        self.state.set(AIState.LISTENING, force=True)
        started = time.monotonic()
        last_frame = time.monotonic()
        buffer = np.zeros(0, dtype=np.float32)

        while True:
            if self._stop_utterance.is_set():
                audio = segmenter.force_end()
                if audio is None:
                    self._notice("Ses algılanmadı.")
                return audio
            if self.mic.error:
                raise AudioDeviceError(self.mic.error)
            if self.chat.busy:
                # A typed message started while listening — yield and resume afterwards.
                return None
            try:
                frame = await asyncio.wait_for(self.mic.queue.get(), timeout=0.25)
            except TimeoutError:
                if time.monotonic() - last_frame > MIC_STALL_S:
                    raise AudioDeviceError("Mikrofondan ses gelmiyor. Bağlantıyı veya mikrofon iznini kontrol et.") from None
                continue
            last_frame = time.monotonic()
            buffer = np.concatenate([buffer, frame])
            while len(buffer) >= 512:
                chunk, buffer = buffer[:512], buffer[512:]
                event = segmenter.feed(chunk)
                if event is None:
                    continue
                if event.kind == "start":
                    self.hub.publish({"type": "voice.speech", "active": True})
                elif event.kind == "end":
                    self.hub.publish({"type": "voice.speech", "active": False})
                    if event.audio is not None:
                        return event.audio

            elapsed = time.monotonic() - started
            if elapsed > MIC_STALL_S and self.mic.peak_since_start == 0.0:
                raise AudioDeviceError(
                    "Mikrofon tamamen sessiz. macOS Sistem Ayarları › Gizlilik ve Güvenlik › Mikrofon bölümünden izin ver."
                )
            if self.mode == "ptt" and not segmenter.active and elapsed > PTT_NO_SPEECH_TIMEOUT_S:
                self._notice("Ses algılanmadı.")
                return None
