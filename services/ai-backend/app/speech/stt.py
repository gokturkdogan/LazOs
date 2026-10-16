"""Local speech recognition with MLX Whisper (Metal-accelerated on Apple Silicon)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("lazos.stt")

# Phrases Whisper is known to hallucinate on silence/noise (common in Turkish/English subtitle data).
# Only whole-utterance matches are discarded, so genuine requests ("Müzik öner", "Teşekkürler") pass through.
_HALLUCINATIONS = re.compile(
    r"^\s*(altyazı\b.*|alt yazı\b.*|.*izlediğiniz için teşekkür.*|.*abone olmayı unutmayın.*|"
    r".*beğenmeyi unutmayın.*|thank you for watching\.?|thanks for watching\.?|you\.?|\.+|"
    r"\[müzik\]|\(müzik\)|♪+|\[music\])\s*$",
    re.IGNORECASE,
)


@dataclass
class Transcript:
    text: str
    language: str
    duration_ms: float
    audio_ms: float


class STTError(RuntimeError):
    pass


class WhisperSTT:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        # MLX state is not thread-safe: keep all Whisper work on one dedicated thread.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def available(self) -> bool:
        return (self.model_dir / "weights.safetensors").exists()

    def _load(self) -> None:
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.get_model(str(self.model_dir), mx.float16)
        self._transcribe_sync(np.zeros(16_000, dtype=np.float32), "tr")  # compile kernels
        self._loaded = True

    async def warm_up(self) -> None:
        if not self.available():
            return
        t = time.perf_counter()
        await asyncio.get_running_loop().run_in_executor(self._executor, self._load)
        log.info("whisper ready", extra={"load_ms": round((time.perf_counter() - t) * 1000)})

    def _transcribe_sync(self, audio: np.ndarray, language: str | None, prompt: str | None = None) -> dict:
        import mlx_whisper

        return mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=str(self.model_dir),
            language=language,
            # Vocabulary hint (names, technical terms). Measured on Turkish-accented technical sentences:
            # 11/16 → 15/16 (Yelda) and 13/16 → 16/16 (Piper) terms recognised, +3 ms latency.
            initial_prompt=prompt,
            temperature=(0.0, 0.2, 0.4),
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            fp16=True,
            verbose=None,
        )

    async def transcribe(self, audio: np.ndarray, language: str = "auto", prompt: str | None = None) -> Transcript:
        if not self.available():
            raise STTError("Konuşma tanıma modeli yüklü değil")
        t = time.perf_counter()
        lang = None if language == "auto" else language

        def run() -> dict:
            result = self._transcribe_sync(audio, lang, prompt)
            # LAZOS supports Turkish and English; re-run as Turkish if detection picked something else.
            if lang is None and result.get("language") not in ("tr", "en"):
                result = self._transcribe_sync(audio, "tr", prompt)
            self._loaded = True
            return result

        try:
            result = await asyncio.get_running_loop().run_in_executor(self._executor, run)
        except Exception as exc:
            raise STTError(f"Konuşma tanıma başarısız: {exc}") from exc
        text = (result.get("text") or "").strip()
        if _HALLUCINATIONS.search(text) or not re.search(r"\w", text):
            log.info("discarded probable hallucination", extra={"text": text})
            text = ""
        return Transcript(
            text=text,
            language=result.get("language") or (lang or "tr"),
            duration_ms=round((time.perf_counter() - t) * 1000, 1),
            audio_ms=round(len(audio) / 16, 1),
        )
