import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { useLiveEvents } from "../lib/session";
import { Empty, FormError, Json, Load, PageHead, Panel, Short, Time } from "../lib/ui";

interface Approval {
  id: string;
  correlation_id: string;
  tool_name: string;
  args_redacted: any;
  reason: string;
  matched_policy_key: string | null;
  status: string;
  created_at: string;
  expires_at: string;
  decided_by: string | null;
  decision_note: string | null;
  run_id: string | null;
  local: { status: string; fixture: string; executable: boolean; result: any } | null;
}

const STATUS_CLS: Record<string, string> = { pending: "tok-approval", approved: "tok-info", denied: "tok-block", expired: "tok-neutral", consumed: "tok-neutral" };

export function ApprovalsView() {
  const [status, setStatus] = useState("pending");
  const r = useApi<{ items: Approval[] }>(`/workbench/approvals${status ? `?status=${status}` : ""}`);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Record<string, any>>({});
  useLiveEvents((e) => {
    if (e.decision === "REQUIRE_APPROVAL") r.reload();
  }, []);

  const act = async (id: string, fn: () => Promise<any>) => {
    setBusy(id);
    setErr(null);
    try {
      const out = await fn();
      setResult((x) => ({ ...x, [id]: out }));
      r.reload();
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHead
        title="Approvals"
        intro="Actions SENTINEL held for review. Approving binds the decision to the exact arguments the agent proposed. Executing then re-submits that same call through SENTINEL, which checks the approval before the tool runs."
        actions={
          <div className="seg" role="group" aria-label="Status">
            {["pending", "approved", "consumed", "denied", "expired", ""].map((s) => (
              <button key={s || "all"} aria-pressed={status === s} onClick={() => setStatus(s)}>{s ? s[0].toUpperCase() + s.slice(1) : "All"}</button>
            ))}
          </div>
        }
      />
      <FormError msg={err} />
      <Panel flush>
        <Load r={r} empty={(d) => (d.items.length ? null : <Empty title={status ? `No ${status} approvals` : "No approvals"}>Approvals appear when the agent proposes a cancellation or return request, or another action a policy holds for review.</Empty>)}>
          {(d) => (
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>Requested</th><th>Tool and arguments</th><th>Why</th><th>Status</th><th>Action</th></tr></thead>
                <tbody>
                  {d.items.map((a) => (
                    <tr key={a.id}>
                      <td>
                        <Time iso={a.created_at} rel />
                        <div className="small dim"><Short id={a.id} /></div>
                        <div className="small"><Link to={`/workbench/runs/${a.correlation_id}`}>Open run</Link></div>
                      </td>
                      <td style={{ minWidth: 220 }}><code>{a.tool_name}</code><Json value={a.args_redacted} /></td>
                      <td className="wrap">
                        {a.reason}
                        {a.matched_policy_key ? <div className="small"><Link to={`/workbench/policies/${a.matched_policy_key}`}>{a.matched_policy_key}</Link></div> : null}
                        <div className="small dim">Expires <Time iso={a.expires_at} /></div>
                      </td>
                      <td>
                        <span className={`tok ${STATUS_CLS[a.status] ?? "tok-neutral"}`}>{a.status}</span>
                        {a.decided_by ? <div className="small muted">by {a.decided_by}</div> : null}
                        {a.local ? <div className="small muted">{a.local.fixture === "sandbox" ? "Sandbox test run" : `Store action ${a.local.status}`}</div> : <div className="small muted">No store record</div>}
                      </td>
                      <td style={{ minWidth: 220 }}>
                        {a.status === "pending" ? (
                          <div style={{ display: "grid", gap: 6 }}>
                            <input aria-label="Decision note" placeholder="Note (optional)" maxLength={300} value={note[a.id] ?? ""} onChange={(e) => setNote({ ...note, [a.id]: e.target.value })} />
                            <div className="row">
                              <button className="btn-primary btn-sm" disabled={busy === a.id} onClick={() => act(a.id, () => api.post(`/workbench/approvals/${a.id}/decide`, { approve: true, note: note[a.id] ?? "" }))}>Approve</button>
                              <button className="btn-danger btn-sm" disabled={busy === a.id} onClick={() => act(a.id, () => api.post(`/workbench/approvals/${a.id}/decide`, { approve: false, note: note[a.id] ?? "" }))}>Deny</button>
                            </div>
                          </div>
                        ) : a.status === "approved" && a.local?.executable ? (
                          <button className="btn-sm" disabled={busy === a.id} onClick={() => act(a.id, () => api.post(`/workbench/approvals/${a.id}/execute`))}>Execute approved action</button>
                        ) : a.status === "approved" && a.local?.fixture === "sandbox" ? (
                          <span className="small muted">Test-run approval. Sandbox actions are not executed from here.</span>
                        ) : null}
                        {result[a.id]?.correlation_id ? <div className="small"><Link to={`/workbench/runs/${result[a.id].correlation_id}`}>Execution run</Link>: {result[a.id].status}</div> : null}
                        {a.local?.result?.correlation_id && !result[a.id] ? <div className="small"><Link to={`/workbench/runs/${a.local.result.correlation_id}`}>Execution run</Link>: {a.local.result.status}</div> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Load>
      </Panel>
    </>
  );
}
