import { render, screen } from "@testing-library/react";
import { DecisionTok, OutcomeTok, pct } from "../src/components/ui";
import { LiveClient } from "../src/services/live";

class FakeWS {
  static last: FakeWS | null = null;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor(public url: string) { FakeWS.last = this; setTimeout(() => this.onopen?.(), 0); }
  send(s: string) { this.sent.push(s); }
  close() { this.onclose?.({ code: 1000 }); }
  emit(o: unknown) { this.onmessage?.({ data: JSON.stringify(o) }); }
}

const ev = (seq: number) => ({ seq, event_id: `e${seq}`, correlation_id: null, run_id: null, agent_id: null, event_type: "tool.requested", severity: "info", summary: "", payload: {}, created_at: "" });

describe("LiveClient", () => {
  beforeEach(() => { (globalThis as any).WebSocket = FakeWS; vi.useFakeTimers(); });
  afterEach(() => vi.useRealTimers());

  it("authenticates in the first frame, de-duplicates and resumes from the last seq", async () => {
    const c = new LiveClient(() => "tok");
    const got: number[] = [];
    c.on((e) => got.push(e.seq));
    c.start();
    await vi.advanceTimersByTimeAsync(1);
    const first = FakeWS.last!;
    expect(JSON.parse(first.sent[0])).toEqual({ type: "auth", token: "tok", since_seq: 0 });
    first.emit({ type: "ready", latest_seq: 10 });
    first.emit({ type: "event", event: ev(11) });
    first.emit({ type: "event", event: ev(11) });
    first.emit({ type: "event", event: ev(9) });
    first.emit({ type: "event", event: ev(12) });
    expect(got).toEqual([11, 12]);
    first.onclose?.({ code: 1006 });
    expect(c.state).toBe("closed");
    await vi.advanceTimersByTimeAsync(600);
    const second = FakeWS.last!;
    expect(second).not.toBe(first);
    expect(JSON.parse(second.sent[0]).since_seq).toBe(12);
    c.stop();
  });

  it("does not reconnect after an unauthorized close", async () => {
    const c = new LiveClient(() => "bad");
    c.start();
    await vi.advanceTimersByTimeAsync(1);
    const ws = FakeWS.last!;
    ws.onclose?.({ code: 4401 });
    await vi.advanceTimersByTimeAsync(20000);
    expect(FakeWS.last).toBe(ws);
  });
});

describe("status tokens", () => {
  it("carry text, not only colour", () => {
    render(<><DecisionTok d="BLOCK" /><DecisionTok d="REQUIRE_APPROVAL" /><OutcomeTok o="INCONCLUSIVE" /></>);
    expect(screen.getByText("Blocked")).toBeTruthy();
    expect(screen.getByText("Needs approval")).toBeTruthy();
    expect(screen.getByText("INCONCLUSIVE")).toBeTruthy();
  });
  it("never shows a number for missing data", () => {
    expect(pct(null)).toBe("Insufficient data");
    expect(pct(0.5)).toBe("50.0%");
  });
});
