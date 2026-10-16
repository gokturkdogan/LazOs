"""Local access control.

The backend binds to loopback only, but any local web page could still try to reach it.
Every API and WebSocket request therefore carries a per-install secret token stored in a
user-only (0600) file, which the Tauri shell reads and hands to the UI.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

ALLOWED_ORIGINS = {
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
}


def load_or_create_token(path: Path) -> str:
    env_token = os.environ.get("LAZOS_TOKEN")
    if env_token:
        return env_token
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


def token_matches(expected: str, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(expected.encode(), provided.encode())


def origin_allowed(origin: str | None) -> bool:
    # Native clients (tests, curl) send no Origin header; browsers always do.
    return origin is None or origin in ALLOWED_ORIGINS
