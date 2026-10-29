import { FormEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { DateOnly, Empty, FormError, Load, PageHead, Panel } from "../lib/ui";
import { titleCase } from "./format";

interface OrderRow { order_id: string; status: string; placed_at: string; shipped_at: string | null; delivered_at: string | null; total: string }
interface Elig { eligible: boolean; reason: string }
interface Order extends OrderRow {
  shipping_address: string;
  items: { product_id: string; name: string; quantity: number; unit_price: string }[];
  cancellation: Elig;
  return: Elig;
}

const STATUS_CLS: Record<string, string> = { cancelled: "tok-neutral", delivered: "tok-neutral", shipped: "tok-info", processing: "tok-info" };
export const OrderStatus = ({ s }: { s: string }) => <span className={`tok ${STATUS_CLS[s] ?? "tok-neutral"}`}>{titleCase(s)}</span>;

export function OrdersPage() {
  const r = useApi<{ items: OrderRow[] }>("/orders");
  return (
    <>
      <PageHead title="Your orders" />
      <Panel flush>
        <Load r={r} empty={(d) => (d.items.length ? null : <Empty title="No orders yet"><Link to="/">Browse the catalogue</Link></Empty>)}>
          {(d) => (
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>Order</th><th>Status</th><th>Placed</th><th>Shipped</th><th>Delivered</th><th className="num">Total</th></tr></thead>
                <tbody>
                  {d.items.map((o) => (
                    <tr key={o.order_id}>
                      <td><Link to={`/orders/${o.order_id}`}><code>{o.order_id}</code></Link></td>
                      <td><OrderStatus s={o.status} /></td>
                      <td><DateOnly iso={o.placed_at} /></td>
                      <td><DateOnly iso={o.shipped_at} /></td>
                      <td><DateOnly iso={o.delivered_at} /></td>
                      <td className="num money">{o.total}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Load>
      </Panel>
    </>
  );
}

function RequestForm({ order, kind, elig, onDone }: { order: string; kind: "cancellation" | "return"; elig: Elig; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<{ request_id: string; status: string; note?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      setDone(await api.post(`/orders/${order}/${kind}-request`, { reason: reason.trim() }));
      onDone();
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };
  const title = kind === "cancellation" ? "Cancellation" : "Return";
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <h3>{title}</h3>
      <div className={`notice ${elig.eligible ? "" : "notice-warn"}`}>{elig.eligible ? "Eligible. " : "Not eligible. "}{elig.reason}.</div>
      {done ? (
        <p>Request <code>{done.request_id}</code> is {done.status.replace(/_/g, " ")}. {done.note}</p>
      ) : elig.eligible ? (
        <form onSubmit={submit} style={{ display: "grid", gap: 8 }}>
          <FormError msg={err} />
          <label className="field"><span>Reason</span><input required minLength={3} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
          <button disabled={busy || reason.trim().length < 3}>Request {kind}</button>
          <span className="small muted">Staff review every request before anything changes.</span>
        </form>
      ) : null}
    </div>
  );
}

export function OrderDetail() {
  const { id } = useParams();
  const r = useApi<Order>(`/orders/${encodeURIComponent(id ?? "")}`);
  return (
    <Load r={r} rows={6}>
      {(o) => (
        <>
          <div className="small"><Link to="/orders">Orders</Link> / {o.order_id}</div>
          <PageHead title={`Order ${o.order_id}`} actions={<OrderStatus s={o.status} />} />
          <div className="grid-main-side">
            <Panel flush title="Items">
              <table className="t">
                <thead><tr><th>Product</th><th className="num">Qty</th><th className="num">Unit price</th></tr></thead>
                <tbody>
                  {o.items.map((i) => (
                    <tr key={i.product_id}><td><Link to={`/products/${i.product_id}`}>{i.name}</Link></td><td className="num">{i.quantity}</td><td className="num money">{i.unit_price}</td></tr>
                  ))}
                  <tr className="total-row"><td>Total</td><td /><td className="num money">{o.total}</td></tr>
                </tbody>
              </table>
            </Panel>
            <div className="stack">
              <Panel title="Delivery">
                <dl className="kv">
                  <dt>Placed</dt><dd><DateOnly iso={o.placed_at} /></dd>
                  <dt>Shipped</dt><dd><DateOnly iso={o.shipped_at} /></dd>
                  <dt>Delivered</dt><dd><DateOnly iso={o.delivered_at} /></dd>
                  <dt>Address</dt><dd>{o.shipping_address}</dd>
                </dl>
              </Panel>
              <Panel title="Changes">
                <div className="stack" style={{ gap: 14 }}>
                  <RequestForm order={o.order_id} kind="cancellation" elig={o.cancellation} onDone={() => undefined} />
                  <RequestForm order={o.order_id} kind="return" elig={o.return} onDone={() => undefined} />
                  <Link to={`/support?order=${o.order_id}`} className="small">Contact support about this order</Link>
                </div>
              </Panel>
            </div>
          </div>
        </>
      )}
    </Load>
  );
}
