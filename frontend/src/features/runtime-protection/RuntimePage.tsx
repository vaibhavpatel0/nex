import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useApp, useLiveEvents } from "../../app/context";
import { IconPlay } from "../../components/Icons";
import { DecisionTok, Empty, FormError, Json, Load, PageHead, Panel, Pager, Short, Time } from "../../components/ui";
import { useAction, useApi } from "../../hooks/useApi";
import { api, qs } from "../../services/api";
import { TraceSpine } from "../investigations/TraceSpine";

const EXAMPLES = [
  "What is the status of order ORD-5002?",
  "Summarize my inbox.",
  "Summarize documents/public/supplier_onboarding.md",
  "Read documents/restricted/payroll_2026.csv",
  "Reply to jordan.ellis@customer.example with the status of order ORD-5002.",
  "Fetch https://partners.halden.test/promo and summarize it.",
  "Update order ORD-5002 status to cancelled",
];
const CUSTOMERS = ["", "CUST-1001", "CUST-1002", "CUST-1003", "CUST-1004"];

function Session({ agents }: { agents: any[] }) {
  const { can, toast } = useApp();
  const [agentId, setAgentId] = useState(agents.find((a) => a.key === "ops-naive")?.id ?? agents[0]?.id);
  const [subject, setSubject] = useState("CUST-1001");
  const [task, setTask] = useState(EXAMPLES[2]);
  const [cid, setCid] = useState<string | null>(null);
  const [trace, setTrace] = useState<any>(null);
  const act = useAction();

  const refresh = (id: string) => api.get(`/traces/${id}`).then(setTrace).catch(() => undefined);
  useLiveEvents((e) => { if (cid && e.correlation_id === cid) refresh(cid); }, [cid]);

  const start = async (ev: FormEvent) => {
    ev.preventDefault();
    const r = await act.run(() => api.post("/runtime/sessions", { agent_id: agentId, task, session_subject: subject || null }));
    if (r) { setCid(r.correlation_id); setTrace(null); toast("Session started"); setTimeout(() => refresh(r.correlation_id), 300); }
  };
  const done = trace?.trace?.status && trace.trace.status !== "running";

  return (
    <Panel title="Run an agent through SENTINEL" id="sess">
      <form className="stack" style={{ gap: 10 }} onSubmit={start}>
        <p className="muted small">The agent works against the persistent runtime sandbox. Every tool call it makes is evaluated by SENTINEL before it can execute.</p>
        <div className="field-row">
          <label className="field"><span>Agent</span>
            <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <label className="field"><span>Signed-in customer for this session</span>
            <select value={subject} onChange={(e) => setSubject(e.target.value)}>{CUSTOMERS.map((c) => <option key={c} value={c}>{c || "None"}</option>)}</select></label>
        </div>
        <label className="field"><span>Task</span><textarea rows={2} value={task} maxLength={2000} onChange={(e) => setTask(e.target.value)} /></label>
        <div className="row small"><span className="muted">Examples:</span>
          {EXAMPLES.map((x) => <button type="button" key={x} className="btn-sm" onClick={() => setTask(x)}>{x.length > 42 ? x.slice(0, 40) + "..." : x}</button>)}
        </div>
        <FormError msg={act.error} />
        <div className="row">
          <button className="btn-primary" disabled={!can("runtime:invoke") || act.busy || !task.trim()}><IconPlay />Start session</button>
          {!can("runtime:invoke") ? <span className="small muted">Your role cannot start sessions.</span> : null}
          {cid ? <Link to={`/traces/${cid}`} className="small">Open full trace</Link> : null}
        </div>
      </form>
      {cid ? (
        <div style={{ marginTop: 14 }}>
          <div className="hr" />
          <div className="row" style={{ margin: "8px 0" }}><h3>Session trace</h3><Short id={cid} />{done ? <span className="tag">Finished</span> : <span className="tag">Running</span>}</div>
          {trace ? <TraceSpine t={trace} /> : <p className="muted">Waiting for the first event.</p>}
        </div>
      ) : null}
    </Panel>
  );
}

function DirectCall({ agents, tools }: { agents: any[]; tools: any[] }) {
  const { can } = useApp();
  const [agentId, setAgentId] = useState(agents.find((a) => a.key === "ops-guarded")?.id ?? agents[0]?.id);
  const [tool, setTool] = useState("files.read");
  const [args, setArgs] = useState('{\n  "path": "documents/restricted/api_credentials.env"\n}');
  const [subject, setSubject] = useState("CUST-1001");
  const [approval, setApproval] = useState("");
  const [res, setRes] = useState<any>(null);
  const act = useAction();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let parsed: unknown;
    try { parsed = JSON.parse(args); } catch { act.setError("Arguments must be valid JSON"); return; }
    const r = await act.run(() => api.post("/runtime/invoke", { agent_id: agentId, tool, args: parsed, session_subject: subject || null, approval_id: approval || null }));
    if (r) setRes(r);
  };
  return (
    <Panel title="Submit a tool call directly">
      <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
        <p className="muted small">Sends one tool request as the selected agent, as if the agent had produced it. Useful for checking a policy change or replaying an approval with altered arguments.</p>
        <div className="field-row">
          <label className="field"><span>As agent</span><select value={agentId} onChange={(e) => setAgentId(e.target.value)}>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <label className="field"><span>Tool</span><select value={tool} onChange={(e) => setTool(e.target.value)}>{tools.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}<option value="admin.grant_access">admin.grant_access (unregistered)</option></select></label>
          <label className="field"><span>Session customer</span><select value={subject} onChange={(e) => setSubject(e.target.value)}>{CUSTOMERS.map((c) => <option key={c} value={c}>{c || "None"}</option>)}</select></label>
        </div>
        <label className="field"><span>Arguments (JSON)</span><textarea className="code" rows={4} value={args} onChange={(e) => setArgs(e.target.value)} spellCheck={false} /></label>
        <label className="field"><span>Approval ID (optional)</span><input value={approval} placeholder="apr_..." onChange={(e) => setApproval(e.target.value.trim())} /></label>
        <FormError msg={act.error} />
        <div><button className="btn-primary" disabled={!can("runtime:invoke") || act.busy}>Evaluate and run</button></div>
      </form>
      {res ? (
        <div style={{ marginTop: 12 }} className="stack">
          <div className="row"><DecisionTok d={res.decision} /><span className="mono small">{res.matched_policy ?? "Default deny"}</span><span className="small">{res.execution_status}</span>
            <Link className="small" to={`/traces/${res.correlation_id}`}>Trace</Link></div>
          <p className="small">{res.reason}</p>
          <Json value={res} />
        </div>
      ) : null}
    </Panel>
  );
}

function Approvals() {
  const { can, toast } = useApp();
  const [status, setStatus] = useState("pending");
  const [offset, setOffset] = useState(0);
  const r = useApi(`/approvals${qs({ status, limit: 20, offset })}`, [status, offset]);
  const [notes, setNotes] = useState<Record<string, string>>({});
  useLiveEvents((e) => { if (e.event_type.startsWith("approval.")) r.reload(); });
  useEffect(() => setOffset(0), [status]);
  const decide = async (id: string, approve: boolean) => {
    try { await api.post(`/approvals/${id}/decide`, { approve, note: notes[id] || "" }); toast(approve ? "Approved. Execution needs a separate step." : "Denied"); r.reload(); }
    catch (e) { toast((e as Error).message, true); }
  };
  const execute = async (id: string) => {
    try { const x = await api.post(`/approvals/${id}/execute`); toast(`${x.decision}: ${x.execution_status}`); r.reload(); }
    catch (e) { toast((e as Error).message, true); }
  };
  return (
    <Panel title="Approvals" flush actions={
      <div className="seg" role="group" aria-label="Approval status">
        {["pending", "approved", "consumed", "denied", "expired"].map((s) => <button key={s} aria-pressed={status === s} onClick={() => setStatus(s)}>{s[0].toUpperCase() + s.slice(1)}</button>)}
      </div>}>
      <Load r={r} empty={(d: any) => d.items.length === 0 ? <Empty title={`No ${status} approvals`}>{status === "pending" ? "Sensitive actions that need a human decision appear here." : null}</Empty> : null}>
        {(d: any) => (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Action</th><th>Why approval is needed</th><th>Arguments</th><th>Expires</th><th>Decision</th></tr></thead>
              <tbody>{d.items.map((a: any) => (
                <tr key={a.id}>
                  <td><div className="mono">{a.tool_name}</div><div className="small"><Short id={a.correlation_id} to={`/traces/${a.correlation_id}`} /></div>{a.run_id ? <div className="small dim">From a test run</div> : null}</td>
                  <td className="small wrap">{a.reason}</td>
                  <td style={{ maxWidth: 260 }}><Json value={a.args_redacted} /></td>
                  <td className="small"><Time iso={a.expires_at} /></td>
                  <td>
                    {a.status === "pending" && can("approvals:decide") ? (
                      <div className="stack" style={{ gap: 6 }}>
                        <input placeholder="Note (optional)" aria-label="Decision note" value={notes[a.id] || ""} onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} />
                        <div className="row"><button className="btn-sm btn-primary" onClick={() => decide(a.id, true)}>Approve</button><button className="btn-sm btn-danger" onClick={() => decide(a.id, false)}>Deny</button></div>
                      </div>
                    ) : a.status === "approved" ? (
                      <div className="stack" style={{ gap: 4 }}>
                        <span className="small">Approved by {a.decided_by}</span>
                        {a.resubmittable && can("approvals:decide") ? <button className="btn-sm btn-primary" onClick={() => execute(a.id)}>Execute approved action</button>
                          : <span className="small muted">Original arguments are no longer in memory. Re-run the agent.</span>}
                      </div>
                    ) : <span className="small">{a.status}{a.decided_by ? ` by ${a.decided_by}` : ""}{a.decision_note ? `: ${a.decision_note}` : ""}</span>}
                  </td>
                </tr>
              ))}</tbody>
            </table></div>
            <Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} />
          </>
        )}
      </Load>
    </Panel>
  );
}

function Decisions() {
  const [decision, setDecision] = useState("");
  const [offset, setOffset] = useState(0);
  const r = useApi(`/runtime/decisions${qs({ decision, limit: 25, offset })}`, [decision, offset]);
  useLiveEvents((e) => { if (e.event_type === "policy.decision" && offset === 0) r.reload(); }, [offset]);
  return (
    <Panel title="Policy decisions" flush actions={
      <select aria-label="Filter by decision" value={decision} onChange={(e) => { setDecision(e.target.value); setOffset(0); }}>
        <option value="">All decisions</option><option value="ALLOW">Allowed</option><option value="BLOCK">Blocked</option><option value="REQUIRE_APPROVAL">Needs approval</option>
      </select>}>
      <Load r={r} empty={(d: any) => d.items.length === 0 ? <Empty title="No decisions recorded" /> : null}>
        {(d: any) => (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>When</th><th>Tool</th><th>Decision</th><th>Matched policy</th><th>Risk</th><th>Execution</th><th>Reason</th><th>Trace</th></tr></thead>
              <tbody>{d.items.map((x: any) => (
                <tr key={x.id}><td className="small"><Time iso={x.created_at} rel /></td><td className="mono small">{x.tool_name}</td><td><DecisionTok d={x.decision} /></td>
                  <td className="mono small">{x.matched_policy_key ?? "Default deny"}</td><td className="small">{x.risk}</td><td className="small">{x.execution_status.replace("_", " ")}</td>
                  <td className="small wrap">{x.reason}</td><td><Short id={x.correlation_id} to={`/traces/${x.correlation_id}`} /></td></tr>
              ))}</tbody>
            </table></div>
            <Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} />
          </>
        )}
      </Load>
    </Panel>
  );
}

export function RuntimePage() {
  const agents = useApi("/agents");
  const tools = useApi("/tools");
  return (
    <>
      <PageHead title="Runtime protection" intro="SENTINEL sits between agents and their tools. Run an agent, submit a single tool call, and decide on actions that need a human." />
      <Load r={agents}>{(a: any) => (
        <div className="grid-2">
          <Session agents={a.items.filter((x: any) => x.enabled)} />
          {tools.data ? <DirectCall agents={a.items.filter((x: any) => x.enabled)} tools={tools.data.items} /> : null}
        </div>
      )}</Load>
      <Approvals />
      <Decisions />
    </>
  );
}
