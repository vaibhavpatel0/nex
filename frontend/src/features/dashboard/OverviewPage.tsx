import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApp, useLiveEvents } from "../../app/context";
import { IconPlay } from "../../components/Icons";
import {
  AgentTypeTag, CallStatusTok, ConnTok, DecisionTok, Empty, Load, OutcomeTok, PageHead, Panel, RunStatusTok, Short, Time,
} from "../../components/ui";
import { useApi } from "../../hooks/useApi";
import { api } from "../../services/api";
import type { RuntimeEvent } from "../../types";
import { EventRow } from "../live-monitoring/EventRow";

function FirstRun({ onStarted }: { onStarted: (id: string) => void }) {
  const { can, toast } = useApp();
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      const agents = await api.get("/agents");
      const naive = agents.items.find((a: any) => a.key === "ops-naive");
      const r = await api.post("/test-runs", { agent_id: naive.id, suite_key: "quick-demo", label: "First run: naive agent, quick suite" });
      toast("Demo run started");
      onStarted(r.run_id);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="No activity recorded yet">
      <div className="stack" style={{ gap: 10 }}>
        <p>
          This installation has no test runs and no tool calls. Every number on this page comes from stored records,
          so there is nothing to show until something runs.
        </p>
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          <li>Run the quick suite: 8 real scenarios against the rule-based <b>Ops assistant v1 (naive)</b> agent. It takes a few seconds and every result is recorded.</li>
          <li>Open the run to see which attacks the agent attempted and which ones SENTINEL stopped.</li>
          <li>Run the same suite against <b>v2 (guarded)</b> and compare the two runs in an investigation.</li>
        </ol>
        <p className="small muted">All scenario targets are local sandbox fixtures. No external system is contacted.</p>
        <div className="row">
          {can("tests:run") ? (
            <button className="btn-primary" disabled={busy} onClick={start}><IconPlay />Run the quick suite</button>
          ) : (
            <span className="muted">Your role is read-only. Ask an operator to start a test run.</span>
          )}
          <Link to="/runtime" className="btn">Try runtime protection</Link>
        </div>
      </div>
    </Panel>
  );
}

export function OverviewPage() {
  const o = useApi<any>("/overview", [], 15000);
  const nav = useNavigate();
  const [live, setLive] = useState<RuntimeEvent[]>([]);
  useLiveEvents((e) => {
    setLive((l) => [e, ...l].slice(0, 15));
    if (["run.completed", "approval.requested", "approval.decided", "policy.decision", "scenario.completed"].includes(e.event_type)) o.reload();
  });

  return (
    <>
      <PageHead title="Overview" intro="Current state of agents, enforcement decisions and test activity. Counts are computed from stored records." />
      <Load r={o} rows={8}>
        {(d) => {
          const events = [...live, ...d.recent_events.filter((e: RuntimeEvent) => !live.some((l) => l.seq === e.seq))].slice(0, 15);
          return (
            <>
              {d.empty ? <FirstRun onStarted={(id) => nav(`/runs/${id}`)} /> : null}
              <div className="strip" role="group" aria-label="Enforcement totals">
                <div><div className="v">{d.decisions.all_time.ALLOW}</div><div className="l">Allowed tool calls</div><div className="s">{d.decisions_live.ALLOW} live, {d.decisions.all_time.ALLOW - d.decisions_live.ALLOW} in tests; {d.decisions.last_24h.ALLOW} in 24 h</div></div>
                <div><div className="v">{d.decisions.all_time.BLOCK}</div><div className="l">Blocked before execution</div><div className="s">{d.decisions_live.BLOCK} live, {d.decisions.all_time.BLOCK - d.decisions_live.BLOCK} in tests; {d.decisions.last_24h.BLOCK} in 24 h</div></div>
                <div><div className="v">{d.decisions.all_time.REQUIRE_APPROVAL}</div><div className="l">Held for approval</div><div className="s">{d.pending_approvals.length} waiting now</div></div>
                <div><div className="v">{d.tool_calls.executed}</div><div className="l">Tool executions</div><div className="s">{d.tool_calls.executed_live} live, {d.tool_calls.executed - d.tool_calls.executed_live} in test sandboxes; {d.tool_calls.failed} failed</div></div>
                <div><div className="v">{d.active_runs.length}</div><div className="l">Test runs in progress</div><div className="s">{d.recent_runs.length ? "Latest shown below" : "None yet"}</div></div>
              </div>

              <div className="grid-main-side">
                <div className="stack">
                  <Panel title="Agents" flush actions={<Link to="/agents" className="btn btn-sm">Manage agents</Link>}>
                    <div className="table-wrap"><table className="t">
                      <thead><tr><th>Agent</th><th>Type</th><th>Status</th><th>Detail</th></tr></thead>
                      <tbody>{d.agents.map((a: any) => (
                        <tr key={a.id}>
                          <td><Link to={`/agents/${a.id}`}>{a.name}</Link></td>
                          <td><AgentTypeTag t={a.agent_type} /></td>
                          <td><ConnTok state={a.connection.state} /></td>
                          <td className="small muted wrap">{a.connection.detail}</td>
                        </tr>
                      ))}</tbody>
                    </table></div>
                  </Panel>

                  <Panel title="Recent test runs" flush actions={<Link to="/tests" className="btn btn-sm">Run security tests</Link>}>
                    {d.recent_runs.length === 0 ? <Empty title="No test runs yet" /> : (
                      <div className="table-wrap"><table className="t">
                        <thead><tr><th>Run</th><th>Agent</th><th>Status</th><th className="num">Pass</th><th className="num">Fail</th><th className="num">Error</th><th>Started</th></tr></thead>
                        <tbody>{d.recent_runs.map((r: any) => {
                          const act = d.active_runs.find((a: any) => a.id === r.id);
                          return (
                            <tr key={r.id}>
                              <td><Link to={`/runs/${r.id}`}>{r.label || r.id}</Link></td>
                              <td className="mono">{r.agent_key}</td>
                              <td>{act ? <span className="row"><RunStatusTok s={r.status} /><span className="progress" style={{ width: 80 }}><i style={{ width: `${(act.completed / Math.max(1, act.total)) * 100}%` }} /></span><span className="small">{act.completed}/{act.total}</span></span> : <RunStatusTok s={r.status} />}</td>
                              <td className="num">{r.counts?.PASS ?? "Not complete"}</td><td className="num">{r.counts?.FAIL ?? ""}</td><td className="num">{r.counts?.ERROR ?? ""}</td>
                              <td><Time iso={r.created_at} rel /></td>
                            </tr>
                          );
                        })}</tbody>
                      </table></div>
                    )}
                  </Panel>

                  <Panel title="Recently blocked operations" flush actions={<Link to="/runtime" className="btn btn-sm">Runtime protection</Link>}>
                    {d.recent_blocks.length === 0 ? <Empty title="Nothing has been blocked yet" /> : (
                      <div className="table-wrap"><table className="t">
                        <thead><tr><th>Tool</th><th>Decision</th><th>Policy</th><th>Reason</th><th>Trace</th><th>When</th></tr></thead>
                        <tbody>{d.recent_blocks.map((b: any) => (
                          <tr key={b.id}>
                            <td className="mono">{b.tool_name}</td><td><DecisionTok d={b.decision} /></td>
                            <td className="mono">{b.matched_policy_key ?? "Default deny"}</td><td className="wrap small">{b.reason}</td>
                            <td><Short id={b.correlation_id} to={`/traces/${b.correlation_id}`} /></td><td><Time iso={b.created_at} rel /></td>
                          </tr>
                        ))}</tbody>
                      </table></div>
                    )}
                  </Panel>

                  <Panel title="Recent failures" flush>
                    {d.recent_failures.results.length + d.recent_failures.tool_calls.length === 0 ? <Empty title="No failures recorded" /> : (
                      <div className="table-wrap"><table className="t">
                        <thead><tr><th>Kind</th><th>Item</th><th>Status</th><th>Detail</th><th>When</th></tr></thead>
                        <tbody>
                          {d.recent_failures.results.map((r: any) => (
                            <tr key={r.id}><td>Scenario</td><td><Link to={`/runs/${r.run_id}`}>{r.scenario_key}</Link></td><td><OutcomeTok o={r.outcome} /></td><td className="wrap small">{r.reason}</td><td><Time iso={r.started_at} rel /></td></tr>
                          ))}
                          {d.recent_failures.tool_calls.map((c: any) => (
                            <tr key={c.id}><td>Tool call</td><td><Link to={`/traces/${c.correlation_id}`} className="mono">{c.tool_name}</Link></td><td><CallStatusTok s={c.status} /></td><td className="small">{c.error_class}: {c.error_message}</td><td><Time iso={c.created_at} rel /></td></tr>
                          ))}
                        </tbody>
                      </table></div>
                    )}
                  </Panel>
                </div>

                <div className="stack">
                  <Panel title="Waiting for approval" flush actions={<Link to="/runtime" className="btn btn-sm">Review</Link>}>
                    {d.pending_approvals.length === 0 ? <Empty title="No pending approvals" /> : (
                      <table className="t"><tbody>{d.pending_approvals.map((a: any) => (
                        <tr key={a.id}><td><div className="mono">{a.tool_name}</div><div className="small muted">{a.reason}</div></td><td><Time iso={a.expires_at} /><div className="small dim">expires</div></td></tr>
                      ))}</tbody></table>
                    )}
                  </Panel>
                  <Panel title="Event timeline" flush actions={<Link to="/live" className="btn btn-sm">All live events</Link>}>
                    {events.length === 0 ? <Empty title="No events yet" /> : (
                      <table className="t"><tbody>{events.map((e) => <EventRow key={e.seq} e={e} compact />)}</tbody></table>
                    )}
                  </Panel>
                </div>
              </div>
            </>
          );
        }}
      </Load>
    </>
  );
}
