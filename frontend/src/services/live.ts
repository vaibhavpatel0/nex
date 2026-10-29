// Live event client: authenticates in the first frame, resumes from the last processed seq after
// any disconnect, de-duplicates by seq, and backs off exponentially when the backend is down.
import type { RuntimeEvent } from "../types";

export type LiveState = "connecting" | "open" | "closed";
type Listener = (e: RuntimeEvent) => void;

export class LiveClient {
  private ws: WebSocket | null = null;
  private lastSeq = 0;
  private listeners = new Set<Listener>();
  private stateListeners = new Set<(s: LiveState) => void>();
  private attempt = 0;
  private timer: number | null = null;
  private stopped = true;
  state: LiveState = "closed";
  reconnects = 0;

  constructor(private getToken: () => string | null) {}

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

  /** Called after REST history loads so replay starts after what the page already shows. */
  seed(seq: number) {
    if (seq > this.lastSeq) this.lastSeq = seq;
  }

  get seq() {
    return this.lastSeq;
  }

  on(fn: Listener) {
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
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/v1/ws`);
    this.ws = ws;
    // since_seq: null on the first connection (live only); afterwards the last processed seq (replay).
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
        const ev = msg.event as RuntimeEvent;
        if (ev.seq <= this.lastSeq) return; // duplicate
        this.lastSeq = ev.seq;
        this.listeners.forEach((f) => f(ev));
      } else if (msg.type === "ping") {
        ws.send(JSON.stringify({ type: "pong" }));
      }
    };
    ws.onclose = (ev) => {
      this.ws = null;
      if (this.stopped) return;
      this.setState("closed");
      if (ev.code === 4401) return; // unauthorized: the app will send the user to sign in
      const delay = Math.min(15000, 500 * 2 ** this.attempt++);
      this.reconnects++;
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
  }
}
