import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useLiveEvents } from "../../app/context";
import { Empty, Load, ms, OutcomeTok, PageHead, Panel, Pager, Short, Time } from "../../components/ui";
import { useApi } from "../../hooks/useApi";
import { qs } from "../../services/api";
import { TraceSpine } from "./TraceSpine";

export function TracesPage() {
  const agents = useApi("/agents");
  const [f, setF] = useState({ q: "", agent_id: "", kind: "", status: "", has_block: "", run_id: "", start: "", end: "" });
  const [offset, setOffset] = useState(0);
  const params = { ...f, start: f.start ? new Date(f.start).toISOString() : "", end: f.end ? new Date(f.end).toISOString() : "", limit: 25, offset };
  const r = useApi(`/traces${qs(params)}`, [JSON.stringify(params)]);
  const set = (k: string, v: string) => { setF({ ...f, [k]: v }); setOffset(0); };
  return (
    <>
      <PageHead title="Execution traces" intro="One trace per agent session or test scenario, keyed by correlation ID. Open a trace to see each step from request to evaluation." />
      <div className="row" role="search" aria-label="Filter traces">
        <label className="field"><span>Search</span><input value={f.q} onChange={(e) => set("q", e.target.value)} placeholder="Correlation ID, task text or scenario" /></label>
        <label className="field"><span>Agent</span><select value={f.agent_id} onChange={(e) => set("agent_id", e.target.value)}><option value="">All agents</option>{agents.data?.items.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        <label className="field"><span>Kind</span><select value={f.kind} onChange={(e) => set("kind", e.target.value)}><option value="">Any</option><option value="test">Test scenario</option><option value="runtime">Runtime session</option><option value="direct">Direct tool call</option></select></label>
        <label className="field"><span>Status</span><select value={f.status} onChange={(e) => set("status", e.target.value)}><option value="">Any</option><option value="completed">Completed</option><option value="error">Error</option><option value="running">Running</option></select></label>
        <label className="field"><span>Contains a block</span><select value={f.has_block} onChange={(e) => set("has_block", e.target.value)}><option value="">Either</option><option value="true">Yes</option></select></label>
        <label className="field"><span>Run ID</span><input value={f.run_id} onChange={(e) => set("run_id", e.target.value.trim())} placeholder="run_..." /></label>
        <label className="field"><span>From</span><input type="datetime-local" value={f.start} onChange={(e) => set("start", e.target.value)} /></label>
        <label className="field"><span>To</span><input type="datetime-local" value={f.end} onChange={(e) => set("end", e.target.value)} /></label>
      </div>
      <Panel flush>
        <Load r={r} rows={8} empty={(d: any) => d.items.length === 0 ? <Empty title="No traces match">Traces appear when an agent runs, in a test or a runtime session.</Empty> : null}>
          {(d: any) => (
            <>
              <div className="table-wrap"><table className="t">
                <thead><tr><th>Trace</th><th>Kind</th><th>Agent</th><th>Task</th><th className="num">Executed</th><th className="num">Blocked</th><th className="num">Approval</th><th className="num">Failed</th><th>Outcome</th><th>Latency</th><th>Started</th></tr></thead>
                <tbody>{d.items.map((t: any) => (
                  <tr key={t.id}>
                    <td><Short id={t.id} to={`/traces/${t.id}`} /></td><td className="small">{t.kind}{t.scenario_key ? ` ${t.scenario_key}` : ""}</td>
                    <td className="mono small">{t.agent_key}</td><td className="small wrap">{t.task_redacted.slice(0, 120)}</td>
                    <td className="num">{t.call_stats.executed ?? 0}</td><td className="num">{t.call_stats.blocked ?? 0}</td><td className="num">{t.call_stats.pending_approval ?? 0}</td><td className="num">{t.call_stats.failed ?? 0}</td>
                    <td>{t.outcome ? <OutcomeTok o={t.outcome} /> : <span className="small dim">{t.status === "error" ? "Agent error" : "Not evaluated"}</span>}</td>
                    <td className="small">{ms(t.latency_ms)}</td><td className="small"><Time iso={t.started_at} rel /></td>
                  </tr>
                ))}</tbody>
              </table></div>
              <Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} />
            </>
          )}
        </Load>
      </Panel>
    </>
  );
}

export function TraceDetailPage() {
  const { id } = useParams();
  const r = useApi(`/traces/${id}`, [id]);
  useLiveEvents((e) => { if (e.correlation_id === id) r.reload(); }, [id]);
  return (
    <Load r={r} rows={10}>
      {(t: any) => (
        <>
          <PageHead title={t.trace.scenario_key ? `Trace for ${t.trace.scenario_key}` : "Execution trace"}
            intro={<span className="mono">{t.trace.id}</span>} />
          <div className="grid-main-side">
            <Panel title="Timeline"><TraceSpine t={t} /></Panel>
            <div className="stack">
              <Panel title="Summary">
                <dl className="kv">
                  <dt>Agent</dt><dd>{t.agent ? <Link to={`/agents/${t.agent.id}`}>{t.agent.name}</Link> : "Unknown"} (revision {t.trace.agent_revision})</dd>
                  <dt>Kind</dt><dd>{t.trace.kind}</dd>
                  <dt>Status</dt><dd>{t.trace.status}</dd>
                  <dt>Run</dt><dd>{t.trace.run_id ? <Link to={`/runs/${t.trace.run_id}`} className="mono">{t.trace.run_id}</Link> : "Not part of a test run"}</dd>
                  <dt>Started</dt><dd><Time iso={t.trace.started_at} /></dd>
                  <dt>Ended</dt><dd><Time iso={t.trace.ended_at} /></dd>
                  <dt>Latency</dt><dd>{ms(t.trace.latency_ms)}</dd>
                  <dt>Tool calls</dt><dd>{t.tool_calls.length} attempted, {t.tool_calls.filter((c: any) => c.executed).length} reached a tool</dd>
                  <dt>Token usage</dt><dd>{t.trace.usage ? `${t.trace.usage.prompt_tokens} prompt, ${t.trace.usage.completion_tokens} completion` : t.usage_note}</dd>
                  <dt>Triggered by</dt><dd>{t.trace.triggered_by ?? "Not recorded"}</dd>
                </dl>
              </Panel>
              <Panel title="Stages">
                <table className="t"><tbody>{t.stages.map((s: any) => (
                  <tr key={s.key}><td>{s.label}</td><td className="small">{s.status === "recorded" ? "Recorded" : s.status === "missing" ? "Missing" : s.status === "none" ? (s.note ?? "None") : "Not applicable"}</td></tr>
                ))}</tbody></table>
              </Panel>
            </div>
          </div>
        </>
      )}
    </Load>
  );
}
