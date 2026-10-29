import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useSession } from "../lib/session";
import { IconWork } from "../lib/icons";
import { LiveDot } from "../lib/ui";

export function WorkbenchLayout() {
  const { me, logout, liveState } = useSession();
  const nav = useNavigate();
  return (
    <div className="store">
      <a className="skip" href="#main">Skip to content</a>
      <header className="store-top">
        <div className="store-top-inner">
          <Link to="/workbench" className="store-brand">
            <IconWork size={20} style={{ color: "#5fb3ab" }} />
            <span>
              <b>Agent Workbench</b>
              <small>Halden Bazaar shopping agent, staff view</small>
            </span>
          </Link>
          <nav className="store-nav" aria-label="Workbench">
            <NavLink to="/workbench" end>Runs</NavLink>
            <NavLink to="/workbench/tests">Security tests</NavLink>
            <NavLink to="/workbench/approvals">Approvals</NavLink>
            <NavLink to="/workbench/setup">Model and integration</NavLink>
            <NavLink to="/">Storefront</NavLink>
          </nav>
          <span className="spacer" />
          <span className="who">{me?.email}</span>
          <button onClick={() => logout().then(() => nav("/login"))}>Sign out</button>
        </div>
      </header>
      <div className="fixture-band">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>Every record here comes from the backend: model calls, SENTINEL decisions and tool results as they happened. Nothing is replayed from a script.</span>
          <LiveDot state={liveState} />
        </div>
      </div>
      <main id="main" className="store-main" style={{ maxWidth: 1480 }}>
        <Outlet />
      </main>
    </div>
  );
}
