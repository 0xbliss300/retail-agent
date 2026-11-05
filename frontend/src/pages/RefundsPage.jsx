import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AppShell from "../components/AppShell.jsx";
import Icon from "../components/Icons.jsx";
import Pager from "../components/Pager.jsx";
import StatusPill from "../components/StatusPill.jsx";
import { get } from "../api.js";
import { formatAmount, formatDateTime, refundStatusLabel, refundStatusTone } from "../format.js";

const PAGE_SIZE = 10;
const TABS = [
  { label: "全部", value: "" },
  { label: "待处理", value: "pending" },
  { label: "已撤回", value: "withdrawn" },
  { label: "已完成", value: "completed" },
  { label: "审核拒绝", value: "rejected" },
];

export default function RefundsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setError("");
    const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (status) query.set("status", status);
    get(`/refunds?${query.toString()}`)
      .then((result) => alive && setData(result))
      .catch((problem) => alive && setError(problem.message));
    return () => {
      alive = false;
    };
  }, [offset, status]);

  const refunds = data?.refunds ?? [];

  return (
    <AppShell>
      <div className="page">
        <header className="page__head">
          <div>
            <h1 className="page__title">退款申请</h1>
            <p className="page__subtitle">查看已提交的售后申请与处理状态</p>
          </div>
        </header>

        <div className="page__body">
          <div className="page__center-chip">
            <span className="sample-chip">示例数据</span>
          </div>

          <div style={{ marginBottom: 16 }}>
            <div className="tabs">
              {TABS.map((tab) => (
                <button
                  key={tab.label}
                  type="button"
                  className={status === tab.value ? "is-active" : ""}
                  onClick={() => {
                    setStatus(tab.value);
                    setOffset(0);
                  }}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          <div className="callout" style={{ marginBottom: 18 }}>
            <Icon name="info" size={17} />
            申请已提交不代表退款已到账。
          </div>

          {error ? <div className="empty">{error}</div> : null}

          {!error ? (
            <>
              <div className="table-card">
                <table className="table">
                  <thead>
                    <tr>
                      <th>申请编号</th>
                      <th>订单编号</th>
                      <th>申请金额</th>
                      <th>申请原因</th>
                      <th>申请状态</th>
                      <th>申请时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {refunds.map((refund) => (
                      <tr key={refund.refund_id}>
                        <td>{refund.refund_id}</td>
                        <td>{refund.order_id}</td>
                        <td>{formatAmount(refund.amount_cents)}</td>
                        <td>{refund.reason}</td>
                        <td>
                          <StatusPill tone={refundStatusTone(refund.status)}>
                            {refundStatusLabel(refund.status)}
                          </StatusPill>
                        </td>
                        <td>{formatDateTime(refund.created_at)}</td>
                        <td>
                          <Link className="table__link" to={`/app/refunds/${refund.refund_id}`}>
                            查看详情
                          </Link>
                        </td>
                      </tr>
                    ))}
                    {refunds.length === 0 ? (
                      <tr>
                        <td className="table__empty" colSpan={7}>
                          当前筛选条件下没有退款申请。
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

              <div className="table-foot">
                <span>此处仅展示售后退款申请，不包含取消订单产生的退款流水。</span>
                <Pager
                  hasPrev={offset > 0}
                  hasNext={Boolean(data?.has_more)}
                  onPrev={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  onNext={() => setOffset(offset + PAGE_SIZE)}
                />
              </div>
            </>
          ) : null}
        </div>

        <div className="page__helpbar">
          <span>对退款结果有疑问？向售后助手提问</span>
          <button className="btn btn--primary btn--sm" type="button" onClick={() => navigate("/app")}>
            咨询助手
          </button>
        </div>
      </div>
    </AppShell>
  );
}
