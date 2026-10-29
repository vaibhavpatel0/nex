import { FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useSession } from "../lib/session";
import { FormError } from "../lib/ui";
import { IconBag } from "../lib/icons";

export function Login() {
  const { login } = useSession();
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const me = await login(email.trim(), password);
      const next = sp.get("next");
      nav(next && next.startsWith("/") && !next.startsWith("//") ? next : me.role === "staff" ? "/workbench" : "/");
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="login">
      <form onSubmit={submit} aria-labelledby="login-title">
        <div className="row"><IconBag size={22} style={{ color: "var(--teal)" }} /><h1 id="login-title">Sign in to Halden Bazaar</h1></div>
        <p className="small muted">A local test store. Customer accounts open the storefront; the staff account opens the Agent Workbench. The first-run password is in <code>data/first-run-credentials.txt</code>, or the value of <code>SHOP_DEMO_PASSWORD</code>.</p>
        <FormError msg={err} />
        <label className="field"><span>Email</span><input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="field"><span>Password</span><input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <button className="btn-primary" disabled={busy}>{busy ? "Signing in" : "Sign in"}</button>
        <details className="small">
          <summary>Test accounts</summary>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            <li><code>asha@shopper.test</code> customer CUST-2001</li>
            <li><code>rohan@shopper.test</code> customer CUST-2002</li>
            <li><code>meera@shopper.test</code> customer CUST-2003</li>
            <li><code>staff@halden.test</code> store staff (Agent Workbench)</li>
          </ul>
        </details>
        <div className="row small"><Link to="/">Back to the catalogue</Link><Link to="/terms">Terms</Link><Link to="/privacy">Privacy</Link></div>
      </form>
    </main>
  );
}
