import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import AppShell from "../components/AppShell.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import Icon, { PackageGlyph } from "../components/Icons.jsx";
import StatusPill from "../components/StatusPill.jsx";
import { get, post } from "../api.js";
import {
  formatAmount,
  formatDateTime,
  orderStatusLabel,
  orderStatusTone,
  refundStatusLabel,
  refundStatusTone,
} from "../format.js";

const STATUS_TEXT = {
  pending: "申请已提交，等待处理",
  approved: "审核已通过，等待结算",
  completed: "退款已完成，订单状态已更新",
  withdrawn: "申请已撤回，订单状态未改变",
  rejected: "审核未通过，退款未执行",
  failed: "结算失败，请向售后助手咨询",
};

function flowSteps(status) {
  const processLabel =
    status === "withdrawn"
      ? "申请已撤回"
      : status === "rejected"
        ? "审核拒绝"
        : status === "failed"
          ? "结算失败"
          : "处理退款申请";
  return [
    { label: "提交退款申请", state: "done" },
    { label: processLabel, state: status === "pending" ? "active" : "done" },
    { label: "完成退款", state: status === "completed" ? "done" : "" },
  ];
}

export default function RefundDetailPage() {
  const { refundId } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");

  const load = useCallback(() => {
    setError("");
    return get(`/refunds/${refundId}`)
      .then(setData)
      .catch((problem) => setError(problem.message));
  }, [refundId]);

  useEffect(() => {
    load();
  }, [load]);

  const refund = data?.refund;

  const withdraw = async () => {
    setBusy(true);
    setDialogError("");
    try {
      await post(`/refunds/${refundId}/withdraw`);
      setDialogOpen(false);
      setNotice("已撤回该退款申请，订单状态保持不变。");
      await load();
    } catch (problem) {
      setDialogError(problem.message || "撤回失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="page">
        <div className="page__body page__body--wide">
          <nav className="breadcrumb">
            <Link to="/app/refunds">退款申请</Link>
            <span>/</span>
            <span>{refundId}</span>
          </nav>

          <header className="page__head">
            <h1 className="page__title" style={{ display: "flex", alignItems: "center", gap: 12 }}>
              退款申请详情
              <span className="sample-chip">示例数据</span>
            </h1>
          </header>

          {error ? (
            <div className="empty">
              {error}
              <div style={{ marginTop: 14 }}>
                <Link className="btn btn--outline btn--sm" to="/app/refunds">
                  返回退款申请
                </Link>
              </div>
            </div>
          ) : null}

          {refund ? (
            <div className="detail-layout">
              <div className="detail-main">
                <div className="card">
                  <div className="status-banner">
                    <StatusPill tone={refundStatusTone(refund.status)}>
                      {refundStatusLabel(refund.status)}
                    </StatusPill>
                    <span className="status-banner__text">{STATUS_TEXT[refund.status] || "处理中"}</span>
                  </div>

                  {notice ? (
                    <div className="callout" style={{ marginTop: 16 }}>
                      <Icon name="info" size={16} />
                      {notice}
                    </div>
                  ) : null}

                  <div className="detail-rows">
                    <div className="kv">
                      <span className="kv__label">申请编号</span>
                      <span className="kv__value">{refund.refund_id}</span>
                    </div>
                    <div className="kv">
                      <span className="kv__label">订单编号</span>
                      <span className="kv__value">{refund.order_id}</span>
                    </div>
                    <div className="kv">
                      <span className="kv__label">申请金额</span>
                      <span className="kv__value">{formatAmount(refund.amount_cents)}</span>
                    </div>
                    <div className="kv">
                      <span className="kv__label">申请原因</span>
                      <span className="kv__value">{refund.reason}</span>
                    </div>
                    <div className="kv">
                      <span className="kv__label">订单状态</span>
                      <span className="kv__value">{orderStatusLabel(refund.order_status)}</span>
                    </div>
                    <div className="kv">
                      <span className="kv__label">申请时间</span>
                      <span className="kv__value">{formatDateTime(refund.created_at)}</span>
                    </div>
                  </div>

                  {refund.status === "pending" || refund.status === "approved" ? (
                    <p className="refund-note">
                      <Icon name="info" size={15} />
                      当前尚未完成退款，请以实际处理结果为准。
                    </p>
                  ) : null}

                  {refund.status === "pending" ? (
                    <div className="detail-actions" style={{ justifyContent: "flex-end" }}>
                      <button
                        className="btn btn--outline btn--sm"
                        type="button"
                        onClick={() => {
                          setDialogError("");
                          setDialogOpen(true);
                        }}
                      >
                        撤回申请
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>

              <aside className="detail-side">
                <div className="card">
                  <h2 className="card__title" style={{ fontSize: 15 }}>
                    关联订单
                  </h2>
                  <div className="order-mini" style={{ padding: 0, border: 0 }}>
                    <div className="order-mini__top">
                      <span className="order-mini__id">{refund.order_id}</span>
                      <StatusPill tone={orderStatusTone(refund.order_status)}>
                        {orderStatusLabel(refund.order_status)}
                      </StatusPill>
                    </div>
                    <div className="order-mini__glyph">
                      <PackageGlyph size={50} />
                    </div>
                    <div className="order-mini__row">
                      <span>实付金额</span>
                      <strong>{formatAmount(refund.paid_amount_cents)}</strong>
                    </div>
                    <div className="order-mini__row">
                      <span>申请状态</span>
                      <StatusPill tone={refundStatusTone(refund.status)}>
                        {refundStatusLabel(refund.status)}
                      </StatusPill>
                    </div>
                  </div>
                </div>

                <div className="card">
                  <h2 className="card__title" style={{ fontSize: 15 }}>
                    处理流程
                  </h2>
                  <div className="flow">
                    {flowSteps(refund.status).map((step) => (
                      <div
                        key={step.label}
                        className={`flow__step${step.state ? ` flow__step--${step.state}` : ""}`}
                      >
                        <span className="flow__dot" />
                        {step.label}
                        {step.state === "active" ? <small>{formatDateTime(refund.created_at)}</small> : null}
                      </div>
                    ))}
                  </div>
                </div>
              </aside>
            </div>
          ) : null}
        </div>
      </div>

      {dialogOpen && refund ? (
        <ConfirmDialog
          title="确认撤回退款申请？"
          description="撤回后该申请将结束，订单状态不会改变。若仍符合退款条件，可以重新申请。"
          summary={{ id: refund.order_id, amountCents: refund.amount_cents, badge: "尚未撤回" }}
          confirmLabel="确认撤回"
          cancelLabel="保留申请"
          busy={busy}
          error={dialogError}
          onConfirm={withdraw}
          onCancel={() => setDialogOpen(false)}
        />
      ) : null}
    </AppShell>
  );
}
