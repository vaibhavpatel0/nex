import { Link, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { IconBox } from "../lib/icons";
import { Load, PageHead, Panel } from "../lib/ui";
import { AddToCart } from "./Catalog";
import { attrText, label, Product, Stock, titleCase } from "./format";

export function ProductPage() {
  const { id } = useParams();
  const r = useApi<Product>(`/catalog/products/${encodeURIComponent(id ?? "")}`);
  return (
    <Load r={r} rows={6}>
      {(p) => (
        <>
          <div className="small"><Link to="/">Catalogue</Link> / {titleCase(p.category)}</div>
          <PageHead title={p.name} intro={`${p.brand} · ${p.product_id}`} />
          <div className="grid-main-side">
            <Panel title="Description">
              <div className="thumb thumb-lg" aria-hidden="true"><IconBox size={48} /></div>
              {/* Shown as plain text. Some synthetic descriptions contain deliberate prompt-injection text used by security tests. */}
              <p style={{ marginTop: 12, whiteSpace: "pre-wrap" }}>{p.description}</p>
              <h3 style={{ marginTop: 12 }}>Specifications</h3>
              <table className="t" style={{ marginTop: 6 }}>
                <tbody>
                  {Object.entries(p.attributes).map(([k, v]) => (
                    <tr key={k}><th scope="row" style={{ position: "static" }}>{label(k)}</th><td>{attrText(k, v)}</td></tr>
                  ))}
                </tbody>
              </table>
            </Panel>
            <Panel title="Buy">
              <div style={{ display: "grid", gap: 10 }}>
                <div style={{ fontSize: 22, fontWeight: 500 }} className="money">{p.price}</div>
                <Stock available={p.available} />
                {p.final_sale ? <div className="notice notice-warn">Final sale. This item cannot be returned.</div> : <div className="small muted">Returnable within 30 days of delivery if unused.</div>}
                <AddToCart p={p} />
                <Link to="/policies/returns" className="small">Returns policy</Link>
              </div>
            </Panel>
          </div>
        </>
      )}
    </Load>
  );
}
