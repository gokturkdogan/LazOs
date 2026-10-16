import { describe, expect, it } from "vitest";

import { lookFor, stateLabel } from "../components/ai-core/palette";
import { linkCitations } from "../components/chat/Markdown";
import type { ChatMessage, ServerEvent } from "../types/protocol";
import { formatBytes, formatMs } from "../utils/format";
import { initialLiveState, type LiveState, reduceEvent } from "./reducer";

function apply(state: LiveState, ...events: ServerEvent[]): LiveState {
  return events.reduce((s, e) => ({ ...s, ...(reduceEvent(s, e) ?? {}) }), state);
}

const conv = { id: "c1", title: "Test", created_at: 1, updated_at: 1, message_count: 1 };
const userMsg: ChatMessage = {
  id: "u1",
  conversation_id: "c1",
  role: "user",
  content: "Merhaba",
  created_at: 1,
  origin: "text",
  status: "complete",
  sources: [],
};

describe("reduceEvent", () => {
  it("streams an assistant message from start to done", () => {
    const s = apply(
      initialLiveState,
      { type: "chat.user", conversation: conv, message: userMsg },
      { type: "chat.start", conversation_id: "c1", message_id: "a1" },
      { type: "chat.delta", message_id: "a1", text: "Gayet " },
      { type: "chat.delta", message_id: "a1", text: "iyiyim. Sistemler hazır." },
    );
    expect(s.conversationId).toBe("c1");
    expect(s.streamingId).toBe("a1");
    expect(s.messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(s.messages[1].content).toBe("Gayet iyiyim. Sistemler hazır.");
    expect(s.caption).toBe("Sistemler hazır.");

    const done = apply(s, {
      type: "chat.done",
      message_id: "a1",
      status: "complete",
      message: { ...s.messages[1], status: "complete", metrics: { ttft_ms: 120 } },
    });
    expect(done.streamingId).toBeNull();
    expect(done.messages[1].status).toBe("complete");
    expect(done.lastTurn?.ttft_ms).toBe(120);
  });

  it("attaches research progress and sources", () => {
    const s = apply(
      initialLiveState,
      { type: "chat.user", conversation: conv, message: userMsg },
      { type: "chat.start", conversation_id: "c1", message_id: "a1" },
      { type: "chat.research", message_id: "a1", status: "searching", query: "dolar kuru" },
      {
        type: "chat.research",
        message_id: "a1",
        status: "done",
        query: "dolar kuru",
        sources: [{ id: 1, title: "T", url: "https://x.y", domain: "x.y", snippet: "", provider: "searxng" }],
      },
    );
    expect(s.messages[1].research?.status).toBe("done");
    expect(s.messages[1].sources).toHaveLength(1);
  });

  it("marks an empty streaming message as error and raises a toast", () => {
    const s = apply(
      initialLiveState,
      { type: "chat.user", conversation: conv, message: userMsg },
      { type: "chat.start", conversation_id: "c1", message_id: "a1" },
      { type: "chat.error", message_id: "a1", code: "llm_unavailable", message: "Ollama yok" },
    );
    expect(s.messages[1].status).toBe("error");
    expect(s.toasts.at(-1)?.level).toBe("error");
    expect(s.streamingId).toBeNull();
  });

  it("ignores stream events for other conversations", () => {
    const s = apply({ ...initialLiveState, conversationId: "other" }, { type: "chat.start", conversation_id: "c1", message_id: "a1" });
    expect(s.messages).toHaveLength(0);
  });

  it("tracks state, voice mode and user speech", () => {
    const s = apply(
      initialLiveState,
      { type: "state", state: "listening", detail: null },
      { type: "voice.mode", mode: "continuous" },
      { type: "voice.speech", active: true },
    );
    expect(s.aiState).toBe("listening");
    expect(s.voiceMode).toBe("continuous");
    expect(s.userSpeaking).toBe(true);
    expect(apply(s, { type: "voice.mode", mode: "off" }).userSpeaking).toBe(false);
  });
});

describe("visual state mapping", () => {
  it("maps internal states to the colour system", () => {
    expect(lookFor("idle").colors[0]).toBe("#00BFFF");
    expect(lookFor("listening").colors[0]).toBe("#00E5FF");
    expect(lookFor("transcribing")).toBe(lookFor("thinking"));
    expect(lookFor("thinking").colors).toContain("#FFB300");
    expect(lookFor("speaking").colors).toContain("#00FF88");
    expect(lookFor("error").colors).toContain("#FF1744");
    expect(lookFor("error").pulse).toBe(1);
    expect(stateLabel("thinking", "research")).toBe("ARAŞTIRIYOR");
  });
});

describe("helpers", () => {
  it("links citation markers outside code only", () => {
    expect(linkCitations("Bilgi [1][2].")).toBe("Bilgi [1](#cite-1)[2](#cite-2).");
    expect(linkCitations("`arr[1]` ve [3]")).toBe("`arr[1]` ve [3](#cite-3)");
    expect(linkCitations("```\nx[1]\n```")).toBe("```\nx[1]\n```");
    expect(linkCitations("[metin](https://a.b)")).toBe("[metin](https://a.b)");
  });

  it("formats sizes and durations", () => {
    expect(formatBytes(7_600_000_000)).toBe("7.6 GB");
    expect(formatBytes(63_206_254)).toBe("63.2 MB");
    expect(formatMs(486)).toBe("486 ms");
    expect(formatMs(7014)).toBe("7.01 s");
    expect(formatMs(undefined)).toBe("—");
  });
});
