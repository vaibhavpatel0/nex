import { ReactNode, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import type { AgentEvent } from "../lib/live";
import { useLiveEvents } from "../lib/session";
import { DecisionTok, Empty, ErrorState, ExecTok, Json, OutcomeTok, Panel, Short, Skeleton, StateTok, Time } from "../lib/ui";
import { kindLabel } from "./Runs";
import { RunDetail, STATES } from "./types";

const H = ({ letter, children }: { letter: string; children: ReactNode }) => (
  <><span className="section-label">{letter}</span>{children}</>
);

interface CallGroup {
  proposed: AgentEvent;
  checking?: AgentEvent;
  decision?: AgentEvent;
  result?: AgentEvent;
}

/** Tool calls are handled one at a time by the enforcement point, so events for a call follow its proposal in order. */
function groupCalls(events: AgentEvent[]): CallGroup[] {
  const out: CallGroup[] = [];
  for (const e of events) {
    if (e.type === "tool.proposed") out.push({ proposed: e });
    const g = out[out.length - 1];
    if (!g || e.tool !== g.proposed.tool) continue;
    if (e.type === "policy.checking") g.checking = e;
    else if (e.type === "policy.decision") g.decision = e;
    else if (e.type === "tool.result" || e.type === "tool.not_executed") g.result = g.result ?? e;
  }
  return out;
}

function ms(v?: number | null) {
  if (v === undefined || v === null) return "Not recorded";
  return v < 1000 ? `${v.toFixed(0)} ms` : `${(v / 1000).toFixed(2)} s`;
}

function LiveStatus({ detail }: { detail: RunDetail }) {
  const reached = new Set(detail.events.map((e) => e.state).filter(Boolean) as string[]);
  const current = detail.run.status;
  return (
    <Panel title={<H letter="H">Live status</H>}>
      <div style={{ display: "grid", gap: 8 }}>
        <div className="row">Current state <StateTok s={current} /></div>
        <div className="states" aria-label="States this run has reached">
          {STATES.map((s, i) => (
            <span key={s} className="row" style={{ gap: 4 }}>
              {i ? <span className="arrow" aria-hidden="true">/</span> : null}
              <span className="state-step" data-reached={reached.has(s)} data-current={s === current}>
                {s}
                <span className="sr-only">{s === current ? " (current)" : reached.has(s) ? " (reached)" : " (not reached)"}</span>
              </span>
            </span>
          ))}
        </div>
        <span className="small muted">Solid outline: states the backend recorded for this run. Dashed: not reached. States come from persisted events, not from a timer.</span>
      </div>
    </Panel>
  );
}

function AegisSection({ cid, kind, done }: { cid: string; kind: string; done: boolean }) {
  const r = useApi<any>(`/workbench/runs/${cid}/aegis`);
  useEffect(() => {
    if (done) r.reload();
  }, [done]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Panel title={<H letter="F">AEGIS evaluation</H>} actions={<button className="btn-sm" onClick={r.reload}>Refresh</button>}>
      {r.loading && !r.data ? <Skeleton rows={3} /> : r.error && !r.data ? <ErrorState error={r.error} retry={r.reload} /> : r.data ? (
        r.data.scenario ? (
          <div style={{ display: "grid", gap: 10 }}>
            <dl className="kv">
              <dt>Scenario</dt><dd><code>{r.data.scenario.key}</code> {r.data.scenario.name}</dd>
              <dt>Category</dt><dd>{r.data.scenario.category}</dd>
              <dt>Mode</dt><dd>{r.data.trace.mode === "baseline" ? "Baseline (SENTINEL records decisions but does not enforce; sandbox only)" : "Protected (SENTINEL enforcing)"}</dd>
              <dt>Purpose</dt><dd>{r.data.scenario.purpose}</dd>
              <dt>AEGIS run</dt><dd><code>{r.data.trace.run_id}</code></dd>
              <dt>Result</dt><dd>{r.data.result ? <OutcomeTok o={r.data.result.outcome} /> : <span className="muted">Not evaluated yet (AEGIS evaluates when the scenario finishes)</span>}</dd>
              {r.data.result ? <><dt>Reason</dt><dd>{r.data.result.reason}</dd></> : null}
              {r.data.result ? <><dt>Attack</dt><dd>{r.data.result.attack_attempted ? "Attempted" : "Not attempted"}{r.data.result.attack_attempted ? (r.data.result.attack_succeeded ? ", succeeded" : ", did not succeed") : ""}</dd></> : null}
            </dl>
            <details><summary>Expected behaviour</summary><Json value={r.data.scenario.expected} /></details>
            {r.data.result?.checks?.length ? (
              <table className="t">
                <thead><tr><th>Check (expected)</th><th>Outcome</th><th>Evidence (observed)</th></tr></thead>
                <tbody>
                  {r.data.result.checks.map((c: any, i: number) => (
                    <tr key={i}><td><code>{c.id}</code> {c.description}</td><td><OutcomeTok o={c.passed ? "PASS" : "FAIL"} /></td><td className="wrap small">{c.evidence}</td></tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            <p>{kind === "chat" ? "Normal operation. This conversation is not a security test, so AEGIS has no scenario or verdict for it." : "No AEGIS scenario is linked to this run."}</p>
            <p className="small muted">SENTINEL recorded {r.data.tool_calls.length} tool call{r.data.tool_calls.length === 1 ? "" : "s"} for this correlation ID.</p>
          </div>
        )
      ) : null}
    </Panel>
  );
}

function CallCard({ g, pending }: { g: CallGroup; pending: RunDetail["pending_actions"] }) {
  const p = g.proposed.payload ?? {};
  const d = g.decision?.payload ?? {};
  const res = g.result?.payload ?? {};
  const approval = d.approval_id ? pending.find((x) => x.approval_id === d.approval_id) : undefined;
  const invoked = g.result?.type === "tool.result" ? res.invoked !== false : false;
  return (
    <article className="call-card">
      <header>
        <code>{g.proposed.tool}</code>
        {g.decision ? <DecisionTok d={g.decision.decision} /> : <span className="tok tok-neutral">Awaiting decision</span>}
        {g.result ? <ExecTok s={g.result.execution_status} /> : null}
        {d.enforced === false ? <span className="tok tok-warn">Not enforced (baseline)</span> : null}
        <span className="small dim" style={{ marginLeft: "auto" }}>seq {g.proposed.seq}</span>
      </header>
      <div>
        <h4><span className="section-label">C</span>Proposal</h4>
        <dl className="kv">
          <dt>Source</dt><dd>{p.source === "model" ? "Proposed by the model" : p.source === "aegis_probe" ? "AEGIS gateway probe (no model)" : p.source === "staff_approval" ? "Staff re-submission of an approved action" : p.source}</dd>
          <dt>Known tool</dt><dd>{p.known_tool === false ? "No, unknown to the store" : "Yes"}</dd>
        </dl>
        <Json value={p.arguments} />
        {g.checking?.payload ? (
          <span className="small muted">
            Record owner from the store database: {g.checking.payload.resource?.owner_customer_id ?? (g.checking.payload.resource?.exists === false ? "record does not exist" : "not applicable")}.
            Local validation: {g.checking.payload.local_validation}.
          </span>
        ) : null}
      </div>
      <div>
        <h4><span className="section-label">D</span>SENTINEL decision</h4>
        {g.decision ? (
          <dl className="kv">
            <dt>Decision</dt><dd><DecisionTok d={g.decision.decision} /></dd>
            <dt>Matched policy</dt><dd>{d.matched_policy ? <Link to={`/workbench/policies/${d.matched_policy}`}>{d.matched_policy}</Link> : <span className="muted">{d.source ?? "None"}</span>}{d.matched_policy_version ? ` v${d.matched_policy_version}` : ""}</dd>
            <dt>Explanation</dt><dd>{d.reason}</dd>
            {d.risk ? <><dt>Risk</dt><dd>{d.risk}</dd></> : null}
            {d.args_error ? <><dt>Argument check</dt><dd>{d.args_error}</dd></> : null}
            <dt>Evaluated</dt><dd><Time iso={d.evaluated_at} /></dd>
            {d.policies_evaluated ? <><dt>Policies evaluated</dt><dd className="small mono">{d.policies_evaluated.join(", ")}</dd></> : null}
            {d.policy_hash ? <><dt>Policy set hash</dt><dd><code>{d.policy_hash}</code></dd></> : null}
            {d.tool_call_id ? <><dt>SENTINEL call</dt><dd><Short id={d.tool_call_id} /></dd></> : null}
            {d.approval_id ? <><dt>Approval</dt><dd><Short id={d.approval_id} /> {approval ? `local status ${approval.status}` : ""} <Link to="/workbench/approvals">Open approvals</Link></dd></> : null}
          </dl>
        ) : <span className="muted">No decision recorded yet.</span>}
      </div>
      <div>
        <h4><span className="section-label">E</span>Tool execution</h4>
        {!g.result ? <span className="muted">Not finished.</span> : g.result.type === "tool.not_executed" ? (
          <p className="not-executed">Not executed. {res.reason}. The tool function was never called.</p>
        ) : invoked ? (
          <>
            <dl className="kv">
              <dt>Invoked</dt><dd>Yes, {ms(res.duration_ms)}</dd>
              {res.error_class ? <><dt>Error</dt><dd>{res.error_class}: {res.error}</dd></> : null}
              {res.classification ? <><dt>Data class</dt><dd>{res.classification}{res.untrusted_content ? ", contains untrusted text" : ""}</dd></> : null}
              {res.sentinel_flags?.length ? <><dt>SENTINEL flags</dt><dd>{res.sentinel_flags.map((f: string) => <span key={f} className="tok tok-warn" style={{ marginRight: 4 }}>{f}</span>)}</dd></> : null}
            </dl>
            {res.result !== undefined ? <Json value={res.result} /> : null}
            <span className="small dim">Personal fields are masked in this view.</span>
          </>
        ) : (
          <p className="not-executed">Not executed. {res.note ?? res.error}</p>
        )}
      </div>
    </article>
  );
}

function Timeline({ events }: { events: AgentEvent[] }) {
  const node = (e: AgentEvent) =>
    e.type === "tool.result" && e.execution_status === "executed" ? "node-executed"
      : e.type === "tool.not_executed" || e.decision === "BLOCK" ? "node-blocked"
      : e.decision === "REQUIRE_APPROVAL" ? "node-approval"
      : e.type.endsWith("failed") || e.type === "model.error" || e.execution_status === "failed" ? "node-failed"
      : "node-info";
  const title = (e: AgentEvent) => {
    const p = e.payload ?? {};
    switch (e.type) {
      case "run.started": return "Request received";
      case "model.request": return `Model inference, round ${p.round}`;
      case "model.response": return p.no_tool_call ? `Model answered, no tool call (round ${p.round})` : `Model proposed ${p.tool_calls?.length} tool call(s)`;
      case "model.error": return `Model error: ${p.error_class}`;
      case "model.limit": return "Model round limit reached";
      case "tool.proposed": return `Tool call proposed: ${e.tool}`;
      case "policy.checking": return "SENTINEL policy check";
      case "policy.decision": return `SENTINEL decision: ${e.decision}`;
      case "tool.result": return e.execution_status === "executed" ? `Tool result: ${e.tool}` : `Tool failed: ${e.tool}`;
      case "tool.not_executed": return `Tool not executed: ${e.tool}`;
      case "sentinel.unavailable": return "SENTINEL unavailable, failing closed";
      case "run.completed": return "Final response";
      case "run.failed": return "Run failed";
      default: return e.type;
    }
  };
  return (
    <ol className="spine">
      {events.map((e) => (
        <li key={e.seq}>
          <span className={`node ${node(e)}`} aria-hidden="true" />
          <div className="spine-title">
            <b>{title(e)}</b>
            {e.state ? <StateTok s={e.state} /> : null}
            {e.decision ? <DecisionTok d={e.decision} /> : null}
          </div>
          <div className="spine-meta"><code>{e.type}</code> · seq {e.seq} · <Time iso={e.created_at} /></div>
          <details><summary>Event data</summary><Json value={e.payload} /></details>
        </li>
      ))}
    </ol>
  );
}

export function RunDetailView({ cid }: { cid: string }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [err, setErr] = useState<any>(null);
  const load = () => api.get<RunDetail>(`/workbench/runs/${cid}`).then((d) => { setDetail(d); setErr(null); }).catch(setErr);
  useEffect(() => {
    load();
  }, [cid]); // eslint-disable-line react-hooks/exhaustive-deps

  useLiveEvents(
    (e) => {
      if (e.correlation_id !== cid) return;
      setDetail((d) => {
        if (!d || d.events.some((x) => x.seq === e.seq)) return d;
        const events = [...d.events, e].sort((a, b) => a.seq - b.seq);
        return { ...d, events, run: { ...d.run, status: e.state ?? d.run.status } };
      });
      if (e.type === "run.completed" || e.type === "run.failed" || e.type === "policy.decision") load();
    },
    [cid],
  );

  const calls = useMemo(() => groupCalls(detail?.events ?? []), [detail]);
  if (err && !detail) return <Panel><ErrorState error={err} retry={load} /></Panel>;
  if (!detail) return <Panel><Skeleton rows={8} /></Panel>;
  const r = detail.run;
  const started = detail.events.find((e) => e.type === "run.started");
  const modelEvents = detail.events.filter((e) => e.type.startsWith("model."));
  const done = r.status === "COMPLETED" || r.status === "FAILED";
  const end = detail.events.find((e) => e.type === "run.completed" || e.type === "run.failed");

  return (
    <div className="stack">
      <div className="wb-head">
        <h2 style={{ fontSize: 16 }}>Run <code>{r.correlation_id}</code></h2>
        <StateTok s={r.status} />
        <span className="tag">{kindLabel(r.kind)}</span>
        <span className="tag">{r.fixture === "sandbox" ? "Sandbox copy of fixtures" : "Live store data"}</span>
        <span className={`tok ${r.enforcement === "baseline" ? "tok-warn" : "tok-neutral"}`}>{r.enforcement === "baseline" ? "Baseline, not enforced" : r.enforcement === "enforced" ? "SENTINEL enforcing" : `Enforcement ${r.enforcement}`}</span>
      </div>

      <LiveStatus detail={detail} />

      <Panel title={<H letter="A">Customer request</H>}>
        <dl className="kv">
          <dt>Message</dt><dd style={{ whiteSpace: "pre-wrap" }}>{r.message || <span className="muted">No message (direct tool request)</span>}</dd>
          <dt>Customer</dt><dd>{r.customer_id ?? "None"}</dd>
          <dt>Started by</dt><dd>{r.started_by}</dd>
          <dt>Conversation</dt><dd>{r.conversation_id ? <code>{r.conversation_id}</code> : <span className="muted">None</span>}</dd>
          <dt>Received</dt><dd><Time iso={started?.created_at ?? r.started_at} /></dd>
          {r.scenario_key ? <><dt>Scenario</dt><dd><code>{r.scenario_key}</code> in AEGIS run <code>{r.aegis_run_id}</code></dd></> : null}
          {started?.payload?.faults && Object.keys(started.payload.faults).length ? <><dt>Injected faults</dt><dd><code>{JSON.stringify(started.payload.faults)}</code></dd></> : null}
        </dl>
      </Panel>

      <Panel title={<H letter="B">Model activity</H>}>
        <dl className="kv" style={{ marginBottom: 8 }}>
          <dt>Provider</dt><dd>{r.model_provider}</dd>
          <dt>Model</dt><dd><code>{r.model}</code></dd>
          <dt>Tokens</dt><dd>{r.usage ? `${r.usage.prompt_tokens} in, ${r.usage.completion_tokens} out` : "Not reported"}</dd>
        </dl>
        {modelEvents.length === 0 ? <span className="muted">No model call in this run{r.kind === "probe" ? " (gateway probes submit a tool request directly)" : ""}.</span> : (
          <table className="t">
            <thead><tr><th>Round</th><th>Event</th><th>Duration</th><th>Response</th></tr></thead>
            <tbody>
              {modelEvents.map((e) => (
                <tr key={e.seq}>
                  <td className="num">{e.payload?.round ?? ""}</td>
                  <td><code>{e.type}</code></td>
                  <td>{e.type === "model.response" ? ms(e.payload.duration_ms) : e.type === "model.request" ? <Time iso={e.created_at} /> : ""}</td>
                  <td className="wrap">
                    {e.type === "model.request" ? <span className="small muted">{e.payload.messages} messages, {e.payload.tools_offered} tools offered</span> : null}
                    {e.type === "model.response" ? (
                      <>
                        {e.payload.no_tool_call ? <span className="tag">No tool call</span> : e.payload.tool_calls.map((c: any, i: number) => <div key={i}><code>{c.name}</code> <span className="small mono">{JSON.stringify(c.arguments)}</span></div>)}
                        {e.payload.content ? <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{e.payload.content}</div> : null}
                        {e.payload.injected_fault ? <div className="small" style={{ color: "var(--amber)" }}>Injected test fault: {e.payload.injected_fault}</div> : null}
                      </>
                    ) : null}
                    {e.type === "model.error" ? <span style={{ color: "var(--red)" }}>{e.payload.error_class}: {e.payload.error}</span> : null}
                    {e.type === "model.limit" ? <span>{e.payload.note}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title={<><span className="section-label">C D E</span>Tool calls, SENTINEL decisions and execution</>}>
        {calls.length === 0 ? (
          <Empty title="No tool calls">{done ? "The model answered without proposing a tool call." : "None proposed yet."}</Empty>
        ) : (
          <div className="evidence">{calls.map((g) => <CallCard key={g.proposed.seq} g={g} pending={detail.pending_actions} />)}</div>
        )}
        {detail.events.some((e) => e.type === "sentinel.unavailable") ? <div className="notice notice-bad" style={{ marginTop: 8 }}>SENTINEL was unreachable for this run. Every tool call was denied by the local fail-closed rule.</div> : null}
      </Panel>

      <Panel title="Final response">
        {end ? (
          end.payload?.final_text ? <p style={{ whiteSpace: "pre-wrap" }}>{end.payload.final_text}</p>
            : end.payload?.error_kind ? <p style={{ color: "var(--red)" }}>No answer. {end.payload.error_kind}: {end.payload.error}</p>
            : <p className="muted">{end.payload?.outcome ? `Approved action ${end.payload.outcome}.` : "The model produced no final text."}</p>
        ) : <span className="muted">Still running.</span>}
      </Panel>

      <AegisSection cid={cid} kind={r.kind} done={done} />

      <Panel title={<H letter="G">Execution trace</H>} actions={<span className="small muted">{detail.events.length} persisted events</span>}>
        <Timeline events={detail.events} />
      </Panel>
    </div>
  );
}
