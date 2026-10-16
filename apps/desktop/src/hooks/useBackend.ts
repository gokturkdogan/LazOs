import { useEffect } from "react";

import { lookFor } from "../components/ai-core/palette";
import { configureApi } from "../services/api";
import { type BackendInfo, getBackendInfo, onBackendStatus } from "../services/backend";
import { socket } from "../services/socket";
import { useApp } from "../stores/app";
import { setLevel } from "../stores/audio";

/** Boots the connection to the local backend and keeps the store in sync with its events. */
export function useBackend(): void {
  useEffect(() => {
    let disposed = false;
    let connected = false;
    let wasOffline = false;
    const store = useApp.getState;

    const offEvent = socket.onEvent((event) => {
      if (event.type === "audio.level") {
        setLevel(event.source, event.level);
        return;
      }
      if (event.type === "hello" && event.conversation_id && !store().conversationId) {
        void store().openConversation(event.conversation_id);
      }
      store().applyEvent(event);
    });

    const offStatus = socket.onStatus((status) => {
      store().setConn(status === "online" ? "online" : status === "connecting" ? "connecting" : "offline");
      if (status === "online") {
        void store().refreshConversations();
        void store()
          .loadSetup()
          .then((setup) => {
            if (setup && !setup.ready) store().setUi({ setupOpen: true });
          });
        // Re-sync the open conversation after a reconnect (messages may have completed meanwhile).
        const conv = store().conversationId;
        if (wasOffline && conv) void store().openConversation(conv);
        wasOffline = false;
      } else if (status === "offline") {
        wasOffline = true;
      }
    });

    const connect = (info: BackendInfo) => {
      if (disposed || connected) return;
      if (info.status === "error") {
        store().setConn("error", info.error);
        return;
      }
      if (info.status !== "ready" || !info.token) {
        store().setConn("booting", info.error);
        return;
      }
      connected = true;
      configureApi(info.url, info.token);
      socket.connect(info.url, info.token);
    };

    let unlisten: (() => void) | undefined;
    void onBackendStatus(connect).then((fn) => {
      unlisten = fn;
    });

    // Poll as a fallback in case the ready event fired before we subscribed.
    const poll = window.setInterval(() => {
      if (connected) return window.clearInterval(poll);
      getBackendInfo()
        .then(connect)
        .catch((err) => store().setConn("error", String(err)));
    }, 700);
    getBackendInfo()
      .then(connect)
      .catch((err) => store().setConn("error", String(err)));

    // Keep the HUD accent colour in sync with the core's state colour.
    const applyAccent = (state: Parameters<typeof lookFor>[0]) => {
      const [a, b] = lookFor(state).colors;
      document.documentElement.style.setProperty("--accent", a);
      document.documentElement.style.setProperty("--accent-2", b);
    };
    applyAccent(store().aiState);
    const offAccent = useApp.subscribe((s, prev) => {
      if (s.aiState !== prev.aiState) applyAccent(s.aiState);
      if (s.settings?.reduced_motion !== prev.settings?.reduced_motion) {
        document.documentElement.dataset.reducedMotion = String(Boolean(s.settings?.reduced_motion));
      }
    });

    return () => {
      disposed = true;
      window.clearInterval(poll);
      offEvent();
      offStatus();
      offAccent();
      unlisten?.();
      socket.close();
    };
  }, []);
}
