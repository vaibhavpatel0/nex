import { Link } from "react-router-dom";
import { DecisionTok, Time } from "../../components/ui";
import type { RuntimeEvent } from "../../types";

const SEV_LABEL: Record<string, string> = { info: "Info", notice: "Notice", warning: "Warning", critical: "Critical", error: "Error" };

export function EventRow({ e, compact = false, onSelect, selected }: { e: RuntimeEvent; compact?: boolean; onSelect?: () => void; selected?: boolean }) {
  const decision = e.event_type === "policy.decision" ? e.payload?.decision : null;
  return (
    <tr className={onSelect ? "clickable" : undefined} aria-expanded={onSelect ? !!selected : undefined}
      onClick={onSelect} tabIndex={onSelect ? 0 : undefined} onKeyDown={(k) => { if (onSelect && (k.key === "Enter" || k.key === " ")) { k.preventDefault(); onSelect(); } }}>
      {!compact ? <td className="num dim small">{e.seq}</td> : null}
      <td style={{ whiteSpace: "nowrap" }} className="small"><Time iso={e.created_at} rel={compact} /></td>
      <td className="small" style={{ whiteSpace: "nowrap" }}>
        <span className={`sev sev-${e.severity}`}>{SEV_LABEL[e.severity] ?? e.severity}</span>
      </td>
      {!compact ? <td className="mono small">{e.event_type}</td> : null}
      <td>
        <div className="row" style={{ gap: 6 }}>
          {decision ? <DecisionTok d={decision} /> : null}
          <span>{e.summary}</span>
        </div>
      </td>
      {!compact ? (
        <td>{e.correlation_id ? <Link to={`/traces/${e.correlation_id}`} onClick={(x) => x.stopPropagation()} className="small">Trace</Link> : <span className="dim small">None</span>}</td>
      ) : null}
    </tr>
  );
}
