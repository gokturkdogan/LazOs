import { create } from "zustand";

import { api } from "../services/api";
import { socket } from "../services/socket";
import type { ChatMessage, ServerEvent, SetupInfo, UserSettings } from "../types/protocol";
import { initialLiveState, type LiveState, makeToast, reduceEvent, type Toast } from "./reducer";

export type ConnStatus = "booting" | "connecting" | "online" | "offline" | "error";

interface UiState {
  chatOpen: boolean;
  historyOpen: boolean;
  settingsOpen: boolean;
  setupOpen: boolean;
}

interface AppState extends LiveState {
  conn: ConnStatus;
  connError: string | null;
  setup: SetupInfo | null;
  ui: UiState;

  // actions
  setConn: (conn: ConnStatus, error?: string | null) => void;
  applyEvent: (e: ServerEvent) => void;
  setUi: (patch: Partial<UiState>) => void;
  pushToast: (level: Toast["level"], message: string) => void;
  dismissToast: (id: number) => void;

  sendMessage: (text: string) => void;
  cancel: () => void;
  stopSpeaking: () => void;
  toggleMic: () => void;
  setContinuous: (enabled: boolean) => void;

  refreshConversations: () => Promise<void>;
  openConversation: (id: string) => Promise<void>;
  newConversation: () => void;
  deleteConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;

  loadSetup: () => Promise<SetupInfo | null>;
  updateSettings: (patch: Partial<UserSettings>) => Promise<void>;
}

export const useApp = create<AppState>((set, get) => ({
  ...initialLiveState,
  conn: "booting",
  connError: null,
  setup: null,
  ui: { chatOpen: true, historyOpen: false, settingsOpen: false, setupOpen: false },

  setConn: (conn, error = null) => set({ conn, connError: error }),

  applyEvent: (e) => {
    const patch = reduceEvent(get(), e);
    if (patch) set(patch);
  },

  setUi: (patch) => set((s) => ({ ui: { ...s.ui, ...patch } })),

  pushToast: (level, message) => set((s) => ({ toasts: [...s.toasts, makeToast(level, message)].slice(-4) })),
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  sendMessage: (text) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    socket.send({ type: "chat.send", text: trimmed, conversation_id: get().conversationId });
  },
  cancel: () => socket.send({ type: "chat.cancel" }),
  stopSpeaking: () => socket.send({ type: "speech.stop" }),

  toggleMic: () => {
    const { voiceMode, aiState, conversationId } = get();
    if (voiceMode === "ptt" && aiState === "listening") {
      socket.send({ type: "voice.ptt", action: "stop", conversation_id: conversationId });
    } else {
      socket.send({ type: "voice.ptt", action: "start", conversation_id: conversationId });
    }
  },
  setContinuous: (enabled) =>
    socket.send({ type: "voice.continuous", enabled, conversation_id: get().conversationId }),

  refreshConversations: async () => {
    try {
      set({ conversations: await api.conversations() });
    } catch {
      /* surfaced through connection status */
    }
  },

  openConversation: async (id) => {
    try {
      const detail = await api.conversation(id);
      const messages: ChatMessage[] = detail.messages.map((m) => ({ ...m, research: null }));
      set({ conversationId: id, messages, streamingId: null, caption: "" });
      socket.send({ type: "conversation.select", conversation_id: id });
    } catch (err) {
      get().pushToast("error", `Konuşma açılamadı: ${(err as Error).message}`);
    }
  },

  newConversation: () => {
    set({ conversationId: null, messages: [], streamingId: null, caption: "" });
    socket.send({ type: "conversation.select", conversation_id: null });
  },

  deleteConversation: async (id) => {
    try {
      await api.deleteConversation(id);
      set((s) => ({
        conversations: s.conversations.filter((c) => c.id !== id),
        ...(s.conversationId === id ? { conversationId: null, messages: [], streamingId: null } : {}),
      }));
    } catch (err) {
      get().pushToast("error", `Silinemedi: ${(err as Error).message}`);
    }
  },

  renameConversation: async (id, title) => {
    try {
      const updated = await api.renameConversation(id, title);
      set((s) => ({ conversations: s.conversations.map((c) => (c.id === id ? { ...c, title: updated.title } : c)) }));
    } catch (err) {
      get().pushToast("error", `Yeniden adlandırılamadı: ${(err as Error).message}`);
    }
  },

  loadSetup: async () => {
    try {
      const setup = await api.setup();
      set({ setup, models: setup.models });
      return setup;
    } catch (err) {
      get().pushToast("error", `Kurulum durumu alınamadı: ${(err as Error).message}`);
      return null;
    }
  },

  updateSettings: async (patch) => {
    const prev = get().settings;
    if (prev) set({ settings: { ...prev, ...patch } });
    try {
      set({ settings: await api.updateSettings(patch) });
    } catch (err) {
      set({ settings: prev });
      get().pushToast("error", `Ayar kaydedilemedi: ${(err as Error).message}`);
    }
  },
}));
