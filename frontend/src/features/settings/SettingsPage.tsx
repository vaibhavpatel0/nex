import { FormEvent, Fragment, useState } from "react";
import { useApp } from "../../app/context";
import { FormError, Load, PageHead, Panel, Time } from "../../components/ui";
import { useAction, useApi } from "../../hooks/useApi";
import { api } from "../../services/api";

function Users() {
  const { toast, me } = useApp();
  const r = useApi("/users");
  const act = useAction();
  const [f, setF] = useState({ username: "", password: "", role: "viewer" });
  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (await act.run(() => api.post("/users", f))) { toast(`Created ${f.username}`); setF({ username: "", password: "", role: "viewer" }); r.reload(); }
  };
  const patch = async (id: string, body: any) => {
    try { await api.patch(`/users/${id}`, body); r.reload(); } catch (e) { toast((e as Error).message, true); }
  };
  return (
    <Panel title="Users and roles">
      <p className="small muted">Viewer: read only. Operator: run tests, start sessions, decide approvals, generate reports. Admin: everything, including policies, agents, users and data deletion.</p>
      <Load r={r}>{(d: any) => (
        <table className="t"><thead><tr><th>User</th><th>Role</th><th>Last sign-in</th><th>State</th></tr></thead>
          <tbody>{d.items.map((u: any) => (
            <tr key={u.id}><td>{u.username}</td>
              <td><select aria-label={`Role for ${u.username}`} value={u.role} disabled={u.id === me?.id} onChange={(e) => patch(u.id, { role: e.target.value })}>{["viewer", "operator", "admin"].map((x) => <option key={x}>{x}</option>)}</select></td>
              <td className="small"><Time iso={u.last_login_at} /></td>
              <td>{u.id === me?.id ? <span className="small muted">You</span> : <button className="btn-sm" onClick={() => patch(u.id, { disabled: !u.disabled })}>{u.disabled ? "Enable" : "Disable"}</button>}</td></tr>
          ))}</tbody></table>
      )}</Load>
      <form className="row" style={{ marginTop: 10, alignItems: "end" }} onSubmit={create}>
        <label className="field"><span>Username</span><input required minLength={3} pattern="[A-Za-z0-9_.\-]+" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></label>
        <label className="field"><span>Password (12+ characters)</span><input required type="password" minLength={12} autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
        <label className="field"><span>Role</span><select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{["viewer", "operator", "admin"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <button className="btn-primary" disabled={act.busy}>Add user</button>
      </form>
      <FormError msg={act.error} />
    </Panel>
  );
}

function Data() {
  const { can, toast } = useApp();
  const r = useApi("/settings/data");
  const [days, setDays] = useState(30);
  const [confirm, setConfirm] = useState("");
  const purge = async (e: FormEvent) => {
    e.preventDefault();
    try { const x = await api.post("/settings/data/purge", { older_than_days: days, confirm }); toast(`Removed ${x.removed.test_runs} run(s) and ${x.removed.runtime_events} event(s)`); setConfirm(""); r.reload(); }
    catch (e2) { toast((e2 as Error).message, true); }
  };
  const reset = async () => {
    try { await api.post("/runtime/sandbox/reset"); toast("Runtime sandbox reset to fixture state"); } catch (e) { toast((e as Error).message, true); }
  };
  return (
    <Panel title="Data and retention">
      <Load r={r}>{(d: any) => (
        <div className="stack" style={{ gap: 10 }}>
          <p className="small">{d.retention}</p>
          <dl className="kv small">
            <dt>Database</dt><dd className="mono">{d.database}</dd>
            {Object.entries(d.counts).map(([k, v]: any) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{v}</dd></Fragment>)}
            <dt>Approval lifetime</dt><dd>{d.approval_ttl_seconds} seconds</dd>
            <dt>Sign-in session lifetime</dt><dd>{d.session_ttl_minutes} minutes</dd>
          </dl>
          {can("data:delete") ? (
            <>
              <form className="row" style={{ alignItems: "end" }} onSubmit={purge}>
                <label className="field"><span>Delete records older than (days)</span><input type="number" min={0} max={3650} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
                <label className="field"><span>Type DELETE to confirm</span><input value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
                <button className="btn-danger" disabled={confirm !== "DELETE"}>Delete records</button>
              </form>
              <div className="row"><button onClick={reset}>Reset runtime sandbox</button><span className="small muted">Restores the demo documents, store database, inbox and outbox used by runtime sessions.</span></div>
            </>
          ) : <p className="small muted">Only admins can delete data.</p>}
        </div>
      )}</Load>
    </Panel>
  );
}

export function SettingsPage() {
  const { me, can, theme, setTheme } = useApp();
  return (
    <>
      <PageHead title="Settings" />
      <div className="grid-2">
        <Panel title="Your account and display">
          <dl className="kv">
            <dt>User</dt><dd>{me?.username}</dd><dt>Role</dt><dd>{me?.role}</dd>
            <dt>Permissions</dt><dd className="small">{me?.permissions.join(", ")}</dd>
            <dt>Time zone</dt><dd>{Intl.DateTimeFormat().resolvedOptions().timeZone}. Times are stored in UTC and shown in this time zone; hover a time for UTC.</dd>
          </dl>
          <div className="hr" />
          <div className="seg" role="group" aria-label="Theme">
            {[["system", "Match system"], ["light", "Light"], ["dark", "Dark"]].map(([k, l]) => <button key={k} aria-pressed={theme === k} onClick={() => setTheme(k)}>{l}</button>)}
          </div>
        </Panel>
        <Data />
      </div>
      {can("users:manage") ? <Users /> : null}
    </>
  );
}
