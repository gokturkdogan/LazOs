import type { ClientMessage, ServerEvent } from "../types/protocol";

type Listener = (event: ServerEvent) => void;
type StatusListener = (status: "connecting" | "online" | "offline") => void;

/**
 * Resilient WebSocket client: exponential-backoff reconnect, heartbeat, and a small outbox so
 * messages sent during a reconnect are delivered once the link is back.
 */
export class LazosSocket {
  private ws: WebSocket | null = null;
  private url = "";
  private listeners = new Set<Listener>();
  private statusListeners = new Set<StatusListener>();
  private retry = 0;
  private reconnectTimer: number | undefined;
  private heartbeat: number | undefined;
  private outbox: ClientMessage[] = [];
  private closedByUser = false;

  connect(httpUrl: string, token: string): void {
    this.url = `${httpUrl.replace(/^http/, "ws")}/ws?token=${encodeURIComponent(token)}`;
    this.closedByUser = false;
    this.open();
  }

  private open(): void {
    window.clearTimeout(this.reconnectTimer);
    this.emitStatus("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;

    ws.onopen = () => {
      this.retry = 0;
      this.emitStatus("online");
      for (const msg of this.outbox.splice(0)) ws.send(JSON.stringify(msg));
      window.clearInterval(this.heartbeat);
      this.heartbeat = window.setInterval(() => this.send({ type: "ping" }), 15000);
    };
    ws.onmessage = (ev) => {
      let event: ServerEvent;
      try {
        event = JSON.parse(ev.data as string) as ServerEvent;
      } catch {
        return;
      }
      for (const l of this.listeners) l(event);
    };
    ws.onclose = () => {
      window.clearInterval(this.heartbeat);
      if (this.ws !== ws) return;
      this.ws = null;
      this.emitStatus("offline");
      if (!this.closedByUser) this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  private scheduleReconnect(): void {
    const delay = Math.min(5000, 400 * 2 ** this.retry) + Math.random() * 250;
    this.retry += 1;
    this.reconnectTimer = window.setTimeout(() => this.open(), delay);
  }

  send(msg: ClientMessage): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    } else if (msg.type !== "ping") {
      this.outbox.push(msg);
      if (this.outbox.length > 20) this.outbox.shift();
    }
  }

  onEvent(l: Listener): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  onStatus(l: StatusListener): () => void {
    this.statusListeners.add(l);
    return () => this.statusListeners.delete(l);
  }

  private emitStatus(s: "connecting" | "online" | "offline"): void {
    for (const l of this.statusListeners) l(s);
  }

  close(): void {
    this.closedByUser = true;
    window.clearTimeout(this.reconnectTimer);
    window.clearInterval(this.heartbeat);
    this.ws?.close();
    this.ws = null;
  }
}

export const socket = new LazosSocket();
