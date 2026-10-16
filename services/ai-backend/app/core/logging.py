"""Structured logging: concise console output plus a rotating JSON-lines file."""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path

_RESERVED = set(vars(logging.makeLogRecord({})))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = {k: v for k, v in vars(record).items() if k not in _RESERVED and not k.startswith("_")}
        base = f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.levelname:<5} {record.name}: {record.getMessage()}"
        if extras:
            base += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(level: str, logs_dir: Path | None) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    console = logging.StreamHandler()
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(logs_dir / "backend.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    for noisy in ("httpx", "httpcore", "uvicorn.access", "numba", "primp", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
