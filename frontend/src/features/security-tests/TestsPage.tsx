import { FormEvent, Fragment, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApp, useLiveEvents } from "../../app/context";
import { IconPlay, IconRefresh } from "../../components/Icons";
import {
  AgentTypeTag, Empty, ModeTag, FormError, Load, ms, OutcomeTok, PageHead, Panel, Pager, pct, RunStatusTok, Sev, Short, Time,
} from "../../components/ui";
import { useAction, useApi } from "../../hooks/useApi";
import { api, qs } from "../../services/api";

function Launcher() {
  const { can, toast } = useApp();
  const nav = useNavigate();
  const agents = useApi("/agents");
  const suites = useApi("/test-suites");
  const scen = useApi("/scenarios");
  const [agentId, setAgentId] = useState("");
  const [mode, setMode] = useState<"suite" | "pick">("suite");
  const [suite, setSuite] = useState("quick-demo");
  const [picked, setPicked] = useState<string[]>([]);
  const [cat, setCat] = useState("");
  const [label, setLabel] = useState("");
  const [runMode, setRunMode] = useState<"protected" | "baseline">("protected");
  const act = useAction();
  const agent = agents.data?.items.find((a: any) => a.id === agentId) ?? agents.data?.items[0];
  const visible = useMemo(() => (scen.data?.items ?? []).filter((s: any) => !cat || s.category === cat), [scen.data, cat]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body = mode === "suite" ? { agent_id: agent.id, suite_key: suite, label, mode: runMode } : { agent_id: agent.id, scenario_keys: picked, label, mode: runMode };
    const r = await act.run(() => api.post("/test-runs", body));
    if (r) { toast("Test run started"); nav(`/runs/${r.run_id}`); }
  };
  if (!agents.data || !suites.data || !scen.data) return <Panel title="Start a test run"><Load r={agents}>{() => null}</Load></Panel>;
  const missing = (s: any) => s.required_tools.filter((t: string) => !agent?.enabled_tools.includes(t));
  const enabledAgents = agents.data.items.filter((a: any) => a.enabled);
  const inSuite = (key: string) => scen.data.items.filter((s: any) => (s.suites ?? []).includes(key));
  const runnable = (key: string) => inSuite(key).filter((s: any) => missing(s).length === 0).length;
  const capableAgents = (key: string) => enabledAgents.filter((a: any) =>
    inSuite(key).some((s: any) => s.required_tools.every((t: string) => a.enabled_tools.includes(t))));
  const suiteBlocked = mode === "suite" && runnable(suite) === 0;

  return (
    <Panel title="Start a test run">
      <form className="stack" style={{ gap: 12 }} onSubmit={submit}>
        <div className="field-row">
          <label className="field"><span>Target agent</span>
            <select value={agent?.id} onChange={(e) => setAgentId(e.target.value)}>
              {agents.data.items.filter((a: any) => a.enabled).map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select></label>
          <label className="field"><span>Label (optional)</span><input value={label} maxLength={160} onChange={(e) => setLabel(e.target.value)} placeholder="For example: v2 after prompt change" /></label>
        </div>
        {agent ? <div className="row small"><AgentTypeTag t={agent.agent_type} /><span className="muted">{agent.connection.detail}</span></div> : null}
        <div className="seg" role="group" aria-label="Scenario selection">
          <button type="button" aria-pressed={mode === "suite"} onClick={() => setMode("suite")}>Run a suite</button>
          <button type="button" aria-pressed={mode === "pick"} onClick={() => setMode("pick")}>Choose scenarios</button>
        </div>
        {mode === "suite" ? (
          <fieldset><legend>Suite</legend>
            {suites.data.items.map((s: any) => {
              const n = runnable(s.key);
              const fit = capableAgents(s.key).map((a: any) => a.name);
              return (
                <label key={s.key} className="check"><input type="radio" name="suite" checked={suite === s.key} onChange={() => setSuite(s.key)} disabled={s.scenario_count === 0} />
                  <span><b>{s.name}</b> <span className="muted">({s.scenario_count} scenarios)</span><br /><span className="small muted">{s.description}</span>
                    {s.scenario_count > 0 && n < s.scenario_count ? (
                      <><br /><span className="small" style={{ color: n === 0 ? "var(--amber)" : undefined }}>
                        {n === 0 ? "None of these scenarios can run against the selected agent." : `${n} of ${s.scenario_count} can run against the selected agent; the rest will be skipped.`}
                        {n === 0 && fit.length ? ` Choose ${fit.join(" or ")}.` : ""}</span></>
                    ) : null}</span></label>
              );
            })}
          </fieldset>
        ) : (
          <fieldset><legend>Scenarios ({picked.length} selected)</legend>
            <div className="row" style={{ marginBottom: 6 }}>
              <select aria-label="Category" value={cat} onChange={(e) => setCat(e.target.value)}><option value="">All categories</option>{scen.data.categories.map((c: string) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}</select>
              <button type="button" className="btn-sm" onClick={() => setPicked([...new Set([...picked, ...visible.map((s: any) => s.key)])])}>Select shown</button>
              <button type="button" className="btn-sm" onClick={() => setPicked([])}>Clear</button>
            </div>
            <div style={{ maxHeight: 300, overflow: "auto" }}>
              {visible.map((s: any) => (
                <label key={s.key} className="check"><input type="checkbox" checked={picked.includes(s.key)} onChange={(e) => setPicked(e.target.checked ? [...picked, s.key] : picked.filter((k) => k !== s.key))} />
                  <span><span className="mono">{s.key}</span> {s.name} <span className="small muted">{s.category.replace(/_/g, " ")}, risk {s.risk}</span>
                    {missing(s).length ? <span className="small dim"> Will be skipped: agent lacks {missing(s).join(", ")}</span> : null}</span></label>
              ))}
            </div>
          </fieldset>
        )}
        <fieldset><legend>Enforcement</legend>
          <label className="check"><input type="radio" name="runmode" checked={runMode === "protected"} onChange={() => setRunMode("protected")} />
            <span><b>Protected.</b> SENTINEL enforces every decision.</span></label>
          <label className="check"><input type="radio" name="runmode" checked={runMode === "baseline"} onChange={() => setRunMode("baseline")} />
            <span><b>Baseline benchmark.</b> SENTINEL records what it would decide but does not enforce, so you can measure the unprotected agent. Allowed only for test runs, which always use sandbox fixtures.</span></label>
        </fieldset>
        <FormError msg={act.error} />
        <div className="row">
          <button className="btn-primary" disabled={!can("tests:run") || act.busy || (mode === "pick" && !picked.length) || suiteBlocked}><IconPlay />Start test run</button>
          {suiteBlocked ? <span className="small muted">This agent lacks the tools this suite needs, so every scenario would be skipped. Pick another agent.</span> : null}
          {!can("tests:run") ? <span className="small muted">Your role cannot start test runs.</span> : null}
        </div>
      </form>
    </Panel>
  );
}

function RunList() {
  const [offset, setOffset] = useState(0);
  const r = useApi(`/test-runs${qs({ limit: 15, offset })}`, [offset]);
  useLiveEvents((e) => { if (e.event_type.startsWith("run.") || e.event_type === "scenario.completed") r.reload(); });
  return (
    <Panel title="Test runs" flush>
      <Load r={r} empty={(d: any) => d.items.length === 0 ? <Empty title="No test runs yet">Start one with the form.</Empty> : null}>
        {(d: any) => (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Run</th><th>Agent</th><th>Status</th><th className="num">Pass</th><th className="num">Fail</th><th className="num">Other</th><th>Started</th></tr></thead>
              <tbody>{d.items.map((x: any) => {
                const c = x.summary?.counts;
                return (
                  <tr key={x.id}>
                    <td><Link to={`/runs/${x.id}`}>{x.label || x.id}</Link> <ModeTag mode={x.mode} />{x.replay_of_run_id ? <div className="small dim">Replay</div> : null}</td>
                    <td className="small"><span className="mono">{x.agent.key}</span></td>
                    <td>{x.status === "running" || x.status === "queued" ? <span className="row"><RunStatusTok s={x.status} /><span className="small">{x.completed_scenarios}/{x.total_scenarios}</span></span> : <RunStatusTok s={x.status} />}</td>
                    <td className="num">{c?.PASS ?? ""}</td><td className="num">{c?.FAIL ?? ""}</td><td className="num">{c ? c.ERROR + c.INCONCLUSIVE + c.SKIPPED : ""}</td>
                    <td className="small"><Time iso={x.created_at} rel /></td>
                  </tr>
                );
              })}</tbody>
            </table></div>
            <Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} />
          </>
        )}
      </Load>
    </Panel>
  );
}

export function TestsPage() {
  return (
    <>
      <PageHead title="Run security tests" intro="AEGIS runs deterministic scenarios against an agent in a fresh sandbox per scenario and judges each result from the recorded tool calls and decisions, not from how plausible the answer sounds." />
      <div className="grid-main-side"><Launcher /><RunList /></div>
    </>
  );
}

// ---------------------------------------------------------------- run detail
const METRIC_LABEL: Record<string, string> = {
  pass_rate: "Pass rate", failure_rate: "Failure rate", error_rate: "Error rate", attack_attempt_rate: "Attack attempt rate (agent)",
  attack_success_rate: "Attack success rate", block_rate: "Block rate", false_positive_rate: "False-positive rate",
  false_negative_rate: "False-negative rate", task_completion_rate: "Task completion rate", scenario_latency_p50_ms: "Scenario latency, median",
  scenario_latency_p95_ms: "Scenario latency, p95", tool_call_latency_p50_ms: "Tool call latency, median", tool_calls_total: "Tool calls",
};
export const metricValue = (m: any) => m.metric.endsWith("_rate") ? pct(m.value, m.note) : m.metric.endsWith("_ms") ? (m.value == null ? m.note || "Insufficient data" : ms(m.value)) : m.value == null ? m.note : String(m.value);

export function RunDetailPage() {
  const { id } = useParams();
  const { can, toast } = useApp();
  const nav = useNavigate();
  const r = useApi(`/test-runs/${id}`, [id]);
  const agents = useApi("/agents");
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [replayAgent, setReplayAgent] = useState("");
  useLiveEvents((e) => { if (e.run_id === id && (e.event_type === "scenario.completed" || e.event_type.startsWith("run."))) r.reload(); }, [id]);

  const replay = async () => {
    try { const x = await api.post(`/test-runs/${id}/replay`, { agent_id: replayAgent || undefined }); toast("Replay started"); nav(`/runs/${x.run_id}`); }
    catch (e) { toast((e as Error).message, true); }
  };
  const investigate = async () => {
    try { const x = await api.post("/investigations", { run_id: id }); nav(`/investigations/${x.id}`); }
    catch (e) { toast((e as Error).message, true); }
  };
  const report = async () => {
    try { const x = await api.post("/reports", { run_id: id }); toast("Report generated"); nav(`/reports/${x.id}`); }
    catch (e) { toast((e as Error).message, true); }
  };
  const regress = async (key: string) => {
    try { await api.patch(`/scenarios/${key}`, { regression: true }); toast(`${key} added to the regression suite`); }
    catch (e) { toast((e as Error).message, true); }
  };

  return (
    <Load r={r} rows={10}>
      {(d: any) => {
        const counts = d.summary?.counts;
        const results = d.results.filter((x: any) => !filter || x.outcome === filter);
        const active = d.status === "running" || d.status === "queued";
        return (
          <>
            <PageHead title={d.label || "Test run"} intro={<>Agent <Link to={`/agents/${d.agent.id}`}>{d.agent.name}</Link> at revision {d.agent_revision}. Suite {d.suite_key ?? "custom selection"}. Run <span className="mono">{d.id}</span>.</>}
              actions={<>
                {can("tests:run") ? <><select aria-label="Agent for replay" value={replayAgent} onChange={(e) => setReplayAgent(e.target.value)}><option value="">Replay with same agent</option>{agents.data?.items.filter((a: any) => a.id !== d.agent.id).map((a: any) => <option key={a.id} value={a.id}>Replay with {a.name}</option>)}</select>
                  <button onClick={replay} disabled={active}><IconRefresh />Replay</button></> : null}
                {can("investigations:write") ? <button onClick={investigate} disabled={active}>Open investigation</button> : null}
                {can("reports:generate") ? <button className="btn-primary" onClick={report} disabled={d.status !== "completed"}>Generate report</button> : null}
              </>} />
            {d.status === "failed" ? <div className="notice notice-bad">{d.error}</div> : null}
            {active ? (
              <div className="row"><RunStatusTok s={d.status} /><span className="progress" style={{ width: 240 }}><i style={{ width: `${(d.completed_scenarios / Math.max(1, d.total_scenarios)) * 100}%` }} /></span><span className="small">{d.completed_scenarios} of {d.total_scenarios} scenarios evaluated</span></div>
            ) : null}
            {counts ? (
              <div className="strip">
                {["PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED"].map((o) => (
                  <div key={o}><div className="v">{counts[o]}</div><div className="l"><OutcomeTok o={o} /></div></div>
                ))}
              </div>
            ) : null}
            <div className="grid-main-side">
              <Panel title="Scenario results" flush actions={
                <select aria-label="Filter outcomes" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="">All outcomes</option>{["PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED"].map((o) => <option key={o}>{o}</option>)}</select>}>
                {d.results.length === 0 ? <Empty title="No scenario has finished yet" /> : (
                  <div className="table-wrap"><table className="t">
                    <thead><tr><th>Scenario</th><th>Outcome</th><th>Agent attempted attack</th><th className="num">Calls</th><th>Latency</th><th>Reason</th></tr></thead>
                    <tbody>{results.map((x: any) => (
                      <Fragment key={x.id}>
                        <tr className="clickable" aria-expanded={open === x.id} tabIndex={0} onClick={() => setOpen(open === x.id ? null : x.id)}
                          onKeyDown={(k) => { if (k.key === "Enter") setOpen(open === x.id ? null : x.id); }}>
                          <td><span className="mono">{x.scenario.key}</span> {x.scenario.name}<div className="small muted">{x.scenario.category.replace(/_/g, " ")}, risk {x.scenario.risk}, {x.scenario.ground_truth}</div></td>
                          <td><OutcomeTok o={x.outcome} /></td>
                          <td className="small">{x.scenario.ground_truth === "malicious" || x.prohibited_attempts ? (x.attack_attempted ? `Yes, ${x.prohibited_blocked} of ${x.prohibited_attempts} stopped` : "No") : "Not applicable"}</td>
                          <td className="num">{x.tool_calls_total}</td><td className="small">{ms(x.latency_ms)}</td>
                          <td className="small wrap">{x.reason}</td>
                        </tr>
                        {open === x.id ? (
                          <tr><td colSpan={6}>
                            <div className="stack" style={{ gap: 8 }}>
                              <p className="small">{x.scenario.purpose}</p>
                              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                                {x.checks.length ? x.checks.map((c: any, i: number) => (
                                  <li key={i}><b style={{ color: c.passed ? "var(--green)" : "var(--red)" }}>{c.passed ? "Passed" : "Failed"}</b>: {c.description}. <span className="muted">{c.evidence}</span></li>
                                )) : <li>No checks ran ({x.outcome.toLowerCase()}).</li>}
                              </ul>
                              <div className="row">
                                <Link className="btn btn-sm" to={`/traces/${x.correlation_id}`}>Open trace</Link>
                                {x.outcome === "FAIL" && can("tests:run") ? <button className="btn-sm" onClick={() => regress(x.scenario.key)}>Add to regression suite</button> : null}
                              </div>
                            </div>
                          </td></tr>
                        ) : null}
                      </Fragment>
                    ))}</tbody>
                  </table></div>
                )}
              </Panel>
              <div className="stack">
                <Panel title="Metrics" flush>
                  {d.metrics.length === 0 ? <Empty title="Computed when the run completes" /> : (
                    <table className="t"><tbody>{d.metrics.map((m: any) => (
                      <tr key={m.metric}><td title={m.formula}>{METRIC_LABEL[m.metric] ?? m.metric}<div className="small dim">{m.formula}</div></td>
                        <td className="num">{metricValue(m)}<div className="small dim">n = {m.sample_size}</div></td></tr>
                    ))}</tbody></table>
                  )}
                </Panel>
                <Panel title={`Findings (${d.findings.length})`} flush>
                  {d.findings.length === 0 ? <Empty title="No findings" /> : (
                    <table className="t"><tbody>{d.findings.map((f: any) => (
                      <tr key={f.id}><td><Sev s={f.severity} /><div className="small dim">{f.status}</div></td><td><b className="small">{f.title}</b><div className="small muted">{f.remediation}</div></td></tr>
                    ))}</tbody></table>
                  )}
                </Panel>
                <Panel title="Reproducibility">
                  <dl className="kv small">
                    <dt>Agent snapshot</dt><dd>{d.agent_snapshot.key} rev {d.agent_snapshot.revision}, profile {d.agent_snapshot.permission_profile.key} rev {d.agent_snapshot.permission_profile.revision}</dd>
                    <dt>Model</dt><dd>{d.agent_snapshot.model_id ?? "None (simulated)"}</dd>
                    <dt>Policy set</dt><dd><Short id={d.policy_hash} /></dd>
                    <dt>Platform</dt><dd>{d.software_version}</dd>
                    <dt>Scenarios</dt><dd>{d.scenario_keys.length}</dd>
                    <dt>Started</dt><dd><Time iso={d.started_at} /></dd>
                    <dt>Finished</dt><dd><Time iso={d.finished_at} /></dd>
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
