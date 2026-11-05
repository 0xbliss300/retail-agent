import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import LoginPage from "./pages/LoginPage.jsx";
import RegisterPage from "./pages/RegisterPage.jsx";
import WorkspacePage from "./pages/WorkspacePage.jsx";
import OrdersPage from "./pages/OrdersPage.jsx";
import OrderDetailPage from "./pages/OrderDetailPage.jsx";
import RefundsPage from "./pages/RefundsPage.jsx";
import RefundDetailPage from "./pages/RefundDetailPage.jsx";
import PoliciesPage from "./pages/PoliciesPage.jsx";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/app" element={<WorkspacePage />} />
        <Route path="/app/orders" element={<OrdersPage />} />
        <Route path="/app/orders/:orderId" element={<OrderDetailPage />} />
        <Route path="/app/refunds" element={<RefundsPage />} />
        <Route path="/app/refunds/:refundId" element={<RefundDetailPage />} />
        <Route path="/app/policies" element={<PoliciesPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
