import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { get, setWorkspaceAccount } from "./api.js";
import LoginPage from "./pages/LoginPage.jsx";
import WorkspacePage from "./pages/WorkspacePage.jsx";
import OrdersPage from "./pages/OrdersPage.jsx";
import OrderDetailPage from "./pages/OrderDetailPage.jsx";
import RefundsPage from "./pages/RefundsPage.jsx";
import RefundDetailPage from "./pages/RefundDetailPage.jsx";
import PoliciesPage from "./pages/PoliciesPage.jsx";

function RequireAccount() {
  const [account, setAccount] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    get("/auth/me")
      .then((profile) => {
        if (!active) return;
        setWorkspaceAccount(profile.user_id);
        setAccount(profile);
      })
      .catch((failure) => {
        if (!active) return;
        setWorkspaceAccount(null);
        setError(failure);
      });
    return () => { active = false; };
  }, []);

  if (error?.status === 401) return <Navigate to="/login" replace />;
  if (error) return <p role="alert">无法检查登录状态，请刷新重试。</p>;
  if (!account) return <p role="status">正在检查登录状态…</p>;
  return <Outlet context={account} />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAccount />}>
          <Route path="/app" element={<WorkspacePage />} />
          <Route path="/app/orders" element={<OrdersPage />} />
          <Route path="/app/orders/:orderId" element={<OrderDetailPage />} />
          <Route path="/app/refunds" element={<RefundsPage />} />
          <Route path="/app/refunds/:refundId" element={<RefundDetailPage />} />
          <Route path="/app/policies" element={<PoliciesPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
