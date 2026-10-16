"""Boot-time configuration (environment) and user-editable runtime settings."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "LAZOS"


class BootConfig(BaseSettings):
    """Process-level configuration. Override with LAZOS_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="LAZOS_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 47811
    data_dir: Path = Field(default_factory=_default_data_dir)
    log_level: str = "INFO"

    ollama_url: str = "http://127.0.0.1:11434"
    searxng_url: str = "http://127.0.0.1:8888"

    # Existing OpenAI-format Whisper checkpoint that can be converted locally instead of downloading.
    whisper_torch_checkpoint: Path = Path.home() / ".cache" / "whisper" / "large-v3-turbo.pt"

    # Disable heavy subsystems (used by tests and headless runs).
    enable_audio: bool = True
    warmup: bool = True

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        if v == "localhost":
            return v
        try:
            if ipaddress.ip_address(v).is_loopback:
                return v
        except ValueError:
            pass
        raise ValueError("LAZOS backend must bind to a loopback address (127.0.0.1 / localhost)")

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lazos.db"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "auth.token"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


ResearchMode = Literal["auto", "always", "off"]
SttLanguage = Literal["auto", "tr", "en"]
TtsEngineName = Literal["piper", "macos"]


class UserSettings(BaseModel):
    """Settings the user can change from the UI. Persisted in SQLite."""

    llm_model: str = "qwen3.5:9b"
    temperature: float = Field(0.7, ge=0.0, le=1.5)
    num_ctx: int = Field(8192, ge=2048, le=65536)
    max_response_tokens: int = Field(1536, ge=128, le=8192)
    keep_alive_minutes: int = Field(30, ge=0, le=1440)

    research_mode: ResearchMode = "auto"
    max_sources: int = Field(3, ge=1, le=8)

    stt_language: SttLanguage = "auto"
    tts_engine: TtsEngineName = "piper"
    tts_speed: float = Field(1.0, ge=0.6, le=1.6)
    speak_text_replies: bool = False

    vad_threshold: float = Field(0.5, ge=0.2, le=0.9)
    vad_silence_ms: int = Field(700, ge=300, le=2500)
    input_device: str | None = None
    output_device: str | None = None

    memory_enabled: bool = True  # use identity profile, learned memories and past conversations in answers
    memory_auto_learn: bool = True  # learn durable facts from conversations in the background

    reduced_motion: bool = False
    visual_quality: Literal["auto", "high", "low"] = "auto"


@lru_cache
def get_boot_config() -> BootConfig:
    cfg = BootConfig()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.models_dir.mkdir(parents=True, exist_ok=True)
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    return cfg
