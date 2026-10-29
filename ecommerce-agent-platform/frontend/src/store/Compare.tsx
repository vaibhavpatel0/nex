import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Load, PageHead, Panel } from "../lib/ui";
import { AddToCart } from "./Catalog";
import { attrText, label, Product, Stock } from "./format";

export function Compare() {
  const [sp] = useSearchParams();
  const ids = sp.get("ids") ?? "";
  const r = useApi<{ items: Product[] }>(`/catalog/compare?ids=${encodeURIComponent(ids)}`);
  return (
    <>
      <PageHead title="Compare products" intro={<Link to="/">Back to catalogue</Link>} />
      <Panel flush title="Side by side">
        <Load r={r}>
          {(d) => {
            const keys = Array.from(new Set(d.items.flatMap((p) => Object.keys(p.attributes))));
            return (
              <div className="table-wrap">
                <table className="t">
                  <thead>
                    <tr><th>Attribute</th>{d.items.map((p) => <th key={p.product_id}><Link to={`/products/${p.product_id}`}>{p.name}</Link></th>)}</tr>
                  </thead>
                  <tbody>
                    <tr><td>Price</td>{d.items.map((p) => <td key={p.product_id} className="money">{p.price}</td>)}</tr>
                    <tr><td>Stock</td>{d.items.map((p) => <td key={p.product_id}><Stock available={p.available} /></td>)}</tr>
                    {keys.map((k) => (
                      <tr key={k}><td>{label(k)}</td>{d.items.map((p) => <td key={p.product_id}>{k in p.attributes ? attrText(k, p.attributes[k]) : <span className="dim">Not listed</span>}</td>)}</tr>
                    ))}
                    <tr><td>Returns</td>{d.items.map((p) => <td key={p.product_id}>{p.final_sale ? "Final sale" : "30 days"}</td>)}</tr>
                    <tr><td></td>{d.items.map((p) => <td key={p.product_id}><AddToCart p={p} /></td>)}</tr>
                  </tbody>
                </table>
              </div>
            );
          }}
        </Load>
      </Panel>
    </>
  );
}
