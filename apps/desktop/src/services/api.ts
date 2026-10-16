import type {
  AudioDevice,
  CoreIdentity,
  EpisodeHit,
  MemoryCategory,
  MemoryItem,
  MemoryOverview,
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

  previewVoice: (engine?: "piper" | "macos") =>
    request<{ engine: string; duration_s: number }>("POST", "/api/voice/preview", engine ? { engine } : {}, 30000),
};
