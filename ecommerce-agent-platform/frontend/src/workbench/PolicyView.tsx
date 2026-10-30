import { Link, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { DecisionTok, Json, Load, PageHead, Panel, Time } from "../lib/ui";

export function PolicyView() {
  const { key } = useParams();
  const r = useApi<any>(`/workbench/aegis/policies/${encodeURIComponent(key ?? "")}`);
  return (
    <Load r={r} rows={6}>
      {(p) => (
        <>
          <PageHead title={`${p.key} ${p.name}`} intro={p.description} actions={<Link className="btn" to="/workbench">Back to runs</Link>} />
          <Panel title="Policy as SENTINEL evaluates it">
            <dl className="kv">
              <dt>Effect</dt><dd><DecisionTok d={p.effect} /></dd>
              <dt>Rule type</dt><dd><code>{p.rule_type}</code></dd>
              <dt>Priority</dt><dd>{p.priority} (higher is evaluated first)</dd>
              <dt>Enabled</dt><dd>{p.enabled ? "Yes" : "No"}{p.locked ? ", locked" : ""}</dd>
              <dt>Version</dt><dd>{p.version}, updated <Time iso={p.updated_at} /> by {p.updated_by}</dd>
              <dt>Risk</dt><dd>{p.risk}</dd>
              <dt>Applies to</dt><dd>{(p.applies_to ?? []).map((t: string) => <code key={t} style={{ marginRight: 8 }}>{t}</code>)}</dd>
            </dl>
            <h3 style={{ marginTop: 12 }}>Parameters</h3>
            <Json value={p.params} />
            <p className="small muted" style={{ marginTop: 8 }}>Policies are edited in AEGIS under Policies. This view is read-only.</p>
          </Panel>
        </>
      )}
    </Load>
  );
}
