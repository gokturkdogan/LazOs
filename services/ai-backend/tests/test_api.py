"""HTTP API: health, auth, conversations, settings, setup."""

from __future__ import annotations

import stat

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_health_is_public(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "app": "lazos", "version": "1.0.0"}


def test_api_requires_token(client: TestClient) -> None:
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_token_file_is_private(client: TestClient, boot) -> None:  # type: ignore[no-untyped-def]
    mode = stat.S_IMODE(boot.token_path.stat().st_mode)
    assert mode == 0o600


def test_websocket_rejects_bad_token(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=nope") as ws:
            ws.receive_json()


def test_websocket_rejects_foreign_origin(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?token={client.token}", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_status(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/api/status", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "idle"
    assert data["voice_mode"] == "off"


def test_conversation_crud(client: TestClient, auth: dict[str, str]) -> None:
    created = client.post("/api/conversations", headers=auth).json()
    cid = created["id"]
    assert client.get("/api/conversations", headers=auth).json()[0]["id"] == cid

    r = client.patch(f"/api/conversations/{cid}", json={"title": "Yeni başlık"}, headers=auth)
    assert r.status_code == 200 and r.json()["title"] == "Yeni başlık"

    detail = client.get(f"/api/conversations/{cid}", headers=auth).json()
    assert detail["messages"] == []

    assert client.delete(f"/api/conversations/{cid}", headers=auth).status_code == 204
    assert client.get(f"/api/conversations/{cid}", headers=auth).status_code == 404
    assert client.delete(f"/api/conversations/{cid}", headers=auth).status_code == 404


def test_settings_update_and_validation(client: TestClient, auth: dict[str, str]) -> None:
    r = client.put("/api/settings", json={"tts_speed": 1.2, "research_mode": "off"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["tts_speed"] == 1.2 and r.json()["research_mode"] == "off"
    assert client.get("/api/settings", headers=auth).json()["research_mode"] == "off"

    assert client.put("/api/settings", json={"tts_speed": 9}, headers=auth).status_code == 422
    assert client.put("/api/settings", json={"research_mode": "sometimes"}, headers=auth).status_code == 422


def test_setup_reports_models(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/api/setup", headers=auth)
    assert r.status_code == 200
    data = r.json()
    ids = {m["id"] for m in data["models"]}
    assert ids == {"llm", "stt", "tts", "vad"}
    llm = next(m for m in data["models"] if m["id"] == "llm")
    assert llm["status"] == "present"  # FakeLLM reports the model as installed
    stt = next(m for m in data["models"] if m["id"] == "stt")
    assert stt["status"] == "missing" and stt["source"] == "download" and stt["download_bytes"] > 1e9
    assert data["ready"] is False


def test_install_rejects_unknown_model(client: TestClient, auth: dict[str, str]) -> None:
    assert client.post("/api/setup/install", json={"ids": ["gpt5"]}, headers=auth).status_code == 422
