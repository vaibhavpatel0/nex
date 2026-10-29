import { ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import type { ApiError } from "../services/api";
import type { Decision, Outcome } from "../types";
import { IconAlert, IconBlock, IconCheck, IconDash, IconHold, IconQuestion, IconRefresh } from "./Icons";

// ---------------------------------------------------------------- status tokens (text + glyph, never colour alone)
const DECISION: Record<string, { cls: string; label: string; icon: ReactNode }> = {
  ALLOW: { cls: "tok-allow", label: "Allowed", icon: <IconCheck size={12} /> },
  BLOCK: { cls: "tok-block", label: "Blocked", icon: <IconBlock size={12} /> },
  REQUIRE_APPROVAL: { cls: "tok-approval", label: "Needs approval", icon: <IconHold size={12} /> },
};
export function DecisionTok({ d }: { d: Decision | string | null | undefined }) {
  if (!d) return <span className="tok tok-neutral"><IconDash size={12} />No decision</span>;
  const m = DECISION[d] ?? { cls: "tok-neutral", label: d, icon: <IconQuestion size={12} /> };
  return <span className={`tok ${m.cls}`}>{m.icon}{m.label}</span>;
}

const OUTCOME: Record<string, { cls: string; icon: ReactNode }> = {
  PASS: { cls: "tok-pass", icon: <IconCheck size={12} /> },
  FAIL: { cls: "tok-fail", icon: <IconBlock size={12} /> },
  ERROR: { cls: "tok-error", icon: <IconAlert size={12} /> },
  INCONCLUSIVE: { cls: "tok-inconclusive", icon: <IconQuestion size={12} /> },
  SKIPPED: { cls: "tok-skipped", icon: <IconDash size={12} /> },
};
export function OutcomeTok({ o }: { o: Outcome | string }) {
  const m = OUTCOME[o] ?? OUTCOME.SKIPPED;
  return <span className={`tok ${m.cls}`}>{m.icon}{o}</span>;
}

const CALL: Record<string, [string, string]> = {
  executed: ["tok-ok", "Executed"],
  blocked: ["tok-block", "Blocked, not executed"],
  pending_approval: ["tok-approval", "Waiting for approval"],
  failed: ["tok-error", "Executed, failed"],
  requested: ["tok-neutral", "Requested"],
};
export function CallStatusTok({ s }: { s: string }) {
  const [cls, label] = CALL[s] ?? ["tok-neutral", s];
  return <span className={`tok ${cls}`}>{label}</span>;
}

const RUN: Record<string, string> = { completed: "tok-neutral", running: "tok-info", queued: "tok-info", failed: "tok-error", cancelled: "tok-neutral" };
export function RunStatusTok({ s }: { s: string }) {
  return <span className={`tok ${RUN[s] ?? "tok-neutral"}`}>{s[0].toUpperCase() + s.slice(1)}</span>;
}

export function ConnTok({ state }: { state: string }) {
  const cls = state === "ready" ? "tok-ok" : state === "unavailable" ? "tok-error" : state === "disabled" ? "tok-neutral" : "tok-warn";
  const label = { ready: "Ready", unavailable: "Unavailable", unknown: "Not checked", disabled: "Disabled" }[state] ?? state;
  return <span className={`tok ${cls}`}>{label}</span>;
}

export function AgentTypeTag({ t }: { t: string }) {
  const label = { simulated: "Simulated", local_model: "Local model", hosted_model: "External hosted", remote_agent: "Remote agent (e-commerce platform)" }[t] ?? t;
  return <span className="tag">{label}</span>;
}

export function Sev({ s }: { s: string }) {
  return <span className={`sev sev-${s}`}>{s[0].toUpperCase() + s.slice(1)}</span>;
}

// ---------------------------------------------------------------- time
export function Time({ iso, rel = false }: { iso?: string | null; rel?: boolean }) {
  if (!iso) return <span className="dim">Not recorded</span>;
  const d = new Date(iso);
  const local = d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  let text = local;
  if (rel) {
    const s = Math.round((Date.now() - d.getTime()) / 1000);
    text = s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : local;
  }
  return <time dateTime={iso} title={`${local} (local) / ${iso} (UTC)`}>{text}</time>;
}

export const pct = (v: number | null | undefined, note?: string) =>
  v === null || v === undefined ? note || "Insufficient data" : `${(v * 100).toFixed(1)}%`;
export const ms = (v: number | null | undefined) => (v === null || v === undefined ? "Not recorded" : v < 1000 ? `${v.toFixed(0)} ms` : `${(v / 1000).toFixed(2)} s`);

// ---------------------------------------------------------------- layout pieces
export function Panel({ title, actions, children, flush, id }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; id?: string }) {
  return (
    <section className="panel" aria-labelledby={id}>
      {title ? (
        <div className="panel-head">
          <h2 id={id}>{title}</h2>
          {actions}
        </div>
      ) : null}
      <div className={"panel-body" + (flush ? " flush" : "")}>{children}</div>
    </section>
  );
}

export function PageHead({ title, intro, actions }: { title: string; intro?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div className="intro">
        <h1>{title}</h1>
        {intro ? <p>{intro}</p> : null}
      </div>
      {actions ? <div className="row">{actions}</div> : null}
    </div>
  );
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="state" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skel" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: ApiError | Error | string; retry?: () => void }) {
  const msg = typeof error === "string" ? error : error.message;
  return (
    <div className="state" role="alert">
      <h3>Could not load this data</h3>
      <p className="muted">{msg}</p>
      {retry ? <button onClick={retry}><IconRefresh />Try again</button> : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state">
      <h3>{title}</h3>
      {children ? <div className="muted">{children}</div> : null}
    </div>
  );
}

/** Renders loading / error / empty / content for a resource. */
export function Load<T>({ r, empty, children, rows }: { r: { data: T | null; error: any; loading: boolean; reload: () => void };
  empty?: (d: T) => ReactNode | null; children: (d: T) => ReactNode; rows?: number }) {
  if (r.loading && !r.data) return <Skeleton rows={rows} />;
  if (r.error && !r.data) return <ErrorState error={r.error} retry={r.reload} />;
  if (!r.data) return null;
  const e = empty?.(r.data);
  if (e) return <>{e}</>;
  return <>{children(r.data)}</>;
}

export function Json({ value }: { value: unknown }) {
  return <pre className="pre">{JSON.stringify(value, null, 2)}</pre>;
}

export function Short({ id, to }: { id?: string | null; to?: string }) {
  if (!id) return <span className="dim">None</span>;
  const s = <code title={id}>{id.length > 16 ? id.slice(0, 4) + id.slice(-8) : id}</code>;
  return to ? <Link to={to}>{s}</Link> : s;
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (o: number) => void }) {
  if (total <= limit) return <div className="small muted" style={{ padding: "8px 10px" }}>{total} record{total === 1 ? "" : "s"}</div>;
  return (
    <div className="row small" style={{ padding: "8px 10px" }}>
      <span className="muted">{offset + 1} to {Math.min(total, offset + limit)} of {total}</span>
      <button className="btn-sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
      <button className="btn-sm" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Next</button>
    </div>
  );
}

export function Expandable({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="btn-ghost btn-sm" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} {summary}</button>
      {open ? <div style={{ marginTop: 6 }}>{children}</div> : null}
    </div>
  );
}

export function FormError({ msg }: { msg: string | null }) {
  return msg ? <div className="notice notice-bad" role="alert">{msg}</div> : null;
}

export function ModeTag({ mode }: { mode?: string | null }) {
  if (mode === "baseline") return <span className="tok tok-warn" title="SENTINEL recorded decisions but did not enforce them (sandbox only)">Baseline, not enforced</span>;
  return <span className="tok tok-info">Protected</span>;
}
