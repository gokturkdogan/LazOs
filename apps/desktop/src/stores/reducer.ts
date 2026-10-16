import type {
  AIState,
  ChatMessage,
  ConversationSummary,
  ModelInfo,
  ServerEvent,
  SystemStatus,
  TurnMetrics,
  UserSettings,
  VoiceMode,
} from "../types/protocol";

export interface Toast {
  id: number;
  level: "info" | "error";
  message: string;
}

/** The slice of application state that is driven purely by backend events. */
export interface LiveState {
  aiState: AIState;
  aiDetail: string | null;
  system: SystemStatus | null;
  settings: UserSettings | null;
  models: ModelInfo[] | null;
  voiceMode: VoiceMode;
  userSpeaking: boolean;
  conversationId: string | null;
  conversations: ConversationSummary[];
  messages: ChatMessage[];
  streamingId: string | null;
  lastTurn: TurnMetrics | null;
  caption: string;
  toasts: Toast[];
}

export const initialLiveState: LiveState = {
  aiState: "idle",
  aiDetail: null,
  system: null,
  settings: null,
  models: null,
  voiceMode: "off",
  userSpeaking: false,
  conversationId: null,
  conversations: [],
  messages: [],
  streamingId: null,
  lastTurn: null,
  caption: "",
  toasts: [],
};

let toastSeq = 0;
export function makeToast(level: Toast["level"], message: string): Toast {
  toastSeq += 1;
  return { id: toastSeq, level, message };
}

function upsertConversation(list: ConversationSummary[], conv: ConversationSummary): ConversationSummary[] {
  const rest = list.filter((c) => c.id !== conv.id);
  return [conv, ...rest];
}

function lastSentence(text: string): string {
  const clean = text.replace(/```[\s\S]*?(```|$)/g, " ").replace(/[#*_`>[\]]/g, "").trim();
  const parts = clean.split(/(?<=[.!?…])\s+/).filter(Boolean);
  return (parts[parts.length - 1] ?? "").slice(-160);
}

/** Pure reducer: (state, server event) → partial state update. */
export function reduceEvent(s: LiveState, e: ServerEvent): Partial<LiveState> | null {
  switch (e.type) {
    case "hello":
      return { settings: e.settings };
    case "state":
      return { aiState: e.state, aiDetail: e.detail };
    case "system": {
      const { type: _t, ...system } = e;
      return { system: system as SystemStatus, lastTurn: system.last_turn ?? s.lastTurn };
    }
    case "settings":
      return { settings: e.settings };
    case "setup.models":
      return { models: e.models };
    case "voice.mode":
      return { voiceMode: e.mode, userSpeaking: e.mode === "off" ? false : s.userSpeaking };
    case "voice.speech":
      return { userSpeaking: e.active };
    case "voice.transcript":
      return { caption: e.text };
    case "voice.notice":
      return { toasts: [...s.toasts, makeToast(e.level, e.message)].slice(-4) };
    case "chat.user": {
      const switching = s.conversationId !== e.conversation.id;
      const base = switching ? [] : s.messages.filter((m) => m.id !== e.message.id);
      return {
        conversationId: e.conversation.id,
        conversations: upsertConversation(s.conversations, e.conversation),
        messages: [...base, e.message],
      };
    }
    case "chat.start": {
      if (e.conversation_id !== s.conversationId) return null;
      const placeholder: ChatMessage = {
        id: e.message_id,
        conversation_id: e.conversation_id,
        role: "assistant",
        content: "",
        created_at: Date.now() / 1000,
        origin: "text",
        status: "streaming",
        sources: [],
        research: null,
      };
      return { messages: [...s.messages, placeholder], streamingId: e.message_id, caption: "" };
    }
    case "chat.delta": {
      const idx = s.messages.findIndex((m) => m.id === e.message_id);
      if (idx === -1) return null;
      const msg = s.messages[idx];
      const content = msg.content + e.text;
      const messages = s.messages.slice();
      messages[idx] = { ...msg, content };
      return { messages, caption: lastSentence(content) };
    }
    case "chat.research": {
      const idx = s.messages.findIndex((m) => m.id === e.message_id);
      if (idx === -1) return null;
      const messages = s.messages.slice();
      const { type: _t, message_id: _m, ...research } = e;
      messages[idx] = { ...messages[idx], research, sources: research.sources ?? messages[idx].sources };
      return { messages };
    }
    case "chat.done": {
      const idx = s.messages.findIndex((m) => m.id === e.message_id);
      const messages = s.messages.slice();
      if (idx !== -1) {
        if (e.message) {
          messages[idx] = { ...e.message, research: messages[idx].research };
        } else {
          messages.splice(idx, 1);
        }
      }
      const conversations = s.conversations.map((c) =>
        c.id === s.conversationId ? { ...c, updated_at: Date.now() / 1000, message_count: c.message_count + 1 } : c,
      );
      return {
        messages,
        conversations,
        streamingId: s.streamingId === e.message_id ? null : s.streamingId,
        lastTurn: e.message?.metrics ?? s.lastTurn,
      };
    }
    case "chat.error": {
      const toasts = [...s.toasts, makeToast("error", e.message)].slice(-4);
      if (!e.message_id) return { toasts, streamingId: null };
      const idx = s.messages.findIndex((m) => m.id === e.message_id);
      if (idx === -1) return { toasts, streamingId: null };
      const messages = s.messages.slice();
      const msg = messages[idx];
      if (msg.content) {
        messages[idx] = { ...msg, status: "interrupted" };
      } else {
        messages[idx] = { ...msg, status: "error", error: e.message };
      }
      return { messages, toasts, streamingId: null };
    }
    case "metrics":
      return { lastTurn: e.turn };
    case "error":
      return { toasts: [...s.toasts, makeToast("error", e.message)].slice(-4) };
    default:
      return null;
  }
}
