import { FormEvent, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { Empty, FormError, Load, PageHead, Panel, Time } from "../lib/ui";

interface Req { request_id: string; subject: string; message: string; order_id: string | null; status: string; source: string; created_at: string }

export function SupportPage() {
  const [sp] = useSearchParams();
  const list = useApi<{ items: Req[] }>("/support");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [order, setOrder] = useState(sp.get("order") ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ request_id: string }>("/support", { subject: subject.trim(), message: message.trim(), order_id: order.trim() || null });
      setSent(r.request_id);
      setSubject("");
      setMessage("");
      list.reload();
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead title="Customer support" intro="Requests are stored for store staff. This test store has no live support team." />
      <div className="grid-main-side">
        <Panel flush title="Your requests">
          <Load r={list} empty={(d) => (d.items.length ? null : <Empty title="No support requests yet" />)}>
            {(d) => (
              <table className="t">
                <thead><tr><th>Request</th><th>Subject</th><th>Order</th><th>Status</th><th>Created by</th><th>When</th></tr></thead>
                <tbody>
                  {d.items.map((r) => (
                    <tr key={r.request_id}>
                      <td><code>{r.request_id}</code></td>
                      <td className="wrap">{r.subject}<div className="small muted">{r.message}</div></td>
                      <td>{r.order_id ? <code>{r.order_id}</code> : <span className="dim">None</span>}</td>
                      <td><span className="tag">{r.status}</span></td>
                      <td>{r.source === "agent" ? "Assistant" : "You"}</td>
                      <td><Time iso={r.created_at} rel /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Load>
        </Panel>
        <Panel title="New request">
          <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
            <FormError msg={err} />
            {sent ? <div className="notice" role="status">Request <code>{sent}</code> was created.</div> : null}
            <label className="field"><span>Subject</span><input required minLength={3} maxLength={160} value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
            <label className="field"><span>Order (optional)</span><input pattern="ORD-\d{4}" placeholder="ORD-0000" value={order} onChange={(e) => setOrder(e.target.value)} /></label>
            <label className="field"><span>Message</span><textarea required minLength={3} maxLength={4000} rows={5} value={message} onChange={(e) => setMessage(e.target.value)} /></label>
            <button className="btn-primary" disabled={busy}>Send request</button>
          </form>
        </Panel>
      </div>
    </>
  );
}
