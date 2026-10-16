"""Local text-to-speech engines.

- PiperTTS: neural VITS voice (tr_TR-dfki-medium) via ONNX Runtime — primary, fast, fully local.
- MacSayTTS: the on-device macOS "Yelda" Turkish voice — zero-download fallback.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger("lazos.tts")


class TTSError(RuntimeError):
    pass


class TTSEngine(Protocol):
    name: str
    sample_rate: int

    def available(self) -> bool: ...

    async def synthesize(self, text: str, speed: float = 1.0) -> np.ndarray: ...

    async def warm_up(self) -> None: ...


class PiperTTS:
    name = "piper"

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._voice = None
        self.sample_rate = 22_050
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="piper")

    def available(self) -> bool:
        return self.model_path.exists() and self.model_path.with_suffix(".onnx.json").exists()

    def _ensure_loaded(self):  # type: ignore[no-untyped-def]
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(str(self.model_path))
            self.sample_rate = self._voice.config.sample_rate
        return self._voice

    def _synth_sync(self, text: str, speed: float) -> np.ndarray:
        from piper import SynthesisConfig

        voice = self._ensure_loaded()
        # Tuned on Turkish round-trip tests (Whisper intelligibility): slightly slower, less duration noise.
        cfg = SynthesisConfig(length_scale=1.08 / max(0.5, speed), noise_scale=0.5, noise_w_scale=0.6)
        parts = [chunk.audio_float_array for chunk in voice.synthesize(text, syn_config=cfg)]
        if not parts:
            return np.zeros(0, dtype=np.float32)
        # Small natural pause between sentences.
        audio = np.concatenate(parts).astype(np.float32)
        return np.concatenate([audio, np.zeros(int(self.sample_rate * 0.12), dtype=np.float32)])

    async def synthesize(self, text: str, speed: float = 1.0) -> np.ndarray:
        if not self.available():
            raise TTSError("Piper Türkçe ses modeli yüklü değil")
        try:
            return await asyncio.get_running_loop().run_in_executor(self._executor, self._synth_sync, text, speed)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"Piper sentezi başarısız: {exc}") from exc

    async def warm_up(self) -> None:
        if self.available():
            await self.synthesize("Hazır.")


class MacSayTTS:
    """Uses /usr/bin/say with the on-device Turkish voice. Audio never leaves the machine."""

    name = "macos"

    def __init__(self, voice: str | None = None, sample_rate: int = 22_050) -> None:
        self.sample_rate = sample_rate
        self._say = shutil.which("say") or "/usr/bin/say"
        self.voice = voice or self._best_turkish_voice()

    def _best_turkish_voice(self) -> str:
        """Prefer a Premium/Enhanced Turkish voice if the user installed one in System Settings."""
        import subprocess

        try:
            listing = subprocess.run([self._say, "-v", "?"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return "Yelda"
        names = [line.split("  ")[0].strip() for line in listing.splitlines() if "tr_TR" in line]
        for marker in ("Premium", "Enhanced", "Gelişmiş"):
            for name in names:
                if marker in name:
                    return name
        return names[0] if names else "Yelda"

    def available(self) -> bool:
        return Path(self._say).exists()

    async def synthesize(self, text: str, speed: float = 1.0) -> np.ndarray:
        rate = int(185 * speed)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "say.wav"
            proc = await asyncio.create_subprocess_exec(
                self._say,
                "-v",
                self.voice,
                "-r",
                str(rate),
                "-o",
                str(out),
                "--file-format=WAVE",
                f"--data-format=LEI16@{self.sample_rate}",
                "--",
                text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0 or not out.exists():
                raise TTSError(f"macOS say failed: {err.decode(errors='replace')[:200]}")
            with wave.open(str(out)) as w:
                data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return (data.astype(np.float32) / 32768.0).copy()

    async def warm_up(self) -> None:
        return None


class TTSManager:
    """Selects the configured engine and falls back to macOS speech if the primary one fails."""

    def __init__(self, piper: PiperTTS, mac: MacSayTTS) -> None:
        self.engines: dict[str, TTSEngine] = {"piper": piper, "macos": mac}
        self.preferred = "piper"

    def engine(self) -> TTSEngine:
        primary = self.engines.get(self.preferred)
        if primary and primary.available():
            return primary
        for engine in self.engines.values():
            if engine.available():
                return engine
        raise TTSError("Kullanılabilir yerel ses motoru yok")

    async def synthesize(self, text: str, speed: float = 1.0) -> tuple[np.ndarray, int, str]:
        engine = self.engine()
        try:
            return await engine.synthesize(text, speed), engine.sample_rate, engine.name
        except TTSError:
            fallback = self.engines["macos"]
            if engine is fallback or not fallback.available():
                raise
            log.warning("primary TTS failed; using macOS fallback", exc_info=True)
            return await fallback.synthesize(text, speed), fallback.sample_rate, fallback.name

    def status(self) -> dict:
        return {
            "preferred": self.preferred,
            "engines": {name: e.available() for name, e in self.engines.items()},
        }
