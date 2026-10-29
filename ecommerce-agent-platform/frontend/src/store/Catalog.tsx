import { FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, qs } from "../lib/api";
import { useApi } from "../lib/hooks";
import { IconBox } from "../lib/icons";
import { useSession } from "../lib/session";
import { Empty, Load, PageHead } from "../lib/ui";
import { attrText, label, Product, Stock, titleCase } from "./format";

export function AddToCart({ p, compact }: { p: Product; compact?: boolean }) {
  const { me, refreshCart } = useSession();
  const nav = useNavigate();
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [err, setErr] = useState<string | null>(null);
  if (p.available !== undefined && p.available <= 0) return <button disabled>Out of stock</button>;
  const add = async () => {
    if (!me) return nav(`/login?next=${encodeURIComponent(`/products/${p.product_id}`)}`);
    setState("busy");
    setErr(null);
    try {
      await api.post("/cart/items", { product_id: p.product_id, quantity: 1 });
      setState("done");
      refreshCart();
    } catch (e: any) {
      setErr(e.message);
      setState("idle");
    }
  };
  if (me?.role === "staff") return null;
  return (
    <div style={{ display: "grid", gap: 4, justifyItems: compact ? "end" : "start" }}>
      <button className="btn-primary" onClick={add} disabled={state === "busy"}>
        {state === "busy" ? "Adding" : state === "done" ? "Added, add another" : "Add to cart"}
      </button>
      {state === "done" ? <Link to="/cart" className="small">View cart</Link> : null}
      {err ? <span className="small" role="alert" style={{ color: "var(--red)" }}>{err}</span> : null}
    </div>
  );
}

export function Catalog() {
  const [sp, setSp] = useSearchParams();
  const [q, setQ] = useState(sp.get("q") ?? "");
  const [picked, setPicked] = useState<string[]>([]);
  const nav = useNavigate();
  const params = {
    q: sp.get("q"),
    category: sp.get("category"),
    min_price: sp.get("min_price"),
    max_price: sp.get("max_price"),
    in_stock: sp.get("in_stock") === "true" ? "true" : undefined,
    sort: sp.get("sort") ?? undefined,
  };
  const cats = useApi<{ items: string[] }>("/catalog/categories");
  const products = useApi<{ items: Product[] }>("/catalog/products" + qs(params));
  const set = (k: string, v: string | null) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    set("q", q.trim() || null);
  };
  const toggle = (id: string) => setPicked((x) => (x.includes(id) ? x.filter((y) => y !== id) : x.length >= 4 ? x : [...x, id]));
  const invalidPrice = !!params.min_price && !!params.max_price && Number(params.min_price) > Number(params.max_price);

  return (
    <>
      <PageHead title="Catalogue" intro="Search the synthetic catalogue, compare products and add them to your cart. Prices are in Indian rupees." />
      <div className="catalog">
        <aside className="filters" aria-label="Filters">
          <form onSubmit={submit} className="panel panel-body" style={{ display: "grid", gap: 10 }} role="search">
            <label className="field">
              <span>Search</span>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. wireless headphones" maxLength={100} />
            </label>
            <button type="submit">Search</button>
          </form>
          <div className="panel panel-body" style={{ display: "grid", gap: 10 }}>
            <label className="field">
              <span>Category</span>
              <select value={params.category ?? ""} onChange={(e) => set("category", e.target.value || null)}>
                <option value="">All categories</option>
                {(cats.data?.items ?? []).map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
              </select>
            </label>
            <div className="field-row" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <label className="field">
                <span>Min ₹</span>
                <input type="number" min={0} inputMode="numeric" value={params.min_price ?? ""} onChange={(e) => set("min_price", e.target.value || null)} />
              </label>
              <label className="field">
                <span>Max ₹</span>
                <input type="number" min={0} inputMode="numeric" value={params.max_price ?? ""} onChange={(e) => set("max_price", e.target.value || null)} />
              </label>
            </div>
            {invalidPrice ? <span className="small" role="alert" style={{ color: "var(--red)" }}>The minimum is above the maximum.</span> : null}
            <label className="check">
              <input type="checkbox" checked={params.in_stock === "true"} onChange={(e) => set("in_stock", e.target.checked ? "true" : null)} />
              In stock only
            </label>
            <label className="field">
              <span>Sort</span>
              <select value={params.sort ?? "relevance"} onChange={(e) => set("sort", e.target.value === "relevance" ? null : e.target.value)}>
                <option value="relevance">Relevance</option>
                <option value="price_asc">Price, low to high</option>
                <option value="price_desc">Price, high to low</option>
              </select>
            </label>
            {[...sp.keys()].length ? <button className="btn-ghost btn-sm" onClick={() => { setQ(""); setSp(new URLSearchParams(), { replace: true }); }}>Clear filters</button> : null}
          </div>
          {picked.length ? (
            <div className="panel panel-body" style={{ display: "grid", gap: 8 }}>
              <span className="small">{picked.length} selected to compare (two to four)</span>
              <button disabled={picked.length < 2} onClick={() => nav(`/compare?ids=${picked.join(",")}`)}>Compare selected</button>
            </div>
          ) : null}
        </aside>
        <section aria-label="Products" aria-live="polite">
          <Load r={products} rows={6} empty={(d) => (d.items.length ? null : <div className="panel"><Empty title="No products match">Try fewer words or remove a filter.</Empty></div>)}>
            {(d) => (
              <>
                <div className="small muted" style={{ marginBottom: 6 }}>{d.items.length} product{d.items.length === 1 ? "" : "s"}</div>
                <ul className="product-list">
                  {d.items.map((p) => (
                    <li key={p.product_id} className="product-row">
                      <div className="thumb" aria-hidden="true"><IconBox size={26} /></div>
                      <div>
                        <h3><Link to={`/products/${p.product_id}`}>{p.name}</Link></h3>
                        <div className="small muted">{p.brand} · {titleCase(p.category)} · <code>{p.product_id}</code></div>
                        <p style={{ margin: "4px 0" }}>{p.summary}</p>
                        <div className="attrs">
                          {Object.entries(p.attributes).slice(0, 4).map(([k, v]) => <span key={k} className="tag">{label(k)}: {attrText(k, v)}</span>)}
                          {p.final_sale ? <span className="tag">Final sale, no returns</span> : null}
                        </div>
                        <label className="check small" style={{ marginTop: 6 }}>
                          <input type="checkbox" checked={picked.includes(p.product_id)} onChange={() => toggle(p.product_id)} />
                          Compare
                        </label>
                      </div>
                      <div className="actions">
                        <div className="price money">{p.price}</div>
                        <Stock available={p.available} />
                        <AddToCart p={p} compact />
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Load>
        </section>
      </div>
    </>
  );
}
