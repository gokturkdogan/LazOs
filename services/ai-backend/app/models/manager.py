"""Model management: detect, download (with progress), convert, verify and report local AI models.

Policy: never download something that already exists on the machine. Downloads only start after the
user approves them in the setup screen.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from app.core.events import EventHub
from app.inference.base import LLMBackend, LLMError

log = logging.getLogger("lazos.models")

Status = Literal["present", "missing", "downloading", "converting", "verifying", "error", "unknown"]


@dataclass(frozen=True)
class RemoteFile:
    url: str
    filename: str
    size: int
    sha256: str | None


@dataclass
class ModelSpec:
    id: str
    kind: Literal["llm", "stt", "tts", "vad"]
    label: str
    description: str
    approx_bytes: int
    files: list[RemoteFile] = field(default_factory=list)
    required: bool = True


@dataclass
class ModelState:
    status: Status = "unknown"
    progress: float = 0.0
    detail: str = ""
    error: str | None = None
    source: str | None = None  # "download" | "local-convert" | "ollama"


WHISPER_DIR_NAME = "whisper-large-v3-turbo"
PIPER_VOICE = "tr_TR-dfki-medium"
_PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/dfki/medium"
_WHISPER_BASE = "https://huggingface.co/mlx-community/whisper-large-v3-turbo/resolve/main"


def build_registry(llm_model: str) -> list[ModelSpec]:
    return [
        ModelSpec(
            id="llm",
            kind="llm",
            label=f"Dil modeli · {llm_model}",
            description="Qwen 3.5 9B (Ollama, Apple Silicon MLX runner). Konuşmanın beyni.",
            approx_bytes=7_600_000_000,
        ),
        ModelSpec(
            id="stt",
            kind="stt",
            label="Konuşma tanıma · Whisper large-v3-turbo (MLX)",
            description="Türkçe ve İngilizce konuşmayı yerel olarak metne çevirir.",
            approx_bytes=1_614_000_000,
            files=[
                RemoteFile(f"{_WHISPER_BASE}/config.json", "config.json", 268, None),
                RemoteFile(f"{_WHISPER_BASE}/weights.safetensors", "weights.safetensors", 1_613_977_612, None),
            ],
        ),
        ModelSpec(
            id="tts",
            kind="tts",
            label=f"Türkçe ses · Piper {PIPER_VOICE}",
            description="Yerel, hızlı Türkçe konuşma sentezi (VITS). macOS 'Yelda' sesi yedek olarak kullanılır.",
            approx_bytes=63_206_254,
            files=[
                RemoteFile(
                    f"{_PIPER_BASE}/{PIPER_VOICE}.onnx",
                    f"{PIPER_VOICE}.onnx",
                    63_201_294,
                    "2844717f524ab965d3fe86e60562cbb601d3e456836efcc2196cc3a14112a8fb",
                ),
                RemoteFile(
                    f"{_PIPER_BASE}/{PIPER_VOICE}.onnx.json",
                    f"{PIPER_VOICE}.onnx.json",
                    4_960,
                    "13ebd7810f1b61b5027583cf3131a0a233b6ea81c38f2200ebc4ff41c3cca039",
                ),
            ],
        ),
        ModelSpec(
            id="vad",
            kind="vad",
            label="Ses etkinliği algılama · Silero VAD v6.2.3",
            description="Konuşmanın başladığı ve bittiği anı algılar.",
            approx_bytes=2_327_524,
            files=[
                RemoteFile(
                    "https://github.com/snakers4/silero-vad/raw/v6.2.3/src/silero_vad/data/silero_vad.onnx",
                    "silero_vad.onnx",
                    2_327_524,
                    "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
                )
            ],
        ),
    ]


class DownloadError(RuntimeError):
    pass


async def download_file(
    client: httpx.AsyncClient,
    remote: RemoteFile,
    dest: Path,
    on_progress: Callable[[int], None],
) -> None:
    """Stream to a .part file, verify size + sha256, then atomically rename."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    received = 0
    async with client.stream("GET", remote.url, follow_redirects=True) as resp:
        if resp.status_code != 200:
            raise DownloadError(f"{remote.filename}: HTTP {resp.status_code}")
        with part.open("wb") as fh:
            async for chunk in resp.aiter_bytes(1 << 20):
                fh.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                on_progress(received)
    if remote.size and received != remote.size:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{remote.filename}: size mismatch ({received} != {remote.size})")
    if remote.sha256 and digest.hexdigest() != remote.sha256:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{remote.filename}: checksum mismatch")
    part.rename(dest)


class ModelManager:
    def __init__(
        self,
        *,
        models_dir: Path,
        whisper_checkpoint: Path,
        llm: LLMBackend,
        hub: EventHub,
        llm_model: str,
    ) -> None:
        self.models_dir = models_dir
        self.whisper_checkpoint = whisper_checkpoint
        self.llm = llm
        self.hub = hub
        self.llm_model = llm_model
        self.specs = {s.id: s for s in build_registry(llm_model)}
        self.states: dict[str, ModelState] = {s: ModelState() for s in self.specs}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._last_emit = 0.0

    # ── paths ──────────────────────────────────────────────────────────────
    @property
    def whisper_dir(self) -> Path:
        return self.models_dir / WHISPER_DIR_NAME

    @property
    def piper_model(self) -> Path:
        return self.models_dir / "piper" / f"{PIPER_VOICE}.onnx"

    @property
    def vad_model(self) -> Path:
        return self.models_dir / "silero_vad.onnx"

    def _target(self, spec_id: str, filename: str) -> Path:
        if spec_id == "stt":
            return self.whisper_dir / filename
        if spec_id == "tts":
            return self.models_dir / "piper" / filename
        return self.models_dir / filename

    def set_llm_model(self, model: str) -> None:
        if model != self.llm_model:
            self.llm_model = model
            self.specs["llm"] = build_registry(model)[0]
            self.states["llm"] = ModelState()

    # ── detection ──────────────────────────────────────────────────────────
    def _files_present(self, spec_id: str) -> bool:
        spec = self.specs[spec_id]
        return all((p := self._target(spec_id, f.filename)).exists() and (not f.size or p.stat().st_size == f.size) for f in spec.files)

    def stt_present(self) -> bool:
        return (self.whisper_dir / "config.json").exists() and (self.whisper_dir / "weights.safetensors").exists()

    async def refresh(self) -> None:
        for spec_id in self.specs:
            state = self.states[spec_id]
            if state.status in ("downloading", "converting", "verifying"):
                continue
            if spec_id == "llm":
                try:
                    present = await self.llm.has_model(self.llm_model)
                    self.states["llm"] = ModelState(status="present" if present else "missing", source="ollama")
                except LLMError as exc:
                    self.states["llm"] = ModelState(status="unknown", error=str(exc), source="ollama")
            elif spec_id == "stt":
                if self.stt_present():
                    self.states["stt"] = ModelState(status="present")
                else:
                    src = "local-convert" if self.whisper_checkpoint.exists() else "download"
                    keep_error = state.error if state.status == "error" else None
                    self.states["stt"] = ModelState(status="error" if keep_error else "missing", source=src, error=keep_error)
            else:
                if self._files_present(spec_id):
                    self.states[spec_id] = ModelState(status="present")
                elif state.status != "error":
                    self.states[spec_id] = ModelState(status="missing", source="download")

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for spec_id, spec in self.specs.items():
            st = self.states[spec_id]
            download_bytes = 0
            if st.status in ("missing", "error"):
                download_bytes = 0 if st.source == "local-convert" else spec.approx_bytes
            out.append(
                {
                    "id": spec.id,
                    "kind": spec.kind,
                    "label": spec.label,
                    "description": spec.description,
                    "required": spec.required,
                    "approx_bytes": spec.approx_bytes,
                    "download_bytes": download_bytes,
                    "status": st.status,
                    "progress": round(st.progress, 4),
                    "detail": st.detail,
                    "error": st.error,
                    "source": st.source,
                }
            )
        return out

    def all_present(self) -> bool:
        return all(self.states[s].status == "present" for s, spec in self.specs.items() if spec.required)

    # ── installation ───────────────────────────────────────────────────────
    def _emit(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_emit > 0.25:
            self._last_emit = now
            self.hub.publish({"type": "setup.models", "models": self.describe()}, sticky=True)

    def _update(self, spec_id: str, **kw: Any) -> None:
        st = self.states[spec_id]
        for k, v in kw.items():
            setattr(st, k, v)
        self._emit(force="status" in kw)

    def install(self, ids: list[str]) -> list[str]:
        started = []
        for spec_id in ids:
            if spec_id not in self.specs or spec_id in self._tasks:
                continue
            if self.states[spec_id].status == "present":
                continue
            task = asyncio.create_task(self._install(spec_id), name=f"install-{spec_id}")
            self._tasks[spec_id] = task
            task.add_done_callback(lambda _t, s=spec_id: self._tasks.pop(s, None))
            started.append(spec_id)
        return started

    async def wait(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _install(self, spec_id: str) -> None:
        try:
            if spec_id == "llm":
                await self._pull_llm()
            elif spec_id == "stt" and self.whisper_checkpoint.exists():
                await self._convert_whisper()
            else:
                await self._download(spec_id)
            self.states[spec_id] = ModelState(status="verifying", progress=1.0)
            self.states[spec_id].status = "unknown"  # let refresh() re-detect from disk / Ollama
            await self.refresh()
            if self.states[spec_id].status != "present":
                raise DownloadError("model not detected after installation")
            self._update(spec_id, status="present", progress=1.0, detail="")
        except Exception as exc:
            log.exception("model install failed", extra={"model": spec_id})
            self._update(spec_id, status="error", error=str(exc), detail="")

    async def _pull_llm(self) -> None:
        self._update("llm", status="downloading", progress=0.0, detail="Ollama ile indiriliyor", error=None)
        totals: dict[str, tuple[int, int]] = {}
        async for event in self.llm.pull(self.llm_model):
            digest = event.get("digest")
            if digest and event.get("total"):
                totals[digest] = (event.get("completed") or 0, event["total"])
                done = sum(c for c, _ in totals.values())
                total = sum(t for _, t in totals.values())
                self._update("llm", progress=done / total if total else 0.0, detail=event.get("status", ""))
            elif event.get("status"):
                self._update("llm", detail=event["status"])
        self._update("llm", status="verifying", detail="doğrulanıyor")

    async def _convert_whisper(self) -> None:
        from app.models.whisper_convert import convert_checkpoint

        self._update("stt", status="converting", progress=0.0, detail="Yerel Whisper modeli dönüştürülüyor", error=None)
        loop = asyncio.get_running_loop()

        def progress(p: float, msg: str) -> None:
            loop.call_soon_threadsafe(lambda: self._update("stt", progress=p, detail=msg))

        await asyncio.to_thread(convert_checkpoint, self.whisper_checkpoint, self.whisper_dir, progress=progress)

    async def _download(self, spec_id: str) -> None:
        spec = self.specs[spec_id]
        total = sum(f.size for f in spec.files)
        self._update(spec_id, status="downloading", progress=0.0, detail="indiriliyor", error=None)
        done_before = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=60.0)) as client:
            for remote in spec.files:
                dest = self._target(spec_id, remote.filename)
                if dest.exists() and dest.stat().st_size == remote.size:
                    done_before += remote.size
                    continue

                def on_progress(received: int, base: int = done_before) -> None:
                    self._update(spec_id, progress=(base + received) / total if total else 0.0)

                await download_file(client, remote, dest, on_progress)
                done_before += remote.size

    def disk_free_bytes(self) -> int:
        return shutil.disk_usage(self.models_dir).free
