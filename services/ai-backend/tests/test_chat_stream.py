"""WebSocket streaming protocol, research tool flow, cancellation and error handling."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.inference.base import LLMUnavailable, ModelMissing
from tests.fakes import FakeLLM, FakeSearchProvider, text, tool_call


def collect_turn(ws: Any, *, until: str = "chat.done", limit: int = 200) -> list[dict[str, Any]]:
    events = []
    for _ in range(limit):
        e = ws.receive_json()
        if e["type"] in ("audio.level", "system", "setup.models", "voice.mode", "settings"):
            continue
        events.append(e)
        if e["type"] == until or e["type"] == "chat.error":
            return events
    raise AssertionError(f"no {until} in {events}")


def connect(client: TestClient):  # type: ignore[no-untyped-def]
    ws = client.websocket_connect(f"/ws?token={client.token}")  # type: ignore[attr-defined]
    return ws


def test_streaming_protocol_and_persistence(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str]) -> None:
    fake_llm.scripts = [text("Gayet ", "iyiyim. ", "Sistemler hazır.")]
    with connect(client) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        ws.send_json({"type": "chat.send", "text": "LAZOS, nasılsın?", "conversation_id": None})
        events = collect_turn(ws)

    types = [e["type"] for e in events]
    assert types.index("chat.user") < types.index("chat.start") < types.index("chat.delta") < types.index("chat.done")
    states = [e["state"] for e in events if e["type"] == "state"]
    assert "thinking" in states

    deltas = "".join(e["text"] for e in events if e["type"] == "chat.delta")
    assert deltas == "Gayet iyiyim. Sistemler hazır."
    done = events[-1]
    assert done["status"] == "complete"
    assert done["message"]["content"] == deltas
    assert done["message"]["metrics"]["ttft_ms"] >= 0

    # Small talk is routed without web search; tools are still offered for prompt-cache stability.
    call = fake_llm.calls[0]
    assert call["tools"] and call["tools"][0]["function"]["name"] == "web_search"
    assert call["messages"][0]["role"] == "system" and "LAZOS" in call["messages"][0]["content"]
    assert call["messages"][-1]["content"].endswith("LAZOS, nasılsın?")

    # Persisted.
    conv_id = done["message"]["conversation_id"]
    detail = client.get(f"/api/conversations/{conv_id}", headers=auth).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["title"] == "Nasılsın?"


def test_context_carries_history(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.scripts = [text("Merhaba Göktürk."), text("Adın Göktürk.")]
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Benim adım Göktürk.", "conversation_id": None})
        first = collect_turn(ws)
        conv_id = first[-1]["message"]["conversation_id"]
        ws.send_json({"type": "chat.send", "text": "Adım neydi?", "conversation_id": conv_id})
        collect_turn(ws)
    second_call = fake_llm.calls[1]["messages"]
    contents = [m["content"] for m in second_call]
    assert any("Benim adım Göktürk." in c for c in contents)
    assert any(c == "Merhaba Göktürk." for c in contents)


def test_forced_research_cites_sources(client: TestClient, fake_llm: FakeLLM, fake_search: FakeSearchProvider) -> None:
    fake_llm.scripts = [text("Bugün iki gelişme var [1][2].")]
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Bugün yapay zeka dünyasında neler oldu?", "conversation_id": None})
        events = collect_turn(ws)

    research = [e for e in events if e["type"] == "chat.research"]
    assert [r["status"] for r in research] == ["searching", "done"]
    assert "Ekim" in research[0]["query"] or any(ch.isdigit() for ch in research[0]["query"])  # date-anchored
    assert fake_search.queries, "search provider was not called"

    # The model received the results as a tool exchange (untrusted-data rules included).
    msgs = fake_llm.calls[0]["messages"]
    assert msgs[-2]["role"] == "assistant" and msgs[-2]["tool_calls"][0]["function"]["name"] == "web_search"
    assert msgs[-1]["role"] == "tool" and "GÜVENİLMEYEN" in msgs[-1]["content"] and "[1] Haber A" in msgs[-1]["content"]

    sources = events[-1]["message"]["sources"]
    assert [s["url"] for s in sources] == ["https://example.com/a", "https://example.org/b"]
    assert all(s["cited"] for s in sources)


def test_model_initiated_tool_call(client: TestClient, fake_llm: FakeLLM, fake_search: FakeSearchProvider) -> None:
    fake_llm.scripts = [tool_call("qwen 3.5 çıkış tarihi"), text("Kaynağa göre şöyle [1].")]
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Qwen 3.5 hakkında ne biliyorsun?", "conversation_id": None})
        events = collect_turn(ws)
    assert fake_search.queries == ["qwen 3.5 çıkış tarihi"]
    assert len(fake_llm.calls) == 2
    done = events[-1]["message"]
    assert done["content"] == "Kaynağa göre şöyle [1]."
    assert done["sources"][0]["cited"] is True and done["sources"][1]["cited"] is False


def test_invalid_tool_arguments_fall_back_to_user_text(client: TestClient, fake_llm: FakeLLM, fake_search: FakeSearchProvider) -> None:
    fake_llm.scripts = [tool_call(""), text("Tamam.")]
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Şu konuyu bir araştır bakalım", "conversation_id": None})
        collect_turn(ws)
    assert fake_search.queries and fake_search.queries[0].startswith("Şu konuyu")


def test_search_failure_is_reported_not_fabricated(client: TestClient, fake_llm: FakeLLM, fake_search: FakeSearchProvider) -> None:
    fake_search.fail = True
    fake_llm.scripts = [text("Şu anda güncel bilgiye ulaşamıyorum.")]
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Dolar kuru şu an ne kadar?", "conversation_id": None})
        events = collect_turn(ws)
    research = [e for e in events if e["type"] == "chat.research"]
    assert research[-1]["status"] == "failed"
    assert "başarısız" in fake_llm.calls[0]["messages"][-1]["content"]
    assert events[-1]["message"]["sources"] == []


def test_cancel_interrupts_generation(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.delay = 0.05
    fake_llm.scripts = [text(*[f"kelime{i} " for i in range(200)])]
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Uzun bir hikâye anlat", "conversation_id": None})
        while ws.receive_json()["type"] != "chat.delta":
            pass
        ws.send_json({"type": "chat.cancel"})
        events = collect_turn(ws)
        assert events[-1]["type"] == "chat.done" and events[-1]["status"] == "interrupted"
        assert 0 < len(events[-1]["message"]["content"]) < len("kelime0 ") * 200
        # The state machine returns to idle.
        state = None
        for _ in range(20):
            e = ws.receive_json()
            if e["type"] == "state":
                state = e["state"]
                break
        assert state in ("idle", None) or client.services.state.state.value == "idle"  # type: ignore[attr-defined]


def test_llm_unavailable_reports_error_state(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.scripts = [LLMUnavailable("Ollama is not running")]
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Merhaba", "conversation_id": None})
        events = collect_turn(ws)
    err = events[-1]
    assert err["type"] == "chat.error" and err["code"] == "llm_unavailable"
    assert "Ollama" in err["message"]
    assert client.services.state.state.value == "error"  # type: ignore[attr-defined]


def test_model_missing_error(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.scripts = [ModelMissing("no model")]
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Merhaba", "conversation_id": None})
        events = collect_turn(ws)
    assert events[-1]["code"] == "model_missing"


def test_bad_client_message_is_rejected_gracefully(client: TestClient) -> None:
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": ""})
        e = ws.receive_json()
        while e["type"] != "error":
            e = ws.receive_json()
        assert e["code"] == "bad_request"
        ws.send_json({"type": "ping"})
        while (e := ws.receive_json())["type"] != "pong":
            pass


def test_long_conversation_is_summarized(client: TestClient, fake_llm: FakeLLM, auth: dict[str, str]) -> None:
    client.put("/api/settings", json={"num_ctx": 4096, "max_response_tokens": 256}, headers=auth)
    long_answer = "Bu uzun bir yanıt cümlesidir. " * 120  # ~3600 chars ≈ 1100 tokens
    fake_llm.default = text(long_answer)
    conv_id = None
    with connect(client) as ws:
        ws.receive_json()
        for i in range(4):
            ws.send_json({"type": "chat.send", "text": f"Soru {i}: biraz anlat", "conversation_id": conv_id})
            conv_id = collect_turn(ws)[-1]["message"]["conversation_id"]
    # Summarization runs in the background after a turn that dropped old messages.
    import time

    for _ in range(50):
        conv = client.portal.call(client.services.store.get_conversation, conv_id)  # type: ignore[attr-defined]
        if conv and conv.summary:
            break
        time.sleep(0.05)
    assert conv and conv.summary and conv.summary_upto > 0
    # The next turn carries the rolling summary in its system prompt and stays within budget.
    fake_llm.calls.clear()
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Peki özetle ne konuştuk?", "conversation_id": conv_id})
        collect_turn(ws)
    system = fake_llm.calls[0]["messages"][0]["content"]
    assert "Önceki konuşmanın özeti" in system
