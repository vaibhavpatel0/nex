// Live agent events over the shop WebSocket. Authenticates in the first frame, resumes from the last
// processed seq after a disconnect (the server replays persisted events), de-duplicates by seq and backs off.
export interface AgentEvent {
  seq: number;
  event_id: string;
  correlation_id: string;
  type: string;
  state: string | null;
  tool: string | null;
  decision: string | null;
  execution_status: string | null;
  error_class: string | null;
  payload: any;
  created_at: string;
}
export type LiveState = "connecting" | "open" | "closed";

import { API_ORIGIN } from "./api";

function wsUrl(path: string): string {
  if (API_ORIGIN) return API_ORIGIN.replace(/^http/, "ws") + path;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

export class LiveClient {
  private ws: WebSocket | null = null;
  private lastSeq = 0;
  private attempt = 0;
  private timer: number | null = null;
  private stopped = true;
  private listeners = new Set<(e: AgentEvent, replayed: boolean) => void>();
  private stateListeners = new Set<(s: LiveState) => void>();
  state: LiveState = "closed";

  constructor(private getToken: () => string | null, private path = "/api/ws") {}

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.timer) window.clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
    this.setState("closed");
  }

  /** After REST history loads, replay starts after what the page already shows. */
  seed(seq: number) {
    if (seq > this.lastSeq) this.lastSeq = seq;
  }

  on(fn: (e: AgentEvent, replayed: boolean) => void) {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }

  onState(fn: (s: LiveState) => void) {
    this.stateListeners.add(fn);
    fn(this.state);
    return () => {
      this.stateListeners.delete(fn);
    };
  }

  private setState(s: LiveState) {
    this.state = s;
    this.stateListeners.forEach((f) => f(s));
  }

  private connect() {
    const token = this.getToken();
    if (!token || this.stopped) return;
    this.setState("connecting");
    const ws = new WebSocket(wsUrl(this.path));
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token, since_seq: this.lastSeq || null }));
    ws.onmessage = (m) => {
      let msg: any;
      try {
        msg = JSON.parse(m.data);
      } catch {
        return;
      }
      if (msg.type === "ready") {
        this.attempt = 0;
        if (!this.lastSeq) this.lastSeq = msg.latest_seq;
        this.setState("open");
      } else if (msg.type === "event") {
        const ev = msg.event as AgentEvent;
        if (ev.seq <= this.lastSeq) return;
        this.lastSeq = ev.seq;
        this.listeners.forEach((f) => f(ev, !!msg.replayed));
      } else if (msg.type === "ping") {
        ws.send(JSON.stringify({ type: "pong" }));
      }
    };
    ws.onclose = (ev) => {
      this.ws = null;
      if (this.stopped) return;
      this.setState("closed");
      if (ev.code === 4401) return;
      const delay = Math.min(15000, 500 * 2 ** this.attempt++);
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
  }
}
