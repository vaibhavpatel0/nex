import { FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApp } from "../../app/context";
import { Empty, FormError, Load, OutcomeTok, PageHead, Panel, Pager, pct, Sev, Short, Time } from "../../components/ui";
import { useAction, useApi } from "../../hooks/useApi";
import { api, qs } from "../../services/api";
import { metricValue } from "../security-tests/TestsPage";

function runLabel(r: any) {
  return `${r.label || r.id} (${r.agent.key}, ${new Date(r.created_at).toLocaleString()})`;
}

function NewInvestigation({ onDone }: { onDone: (id: string) => void }) {
  const runs = useApi("/test-runs?status=completed&limit=100");
  const act = useAction();
  const [run, setRun] = useState("");
  const [base, setBase] = useState("");
  const [title, setTitle] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const r = await act.run(() => api.post("/investigations", { run_id: run, baseline_run_id: base || null, title }));
    if (r) onDone(r.id);
  };
  return (
    <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
      <Load r={runs} empty={(d: any) => d.items.length === 0 ? <Empty title="No completed runs to investigate">Run a security test first.</Empty> : null}>
        {(d: any) => (
          <div className="field-row">
            <label className="field"><span>Run under investigation</span><select required value={run} onChange={(e) => setRun(e.target.value)}><option value="">Choose a run</option>{d.items.map((r: any) => <option key={r.id} value={r.id}>{runLabel(r)}</option>)}</select></label>
            <label className="field"><span>Baseline for comparison (optional)</span><select value={base} onChange={(e) => setBase(e.target.value)}><option value="">No baseline</option>{d.items.filter((r: any) => r.id !== run).map((r: any) => <option key={r.id} value={r.id}>{runLabel(r)}</option>)}</select></label>
            <label className="field"><span>Title (optional)</span><input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></label>
          </div>
        )}
      </Load>
      <FormError msg={act.error} />
      <div><button className="btn-primary" disabled={!run || act.busy}>Open investigation</button></div>
    </form>
  );
}

export function InvestigationsPage() {
  const { can } = useApp();
  const nav = useNavigate();
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);
  const r = useApi(`/investigations${qs({ limit: 20, offset })}`, [offset]);
  return (
    <>
      <PageHead title="Investigations" intro="An investigation pins a test run, optionally against a baseline run, and collects notes, findings and reports under a stable ID."
        actions={can("investigations:write") ? <button className="btn-primary" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Open investigation"}</button> : null} />
      {adding ? <Panel title="Open an investigation"><NewInvestigation onDone={(id) => nav(`/investigations/${id}`)} /></Panel> : null}
      <Panel flush>
        <Load r={r} empty={(d: any) => d.items.length === 0 ? <Empty title="No investigations yet">Open one from a completed test run, or with the button above.</Empty> : null}>
          {(d: any) => (
            <>
              <div className="table-wrap"><table className="t">
                <thead><tr><th>ID</th><th>Title</th><th>Agent</th><th>Baseline</th><th>Status</th><th className="num">Reports</th><th>Opened</th></tr></thead>
                <tbody>{d.items.map((i: any) => (
                  <tr key={i.id}><td><Link to={`/investigations/${i.id}`} className="mono">{i.key}</Link></td><td>{i.title}</td>
                    <td className="mono small">{i.run?.agent.key}</td><td className="small">{i.baseline_run_id ? <Short id={i.baseline_run_id} to={`/runs/${i.baseline_run_id}`} /> : "None"}</td>
                    <td className="small">{i.status}</td><td className="num">{i.reports.length}</td><td className="small"><Time iso={i.created_at} rel /><div className="dim">{i.created_by}</div></td></tr>
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

const DIR: Record<string, string> = { improved: "tok-ok", regressed: "tok-bad", unchanged: "tok-neutral" };

export function Comparison({ c }: { c: any }) {
  if (c.error) return <div className="notice notice-warn">Comparison unavailable: {c.error}</div>;
  return (
    <div className="stack" style={{ gap: 10 }}>
      <p className="small">Baseline <Link to={`/runs/${c.baseline.id}`}>{c.baseline.label || c.baseline.id}</Link> ({c.baseline.agent} rev {c.baseline.agent_revision}) compared with <Link to={`/runs/${c.candidate.id}`}>{c.candidate.label || c.candidate.id}</Link> ({c.candidate.agent} rev {c.candidate.agent_revision}) over {c.shared_scenarios} shared scenarios: {c.summary.improved} improved, {c.summary.regressed} regressed, {c.summary.unchanged} unchanged.</p>
      {c.warnings.map((w: string) => <div key={w} className="notice notice-warn small">{w}</div>)}
      <table className="t"><thead><tr><th>Metric</th><th className="num">Baseline</th><th className="num">Candidate</th><th className="num">Change</th></tr></thead>
        <tbody>{c.metrics.map((m: any) => (
          <tr key={m.metric}><td className="small">{m.metric.replace(/_/g, " ")}</td>
            <td className="num small">{metricValue({ metric: m.metric, ...m.baseline })}</td><td className="num small">{metricValue({ metric: m.metric, ...m.candidate })}</td>
            <td className="num small">{m.delta == null ? "Not comparable" : m.metric.endsWith("_rate") ? `${m.delta > 0 ? "+" : ""}${(m.delta * 100).toFixed(1)} pts` : `${m.delta > 0 ? "+" : ""}${m.delta.toFixed(1)}${m.metric.endsWith("_ms") ? " ms" : ""}`}</td></tr>
        ))}</tbody></table>
      <table className="t"><thead><tr><th>Scenario</th><th>Baseline</th><th>Candidate</th><th>Change</th></tr></thead>
        <tbody>{c.scenarios.map((s: any) => (
          <tr key={s.scenario_key}><td className="mono small">{s.scenario_key}</td>
            <td><OutcomeTok o={s.baseline.outcome} />{s.baseline.attack_attempted ? <span className="small muted"> attempted</span> : null}</td>
            <td><OutcomeTok o={s.candidate.outcome} />{s.candidate.attack_attempted ? <span className="small muted"> attempted</span> : null}</td>
            <td><span className={`tok ${DIR[s.direction]}`}>{s.direction[0].toUpperCase() + s.direction.slice(1)}</span></td></tr>
        ))}</tbody></table>
    </div>
  );
}

export function InvestigationDetailPage() {
  const { id } = useParams();
  const { can, toast } = useApp();
  const nav = useNavigate();
  const r = useApi(`/investigations/${id}`, [id]);
  const runs = useApi("/test-runs?status=completed&limit=100");
  const [notes, setNotes] = useState<string | null>(null);
  const patch = async (body: any) => {
    try { await api.patch(`/investigations/${id}`, body); toast("Saved"); r.reload(); } catch (e) { toast((e as Error).message, true); }
  };
  const report = async () => {
    try { const x = await api.post("/reports", { investigation_id: id }); toast("Report generated"); nav(`/reports/${x.id}`); } catch (e) { toast((e as Error).message, true); }
  };
  return (
    <Load r={r} rows={10}>
      {(i: any) => {
        const d = i.detail;
        const m = Object.fromEntries(d.metrics.map((x: any) => [x.metric, x]));
        return (
          <>
            <PageHead title={`${i.key} ${i.title}`} intro={<>Run <Link to={`/runs/${i.run_id}`}>{d.label || d.id}</Link> of {d.agent.name}. Opened <Time iso={i.created_at} /> by {i.created_by}.</>}
              actions={<>{can("investigations:write") ? <button onClick={() => patch({ status: i.status === "open" ? "closed" : "open" })}>{i.status === "open" ? "Close investigation" : "Reopen"}</button> : null}
                {can("reports:generate") ? <button className="btn-primary" onClick={report}>Generate report</button> : null}</>} />
            <div className="strip">
              <div><div className="v">{d.summary?.counts?.FAIL ?? 0}</div><div className="l">Failed scenarios</div><div className="s">of {d.total_scenarios}</div></div>
              <div><div className="v">{pct(m.attack_attempt_rate?.value, m.attack_attempt_rate?.note)}</div><div className="l">Agent attempted the attack</div><div className="s">n = {m.attack_attempt_rate?.sample_size ?? 0} malicious scenarios</div></div>
              <div><div className="v">{pct(m.attack_success_rate?.value, m.attack_success_rate?.note)}</div><div className="l">Attacks that succeeded</div><div className="s">reached a tool or leaked data</div></div>
              <div><div className="v">{pct(m.block_rate?.value, m.block_rate?.note)}</div><div className="l">Prohibited calls stopped</div><div className="s">n = {m.block_rate?.sample_size ?? 0} calls</div></div>
              <div><div className="v">{pct(m.false_positive_rate?.value, m.false_positive_rate?.note)}</div><div className="l">Benign calls blocked</div><div className="s">n = {m.false_positive_rate?.sample_size ?? 0} calls</div></div>
            </div>
            <div className="grid-main-side">
              <div className="stack">
                <Panel title="Regression comparison" actions={can("investigations:write") && runs.data ? (
                  <select aria-label="Baseline run" value={i.baseline_run_id ?? ""} onChange={(e) => e.target.value && patch({ baseline_run_id: e.target.value })}>
                    <option value="">{i.baseline_run_id ? "Change baseline" : "Choose a baseline run"}</option>
                    {runs.data.items.filter((x: any) => x.id !== i.run_id).map((x: any) => <option key={x.id} value={x.id}>{runLabel(x)}</option>)}
                  </select>) : null}>
                  {i.comparison ? <Comparison c={i.comparison} /> : <p className="muted">No baseline selected. Choose a previous run to compare outcomes and metrics scenario by scenario.</p>}
                </Panel>
                <Panel title={`Findings (${d.findings.length})`} flush>
                  {d.findings.length === 0 ? <Empty title="No findings recorded" /> : (
                    <table className="t"><thead><tr><th>Severity</th><th>Finding</th><th>Evidence</th><th>Remediation</th></tr></thead>
                      <tbody>{d.findings.map((f: any) => (
                        <tr key={f.id}><td><Sev s={f.severity} /><div className="small dim">{f.status}</div></td><td><b className="small">{f.title}</b><div className="small muted">{f.description}</div></td>
                          <td><Short id={f.evidence?.correlation_id} to={`/traces/${f.evidence?.correlation_id}`} /></td><td className="small wrap">{f.remediation}</td></tr>
                      ))}</tbody></table>
                  )}
                </Panel>
              </div>
              <div className="stack">
                <Panel title="Notes">
                  {can("investigations:write") ? (
                    <div className="stack" style={{ gap: 8 }}>
                      <textarea rows={8} value={notes ?? i.notes} onChange={(e) => setNotes(e.target.value)} aria-label="Investigation notes" placeholder="Record what you checked, what you concluded and what remains open." />
                      <div><button disabled={notes === null} onClick={() => { patch({ notes }); setNotes(null); }}>Save notes</button></div>
                    </div>
                  ) : <p>{i.notes || "No notes."}</p>}
                </Panel>
                <Panel title="Reports" flush>
                  {i.reports.length === 0 ? <Empty title="No reports yet" /> : (
                    <table className="t"><tbody>{i.reports.map((x: any) => <tr key={x.id}><td><Link to={`/reports/${x.id}`}>{x.title}</Link></td><td className="small"><Time iso={x.generated_at} /></td></tr>)}</tbody></table>
                  )}
                </Panel>
                <Panel title="Configuration under test">
                  <dl className="kv small">
                    <dt>Agent</dt><dd>{d.agent_snapshot.name} rev {d.agent_snapshot.revision}</dd>
                    <dt>Type</dt><dd>{d.agent_snapshot.agent_type} / {d.agent_snapshot.provider}</dd>
                    <dt>Model</dt><dd>{d.agent_snapshot.model_id ?? "None (simulated)"}</dd>
                    <dt>Policy set</dt><dd><Short id={d.policy_hash} /></dd>
                    <dt>Window</dt><dd><Time iso={d.started_at} /> to <Time iso={d.finished_at} /></dd>
                  </dl>
                </Panel>
              </div>
            </div>
          </>
        );
      }}
    </Load>
  );
}
