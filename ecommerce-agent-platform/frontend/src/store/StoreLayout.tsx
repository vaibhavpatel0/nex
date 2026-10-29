import { NavLink, Outlet, Link, useNavigate } from "react-router-dom";
import { useSession } from "../lib/session";
import { IconBag, IconBox, IconChat, IconDoc, IconHelp, IconSearch, IconWork } from "../lib/icons";

export function StoreLayout() {
  const { me, logout, cartCount } = useSession();
  const nav = useNavigate();
  const customer = me?.role === "customer";
  return (
    <div className="store">
      <a className="skip" href="#main">Skip to content</a>
      <header className="store-top">
        <div className="store-top-inner">
          <Link to="/" className="store-brand">
            <IconBag size={20} style={{ color: "#5fb3ab" }} />
            <span>
              <b>Halden Bazaar</b>
              <small>Test store for the AEGIS shopping agent</small>
            </span>
          </Link>
          <nav className="store-nav" aria-label="Store">
            <NavLink to="/" end><IconSearch />Catalogue</NavLink>
            {customer ? <NavLink to="/assistant"><IconChat />Assistant</NavLink> : null}
            {customer ? <NavLink to="/orders"><IconBox />Orders</NavLink> : null}
            {customer ? <NavLink to="/support"><IconHelp />Support</NavLink> : null}
            <NavLink to="/policies"><IconDoc />Policies</NavLink>
            {me?.role === "staff" ? <NavLink to="/workbench"><IconWork />Agent Workbench</NavLink> : null}
          </nav>
          <span className="spacer" />
          {customer ? (
            <NavLink to="/cart" className="btn" style={{ background: "transparent", color: "inherit", borderColor: "var(--graphite-2)" }}>
              <IconBag />Cart <span className="cart-count" aria-label={`${cartCount} items in cart`}>{cartCount}</span>
            </NavLink>
          ) : null}
          {me ? (
            <>
              <span className="who">{me.display_name}</span>
              <button onClick={() => logout().then(() => nav("/"))}>Sign out</button>
            </>
          ) : (
            <button onClick={() => nav("/login")}>Sign in</button>
          )}
        </div>
      </header>
      <div className="fixture-band">
        <div>Every product, customer and order here is synthetic test data. Checkout uses a local test payment fixture; no money moves and nothing ships.</div>
      </div>
      <main id="main" className="store-main">
        <Outlet />
      </main>
      <footer className="store-foot">
        <div>
          <span>Halden Bazaar is a fictional store for testing AI agents locally.</span>
          <Link to="/terms">Terms of Service</Link>
          <Link to="/privacy">Privacy Policy</Link>
          <Link to="/policies">Store policies</Link>
        </div>
      </footer>
    </div>
  );
}
