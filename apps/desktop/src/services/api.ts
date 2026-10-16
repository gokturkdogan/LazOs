import type {
  AudioDevice,
  CoreIdentity,
  EpisodeHit,
  MemoryCategory,
  MemoryItem,
  FeedbackReason,
  LearningOverview,
  LearningSuggestion,
  MemoryOverview,
  MessageFeedback,
  ResponseCorrection,
  TrainingPreview,
  PronunciationOverview,
  PronunciationRule,
  ConversationDetail,
  ConversationSummary,
  SetupInfo,
  SystemStatus,
  UserSettings,
} from "../types/protocol";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let base = "";
let token = "";

export function configureApi(url: string, accessToken: string): void {
  base = url.replace(/\/$/, "");
  token = accessToken;
}

async function request<T>(method: string, path: string, body?: unknown, timeoutMs = 15000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${base}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if ((err as Error).name === "AbortError") throw new ApiError(0, "İstek zaman aşımına uğradı");
    throw new ApiError(0, "Yerel servise ulaşılamıyor");
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  status: () => request<SystemStatus & { state: string; voice_mode: string }>("GET", "/api/status"),
  setup: () => request<SetupInfo>("GET", "/api/setup", undefined, 20000),
  install: (ids: string[]) => request<{ started: string[] }>("POST", "/api/setup/install", { ids }),
  startOllama: () =>
    request<{ started: boolean; running: boolean; error?: string }>("POST", "/api/setup/ollama/start", undefined, 30000),
  micTest: () => request<{ ok: boolean; error?: string; peak?: number }>("POST", "/api/setup/mic-test", undefined, 10000),

  conversations: () => request<ConversationSummary[]>("GET", "/api/conversations"),
  conversation: (id: string) => request<ConversationDetail>("GET", `/api/conversations/${id}`),
  renameConversation: (id: string, title: string) =>
    request<ConversationSummary>("PATCH", `/api/conversations/${id}`, { title }),
  deleteConversation: (id: string) => request<void>("DELETE", `/api/conversations/${id}`),

  settings: () => request<UserSettings>("GET", "/api/settings"),
  updateSettings: (patch: Partial<UserSettings>) => request<UserSettings>("PUT", "/api/settings", patch),

  devices: (refresh = false) =>
    request<{ inputs: AudioDevice[]; outputs: AudioDevice[] }>("GET", `/api/audio/devices?refresh=${refresh}`),
  memory: () => request<MemoryOverview>("GET", "/api/memory"),
  saveIdentity: (identity: CoreIdentity) => request<CoreIdentity>("PUT", "/api/memory/identity", identity),
  resetIdentity: () => request<CoreIdentity>("POST", "/api/memory/identity/reset"),
  addMemory: (category: MemoryCategory, content: string) =>
    request<MemoryItem & { created: boolean }>("POST", "/api/memory/items", { category, content }),
  updateMemory: (id: string, patch: { category?: MemoryCategory; content?: string }) =>
    request<MemoryItem>("PATCH", `/api/memory/items/${id}`, patch),
  deleteMemory: (id: string) => request<void>("DELETE", `/api/memory/items/${id}`),
  clearMemories: () => request<{ deleted: number }>("DELETE", "/api/memory/items?confirm=true"),
  searchEpisodes: (q: string) => request<{ query: string; hits: EpisodeHit[] }>("GET", `/api/memory/episodes?q=${encodeURIComponent(q)}`),

  pronunciation: () => request<PronunciationOverview>("GET", "/api/pronunciation"),
  addPronunciation: (rule: Pick<PronunciationRule, "term" | "replacement" | "engine" | "case_sensitive">) =>
    request<PronunciationRule>("POST", "/api/pronunciation/rules", rule),
  updatePronunciation: (id: string, patch: Partial<Pick<PronunciationRule, "term" | "replacement" | "engine" | "case_sensitive" | "enabled">>) =>
    request<PronunciationRule>("PATCH", `/api/pronunciation/rules/${id}`, patch),
  deletePronunciation: (id: string) => request<void>("DELETE", `/api/pronunciation/rules/${id}`),
  testPronunciation: (text: string, engine?: "piper" | "macos", speak = false) =>
    request<{ original: string; spoken: Record<string, string>; played_with?: string }>(
      "POST",
      "/api/pronunciation/test",
      { text, engine, speak },
      30000,
    ),

  rateMessage: (id: string, rating: 1 | -1, reasons: FeedbackReason[] = [], note?: string) =>
    request<{ feedback: MessageFeedback; suggestions: LearningSuggestion[] }>("POST", `/api/feedback/messages/${id}/rating`, {
      rating,
      reasons,
      note: note || null,
    }),
  withdrawRating: (id: string) => request<void>("DELETE", `/api/feedback/messages/${id}/rating`),
  correctMessage: (id: string, preferred: string, useAsExample: boolean) =>
    request<{ correction: ResponseCorrection; suggestions: LearningSuggestion[] }>("POST", `/api/feedback/messages/${id}/correction`, {
      preferred,
      use_as_example: useAsExample,
    }),
  correctTranscript: (id: string, corrected: string) =>
    request<{ original: string; corrected: string; terms: string[]; suggestions: LearningSuggestion[] }>(
      "POST",
      `/api/feedback/messages/${id}/transcript`,
      { corrected },
    ),
  learning: () => request<LearningOverview>("GET", "/api/feedback"),
  updateCorrection: (id: string, patch: Partial<Pick<ResponseCorrection, "preferred_response" | "kind" | "status">>) =>
    request<ResponseCorrection>("PATCH", `/api/feedback/corrections/${id}`, {
      preferred: patch.preferred_response,
      kind: patch.kind,
      status: patch.status,
    }),
  deleteCorrection: (id: string) => request<void>("DELETE", `/api/feedback/corrections/${id}`),
  deleteTranscriptCorrection: (id: string) => request<void>("DELETE", `/api/feedback/transcripts/${id}`),
  decideSuggestion: (id: string, action: "approve" | "reject" | "revoke", proposal?: string) =>
    request<LearningSuggestion>("POST", `/api/feedback/suggestions/${id}/${action}`, proposal ? { proposal } : {}),
  editSuggestion: (id: string, proposal: string) => request<LearningSuggestion>("PATCH", `/api/feedback/suggestions/${id}`, { proposal }),
  clearFeedback: () => request<Record<string, number>>("DELETE", "/api/feedback?confirm=true"),
  exportPreview: () => request<TrainingPreview>("GET", "/api/feedback/export/preview"),
  exportFeedback: (kind: "training" | "all", skipIds: string[] = []) =>
    request<{ path: string; records: number }>("POST", "/api/feedback/export", { kind, skip_ids: skipIds }),

  previewVoice: (engine?: "piper" | "macos") =>
    request<{ engine: string; duration_s: number }>("POST", "/api/voice/preview", engine ? { engine } : {}, 30000),
};
