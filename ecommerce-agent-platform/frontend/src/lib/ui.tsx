import { ReactNode } from "react";
import type { ApiError } from "./api";
import { IconAlert, IconBlock, IconCheck, IconDash, IconHold, IconQuestion, IconRefresh } from "./icons";

export function Panel({ title, actions, children, flush, label }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; label?: string }) {
  return (
    <section className="panel" aria-label={label ?? (typeof title === "string" ? title : undefined)}>
      {title ? (
        <div className="panel-head">
          <h2>{title}</h2>
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
  const status = typeof error === "object" && "status" in error ? (error as ApiError).status : undefined;
  return (
    <div className="state" role="alert">
      <h3>{status === 404 ? "Not found" : status === 403 ? "Not allowed" : "Could not load this data"}</h3>
      <p className="muted">{msg}</p>
      {retry && status !== 404 && status !== 403 ? <button onClick={retry}><IconRefresh />Try again</button> : null}
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

export function Load<T>({ r, empty, children, rows }: { r: { data: T | null; error: any; loading: boolean; reload: () => void };
  empty?: (d: T) => ReactNode | null; children: (d: T) => ReactNode; rows?: number }) {
  if (r.loading && !r.data) return <Skeleton rows={rows} />;
  if (r.error && !r.data) return <ErrorState error={r.error} retry={r.reload} />;
  if (!r.data) return null;
  const e = empty?.(r.data);
  if (e) return <>{e}</>;
  return <>{children(r.data)}</>;
}

export function FormError({ msg }: { msg: string | null }) {
  return msg ? <div className="notice notice-bad" role="alert">{msg}</div> : null;
}

export function Json({ value }: { value: unknown }) {
  return <pre className="pre">{JSON.stringify(value, null, 2)}</pre>;
}

export function Time({ iso, rel = false }: { iso?: string | null; rel?: boolean }) {
  if (!iso) return <span className="dim">Not recorded</span>;
  const d = new Date(iso);
  const local = d.toLocaleString("en-IN", { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  let text = local;
  if (rel) {
    const s = Math.round((Date.now() - d.getTime()) / 1000);
    text = s < 60 ? `${Math.max(0, s)}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : local;
  }
  return <time dateTime={iso} title={`${local} (local) / ${iso} (UTC)`}>{text}</time>;
}

export const DateOnly = ({ iso }: { iso?: string | null }) =>
  iso ? <time dateTime={iso}>{new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</time> : <span className="dim">Not yet</span>;

// ---------------------------------------------------------------- status tokens (text + glyph, never colour alone)
const DEC: Record<string, [string, string, ReactNode]> = {
  ALLOW: ["tok-allow", "ALLOW", <IconCheck size={12} />],
  BLOCK: ["tok-block", "BLOCK", <IconBlock size={12} />],
  REQUIRE_APPROVAL: ["tok-approval", "REQUIRE_APPROVAL", <IconHold size={12} />],
};
export function DecisionTok({ d }: { d?: string | null }) {
  if (!d) return <span className="tok tok-neutral"><IconDash size={12} />No decision</span>;
  const [cls, label, icon] = DEC[d] ?? ["tok-neutral", d, <IconQuestion size={12} />];
  return <span className={`tok ${cls}`}>{icon}{label}</span>;
}

const OUT: Record<string, [string, ReactNode]> = {
  PASS: ["tok-pass", <IconCheck size={12} />],
  FAIL: ["tok-fail", <IconBlock size={12} />],
  ERROR: ["tok-error", <IconAlert size={12} />],
  INCONCLUSIVE: ["tok-inconclusive", <IconQuestion size={12} />],
  SKIPPED: ["tok-skipped", <IconDash size={12} />],
};
export function OutcomeTok({ o }: { o: string }) {
  const [cls, icon] = OUT[o] ?? OUT.SKIPPED;
  return <span className={`tok ${cls}`}>{icon}{o}</span>;
}

export function StateTok({ s }: { s?: string | null }) {
  if (!s) return null;
  return <span className={`state-tok state-${s}`}>{s}</span>;
}

export function ExecTok({ s }: { s?: string | null }) {
  const m: Record<string, [string, string]> = {
    executed: ["tok-ok", "Executed"],
    failed: ["tok-error", "Executed, failed"],
    not_executed: ["tok-block", "Not executed"],
    pending: ["tok-neutral", "Pending"],
  };
  const [cls, label] = m[s ?? ""] ?? ["tok-neutral", s ?? "Unknown"];
  return <span className={`tok ${cls}`}>{label}</span>;
}

export function LiveDot({ state }: { state: string }) {
  const label = { open: "Live updates connected", connecting: "Connecting to live updates", closed: "Live updates disconnected, retrying" }[state] ?? state;
  return (
    <span className="row small muted" role="status">
      <span className="live-dot" data-state={state} aria-hidden="true" />
      {label}
    </span>
  );
}

export function Short({ id }: { id?: string | null }) {
  if (!id) return <span className="dim">None</span>;
  return <code title={id}>{id.length > 18 ? id.slice(0, 4) + "…" + id.slice(-8) : id}</code>;
}
