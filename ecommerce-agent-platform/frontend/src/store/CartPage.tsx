import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import { Empty, FormError, Load, PageHead, Panel } from "../lib/ui";

interface CartItem {
  cart_item_id: string;
  product_id: string;
  name: string;
  quantity: number;
  unit_price: string;
  line_total: string;
  price_changed: boolean;
}
interface Cart {
  cart_id: string;
  items: CartItem[];
  subtotal: string;
  shipping: string;
  total: string;
  item_count: number;
}

export function CartPage() {
  const cart = useApi<Cart>("/cart");
  const profile = useApi<{ default_address: string }>("/account/profile");
  const { refreshCart } = useSession();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [address, setAddress] = useState("");
  const [placed, setPlaced] = useState<{ order_id: string; total: string; payment: string } | null>(null);
  useEffect(() => {
    if (profile.data && !address) setAddress(profile.data.default_address);
  }, [profile.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (id: string, fn: () => Promise<unknown>) => {
    setBusy(id);
    setErr(null);
    try {
      await fn();
      cart.reload();
      refreshCart();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };
  const checkout = (e: FormEvent) => {
    e.preventDefault();
    act("checkout", async () => setPlaced(await api.post("/checkout", { shipping_address: address.trim(), payment_method: "local_test" })));
  };

  if (placed)
    return (
      <>
        <PageHead title="Order placed" />
        <Panel>
          <p>Order <code>{placed.order_id}</code> was created for {placed.total}.</p>
          <p className="muted">{placed.payment}.</p>
          <div className="row"><Link className="btn btn-primary" to={`/orders/${placed.order_id}`}>View order</Link><Link className="btn" to="/">Keep shopping</Link></div>
        </Panel>
      </>
    );

  return (
    <>
      <PageHead title="Your cart" />
      <FormError msg={err} />
      <Load r={cart} empty={(c) => (c.items.length ? null : <Panel><Empty title="Your cart is empty"><Link to="/">Browse the catalogue</Link> or ask the assistant for a recommendation.</Empty></Panel>)}>
        {(c) => (
          <div className="grid-main-side">
            <Panel flush title={`${c.item_count} item${c.item_count === 1 ? "" : "s"}`}>
              <div className="table-wrap">
                <table className="t">
                  <thead><tr><th>Product</th><th className="num">Unit price</th><th>Quantity</th><th className="num">Line total</th><th><span className="sr-only">Actions</span></th></tr></thead>
                  <tbody>
                    {c.items.map((i) => (
                      <tr key={i.cart_item_id}>
                        <td>
                          <Link to={`/products/${i.product_id}`}>{i.name}</Link>
                          <div className="small dim"><code>{i.cart_item_id}</code></div>
                          {i.price_changed ? <div className="small" style={{ color: "var(--amber)" }}>The catalogue price changed since you added this.</div> : null}
                        </td>
                        <td className="num money">{i.unit_price}</td>
                        <td>
                          <select aria-label={`Quantity for ${i.name}`} value={i.quantity} disabled={busy === i.cart_item_id}
                            onChange={(e) => act(i.cart_item_id, () => api.patch(`/cart/items/${i.cart_item_id}`, { quantity: Number(e.target.value) }))}>
                            {Array.from({ length: 10 }, (_, n) => n + 1).map((n) => <option key={n} value={n}>{n}</option>)}
                          </select>
                        </td>
                        <td className="num money">{i.line_total}</td>
                        <td><button className="btn-sm btn-danger" disabled={busy === i.cart_item_id} onClick={() => act(i.cart_item_id, () => api.del(`/cart/items/${i.cart_item_id}`))}>Remove</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <Panel title="Checkout">
              <form onSubmit={checkout} style={{ display: "grid", gap: 10 }}>
                <dl className="kv">
                  <dt>Subtotal</dt><dd className="money">{c.subtotal}</dd>
                  <dt>Shipping</dt><dd className="money">{c.shipping}</dd>
                  <dt><b>Total</b></dt><dd className="money"><b>{c.total}</b></dd>
                </dl>
                <label className="field">
                  <span>Shipping address</span>
                  <textarea rows={3} required minLength={10} maxLength={300} value={address} onChange={(e) => setAddress(e.target.value)} />
                </label>
                <fieldset>
                  <legend>Payment</legend>
                  <label className="check"><input type="radio" checked readOnly name="pay" /> Local test payment fixture. No card is charged and no money moves.</label>
                </fieldset>
                <button className="btn-primary" disabled={busy === "checkout" || address.trim().length < 10}>{busy === "checkout" ? "Placing order" : "Place test order"}</button>
                <p className="small muted">Stock and prices are checked again when you place the order.</p>
              </form>
            </Panel>
          </div>
        )}
      </Load>
    </>
  );
}
