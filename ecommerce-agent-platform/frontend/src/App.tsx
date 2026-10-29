import { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { SessionProvider, useSession } from "./lib/session";
import { Skeleton } from "./lib/ui";
import { StoreLayout } from "./store/StoreLayout";
import { Catalog } from "./store/Catalog";
import { ProductPage } from "./store/ProductPage";
import { Compare } from "./store/Compare";
import { CartPage } from "./store/CartPage";
import { OrdersPage, OrderDetail } from "./store/Orders";
import { SupportPage } from "./store/Support";
import { Assistant } from "./store/Assistant";
import { PoliciesPage } from "./store/Policies";
import { Login } from "./store/Login";
import { Privacy, Terms } from "./store/Legal";
import { WorkbenchLayout } from "./workbench/WorkbenchLayout";
import { RunsView } from "./workbench/Runs";
import { TestsView } from "./workbench/Tests";
import { ApprovalsView } from "./workbench/Approvals";
import { SetupView } from "./workbench/Setup";
import { PolicyView } from "./workbench/PolicyView";

function Require({ role, children }: { role: "customer" | "staff"; children: ReactNode }) {
  const { me, ready } = useSession();
  const loc = useLocation();
  if (!ready) return <Skeleton rows={3} />;
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  if (me.role !== role)
    return (
      <div className="state">
        <h3>{role === "staff" ? "Staff sign-in required" : "Customer sign-in required"}</h3>
        <p className="muted">
          You are signed in as {me.email} ({me.role}). {role === "staff" ? "The Agent Workbench is for store staff." : "This page belongs to a customer account."}
        </p>
      </div>
    );
  return <>{children}</>;
}

export function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<StoreLayout />}>
            <Route index element={<Catalog />} />
            <Route path="products/:id" element={<ProductPage />} />
            <Route path="compare" element={<Compare />} />
            <Route path="policies" element={<PoliciesPage />} />
            <Route path="policies/:topic" element={<PoliciesPage />} />
            <Route path="cart" element={<Require role="customer"><CartPage /></Require>} />
            <Route path="orders" element={<Require role="customer"><OrdersPage /></Require>} />
            <Route path="orders/:id" element={<Require role="customer"><OrderDetail /></Require>} />
            <Route path="support" element={<Require role="customer"><SupportPage /></Require>} />
            <Route path="assistant" element={<Require role="customer"><Assistant /></Require>} />
            <Route path="terms" element={<Terms />} />
            <Route path="privacy" element={<Privacy />} />
          </Route>
          <Route path="/workbench" element={<Require role="staff"><WorkbenchLayout /></Require>}>
            <Route index element={<RunsView />} />
            <Route path="runs/:cid" element={<RunsView />} />
            <Route path="tests" element={<TestsView />} />
            <Route path="approvals" element={<ApprovalsView />} />
            <Route path="setup" element={<SetupView />} />
            <Route path="policies/:key" element={<PolicyView />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  );
}
