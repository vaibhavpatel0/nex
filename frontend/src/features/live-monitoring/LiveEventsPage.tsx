import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useApp, useLiveEvents } from "../../app/context";
import { IconPause, IconPlay } from "../../components/Icons";
import { Empty, ErrorState, Json, PageHead, Panel, Skeleton } from "../../components/ui";
import { useApi } from "../../hooks/useApi";
import { api, qs } from "../../services/api";
import type { RuntimeEvent } from "../../types";
import { EventRow } from "./EventRow";

const TYPES = [
  ["", "All event types"], ["policy.*", "Policy decisions"], ["tool.*", "Tool calls"], ["approval.*", "Approvals"],
  ["agent.*", "Agent requests and output"], ["model.*", "Model responses"], ["scenario.*", "Scenario results"],
  ["run.*", "Test runs"], ["system.*", "System"],
];
const MAX = 500;

function matchType(t: string, f: string) {
  return !f || (f.endsWith("*") ? t.startsWith(f.slice(0, -1)) : t === f);
}

export function LiveEventsPage() {
  const { liveState, live } = useApp();
  const agents = useApi("/agents");
  const [type, setType] = useState("");
  const [severity, setSeverity] = useState("");
  const [agent, setAgent] = useState("");
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const buffer = useRef<RuntimeEvent[]>([]);
  const [buffered, setBuffered] = useState(0);
  const [sel, setSel] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    api.get(`/runtime/events${qs({ limit: 200, event_type: type, severity, agent_id: agent })}`)
      .then((r) => { setEvents(r.items); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [type, severity, agent]); // eslint-disable-line react-hooks/exhaustive-deps

  const passes = (e: RuntimeEvent) => matchType(e.event_type, type) && (!severity || e.severity === severity) && (!agent || e.agent_id === agent);

  useLiveEvents((e) => {
    if (!passes(e)) return;
    if (paused) {
      buffer.current.push(e);
      setBuffered(buffer.current.length);
      return;
    }
    setEvents((cur) => (cur.some((c) => c.seq === e.seq) ? cur : [e, ...cur].slice(0, MAX)));
  }, [type, severity, agent, paused]);

  const resume = () => {
    const add = buffer.current.reverse();
    buffer.current = [];
    setBuffered(0);
    setPaused(false);
    setEvents((cur) => {
      const seen = new Set(cur.map((c) => c.seq));
      return [...add.filter((a) => !seen.has(a.seq)), ...cur].slice(0, MAX);
    });
  };

  const selected = useMemo(() => events.find((e) => e.seq === sel) ?? null, [events, sel]);
  const agentKey = (id: string | null) => agents.data?.items.find((a: any) => a.id === id)?.key ?? id;

  return (
    <>
      <PageHead
        title="Live events"
        intro="Every request, model step, policy decision and tool result, streamed as it is persisted. Missed events are replayed after reconnecting."
        actions={paused ? <button className="btn-primary" onClick={resume}><IconPlay />Resume ({buffered} new)</button>
          : <button onClick={() => setPaused(true)}><IconPause />Pause stream</button>}
      />
      <div className="row" role="group" aria-label="Filters">
        <label className="field"><span>Event type</span>
          <select value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label className="field"><span>Severity</span>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">Any severity</option>{["info", "notice", "warning", "critical", "error"].map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
        <label className="field"><span>Agent</span>
          <select value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">All agents</option>{agents.data?.items.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        <span className="small muted" style={{ alignSelf: "end", paddingBottom: 6 }}>
          Stream {liveState === "open" ? "connected" : liveState}. Last sequence {live.seq || "none"}. Reconnects this session: {live.reconnects}.
        </span>
      </div>
      <div className="grid-main-side">
        <Panel flush title={`Events (${events.length}${events.length >= MAX ? ", oldest trimmed" : ""})`}>
          {loading && !events.length ? <Skeleton rows={10} /> : error ? <ErrorState error={error} retry={load} /> :
            events.length === 0 ? <Empty title="No events match these filters">Run a test or a runtime session to generate activity.</Empty> : (
              <div className="table-wrap" style={{ maxHeight: "70vh" }}>
                <table className="t" aria-live="off">
                  <thead><tr><th className="num">Seq</th><th>Time</th><th>Severity</th><th>Type</th><th>Summary</th><th>Trace</th></tr></thead>
                  <tbody>{events.map((e) => <EventRow key={e.seq} e={e} selected={sel === e.seq} onSelect={() => setSel(sel === e.seq ? null : e.seq)} />)}</tbody>
                </table>
              </div>
            )}
        </Panel>
        <Panel title="Event detail">
          {!selected ? <p className="muted">Select an event to inspect its payload. Payloads are redacted before they are stored.</p> : (
            <div className="stack" style={{ gap: 8 }}>
              <dl className="kv">
                <dt>Type</dt><dd className="mono">{selected.event_type}</dd>
                <dt>Sequence</dt><dd>{selected.seq}</dd>
                <dt>Agent</dt><dd>{agentKey(selected.agent_id) ?? "None"}</dd>
                <dt>Correlation</dt><dd>{selected.correlation_id ? <Link to={`/traces/${selected.correlation_id}`} className="mono">{selected.correlation_id}</Link> : "None"}</dd>
                <dt>Run</dt><dd>{selected.run_id ? <Link to={`/runs/${selected.run_id}`} className="mono">{selected.run_id}</Link> : "None"}</dd>
                <dt>UTC</dt><dd className="mono">{selected.created_at}</dd>
              </dl>
              <Json value={selected.payload} />
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
