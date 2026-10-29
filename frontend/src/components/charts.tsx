// Two small hand-built SVG charts. Palette validated with the dataviz validator:
// light #00897a / #4c6fd8, dark #1f9e8f / #6e8cf0 (CVD dE >= 14.8, normal-vision dE >= 18.3).
import { useState } from "react";

export interface Series {
  name: string;
  color: string; // CSS var
  values: (number | null)[];
}

/** Line chart for 0..1 rates across runs. Legend + end labels + nearest-point tooltip + table view. */
export function RateLineChart({ labels, series, height = 200 }: { labels: string[]; series: Series[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const W = 640, H = height, L = 40, R = 90, T = 12, B = 26;
  const n = labels.length;
  const x = (i: number) => (n <= 1 ? L + (W - L - R) / 2 : L + (i * (W - L - R)) / (n - 1));
  const y = (v: number) => T + (1 - v) * (H - T - B);
  if (n === 0) return <p className="muted">Insufficient data: no completed runs yet.</p>;
  return (
    <div className="chart-wrap">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <div className="legend" aria-label="Legend">
          {series.map((s) => (
            <span key={s.name}><i className="swatch" style={{ background: s.color }} />{s.name}</span>
          ))}
        </div>
        <button className="btn-sm" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button>
      </div>
      {table ? (
        <table className="t">
          <thead><tr><th>Run</th>{series.map((s) => <th key={s.name} className="num">{s.name}</th>)}</tr></thead>
          <tbody>{labels.map((l, i) => (
            <tr key={i}><td>{l}</td>{series.map((s) => <td key={s.name} className="num">{s.values[i] == null ? "Insufficient data" : `${(s.values[i]! * 100).toFixed(1)}%`}</td>)}</tr>
          ))}</tbody>
        </table>
      ) : (
        <svg className="chart" viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
          aria-label={`Line chart of ${series.map((s) => s.name).join(" and ")} across ${n} runs`}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const px = ((e.clientX - box.left) / box.width) * W;
            let best = 0;
            for (let i = 1; i < n; i++) if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
            setHover(best);
          }}>
          {[0, 0.25, 0.5, 0.75, 1].map((g) => (
            <g key={g}>
              <line className="grid" x1={L} x2={W - R} y1={y(g)} y2={y(g)} strokeWidth={1} />
              <text x={L - 6} y={y(g) + 4} textAnchor="end">{g * 100}%</text>
            </g>
          ))}
          {hover !== null ? <line className="axis" x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} strokeWidth={1} /> : null}
          {(() => {
            // End labels: push apart vertically so they never overlap (min 14px).
            const ends = series.map((s, si) => {
              const li = s.values.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0).pop();
              return li === undefined ? null : { si, y: y(s.values[li]!), x: x(li), v: s.values[li]! };
            }).filter(Boolean) as { si: number; y: number; x: number; v: number }[];
            ends.sort((a, b) => a.y - b.y);
            for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
            return ends.map((e) => (
              <text key={`lbl${e.si}`} x={e.x + 9} y={e.y + 4}>{series[e.si].name.split(" ")[0]} {(e.v * 100).toFixed(0)}%</text>
            ));
          })()}
          {series.map((s) => {
            const pts = s.values.map((v, i) => (v == null ? null : [x(i), y(v)] as const));
            const d = pts.reduce((acc, p, i) => (p ? acc + `${acc && pts[i - 1] ? "L" : "M"}${p[0]},${p[1]}` : acc), "");
            const lastIdx = s.values.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0).pop();
            return (
              <g key={s.name}>
                <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                {pts.map((p, i) => (p && (i === lastIdx || i === hover) ? (
                  <circle key={i} cx={p[0]} cy={p[1]} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
                ) : null))}
              </g>
            );
          })}
          {labels.map((l, i) => (n <= 8 || i % Math.ceil(n / 8) === 0 ? (
            <text key={i} x={x(i)} y={H - 8} textAnchor="middle">{l}</text>
          ) : null))}
        </svg>
      )}
      {!table && hover !== null ? (
        <div className="chart-tip" style={{ left: `${(x(hover) / W) * 100}%`, top: 24, transform: "translateX(-50%)" }}>
          <div>{labels[hover]}</div>
          {series.map((s) => (
            <div key={s.name}><i className="swatch" style={{ background: s.color }} /> {s.name}: {s.values[hover] == null ? "Insufficient data" : `${(s.values[hover]! * 100).toFixed(1)}%`}</div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const OUT = [
  { k: "PASS", color: "var(--green)", label: "Pass" },
  { k: "FAIL", color: "var(--red)", label: "Fail" },
  { k: "ERROR", color: "var(--red)", label: "Error (hatched)", hatch: true },
  { k: "INCONCLUSIVE", color: "var(--amber)", label: "Inconclusive" },
  { k: "SKIPPED", color: "var(--line-strong)", label: "Skipped" },
];

/** Horizontal stacked bars of outcome counts per category (status colours + labels + hatch for ERROR). */
export function OutcomeBars({ rows }: { rows: { label: string; counts: Record<string, number> }[] }) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const max = Math.max(1, ...rows.map((r) => OUT.reduce((a, o) => a + (r.counts[o.k] || 0), 0)));
  const W = 640, L = 210, BAR = 14, GAP = 10, R = 40;
  const H = rows.length * (BAR + GAP) + 6;
  return (
    <div className="chart-wrap">
      <div className="legend" style={{ marginBottom: 6 }} aria-label="Legend">
        {OUT.map((o) => (
          <span key={o.k}>
            <svg width="10" height="10" aria-hidden="true"><rect width="10" height="10" rx="2" fill={o.hatch ? "url(#hatch)" : o.color} /></svg>
            {o.label}
          </span>
        ))}
      </div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Outcome counts by scenario category"
        onMouseLeave={() => setTip(null)}>
        <defs>
          <pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="4" height="4" fill="var(--red-soft)" /><line x1="0" y1="0" x2="0" y2="4" stroke="var(--red)" strokeWidth="2" />
          </pattern>
        </defs>
        {rows.map((r, i) => {
          let cx = L;
          const yy = i * (BAR + GAP) + 3;
          const total = OUT.reduce((a, o) => a + (r.counts[o.k] || 0), 0);
          return (
            <g key={r.label}>
              <text x={L - 8} y={yy + BAR - 3} textAnchor="end">{r.label}</text>
              {OUT.map((o) => {
                const c = r.counts[o.k] || 0;
                if (!c) return null;
                const w = Math.max(2, (c / max) * (W - L - R) - 2);
                const el = (
                  <rect key={o.k} x={cx} y={yy} width={w} height={BAR} rx={2} fill={o.hatch ? "url(#hatch)" : o.color}
                    onMouseMove={(e) => {
                      const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                      setTip({ x: e.clientX - box.left, y: e.clientY - box.top, text: `${r.label}: ${c} ${o.label.split(" ")[0].toLowerCase()}` });
                    }} />
                );
                cx += w + 2;
                return el;
              })}
              <text x={cx + 6} y={yy + BAR - 3}>{total}</text>
            </g>
          );
        })}
      </svg>
      {tip ? <div className="chart-tip" style={{ left: tip.x + 12, top: tip.y - 10 }}>{tip.text}</div> : null}
    </div>
  );
}
