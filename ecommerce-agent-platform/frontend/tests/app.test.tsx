import { render, screen } from "@testing-library/react";
import { LiveClient } from "../src/lib/live";
import { ErrorState, Load } from "../src/lib/ui";
import { ApiError } from "../src/lib/api";

class FakeWS {
  static all: FakeWS[] = [];
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor() { FakeWS.all.push(this); }
  send(s: string) { this.sent.push(s); }
  close() {}
}

test("live client asks for live-only first, then replays from the last seq it processed", () => {
  (globalThis as any).WebSocket = FakeWS;
  const c = new LiveClient(() => "tok");
  const seen: number[] = [];
  c.on((e) => seen.push(e.seq));
  c.start();
  const ws = FakeWS.all[0];
  ws.onopen!();
  expect(JSON.parse(ws.sent[0])).toEqual({ type: "auth", token: "tok", since_seq: null });
  ws.onmessage!({ data: JSON.stringify({ type: "ready", latest_seq: 4 }) });
  ws.onmessage!({ data: JSON.stringify({ type: "event", event: { seq: 5 } }) });
  ws.onmessage!({ data: JSON.stringify({ type: "event", event: { seq: 5 } }) }); // duplicate ignored
  expect(seen).toEqual([5]);
  c.stop();
  c.start();
  const ws2 = FakeWS.all[1];
  ws2.onopen!();
  expect(JSON.parse(ws2.sent[0]).since_seq).toBe(5);
});

test("load shows skeleton, then a 404 as not found without a retry button", () => {
  const { rerender } = render(<Load r={{ data: null, error: null, loading: true, reload: () => undefined }}>{() => <p>x</p>}</Load>);
  expect(screen.getByLabelText("Loading")).toBeTruthy();
  rerender(<ErrorState error={new ApiError(404, "Order not found")} retry={() => undefined} />);
  expect(screen.getByText("Not found")).toBeTruthy();
  expect(screen.queryByText("Try again")).toBeNull();
});
