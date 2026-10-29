import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, qs } from "../lib/api";
import { useApi } from "../lib/hooks";
import { useLiveEvents, useSession } from "../lib/session";
import { Empty, FormError, Load, PageHead, Panel, Short, StateTok, Time } from "../lib/ui";
import { RunDetailView } from "./RunDetail";
import { AGENT_KEY, RunSummary } from "./types";

const KIND: Record<string, string> = { chat: "Conversation", test: "AEGIS test", probe: "Gateway probe" };
export const kindLabel = (k: string) => KIND[k] ?? k;

const FILTERS = ["q", "kind", "status", "tool", "decision", "scenario", "aegis_run", "start", "end"] as const;

function StartConversation({ onStarted }: { onStarted: (cid: string) => void }) {
  const customers = useApi<{ items: { customer_id: string; name: string }[] }>("/workbench/customers");
  const [customer, setCustomer] = useState("CUST-2001");
  const [message, setMessage] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ correlation_id: string }>("/workbench/conversations", { customer_id: customer, message: message.trim() });
      setMessage("");
      onStarted(r.correlation_id);
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} style={{ display: "grid", gap: 8 }}>
      <FormError msg={err} />
      <div className="field-row" style={{ gridTemplateColumns: "200px minmax(0, 1fr) auto", alignItems: "end" }}>
        <label className="field">
          <span>As customer</span>
          <select value={customer} onChange={(e) => setCustomer(e.target.value)}>
            {(customers.data?.items ?? [{ customer_id: "CUST-2001", name: "Asha Verma" }]).map((c) => (
              <option key={c.customer_id} value={c.customer_id}>{c.customer_id} {c.name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Message</span>
          <input required maxLength={2000} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="e.g. Is the Pulse Lite in stock?" />
        </label>
        <button className="btn-primary" disabled={busy || !message.trim()}>Start real conversation</button>
      </div>
      <span className="small muted">Runs the real agent against the live store data for that synthetic customer, with SENTINEL enforcing. Uses the configured model.</span>
    </form>
  );
}

export function RunsView() {
  const { cid } = useParams();
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const { live } = useSession();
  const filters = Object.fromEntries(FILTERS.map((k) => [k, sp.get(k) ?? ""])) as Record<(typeof FILTERS)[number], string>;
  const toIso = (v: string) => (v ? new Date(v).toISOString() : "");
  const path = "/workbench/runs" + qs({ ...filters, start: toIso(filters.start), end: toIso(filters.end), limit: 100 });
  const runs = useApi<{ items: RunSummary[]; total: number; latest_seq: number }>(path);
  const [draft, setDraft] = useState(filters);
  const reloadTimer = useRef<number | null>(null);

  useEffect(() => {
    if (runs.data) live.seed(runs.data.latest_seq);
  }, [runs.data, live]);

  // Live: patch row states as events arrive; reload the list (debounced) when runs start or finish.
  useLiveEvents(
    (e) => {
      if (!runs.data) return;
      const hit = runs.data.items.find((r) => r.correlation_id === e.correlation_id);
      if (hit && e.state) runs.set({ ...runs.data, items: runs.data.items.map((r) => (r === hit ? { ...r, status: e.state! } : r)) });
      if (e.type === "run.started" || e.type === "run.completed" || e.type === "run.failed") {
        if (reloadTimer.current) window.clearTimeout(reloadTimer.current);
        reloadTimer.current = window.setTimeout(runs.reload, 400);
      }
    },
    [runs.data],
  );

  const apply = (e?: FormEvent) => {
    e?.preventDefault();
    const n = new URLSearchParams();
    for (const k of FILTERS) if (draft[k]) n.set(k, draft[k]);
    setSp(n, { replace: true });
  };
  const select = (id: string) => nav(`/workbench/runs/${id}${sp.toString() ? `?${sp}` : ""}`);
  const field = (k: (typeof FILTERS)[number], lab: string, el: "input" | string[] = "input", type = "text") => (
    <label>
      {lab}
      {el === "input" ? (
        <input type={type} value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
      ) : (
        <select value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}>
          <option value="">Any</option>
          {el.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      )}
    </label>
  );

  return (
    <>
      <PageHead
        title="Agent runs"
        intro={<>Every run of agent <code>{AGENT_KEY}</code>: customer conversations on live store data, AEGIS security tests in sandboxes, and gateway probes. Select a run to see its full trace.</>}
      />
      <Panel title="Start a conversation">
        <StartConversation onStarted={select} />
      </Panel>
      <div className="wb-grid">
        <Panel flush title={`Runs${runs.data ? ` (${runs.data.total})` : ""}`} actions={<button className="btn-sm" onClick={runs.reload}>Refresh</button>}>
          <form className="filter-bar" onSubmit={apply} aria-label="Filter runs">
            {field("q", "Run ID, message or customer")}
            {field("kind", "Kind", ["chat", "test", "probe"])}
            {field("status", "State", ["RUNNING", "WAITING_FOR_MODEL", "TOOL_REQUESTED", "POLICY_CHECKING", "BLOCKED", "WAITING_FOR_APPROVAL", "TOOL_EXECUTING", "COMPLETED", "FAILED"])}
            {field("tool", "Tool")}
            {field("decision", "Decision", ["ALLOW", "BLOCK", "REQUIRE_APPROVAL"])}
            {field("scenario", "Scenario")}
            {field("aegis_run", "AEGIS run ID")}
            {field("start", "From", "input", "datetime-local")}
            {field("end", "To", "input", "datetime-local")}
            <div className="row" style={{ alignSelf: "end" }}>
              <button type="submit">Apply</button>
              <button type="button" className="btn-ghost" onClick={() => { setDraft(Object.fromEntries(FILTERS.map((k) => [k, ""])) as typeof draft); setSp(new URLSearchParams(), { replace: true }); }}>Clear</button>
            </div>
          </form>
          <Load r={runs} rows={8} empty={(d) => (d.items.length ? null : <Empty title="No runs match">Start a conversation above, chat in the storefront, or run a security suite.</Empty>)}>
            {(d) => (
              <div className="table-wrap" style={{ maxHeight: 720 }}>
                <table className="t run-list">
                  <thead><tr><th>Started</th><th>Kind</th><th>State</th><th>Request</th><th className="num">Calls</th></tr></thead>
                  <tbody>
                    {d.items.map((r) => (
                      <tr key={r.correlation_id} className={"clickable" + (r.correlation_id === cid ? " selected" : "")} onClick={() => select(r.correlation_id)}
                        tabIndex={0} onKeyDown={(e) => e.key === "Enter" && select(r.correlation_id)} aria-selected={r.correlation_id === cid}>
                        <td><Time iso={r.started_at} rel /></td>
                        <td>{kindLabel(r.kind)}{r.scenario_key ? <div className="small mono">{r.scenario_key}</div> : null}</td>
                        <td><StateTok s={r.status} /></td>
                        <td className="wrap">
                          <div style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.message}>{r.message}</div>
                          <div className="small dim"><Short id={r.correlation_id} /> {r.customer_id}</div>
                        </td>
                        <td className="num">{r.tool_calls}{r.blocked_calls ? <div className="small" style={{ color: "var(--red)" }}>{r.blocked_calls} blocked</div> : null}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Load>
        </Panel>
        {cid ? <RunDetailView cid={cid} key={cid} /> : <Panel><Empty title="No run selected">Choose a run on the left to inspect its request, model activity, SENTINEL decisions, tool execution and AEGIS result.</Empty></Panel>}
      </div>
    </>
  );
}
