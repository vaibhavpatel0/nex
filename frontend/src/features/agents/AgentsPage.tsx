import { FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApp } from "../../app/context";
import { AgentTypeTag, ConnTok, Empty, FormError, Json, Load, PageHead, Panel, Time } from "../../components/ui";
import { useAction, useApi } from "../../hooks/useApi";
import { api } from "../../services/api";
import type { Agent } from "../../types";

function AgentForm({ initial, onDone }: { initial?: Agent; onDone: (a: Agent) => void }) {
  const tools = useApi("/tools");
  const profiles = useApi("/permission-profiles");
  const act = useAction();
  const [f, setF] = useState({
    key: initial?.key ?? "", name: initial?.name ?? "", description: initial?.description ?? "",
    agent_type: initial?.agent_type ?? "simulated", behavior: initial?.behavior ?? "guarded", model_id: initial?.model_id ?? "qwen3:8b",
    system_prompt: initial?.system_prompt ?? "", enabled_tools: initial?.enabled_tools ?? [], permission_profile_key: initial?.permission_profile.key ?? "operations-full",
    enabled: initial?.enabled ?? true,
  });
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body: any = { ...f, behavior: f.agent_type === "simulated" ? f.behavior : null, model_id: f.agent_type === "simulated" ? null : f.model_id, system_prompt: f.system_prompt || null };
    let r;
    if (initial) { delete body.key; delete body.agent_type; r = await act.run(() => api.patch(`/agents/${initial.id}`, body)); }
    else { delete body.enabled; r = await act.run(() => api.post("/agents", body)); }
    if (r) onDone(r);
  };
  const profile = profiles.data?.items.find((p: any) => p.key === f.permission_profile_key);
  return (
    <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
      <div className="field-row">
        {!initial ? <label className="field"><span>Key</span><input required pattern="[a-z0-9][a-z0-9-]*" minLength={3} value={f.key} onChange={(e) => set("key", e.target.value)} placeholder="my-agent" /></label> : null}
        <label className="field"><span>Display name</span><input required value={f.name} onChange={(e) => set("name", e.target.value)} /></label>
        {!initial ? <label className="field"><span>Agent type</span><select value={f.agent_type} onChange={(e) => set("agent_type", e.target.value)}>
          <option value="simulated">Simulated (deterministic)</option><option value="local_model">Local model (Ollama)</option><option value="hosted_model">Hosted model (OpenAI-compatible)</option></select></label> : null}
        {f.agent_type === "simulated" ? (
          <label className="field"><span>Behaviour</span><select value={f.behavior ?? "guarded"} onChange={(e) => set("behavior", e.target.value)}><option value="naive">Naive</option><option value="guarded">Guarded</option></select></label>
        ) : (
          <label className="field"><span>Model identifier</span><input required value={f.model_id ?? ""} onChange={(e) => set("model_id", e.target.value)} placeholder="qwen3:8b or gemma4:cloud" /></label>
        )}
        <label className="field"><span>Permission profile</span><select value={f.permission_profile_key} onChange={(e) => set("permission_profile_key", e.target.value)}>
          {profiles.data?.items.map((p: any) => <option key={p.key} value={p.key}>{p.name}</option>)}</select></label>
      </div>
      <label className="field"><span>Description</span><textarea rows={2} value={f.description} onChange={(e) => set("description", e.target.value)} /></label>
      {f.agent_type !== "simulated" ? <label className="field"><span>System prompt (blank uses the default)</span><textarea rows={3} value={f.system_prompt ?? ""} onChange={(e) => set("system_prompt", e.target.value)} /></label> : null}
      <fieldset><legend>Enabled tools. A tool also needs a grant in the permission profile to be allowed.</legend>
        <div style={{ columns: 2 }}>
          {tools.data?.items.map((t: any) => {
            const granted = !!profile?.grants?.tools?.[t.name];
            return (
              <label key={t.name} className="check"><input type="checkbox" checked={f.enabled_tools.includes(t.name)}
                onChange={(e) => set("enabled_tools", e.target.checked ? [...f.enabled_tools, t.name] : f.enabled_tools.filter((x: string) => x !== t.name))} />
                <span><span className="mono">{t.name}</span> <span className="small dim">{t.operation}{granted ? "" : ", not granted by profile"}</span></span></label>
            );
          })}
        </div>
      </fieldset>
      {initial ? <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Agent enabled</label> : null}
      <FormError msg={act.error} />
      <div><button className="btn-primary" disabled={act.busy}>{initial ? "Save as new revision" : "Register agent"}</button></div>
    </form>
  );
}

export function AgentsPage() {
  const { can } = useApp();
  const r = useApi("/agents");
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();
  return (
    <>
      <PageHead title="Agents" intro="Agents AEGIS can test and SENTINEL protects. Simulated agents are deterministic stand-ins; model agents need Ollama or a hosted provider."
        actions={can("agents:write") ? <button className="btn-primary" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Register agent"}</button> : null} />
      {adding ? <Panel title="Register an agent"><AgentForm onDone={(a) => nav(`/agents/${a.id}`)} /></Panel> : null}
      <Panel flush>
        <Load r={r} empty={(d: any) => d.items.length === 0 ? <Empty title="No agents registered" /> : null}>
          {(d: any) => (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Agent</th><th>Type</th><th>Model</th><th>Profile</th><th className="num">Tools</th><th className="num">Revision</th><th>Status</th><th>Last test run</th><th>Last event</th></tr></thead>
              <tbody>{d.items.map((a: Agent) => (
                <tr key={a.id}>
                  <td><Link to={`/agents/${a.id}`}>{a.name}</Link><div className="small mono dim">{a.key}</div></td>
                  <td><AgentTypeTag t={a.agent_type} />{a.behavior ? <div className="small dim">{a.behavior}</div> : null}</td>
                  <td className="small mono">{a.model_id ?? "None"}</td>
                  <td className="small">{a.permission_profile.name}</td>
                  <td className="num">{a.enabled_tools.length}</td><td className="num">{a.revision}</td>
                  <td><ConnTok state={a.connection.state} /></td>
                  <td className="small">{a.last_run ? <Link to={`/runs/${a.last_run.id}`}><Time iso={a.last_run.created_at} rel /></Link> : <span className="dim">Never tested</span>}</td>
                  <td className="small wrap">{a.last_event ? <>{a.last_event.summary}<div className="dim"><Time iso={a.last_event.created_at} rel /></div></> : <span className="dim">None</span>}</td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
        </Load>
      </Panel>
    </>
  );
}

export function AgentDetailPage() {
  const { id } = useParams();
  const { can, toast } = useApp();
  const r = useApi<Agent>(`/agents/${id}`, [id]);
  const profiles = useApi("/permission-profiles");
  const [edit, setEdit] = useState(false);
  return (
    <Load r={r} rows={8}>
      {(a) => {
        const prof = profiles.data?.items.find((p: any) => p.key === a.permission_profile.key);
        return (
          <>
            <PageHead title={a.name} intro={a.description}
              actions={<>{can("agents:write") ? <button onClick={() => setEdit(!edit)}>{edit ? "Cancel editing" : "Edit configuration"}</button> : null}
                <Link className="btn btn-primary" to="/tests">Test this agent</Link></>} />
            {edit ? <Panel title="Edit configuration"><AgentForm initial={a} onDone={() => { setEdit(false); toast("Saved as a new revision"); r.reload(); }} /></Panel> : null}
            <div className="grid-main-side">
              <div className="stack">
                <Panel title="Configuration">
                  <dl className="kv">
                    <dt>Agent ID</dt><dd className="mono">{a.id}</dd>
                    <dt>Key</dt><dd className="mono">{a.key}</dd>
                    <dt>Type</dt><dd><AgentTypeTag t={a.agent_type} /> {a.behavior ? `behaviour ${a.behavior}` : ""}</dd>
                    <dt>Provider and model</dt><dd>{a.provider}{a.model_id ? ` / ${a.model_id}` : ""}</dd>
                    <dt>Status</dt><dd><ConnTok state={a.connection.state} /> <span className="small muted">{a.connection.detail}</span></dd>
                    <dt>Revision</dt><dd>{a.revision}</dd>
                    <dt>Permission profile</dt><dd>{a.permission_profile.name}</dd>
                    <dt>Enabled tools</dt><dd>{a.enabled_tools.map((t) => <span key={t} className="tag" style={{ marginRight: 4 }}>{t}</span>)}</dd>
                    <dt>Last test run</dt><dd>{a.last_run ? <Link to={`/runs/${a.last_run.id}`}><Time iso={a.last_run.created_at} /></Link> : "Never tested"}</dd>
                    <dt>Last event</dt><dd>{a.last_event ? a.last_event.summary : "None"}</dd>
                  </dl>
                  {a.system_prompt ? <><div className="hr" /><div className="small muted">System prompt</div><pre className="pre">{a.system_prompt}</pre></> : null}
                </Panel>
                <Panel title="Permission grants">{prof ? <Json value={prof.grants} /> : <p className="muted">Loading profile.</p>}</Panel>
              </div>
              <Panel title="Revisions" flush>
                <table className="t"><thead><tr><th className="num">Rev</th><th>By</th><th>When</th></tr></thead>
                  <tbody>{a.versions?.map((v) => <tr key={v.revision}><td className="num">{v.revision}</td><td className="small">{v.created_by}</td><td className="small"><Time iso={v.created_at} /></td></tr>)}</tbody></table>
              </Panel>
            </div>
          </>
        );
      }}
    </Load>
  );
}
