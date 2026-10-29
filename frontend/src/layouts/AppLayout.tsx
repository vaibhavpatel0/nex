import { ReactNode, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useApp, useLiveEvents } from "../app/context";
import {
  IconAgent, IconBars, IconBranch, IconDoc, IconFlask, IconGate, IconGauge, IconGear, IconMagnifier, IconMenu,
  IconPlug, IconPulse, IconRules, IconShield,
} from "../components/Icons";
import { api } from "../services/api";

const GROUPS: { title: string; items: { to: string; label: string; icon: ReactNode; end?: boolean }[] }[] = [
  {
    title: "Operate",
    items: [
      { to: "/", label: "Overview", icon: <IconGauge />, end: true },
      { to: "/live", label: "Live events", icon: <IconPulse /> },
      { to: "/runtime", label: "Runtime protection", icon: <IconGate /> },
    ],
  },
  {
    title: "Test and investigate",
    items: [
      { to: "/agents", label: "Agents", icon: <IconAgent /> },
      { to: "/tests", label: "Run security tests", icon: <IconFlask /> },
      { to: "/investigations", label: "Investigations", icon: <IconMagnifier /> },
      { to: "/traces", label: "Execution traces", icon: <IconBranch /> },
      { to: "/reports", label: "Reports", icon: <IconDoc /> },
      { to: "/analytics", label: "Analytics", icon: <IconBars /> },
    ],
  },
  {
    title: "Configure",
    items: [
      { to: "/policies", label: "Policies", icon: <IconRules /> },
      { to: "/integrations", label: "Integrations", icon: <IconPlug /> },
      { to: "/settings", label: "Settings", icon: <IconGear /> },
    ],
  },
];

export function AppLayout() {
  const { me, logout, liveState } = useApp();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(0);
  const loc = useLocation();

  const refreshPending = () =>
    api.get("/approvals?status=pending&limit=1").then((r) => setPending(r.total)).catch(() => undefined);
  useEffect(() => {
    refreshPending();
  }, []);
  useLiveEvents((e) => {
    if (e.event_type.startsWith("approval.") || e.event_type === "scenario.completed") refreshPending();
  });
  useEffect(() => setOpen(false), [loc.pathname]);

  const liveLabel = { open: "Live stream connected", connecting: "Connecting to live stream", closed: "Live stream disconnected, retrying" }[liveState];

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <aside className="sidebar" data-open={open} aria-label="Primary">
        <div className="brand">
          <IconShield size={22} style={{ color: "#5fb3ab" }} />
          <div>
            <div className="brand-name">AEGIS AI + SENTINEL</div>
            <div className="brand-sub">Agent security testing and runtime protection</div>
          </div>
        </div>
        <nav className="nav">
          {GROUPS.map((g) => (
            <div className="nav-group" key={g.title}>
              <div className="nav-group-title">{g.title}</div>
              {g.items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.end}>
                  {i.icon}
                  {i.label}
                  {i.to === "/runtime" && pending > 0 ? <span className="count" aria-label={`${pending} pending approvals`}>{pending}</span> : null}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <NavLink to="/terms">Terms of Service</NavLink>
          <NavLink to="/privacy">Privacy Policy</NavLink>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="menu-btn btn-ghost" aria-label="Open navigation" onClick={() => setOpen(!open)}><IconMenu /></button>
          <span className="row small muted" title={liveLabel}>
            <span className="live-dot" data-state={liveState} aria-hidden="true" />
            {liveState === "open" ? "Live" : liveState === "connecting" ? "Connecting" : "Reconnecting"}
            <span className="sr-only">{liveLabel}</span>
          </span>
          <span className="spacer" />
          <span className="small muted">Signed in as <b>{me?.username}</b> ({me?.role})</span>
          <button className="btn-sm" onClick={logout}>Sign out</button>
        </header>
        <main id="main" className="page" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
