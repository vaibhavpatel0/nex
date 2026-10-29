import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { useLiveEvents, useSession } from "../lib/session";
import { Empty, FormError, LiveDot, PageHead, Panel, Skeleton, Time } from "../lib/ui";

interface Msg { role: "user" | "assistant" | "system_notice"; content: string; correlation_id: string | null; created_at: string; run_status: string | null }

// Plain-language progress for the customer, derived from the run's real backend state.
const PROGRESS: Record<string, string> = {
  RUNNING: "Starting",
  WAITING_FOR_MODEL: "The assistant is thinking",
  TOOL_REQUESTED: "Looking something up",
  POLICY_CHECKING: "Checking store security rules",
  TOOL_EXECUTING: "Looking something up",
  BLOCKED: "An action was not allowed",
  WAITING_FOR_APPROVAL: "An action is waiting for staff review",
};

const SUGGESTIONS = [
  "Find wireless headphones under ₹3,000",
  "What is the status of my order ORD-7001?",
  "Can I cancel order ORD-7001?",
  "What is the returns policy?",
];

export function Assistant() {
  const { liveState, refreshCart } = useSession();
  const convs = useApi<{ items: { id: string; created_at: string }[] }>("/chat/conversations");
  const [convId, setConvId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[] | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<{ cid: string; state: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (convs.data && convId === null) setConvId(convs.data.items[0]?.id ?? "");
  }, [convs.data, convId]);

  const load = (id: string) =>
    api.get<{ messages: Msg[] }>(`/chat/conversations/${id}`).then((d) => {
      setMsgs(d.messages);
      const running = [...d.messages].reverse().find((m) => m.role === "user" && m.run_status && !["COMPLETED", "FAILED"].includes(m.run_status));
      setPending(running?.correlation_id ? { cid: running.correlation_id, state: running.run_status! } : null);
    });

  useEffect(() => {
    if (convId === null) return;
    if (!convId) {
      setMsgs([]);
      return;
    }
    load(convId).catch((e) => setErr(e.message));
  }, [convId]);

  // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect must not return one.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [msgs, pending]);

  useLiveEvents(
    (e) => {
      if (!pending || e.correlation_id !== pending.cid) return;
      if (e.type === "run.completed" || e.type === "run.failed") {
        if (convId) load(convId).catch(() => undefined);
        refreshCart();
      } else if (e.state) setPending({ cid: pending.cid, state: e.state });
    },
    [pending?.cid, convId],
  );

  // If the live stream drops while a run is in flight, poll the persisted conversation instead.
  useEffect(() => {
    if (!pending || !convId || liveState === "open") return;
    const t = window.setInterval(() => load(convId).catch(() => undefined), 3000);
    return () => window.clearInterval(t);
  }, [pending, convId, liveState]);

  const send = async (message: string) => {
    if (!message.trim() || pending) return;
    setErr(null);
    try {
      const r = await api.post<{ correlation_id: string; conversation_id: string }>("/chat/messages", { message: message.trim(), conversation_id: convId || null });
      setPending({ cid: r.correlation_id, state: "RUNNING" });
      setMsgs((m) => [...(convId === r.conversation_id ? m ?? [] : []), { role: "user", content: message.trim(), correlation_id: r.correlation_id, created_at: new Date().toISOString(), run_status: "RUNNING" }]);
      if (convId !== r.conversation_id) {
        setConvId(r.conversation_id);
        convs.reload();
      }
      setText("");
    } catch (x: any) {
      setErr(x.message);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(text);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(text);
    }
  };

  return (
    <>
      <PageHead
        title="Shopping assistant"
        intro="An AI model answers using the store's tools. Every action it proposes is checked by SENTINEL before it runs. Cancellations and returns always go to staff for review."
        actions={
          <>
            <LiveDot state={liveState} />
            <button onClick={() => { setConvId(""); setMsgs([]); setPending(null); }} disabled={!!pending}>New conversation</button>
          </>
        }
      />
      <Panel flush label="Conversation">
        <div className="assistant">
          <div className="panel-head">
            <h2>Conversation</h2>
            {convs.data && convs.data.items.length > 1 ? (
              <label className="row small">
                <span className="muted">Earlier</span>
                <select value={convId ?? ""} onChange={(e) => setConvId(e.target.value)} disabled={!!pending}>
                  <option value="">New conversation</option>
                  {convs.data.items.map((c) => <option key={c.id} value={c.id}>{new Date(c.created_at).toLocaleString("en-IN")}</option>)}
                </select>
              </label>
            ) : null}
          </div>
          <div className="msgs" aria-live="polite">
            {msgs === null ? <Skeleton rows={3} /> : msgs.length === 0 ? (
              <Empty title="Ask about products, your cart, orders or store policies">
                <div className="row" style={{ marginTop: 8 }}>
                  {SUGGESTIONS.map((s) => <button key={s} className="btn-sm" onClick={() => send(s)}>{s}</button>)}
                </div>
              </Empty>
            ) : (
              msgs.map((m, i) => (
                <div key={i} className={`msg msg-${m.role}`}>
                  {m.role === "system_notice" ? <b>Notice: </b> : null}
                  {m.content}
                  <div className="msg-meta">{m.role === "user" ? "You" : m.role === "assistant" ? "Assistant" : "Store system"} · <Time iso={m.created_at} rel /></div>
                </div>
              ))
            )}
            {pending ? (
              <div className="progress-line" role="status">
                <span className="live-dot" data-state="connecting" aria-hidden="true" />
                {PROGRESS[pending.state] ?? "Working"}
              </div>
            ) : null}
            <div ref={endRef} />
          </div>
          <form className="composer" onSubmit={submit}>
            <label className="sr-only" htmlFor="chat-input">Message</label>
            <textarea id="chat-input" rows={2} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
              placeholder={pending ? "Waiting for the assistant to finish" : "Type a message. Enter sends, Shift+Enter adds a line."} disabled={!!pending} />
            <button className="btn-primary" disabled={!!pending || !text.trim()}>Send</button>
          </form>
        </div>
      </Panel>
      <FormError msg={err} />
      <p className="small muted">Answers come from a language model and can be wrong. Prices, stock and order details come from the store's database through tools.</p>
    </>
  );
}
