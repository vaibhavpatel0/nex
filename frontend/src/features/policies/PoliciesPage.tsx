import { FormEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useApp } from "../../app/context";
import { Empty, FormError, Json, Load, PageHead, Panel, Short, Time } from "../../components/ui";
import { useAction, useApi } from "../../hooks/useApi";
import { api } from "../../services/api";

const EFFECT: Record<string, string> = { BLOCK: "Block", REQUIRE_APPROVAL: "Require approval", ALLOW: "Allow", CONSTRAIN: "Constrain" };

function NewPolicy({ ruleTypes, onDone }: { ruleTypes: any[]; onDone: () => void }) {
  const act = useAction();
  const [f, setF] = useState({ key: "CUS-001", name: "", description: "", rule_type: "rate_limit", effect: "BLOCK", priority: 600, applies_to: "inventory.get_stock", params: '{\n  "max_calls": 3,\n  "window_seconds": 60,\n  "scope": "session",\n  "per_tool": true\n}', risk: "medium" });
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let params;
    try { params = JSON.parse(f.params); } catch { act.setError("Parameters must be valid JSON"); return; }
    const r = await act.run(() => api.post("/policies", { ...f, params, applies_to: f.applies_to.split(",").map((s) => s.trim()).filter(Boolean) }));
    if (r) onDone();
  };
  const doc = ruleTypes.find((r) => r.rule_type === f.rule_type)?.doc;
  return (
    <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
      <div className="field-row">
        <label className="field"><span>Key (CUS-001 to CUS-999)</span><input required pattern="CUS-\d{3}" value={f.key} onChange={(e) => set("key", e.target.value)} /></label>
        <label className="field"><span>Name</span><input required minLength={3} value={f.name} onChange={(e) => set("name", e.target.value)} /></label>
        <label className="field"><span>Rule type</span><select value={f.rule_type} onChange={(e) => set("rule_type", e.target.value)}>{ruleTypes.map((r) => <option key={r.rule_type}>{r.rule_type}</option>)}</select></label>
        <label className="field"><span>Effect</span><select value={f.effect} onChange={(e) => set("effect", e.target.value)}>{Object.entries(EFFECT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label className="field"><span>Priority (higher reports first)</span><input type="number" min={1} max={10000} value={f.priority} onChange={(e) => set("priority", Number(e.target.value))} /></label>
        <label className="field"><span>Risk</span><select value={f.risk} onChange={(e) => set("risk", e.target.value)}>{["low", "medium", "high", "critical"].map((r) => <option key={r}>{r}</option>)}</select></label>
      </div>
      {doc ? <p className="small muted">{doc}</p> : null}
      <label className="field"><span>Applies to tools (comma separated, * allowed)</span><input value={f.applies_to} onChange={(e) => set("applies_to", e.target.value)} /></label>
      <label className="field"><span>Description</span><input required minLength={3} value={f.description} onChange={(e) => set("description", e.target.value)} /></label>
      <label className="field"><span>Parameters (JSON)</span><textarea className="code" rows={5} value={f.params} onChange={(e) => set("params", e.target.value)} spellCheck={false} /></label>
      <FormError msg={act.error} />
      <div><button className="btn-primary" disabled={act.busy}>Create policy</button></div>
    </form>
  );
}

export function PoliciesPage() {
  const { can, toast } = useApp();
  const r = useApi("/policies");
  const [adding, setAdding] = useState(false);
  const [toggling, setToggling] = useState<{ id: string; note: string } | null>(null);
  const toggle = async (p: any, note: string) => {
    try { await api.patch(`/policies/${p.id}`, { enabled: !p.enabled, change_note: note }); toast(`${p.key} ${p.enabled ? "disabled" : "enabled"}`); setToggling(null); r.reload(); }
    catch (e) { toast((e as Error).message, true); }
  };
  return (
    <>
      <PageHead title="Policies" intro="Deterministic rules SENTINEL evaluates for every tool call. Changes create a new version and take effect on the next call."
        actions={can("policies:write") ? <button className="btn-primary" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Create policy"}</button> : null} />
      <Load r={r}>
        {(d: any) => (
          <>
            <Panel title="How decisions combine">
              <ol style={{ margin: 0, paddingLeft: 20 }} className="small">{d.precedence.map((p: string) => <li key={p}>{p}</li>)}</ol>
              <p className="small muted" style={{ marginTop: 6 }}>Priority decides which triggered policy is reported as the reason. It never lets an allow override a block. Current policy set <Short id={d.policy_hash} />.</p>
            </Panel>
            {adding ? <Panel title="Create a custom policy"><NewPolicy ruleTypes={d.rule_types} onDone={() => { setAdding(false); toast("Policy created"); r.reload(); }} /></Panel> : null}
            <Panel flush>
              {d.items.length === 0 ? <Empty title="No policies" /> : (
                <div className="table-wrap"><table className="t">
                  <thead><tr><th>Policy</th><th>Effect</th><th className="num">Priority</th><th>Applies to</th><th>Risk</th><th className="num">Version</th><th>Updated</th><th>State</th></tr></thead>
                  <tbody>{d.items.map((p: any) => (
                    <tr key={p.id}>
                      <td><Link to={`/policies/${p.id}`}><span className="mono">{p.key}</span> {p.name}</Link><div className="small muted wrap">{p.description}</div></td>
                      <td className="small">{EFFECT[p.effect]}</td><td className="num">{p.priority}</td>
                      <td className="small mono wrap">{p.applies_to.join(", ")}</td><td className="small">{p.risk}</td><td className="num">{p.version}</td>
                      <td className="small"><Time iso={p.updated_at} rel /><div className="dim">{p.updated_by}</div></td>
                      <td>
                        <span className={`tok ${p.enabled ? "tok-info" : "tok-neutral"}`}>{p.enabled ? "Enabled" : "Disabled"}</span>
                        {p.locked ? <div className="small dim">Locked core rule</div> : !can("policies:write") ? null : toggling?.id === p.id ? (
                          <form className="stack" style={{ gap: 4, marginTop: 4 }} onSubmit={(e) => { e.preventDefault(); toggle(p, toggling?.note ?? ""); }}>
                            <input autoFocus required minLength={3} placeholder="Reason for the change" aria-label={`Reason for changing ${p.key}`} value={toggling?.note ?? ""} onChange={(e) => setToggling({ id: p.id, note: e.target.value })} />
                            <div className="row"><button className="btn-sm btn-primary">{p.enabled ? "Disable" : "Enable"}</button><button type="button" className="btn-sm" onClick={() => setToggling(null)}>Cancel</button></div>
                          </form>
                        ) : <div><button className="btn-sm btn-ghost" onClick={() => setToggling({ id: p.id, note: "" })}>{p.enabled ? "Disable" : "Enable"}</button></div>}
                      </td>
                    </tr>
                  ))}</tbody>
                </table></div>
              )}
            </Panel>
          </>
        )}
      </Load>
    </>
  );
}

export function PolicyDetailPage() {
  const { id } = useParams();
  const { can, toast } = useApp();
  const r = useApi(`/policies/${id}`, [id]);
  const act = useAction();
  const [params, setParams] = useState<string | null>(null);
  const [priority, setPriority] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const save = async (p: any, e: FormEvent) => {
    e.preventDefault();
    const body: any = { change_note: note };
    if (params !== null) { try { body.params = JSON.parse(params); } catch { act.setError("Parameters must be valid JSON"); return; } }
    if (priority !== null && priority !== p.priority) body.priority = priority;
    const x = await act.run(() => api.patch(`/policies/${p.id}`, body));
    if (x) { toast(`Saved ${p.key} version ${x.version}`); setParams(null); setPriority(null); setNote(""); r.reload(); }
  };
  return (
    <Load r={r}>
      {(p: any) => (
        <>
          <PageHead title={`${p.key} ${p.name}`} intro={p.description} />
          <div className="grid-main-side">
            <div className="stack">
              <Panel title="Definition">
                <dl className="kv">
                  <dt>Rule type</dt><dd className="mono">{p.rule_type}</dd><dt>Rule behaviour</dt><dd className="small">{p.rule_doc}</dd>
                  <dt>Effect</dt><dd>{EFFECT[p.effect]}</dd><dt>Priority</dt><dd>{p.priority}</dd>
                  <dt>Applies to</dt><dd className="mono">{p.applies_to.join(", ")}</dd><dt>Risk</dt><dd>{p.risk}</dd>
                  <dt>State</dt><dd>{p.enabled ? "Enabled" : "Disabled"}{p.locked ? ", locked core rule" : ""}</dd>
                  <dt>Version</dt><dd>{p.version}, updated <Time iso={p.updated_at} /> by {p.updated_by}</dd>
                </dl>
                <div className="hr" /><div className="small muted">Parameters</div><Json value={p.params} />
              </Panel>
              {can("policies:write") && !p.locked ? (
                <Panel title="Edit">
                  <form className="stack" style={{ gap: 10 }} onSubmit={(e) => save(p, e)}>
                    <label className="field"><span>Priority</span><input type="number" min={1} max={10000} value={priority ?? p.priority} onChange={(e) => setPriority(Number(e.target.value))} /></label>
                    <label className="field"><span>Parameters (JSON)</span><textarea className="code" rows={6} spellCheck={false} value={params ?? JSON.stringify(p.params, null, 2)} onChange={(e) => setParams(e.target.value)} /></label>
                    <label className="field"><span>Change note (required, kept in the version history)</span><input required minLength={3} value={note} onChange={(e) => setNote(e.target.value)} /></label>
                    <FormError msg={act.error} />
                    <div><button className="btn-primary" disabled={act.busy}>Save new version</button></div>
                  </form>
                </Panel>
              ) : p.locked ? <div className="notice">Locked core rules cannot be disabled or re-scoped. They implement default deny and schema validation.</div> : null}
            </div>
            <Panel title="Version history" flush>
              <table className="t"><thead><tr><th className="num">v</th><th>Change</th><th>When</th></tr></thead>
                <tbody>{p.versions.map((v: any) => <tr key={v.version}><td className="num">{v.version}</td><td className="small">{v.change_note}<div className="dim">{v.changed_by}</div></td><td className="small"><Time iso={v.created_at} /></td></tr>)}</tbody></table>
            </Panel>
          </div>
        </>
      )}
    </Load>
  );
}
