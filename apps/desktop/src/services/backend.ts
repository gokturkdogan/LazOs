/**
 * Discovers how to reach the local backend.
 *
 * Inside Tauri the Rust shell supervises the Python backend and hands us its URL + access token.
 * When the UI runs in a plain browser (UI development only), VITE_LAZOS_TOKEN must be provided.
 */
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export interface BackendInfo {
  status: "starting" | "ready" | "error";
  url: string;
  token: string | null;
  error: string | null;
  managed: boolean;
  log_path: string | null;
}

export const isTauri = (): boolean => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export async function getBackendInfo(): Promise<BackendInfo> {
  if (isTauri()) {
    return invoke<BackendInfo>("backend_info");
  }
  const port = import.meta.env.VITE_LAZOS_PORT ?? "47811";
  return {
    status: "ready",
    url: `http://127.0.0.1:${port}`,
    token: import.meta.env.VITE_LAZOS_TOKEN ?? null,
    error: import.meta.env.VITE_LAZOS_TOKEN ? null : "VITE_LAZOS_TOKEN tanımlı değil (tarayıcı geliştirme modu).",
    managed: false,
    log_path: null,
  };
}

export async function restartBackend(): Promise<void> {
  if (isTauri()) await invoke("restart_backend");
}

export async function onBackendStatus(cb: (info: BackendInfo) => void): Promise<() => void> {
  if (!isTauri()) return () => {};
  return listen<BackendInfo>("backend://status", (e) => cb(e.payload));
}

export async function openExternal(url: string): Promise<void> {
  if (!/^https?:\/\//i.test(url)) return;
  if (isTauri()) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}
