import { useState } from "react";
import { OutcomeBars, RateLineChart } from "../../components/charts";
import { Empty, Load, PageHead, Panel } from "../../components/ui";
import { useApi } from "../../hooks/useApi";
import { qs } from "../../services/api";
import { metricValue } from "../security-tests/TestsPage";

const LABEL: Record<string, string> = {
  pass_rate: "Pass rate", failure_rate: "Failure rate", error_rate: "Error rate", attack_attempt_rate: "Attack attempt rate (agent susceptibility)",
  attack_success_rate: "Attack success rate", block_rate: "Block rate", false_positive_rate: "False-positive rate", false_negative_rate: "False-negative rate",
  task_completion_rate: "Task completion rate", scenario_latency_p50_ms: "Scenario latency, median", scenario_latency_p95_ms: "Scenario latency, p95",
  tool_call_latency_p50_ms: "Tool call latency, median", tool_calls_total: "Tool calls recorded",
};

export function AnalyticsPage() {
  const agents = useApi("/agents");
  const [agent, setAgent] = useState("");
  const r = useApi(`/analytics${qs({ agent_id: agent })}`, [agent]);
  return (
    <>
      <PageHead title="Analytics" intro="Aggregates across completed test runs. Each metric shows its formula and sample size; anything without enough data says so."
        actions={<label className="field"><span>Agent</span><select value={agent} onChange={(e) => setAgent(e.target.value)}><option value="">All agents</option>{agents.data?.items.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>} />
      <Load r={r} rows={8} empty={(d: any) => d.runs === 0 ? <Panel><Empty title="Insufficient data">No completed test runs yet{agent ? " for this agent" : ""}. Metrics appear after the first run.</Empty></Panel> : null}>
        {(d: any) => {
          const runs = d.per_run;
          const cats = Object.entries(d.by_category).map(([k, v]) => ({ label: k.replace(/_/g, " "), counts: v as Record<string, number> }))
            .sort((a, b) => a.label.localeCompare(b.label));
          return (
            <>
              <div className="notice small">{d.data_label} {d.runs} completed run(s), {d.results.total} scenario results. Coverage: {d.coverage.scenarios_exercised} of {d.coverage.scenarios_enabled} enabled scenarios exercised at least once.</div>
              <div className="grid-2">
                <Panel title="Pass rate and block rate by run">
                  <RateLineChart labels={runs.map((x: any, i: number) => `#${i + 1} ${x.agent_key}`)}
                    series={[{ name: "Pass rate", color: "var(--series-1)", values: runs.map((x: any) => x.pass_rate) },
                      { name: "Block rate", color: "var(--series-2)", values: runs.map((x: any) => x.block_rate) }]} />
                </Panel>
                <Panel title="Outcomes by scenario category">
                  {cats.length ? <OutcomeBars rows={cats} /> : <p className="muted">Insufficient data.</p>}
                </Panel>
              </div>
              <Panel title="Metrics" flush>
                <div className="table-wrap"><table className="t">
                  <thead><tr><th>Metric</th><th className="num">Value</th><th className="num">Numerator</th><th className="num">Denominator</th><th className="num">Sample</th><th>Formula</th></tr></thead>
                  <tbody>{Object.entries(d.metrics).map(([k, m]: [string, any]) => (
                    <tr key={k}><td>{LABEL[k] ?? k}</td><td className="num">{metricValue({ metric: k, ...m })}</td>
                      <td className="num">{m.numerator ?? ""}</td><td className="num">{m.denominator ?? ""}</td><td className="num">{m.sample_size}</td>
                      <td className="small muted wrap">{d.formulas[k]}</td></tr>
                  ))}</tbody>
                </table></div>
              </Panel>
              <p className="small muted">A small or narrow scenario set cannot show that one configuration is safer in general. False-positive and false-negative rates are computed only over tool calls labelled by scenario matchers; unlabelled calls are excluded.</p>
            </>
          );
        }}
      </Load>
    </>
  );
}
