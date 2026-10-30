import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { Empty, FormError, Load, OutcomeTok, PageHead, Panel, Short, Time } from "../lib/ui";

interface Scenario { key: string; name: string; category: string; purpose: string; risk: string; ground_truth: string; suites: string[] }
interface TestRun { id: string; label: string; status: string; mode: string; suite_key: string | null; completed: number; total: number; counts: Record<string, number> | null; created_at: string; finished_at: string | null }
interface RunResult { scenario_key: string; outcome: string; reason: string; correlation_id: string; attack_attempted: boolean; attack_succeeded: boolean }
interface RunFull extends TestRun { metrics: Record<string, number | null> | null; results: RunResult[] }

const SUITES: [string, string][] = [
  ["ecommerce-quick", "Quick e-commerce suite"],
  ["ecommerce", "Full e-commerce suite"],
  ["ecommerce-gateway", "Gateway probes (direct tool requests)"],
];

const ModeTag = ({ mode }: { mode: string }) => (
  <span className={`tok ${mode === "baseline" ? "tok-warn" : "tok-info"}`}>{mode === "baseline" ? "Baseline" : "Protected"}</span>
);

function fmtMetric(k: string, v: number | null) {
  if (v === null || v === undefined) return "Insufficient data";
  return /rate|ratio|share/.test(k) ? `${(v * 100).toFixed(1)}%` : String(Math.round(v * 100) / 100);
}

function RunResults({ id, aegisUi }: { id: string; aegisUi?: string }) {
  const r = useApi<RunFull>(`/workbench/aegis/test-runs/${id}`);
  useEffect(() => {
    if (!r.data || !["queued", "running"].includes(r.data.status)) return;
    const t = window.setTimeout(r.reload, 2000);
    return () => window.clearTimeout(t);
  }, [r.data]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Load r={r}>
      {(d) => (
        <div style={{ display: "grid", gap: 10 }}>
          <div className="row">
            <ModeTag mode={d.mode} /> <span className="tag">{d.status}</span>
            <span className="small muted">{d.completed} of {d.total} scenarios</span>
            {aegisUi ? <a className="small" href={`${aegisUi}/runs/${d.id}`} target="_blank" rel="noreferrer">Open in AEGIS</a> : null}
          </div>
          {d.metrics ? (
            <div className="strip">
              {Object.entries(d.metrics).filter(([, v]) => typeof v === "number" || v === null).slice(0, 6).map(([k, v]) => (
                <div key={k}><div className="v">{fmtMetric(k, v)}</div><div className="l">{k.replace(/_/g, " ")}</div></div>
              ))}
            </div>
          ) : null}
          {d.results.length === 0 ? <Empty title="No results yet" /> : (
            <table className="t">
              <thead><tr><th>Scenario</th><th>Outcome</th><th>Reason</th><th>Trace</th></tr></thead>
              <tbody>
                {d.results.map((x) => (
                  <tr key={x.correlation_id}>
                    <td><code>{x.scenario_key}</code></td>
                    <td><OutcomeTok o={x.outcome} />{x.attack_succeeded ? <div className="small" style={{ color: "var(--red)" }}>Attack succeeded</div> : null}</td>
                    <td className="wrap small">{x.reason}</td>
                    <td><Link to={`/workbench/runs/${x.correlation_id}`}>Workbench</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </Load>
  );
}

function CompareRuns({ runs }: { runs: TestRun[] }) {
  const done = runs.filter((r) => r.status === "completed");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [data, setData] = useState<[RunFull, RunFull] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!a && !b && done.length >= 2) {
      const base = done.find((r) => r.mode === "baseline");
      const prot = done.find((r) => r.mode === "protected");
      if (base && prot) { setA(base.id); setB(prot.id); }
    }
  }, [done.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const go = async () => {
    setErr(null);
    try {
      setData(await Promise.all([api.get<RunFull>(`/workbench/aegis/test-runs/${a}`), api.get<RunFull>(`/workbench/aegis/test-runs/${b}`)]) as [RunFull, RunFull]);
    } catch (x: any) {
      setErr(x.message);
    }
  };
  const keys = data ? Array.from(new Set([...data[0].results, ...data[1].results].map((x) => x.scenario_key))).sort() : [];
  const opt = (r: TestRun) => <option key={r.id} value={r.id}>{r.mode} · {r.suite_key ?? "selected scenarios"} · {new Date(r.created_at).toLocaleString("en-IN")}</option>;
  return (
    <Panel title="Compare baseline and protected runs">
      {done.length < 2 ? <p className="muted">Run the same suite once in baseline mode and once in protected mode to compare them here.</p> : (
        <div style={{ display: "grid", gap: 10 }}>
          <div className="field-row" style={{ alignItems: "end" }}>
            <label className="field"><span>First run</span><select value={a} onChange={(e) => setA(e.target.value)}><option value="">Choose</option>{done.map(opt)}</select></label>
            <label className="field"><span>Second run</span><select value={b} onChange={(e) => setB(e.target.value)}><option value="">Choose</option>{done.map(opt)}</select></label>
            <button onClick={go} disabled={!a || !b || a === b}>Compare</button>
          </div>
          <FormError msg={err} />
          {data ? (
            <table className="t">
              <thead><tr><th>Scenario</th><th><ModeTag mode={data[0].mode} /> <Short id={data[0].id} /></th><th><ModeTag mode={data[1].mode} /> <Short id={data[1].id} /></th><th>Changed</th></tr></thead>
              <tbody>
                {keys.map((k) => {
                  const x = data[0].results.find((y) => y.scenario_key === k);
                  const y = data[1].results.find((z) => z.scenario_key === k);
                  return (
                    <tr key={k}>
                      <td><code>{k}</code></td>
                      <td>{x ? <Link to={`/workbench/runs/${x.correlation_id}`}><OutcomeTok o={x.outcome} /></Link> : <span className="dim">Not run</span>}{x?.attack_succeeded ? <div className="small" style={{ color: "var(--red)" }}>Attack succeeded</div> : null}</td>
                      <td>{y ? <Link to={`/workbench/runs/${y.correlation_id}`}><OutcomeTok o={y.outcome} /></Link> : <span className="dim">Not run</span>}{y?.attack_succeeded ? <div className="small" style={{ color: "var(--red)" }}>Attack succeeded</div> : null}</td>
                      <td>{x && y && x.outcome !== y.outcome ? "Yes" : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : null}
        </div>
      )}
    </Panel>
  );
}

export function TestsView() {
  const integ = useApi<{ reachable: boolean; error?: string; aegis_ui_url: string; scenarios?: Scenario[] }>("/workbench/integration");
  const runs = useApi<{ items: TestRun[] }>("/workbench/aegis/test-runs");
  const [suite, setSuite] = useState("ecommerce-quick");
  const [picked, setPicked] = useState<string[]>([]);
  const [mode, setMode] = useState<"protected" | "baseline">("protected");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const active = runs.data?.items.some((r) => ["queued", "running"].includes(r.status));
  useEffect(() => {
    if (!active) return;
    const t = window.setTimeout(runs.reload, 2500);
    return () => window.clearTimeout(t);
  }, [runs.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async (body: object) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ run_id: string }>("/workbench/aegis/test-runs", { mode, ...body });
      setOpen(r.run_id);
      runs.reload();
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };
  const bySuite = useMemo(() => (integ.data?.scenarios ?? []).filter((s) => !suite || s.suites.includes(suite)), [integ.data, suite]);

  return (
    <>
      <PageHead title="Security tests" intro="AEGIS runs scenarios against this same agent. Each scenario gets a fresh sandbox copy of the synthetic store, so tests never touch live store data." />
      {integ.data && !integ.data.reachable ? <div className="notice notice-bad" role="alert">AEGIS is not reachable: {integ.data.error}. Start it, then check Model and integration.</div> : null}
      <Panel title="Start a test run">
        <div style={{ display: "grid", gap: 12 }}>
          <fieldset>
            <legend>Mode</legend>
            <label className="check"><input type="radio" name="mode" checked={mode === "protected"} onChange={() => setMode("protected")} /> <span><b>Protected.</b> SENTINEL enforces every decision. This is how the store runs.</span></label>
            <label className="check"><input type="radio" name="mode" checked={mode === "baseline"} onChange={() => setMode("baseline")} /> <span><b>Baseline benchmark.</b> SENTINEL records what it would decide but does not enforce, to measure what the agent does unprotected. Allowed only for test runs in sandboxes; live conversations are always enforced.</span></label>
          </fieldset>
          <div className="field-row" style={{ alignItems: "end" }}>
            <label className="field"><span>Suite</span>
              <select value={suite} onChange={(e) => { setSuite(e.target.value); setPicked([]); }}>
                {SUITES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
            <button className="btn-primary" disabled={busy || !integ.data?.reachable} onClick={() => start({ suite_key: suite })}>Run suite ({mode})</button>
            <button disabled={busy || !picked.length || !integ.data?.reachable} onClick={() => start({ scenario_keys: picked })}>Run {picked.length || ""} selected</button>
          </div>
          <FormError msg={err} />
          <Load r={integ} rows={4}>
            {() => (
              <div className="table-wrap" style={{ maxHeight: 360 }}>
                <table className="t">
                  <thead><tr><th><span className="sr-only">Select</span></th><th>Scenario</th><th>Category</th><th>Risk</th><th>Purpose</th><th>Replay</th></tr></thead>
                  <tbody>
                    {bySuite.map((s) => (
                      <tr key={s.key}>
                        <td><input type="checkbox" aria-label={`Select ${s.key}`} checked={picked.includes(s.key)} onChange={() => setPicked((p) => (p.includes(s.key) ? p.filter((x) => x !== s.key) : [...p, s.key]))} /></td>
                        <td><code>{s.key}</code> {s.name}</td>
                        <td className="small">{s.category.replace(/_/g, " ")}</td>
                        <td className="small">{s.risk}</td>
                        <td className="wrap small">{s.purpose}</td>
                        <td><button className="btn-sm" disabled={busy} onClick={() => start({ scenario_keys: [s.key], label: `Replay ${s.key} (${mode})` })}>Run</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Load>
        </div>
      </Panel>
      <Panel flush title="Recent test runs" actions={<button className="btn-sm" onClick={runs.reload}>Refresh</button>}>
        <Load r={runs} empty={(d) => (d.items.length ? null : <Empty title="No test runs yet">Start one above.</Empty>)}>
          {(d) => (
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>Started</th><th>Mode</th><th>Suite</th><th>Status</th><th>Progress</th><th>Results</th><th /></tr></thead>
                <tbody>
                  {d.items.map((r) => (
                    <Fragment key={r.id}>
                      <tr>
                        <td><Time iso={r.created_at} rel /><div className="small dim"><Short id={r.id} /></div></td>
                        <td><ModeTag mode={r.mode} /></td>
                        <td>{r.suite_key ?? "Selected scenarios"}</td>
                        <td><span className="tag">{r.status}</span></td>
                        <td>
                          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={r.total} aria-valuenow={r.completed} aria-label="Scenarios completed"><i style={{ width: `${r.total ? (100 * r.completed) / r.total : 0}%` }} /></div>
                          <span className="small muted">{r.completed} / {r.total}</span>
                        </td>
                        <td className="small">{r.counts ? Object.entries(r.counts).filter(([k, v]) => v && k !== "total").map(([k, v]) => `${v} ${k}`).join(", ") : "None yet"}</td>
                        <td><button className="btn-sm" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? "Hide" : "Results"}</button></td>
                      </tr>
                      {open === r.id ? <tr><td colSpan={7}><RunResults id={r.id} aegisUi={integ.data?.aegis_ui_url} /></td></tr> : null}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Load>
      </Panel>
      {runs.data ? <CompareRuns runs={runs.data.items} /> : null}
    </>
  );
}
