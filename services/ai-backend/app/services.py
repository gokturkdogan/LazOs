"""Service container: builds every subsystem once and wires them together."""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from app import __version__
from app.chat import ChatService
from app.conversations.store import ConversationStore
from app.core.config import BootConfig, UserSettings
from app.core.events import EventHub
from app.core.metrics import MetricsLog
from app.core.state import AIState, StateMachine
from app.inference.base import GenerationOptions, LLMBackend, LLMError
from app.inference.ollama import OllamaBackend
from app.inference.prompts import WEB_SEARCH_TOOL, build_system_prompt
from app.models.manager import ModelManager
from app.research.providers.ddgs_provider import DdgsProvider
from app.research.providers.searxng import SearxngProvider
from app.research.service import ResearchService
from app.speech.audio_io import AudioPlayer, MicCapture
from app.speech.stt import WhisperSTT
from app.speech.tts import MacSayTTS, PiperTTS, TTSManager
from app.speech.vad import SileroVAD
from app.speech.voice import VoiceController

log = logging.getLogger("lazos.services")


def _throttle(fn, interval: float):  # type: ignore[no-untyped-def]
    last = [0.0]

    def wrapper(level: float) -> None:
        now = time.monotonic()
        if level == 0.0 or now - last[0] >= interval:
            last[0] = now
            fn(level)

    return wrapper


class Services:
    def __init__(
        self,
        boot: BootConfig,
        *,
        llm: LLMBackend | None = None,
        research: ResearchService | None = None,
    ) -> None:
        self.boot = boot
        self.hub = EventHub()
        self.state = StateMachine(self.hub)
        self.metrics = MetricsLog()
        self.store = ConversationStore(boot.db_path)
        self.user_settings = UserSettings()
        self.llm: LLMBackend = llm or OllamaBackend(boot.ollama_url)
        self.research = research or ResearchService([SearxngProvider(boot.searxng_url), DdgsProvider()])
        self.models = ModelManager(
            models_dir=boot.models_dir,
            whisper_checkpoint=boot.whisper_torch_checkpoint,
            llm=self.llm,
            hub=self.hub,
            llm_model=self.user_settings.llm_model,
        )
        self.stt = WhisperSTT(self.models.whisper_dir)
        self.tts = TTSManager(PiperTTS(self.models.piper_model), MacSayTTS())
        self.online: bool | None = None
        self.search_provider: str | None = None
        self.llm_status: dict[str, Any] = {"reachable": False}
        self._tasks: list[asyncio.Task[Any]] = []
        self._ollama_proc: subprocess.Popen[bytes] | None = None
        # Audio objects need the running loop; created in start().
        self.player: AudioPlayer
        self.mic: MicCapture
        self.chat: ChatService
        self.voice: VoiceController

    def settings(self) -> UserSettings:
        return self.user_settings

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        await self.store.open()
        stored = await self.store.load_settings()
        if stored:
            try:
                self.user_settings = UserSettings.model_validate({**self.user_settings.model_dump(), **stored})
            except Exception:
                log.warning("stored settings invalid; using defaults", exc_info=True)
        self.tts.preferred = self.user_settings.tts_engine
        self.models.set_llm_model(self.user_settings.llm_model)

        def mic_level(level: float) -> None:
            if self.state.state == AIState.LISTENING or level == 0.0:
                self.hub.publish({"type": "audio.level", "source": "mic", "level": round(level, 3)})

        def tts_level(level: float) -> None:
            self.hub.publish({"type": "audio.level", "source": "tts", "level": round(level, 3)})

        self.player = AudioPlayer(loop, tts_level)
        self.mic = MicCapture(loop, _throttle(mic_level, 1 / 30))
        self.chat = ChatService(
            store=self.store,
            llm=self.llm,
            research=self.research,
            hub=self.hub,
            state=self.state,
            tts=self.tts,
            player=self.player,
            metrics=self.metrics,
            settings=self.settings,
        )
        self.voice = VoiceController(
            mic=self.mic,
            vad_factory=lambda: SileroVAD(self.models.vad_model),
            stt=self.stt,
            chat=self.chat,
            state=self.state,
            hub=self.hub,
            settings=self.settings,
        )
        self.hub.publish({"type": "voice.mode", "mode": "off"}, sticky=True)
        await self.models.refresh()
        self.hub.publish({"type": "setup.models", "models": self.models.describe()}, sticky=True)
        self._tasks.append(asyncio.create_task(self._monitor(), name="monitor"))
        if self.boot.warmup:
            self._tasks.append(asyncio.create_task(self._warm_up(), name="warmup"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        try:
            await self.voice.stop()
            await self.chat.cancel()
            self.player.close()
        except Exception:
            log.warning("error during shutdown", exc_info=True)
        if hasattr(self.llm, "aclose"):
            await self.llm.aclose()
        await self.store.close()

    # ── background work ────────────────────────────────────────────────────
    async def _warm_up(self) -> None:
        """Load models so the first interaction is fast; prime the LLM prompt cache with the system prompt."""
        await asyncio.sleep(0.5)
        try:
            if self.models.states["llm"].status == "present":
                t = time.perf_counter()
                await self._prime_llm()
                log.info("llm warm", extra={"ms": round((time.perf_counter() - t) * 1000)})
        except LLMError as exc:
            log.warning("llm warm-up skipped: %s", exc)
        await self._refresh_llm_status()
        try:
            await self.stt.warm_up()
        except Exception:
            log.warning("whisper warm-up failed", exc_info=True)
        for engine in self.tts.engines.values():
            try:
                await engine.warm_up()
            except Exception:
                log.warning("tts warm-up failed", extra={"engine": engine.name}, exc_info=True)
        self.publish_system()

    async def _prime_llm(self) -> None:
        """Generate one token with the exact system prompt + tools used for chat, so Ollama caches that prefix."""
        s = self.user_settings
        tools = [WEB_SEARCH_TOOL] if s.research_mode != "off" else None
        messages = [
            {"role": "system", "content": build_system_prompt(voice=False, has_tools=bool(tools))},
            {"role": "user", "content": "Merhaba"},
        ]
        options = GenerationOptions(max_tokens=1, num_ctx=s.num_ctx, keep_alive_minutes=s.keep_alive_minutes)
        async for _ in self.llm.stream_chat(s.llm_model, messages, options, tools):
            pass

    async def _monitor(self) -> None:
        while True:
            try:
                await self._refresh_llm_status()
                self.online = await self._check_online()
                providers = await self.research.provider_status()
                self.search_provider = next((p["name"] for p in providers if p["available"]), None)
                self.publish_system()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("monitor iteration failed", exc_info=True)
            await asyncio.sleep(10)

    async def _refresh_llm_status(self) -> None:
        health = await self.llm.health()
        loaded = []
        if health.reachable:
            loaded = [m.get("name") for m in await self.llm.loaded_models()]
            if self.models.states["llm"].status in ("unknown", "missing"):
                await self.models.refresh()
        self.llm_status = {
            "reachable": health.reachable,
            "version": health.version,
            "model": self.user_settings.llm_model,
            "loaded": self.user_settings.llm_model in loaded,
            "error": health.error if not health.reachable else None,
        }

    @staticmethod
    async def _check_online() -> bool:
        """TCP reachability only — no data is sent anywhere."""
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection("1.1.1.1", 443), timeout=1.5)
            writer.close()
            return True
        except (OSError, TimeoutError):
            try:
                await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo("example.com", 443), timeout=1.5)
                return True
            except (OSError, TimeoutError, socket.gaierror):
                return False

    def system_snapshot(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "llm": self.llm_status,
            "stt": {"available": self.stt.available(), "loaded": self.stt.loaded, "model": "whisper-large-v3-turbo"},
            "tts": self.tts.status(),
            "vad": {"available": self.models.vad_model.exists()},
            "online": self.online,
            "search_provider": self.search_provider,
            "models_ready": self.models.all_present(),
            "last_turn": self.metrics.last(),
        }

    def publish_system(self) -> None:
        self.hub.publish({"type": "system", **self.system_snapshot()}, sticky=True)

    # ── settings ───────────────────────────────────────────────────────────
    async def update_settings(self, patch: dict[str, Any]) -> UserSettings:
        merged = UserSettings.model_validate({**self.user_settings.model_dump(), **patch})
        changed = {k: v for k, v in merged.model_dump().items() if getattr(self.user_settings, k) != v}
        self.user_settings = merged
        if changed:
            await self.store.save_settings(changed)
        self.tts.preferred = merged.tts_engine
        if "llm_model" in changed:
            self.models.set_llm_model(merged.llm_model)
            await self.models.refresh()
            self.hub.publish({"type": "setup.models", "models": self.models.describe()}, sticky=True)
        self.hub.publish({"type": "settings", "settings": merged.model_dump()}, sticky=True)
        return merged

    # ── setup helpers ──────────────────────────────────────────────────────
    def system_info(self) -> dict[str, Any]:
        import os

        mem = 0
        try:
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip())
        except (OSError, ValueError):
            pass
        chip = ""
        try:
            chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        except OSError:
            pass
        arch = platform.machine()
        return {
            "os": f"macOS {platform.mac_ver()[0]}",
            "arch": arch,
            "chip": chip,
            "memory_bytes": mem,
            "cpu_count": os.cpu_count(),
            "apple_silicon": arch == "arm64",
            "disk_free_bytes": self.models.disk_free_bytes(),
            "compatible": arch == "arm64" and mem >= 16 * 1024**3,
        }

    @staticmethod
    def ollama_binary() -> str | None:
        for candidate in (shutil.which("ollama"), "/opt/homebrew/bin/ollama", "/usr/local/bin/ollama"):
            if candidate and Path(candidate).exists():
                return candidate
        return None

    async def start_ollama(self) -> dict[str, Any]:
        if (await self.llm.health()).reachable:
            return {"started": False, "running": True}
        app = Path("/Applications/Ollama.app")
        if app.exists():
            subprocess.Popen(["open", "-g", "-a", str(app)])
        else:
            binary = self.ollama_binary()
            if not binary:
                return {"started": False, "running": False, "error": "Ollama bulunamadı. https://ollama.com adresinden kur."}
            log_file = (self.boot.logs_dir / "ollama.log").open("ab")
            self._ollama_proc = subprocess.Popen([binary, "serve"], stdout=log_file, stderr=log_file, start_new_session=True)
        for _ in range(30):
            await asyncio.sleep(0.5)
            if (await self.llm.health()).reachable:
                await self.models.refresh()
                await self._refresh_llm_status()
                self.publish_system()
                return {"started": True, "running": True}
        return {"started": True, "running": False, "error": "Ollama başlatıldı ama yanıt vermiyor."}
