import { Link } from "react-router-dom";
import { CallStatusTok, DecisionTok, Json, ms, OutcomeTok, Time } from "../../components/ui";

// A trace drawn as a vertical spine. Node fill encodes what actually happened:
// hollow = attempted but not executed, filled = reached the tool, struck = blocked,
// dashed amber = held for approval. Every node also carries a text label.
function nodeClass(status: string) {
  return { executed: "node-executed", blocked: "node-blocked", pending_approval: "node-approval", failed: "node-failed" }[status] ?? "";
}

export function SpineLegend() {
  return (
    <div className="legend" aria-label="Legend">
      <span><i className="node node-executed" />Executed (reached the tool)</span>
      <span><i className="node node-failed" />Executed, then failed</span>
      <span><i className="node node-blocked" />Attempted, blocked before execution</span>
      <span><i className="node node-approval" />Attempted, held for approval</span>
      <span><i className="node node-info" />Agent or model step</span>
    </div>
  );
}

export function TraceSpine({ t }: { t: any }) {
  const calls: Record<string, any> = Object.fromEntries(t.tool_calls.map((c: any) => [c.id, c]));
  const items: JSX.Element[] = [];
  for (const e of t.events) {
    if (e.event_type === "agent.request") {
      items.push(
        <li key={e.seq}><i className="node node-info" />
          <div className="spine-title"><b>Request received</b><span className="spine-meta"><Time iso={e.created_at} /></span></div>
          <div className="small">{e.payload?.task}</div>
          {e.payload?.session_subject ? <div className="spine-meta">Session customer {e.payload.session_subject}</div> : null}
          {e.payload?.faults && Object.keys(e.payload.faults).length ? <div className="spine-meta">Injected faults: {JSON.stringify(e.payload.faults)}</div> : null}
          {e.payload?.model_fault ? <div className="spine-meta">Injected model fault: {e.payload.model_fault}</div> : null}
        </li>,
      );
    } else if (e.event_type === "model.response") {
      items.push(
        <li key={e.seq}><i className="node node-info" />
          <div className="spine-title"><b>Model response</b><span className="spine-meta">{e.payload?.simulated ? "Simulated agent" : e.payload?.model}</span></div>
          <div className="small">{e.summary}</div>
          {e.payload?.source === "tool_output" ? <div className="spine-meta">Instructions came from untrusted tool output</div> : null}
          {e.payload?.usage ? <div className="spine-meta">Tokens: {e.payload.usage.prompt_tokens} prompt, {e.payload.usage.completion_tokens} completion</div> : null}
        </li>,
      );
    } else if (e.event_type === "tool.requested") {
      const c = calls[e.payload?.tool_call_id];
      if (!c) continue;
      const d = c.decision_record;
      items.push(
        <li key={e.seq}><i className={`node ${nodeClass(c.status)}`} />
          <div className="spine-title">
            <b className="mono">{c.tool_name}</b>
            <span className="tag">{c.origin === "tool_output" ? "from tool output (agent-reported)" : c.origin.replace("_", " ")}</span>
          </div>
          <div className="call-lane">
            <span className="small muted">Attempted, then</span>
            <DecisionTok d={c.decision} />
            <CallStatusTok s={c.status} />
            {c.enforced === false ? <span className="tok tok-warn" title="Baseline run: SENTINEL recorded this decision but did not enforce it">Not enforced (baseline)</span> : null}
          </div>
          <div className="small" style={{ marginTop: 2 }}>
            {d?.matched_policy_key ? <><span className="mono">{d.matched_policy_key}</span>: </> : null}{d?.reason ?? "No decision recorded"}
          </div>
          {c.error_class ? <div className="small" style={{ color: "var(--red)" }}>Failure class {c.error_class}: {c.error_message}</div> : null}
          <div className="spine-meta">Gateway latency {ms(c.latency_ms)}{c.approval_id ? ` | approval ${c.approval_id}` : ""}</div>
          <details><summary>Arguments, result and evaluated policies</summary>
            <div className="stack" style={{ gap: 6, marginTop: 6 }}>
              <div className="small muted">Arguments (redacted)</div><Json value={c.args_redacted} />
              {c.result_summary ? (<><div className="small muted">Result summary ({c.result_classification})</div><Json value={c.result_summary} /></>) : null}
              {c.flags?.length ? <div className="small">Flags: {c.flags.join(", ")}</div> : null}
              {d ? (
                <table className="t"><thead><tr><th>Policy</th><th>Effect</th><th>Result</th><th>Detail</th></tr></thead>
                  <tbody>{d.evaluated.map((p: any) => (
                    <tr key={p.key}><td className="mono">{p.key}</td><td className="small">{p.effect}</td><td className="small">{p.result}</td><td className="small">{p.detail}</td></tr>
                  ))}</tbody></table>
              ) : null}
            </div>
          </details>
        </li>,
      );
    } else if (e.event_type === "agent.output" || e.event_type === "agent.error") {
      const missing = e.payload?.missing;
      items.push(
        <li key={e.seq}><i className={`node ${e.event_type === "agent.error" ? "node-failed" : missing ? "node-missing" : "node-info"}`} />
          <div className="spine-title"><b>Final result</b><span className="spine-meta">{ms(e.payload?.latency_ms)} end to end</span></div>
          {e.event_type === "agent.error" ? <div className="small" style={{ color: "var(--red)" }}>{e.payload?.error}</div>
            : missing ? <div className="small">The agent returned no final answer. Recorded as missing; nothing was substituted.</div>
            : <pre className="pre">{t.trace.final_output_redacted}</pre>}
          {!e.payload?.usage ? <div className="spine-meta">Token usage: not reported by this agent type</div> : null}
        </li>,
      );
    } else if (e.event_type === "scenario.completed" && t.evaluation) {
      items.push(
        <li key={e.seq}><i className="node node-info" />
          <div className="spine-title"><b>Evaluation</b><OutcomeTok o={t.evaluation.outcome} />
            <Link className="small" to={`/runs/${t.evaluation.run_id}`}>Open run</Link></div>
          <div className="small">{t.evaluation.reason}</div>
          <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {t.evaluation.checks.map((c: any, i: number) => (
              <li key={i}><b style={{ color: c.passed ? "var(--green)" : "var(--red)" }}>{c.passed ? "Passed" : "Failed"}</b>: {c.description}. <span className="muted">{c.evidence}</span></li>
            ))}
          </ul>
        </li>,
      );
    }
  }
  return (
    <div className="stack" style={{ gap: 10 }}>
      <SpineLegend />
      {t.missing?.length ? <div className="notice notice-warn">Not recorded for this trace: {t.missing.join(", ")}. These stages are shown as missing rather than reconstructed.</div> : null}
      {items.length ? <ol className="spine">{items}</ol> : <p className="muted">No events recorded yet.</p>}
    </div>
  );
}
