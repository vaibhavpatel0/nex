import { FormEvent, useState } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { IconShield } from "../components/Icons";
import { AppLayout } from "../layouts/AppLayout";
import { AgentDetailPage, AgentsPage } from "../features/agents/AgentsPage";
import { AnalyticsPage } from "../features/analytics/AnalyticsPage";
import { ConnectionDetailPage, ConnectionsPage } from "../features/connections/ConnectionsPage";
import { OverviewPage } from "../features/dashboard/OverviewPage";
import { IntegrationsPage } from "../features/settings/IntegrationsPage";
import { InvestigationDetailPage, InvestigationsPage } from "../features/investigations/InvestigationsPage";
import { LegalPrivacy, LegalTerms } from "../pages/Legal";
import { LiveEventsPage } from "../features/live-monitoring/LiveEventsPage";
import { PoliciesPage, PolicyDetailPage } from "../features/policies/PoliciesPage";
import { ReportsPage, ReportViewPage } from "../features/reports/ReportsPage";
import { RuntimePage } from "../features/runtime-protection/RuntimePage";
import { RunDetailPage, TestsPage } from "../features/security-tests/TestsPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { TraceDetailPage, TracesPage } from "../features/investigations/TracesPage";
import { AppProvider, useApp } from "./context";

function Login() {
  const { login } = useApp();
  const [u, setU] = useState("admin");
  const [p, setP] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await login(u, p);
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <form onSubmit={submit} aria-labelledby="login-title">
        <div className="row"><IconShield size={22} style={{ color: "var(--teal)" }} /><h1 id="login-title">Sign in to AEGIS</h1></div>
        <p className="muted small">Use the admin account created by <code>make init</code>. The generated password was printed once in the terminal.</p>
        <label className="field"><span>Username</span><input autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} required /></label>
        <label className="field"><span>Password</span><input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} required /></label>
        {err ? <div className="notice notice-bad" role="alert">{err}</div> : null}
        <button className="btn-primary" type="submit" disabled={busy}>{busy ? "Signing in" : "Sign in"}</button>
        <p className="small muted">By signing in you accept the <Link to="/terms">Terms of Service</Link>. See the <Link to="/privacy">Privacy Policy</Link> for what this app stores.</p>
      </form>
    </div>
  );
}

function Gate() {
  const { me, ready } = useApp();
  if (!ready) return <div className="login" aria-busy="true" />;
  return (
    <Routes>
      <Route path="/terms" element={me ? <AppLayout /> : <div className="page legal"><LegalTerms /></div>}>
        {me ? <Route index element={<LegalTerms />} /> : null}
      </Route>
      <Route path="/privacy" element={me ? <AppLayout /> : <div className="page legal"><LegalPrivacy /></div>}>
        {me ? <Route index element={<LegalPrivacy />} /> : null}
      </Route>
      {!me ? <Route path="*" element={<Login />} /> : (
        <Route element={<AppLayout />}>
          <Route index element={<OverviewPage />} />
          <Route path="live" element={<LiveEventsPage />} />
          <Route path="runtime" element={<RuntimePage />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="agents/:id" element={<AgentDetailPage />} />
          <Route path="connections" element={<ConnectionsPage />} />
          <Route path="connections/:id" element={<ConnectionDetailPage />} />
          <Route path="tests" element={<TestsPage />} />
          <Route path="runs/:id" element={<RunDetailPage />} />
          <Route path="investigations" element={<InvestigationsPage />} />
          <Route path="investigations/:id" element={<InvestigationDetailPage />} />
          <Route path="traces" element={<TracesPage />} />
          <Route path="traces/:id" element={<TraceDetailPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="reports/:id" element={<ReportViewPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="policies" element={<PoliciesPage />} />
          <Route path="policies/:id" element={<PolicyDetailPage />} />
          <Route path="integrations" element={<IntegrationsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="state"><h3>Page not found</h3><Link to="/">Go to the overview</Link></div>} />
        </Route>
      )}
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppProvider>
        <Gate />
      </AppProvider>
    </BrowserRouter>
  );
}
