"""Silero VAD (ONNX, no torch) and an utterance segmenter built on top of it."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
FRAME = 512  # 32 ms at 16 kHz — the window size Silero v5+/v6 expects
_CONTEXT = 64


class SileroVAD:
    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        if frame.shape[-1] != FRAME:
            raise ValueError(f"expected {FRAME} samples, got {frame.shape[-1]}")
        x = np.concatenate([self._context, frame.reshape(1, -1).astype(np.float32)], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -_CONTEXT:]
        return float(out[0][0])


@dataclass
class SegmenterConfig:
    threshold: float = 0.5
    neg_threshold: float = 0.35
    min_speech_ms: int = 250
    silence_ms: int = 700
    pre_roll_ms: int = 300
    max_utterance_s: float = 30.0


@dataclass
class SegmentEvent:
    kind: str  # "start" | "end"
    audio: np.ndarray | None = None


class UtteranceSegmenter:
    """Feeds 512-sample frames; emits "start" when speech begins and "end" (with audio) when it stops."""

    def __init__(self, vad, config: SegmenterConfig) -> None:  # vad: Callable[[np.ndarray], float]
        self.vad = vad
        self.cfg = config
        frame_ms = FRAME * 1000 / SAMPLE_RATE
        self._pre_frames = int(config.pre_roll_ms / frame_ms)
        self._silence_frames = int(config.silence_ms / frame_ms)
        self._min_frames = int(config.min_speech_ms / frame_ms)
        self._max_frames = int(config.max_utterance_s * 1000 / frame_ms)
        self.reset()

    def reset(self) -> None:
        if hasattr(self.vad, "reset"):
            self.vad.reset()
        self._pre: deque[np.ndarray] = deque(maxlen=max(1, self._pre_frames))
        self._speech: list[np.ndarray] = []
        self._active = False
        self._voiced = 0
        self._silent = 0
        self._pending = 0  # consecutive voiced frames before committing to "start"

    @property
    def active(self) -> bool:
        return self._active

    def force_end(self) -> np.ndarray | None:
        """Manual end of utterance (push-to-talk release). Returns audio if enough speech was captured."""
        if not self._active or self._voiced < self._min_frames:
            self.reset()
            return None
        audio = np.concatenate(self._speech)
        self.reset()
        return audio

    def feed(self, frame: np.ndarray) -> SegmentEvent | None:
        prob = self.vad(frame)
        if not self._active:
            self._pre.append(frame)
            if prob >= self.cfg.threshold:
                self._pending += 1
                if self._pending >= 2:  # ~64 ms of speech avoids triggering on clicks
                    self._active = True
                    self._speech = list(self._pre)
                    self._voiced = self._pending
                    self._silent = 0
                    return SegmentEvent("start")
            else:
                self._pending = 0
            return None

        self._speech.append(frame)
        if prob >= self.cfg.neg_threshold:
            self._silent = 0
            if prob >= self.cfg.threshold:
                self._voiced += 1
        else:
            self._silent += 1

        if self._silent >= self._silence_frames or len(self._speech) >= self._max_frames:
            voiced = self._voiced
            audio = np.concatenate(self._speech) if self._speech else np.zeros(0, np.float32)
            self.reset()
            if voiced < self._min_frames:
                return SegmentEvent("end", None)  # too short: noise / click
            return SegmentEvent("end", audio)
        return None
