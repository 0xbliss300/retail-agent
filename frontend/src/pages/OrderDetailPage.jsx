import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AppShell from "../components/AppShell.jsx";
import Icon, { PackageGlyph } from "../components/Icons.jsx";
import StatusPill from "../components/StatusPill.jsx";
import { get } from "../api.js";
import {
  formatAmount,
  formatDateTime,
  formatShortDateTime,
  orderStatusLabel,
  orderStatusTone,
  refundStatusLabel,
  refundStatusTone,
} from "../format.js";

function cleanEventText(text) {
  return (text || "").replace(/^模拟[:：]/, "");
}

export default function OrderDetailPage() {
  const { orderId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setError("");
    get(`/orders/${orderId}`)
      .then((result) => alive && setData(result))
      .catch((problem) => alive && setError(problem.message));
    return () => {
      alive = false;
    };
  }, [orderId]);

  const order = data?.order;
  const events = data?.logistics?.events ?? [];
  const eligibility = data?.refund_eligibility;

  return (
    <AppShell>
      <div className="page">
        <div className="page__body page__body--wide">
          <nav className="breadcrumb">
            <Link to="/app/orders">我的订单</Link>
            <span>/</span>
            <span>{orderId}</span>
          </nav>

          <header className="page__head">
            <h1 className="page__title" style={{ display: "flex", alignItems: "center", gap: 12 }}>
              订单详情
              <span className="sample-chip">示例数据</span>
            </h1>
          </header>

          {error ? (
            <div className="empty">
              {error}
              <div style={{ marginTop: 14 }}>
                <Link className="btn btn--outline btn--sm" to="/app/orders">
                  返回订单列表
                </Link>
              </div>
            </div>
          ) : null}

          {order ? (
            <div className="detail-layout">
              <div className="detail-main">
                <div className="card summary-card">
                  <div className="summary-card__glyph">
                    <PackageGlyph size={46} />
                  </div>
                  <div className="summary-card__main">
                    <div className="summary-card__head">
                      <span className="summary-card__id">{order.order_id}</span>
                      <StatusPill tone={orderStatusTone(order.status)}>{orderStatusLabel(order.status)}</StatusPill>
                    </div>
                    <dl className="summary-fields">
                      <div className="summary-fields__item">
                        <dt>实付金额</dt>
                        <dd>{formatAmount(order.paid_amount_cents)}</dd>
                      </div>
                      <div className="summary-fields__item">
                        <dt>付款时间</dt>
                        <dd>{formatDateTime(order.paid_at)}</dd>
                      </div>
                      <div className="summary-fields__item">
                        <dt>发货时间</dt>
                        <dd>{formatDateTime(order.shipped_at)}</dd>
                      </div>
                      <div className="summary-fields__item">
                        <dt>签收时间</dt>
                        <dd>{formatDateTime(order.delivered_at)}</dd>
                      </div>
                    </dl>
                  </div>
                </div>

                <div className="card">
                  <h2 className="card__title">物流信息</h2>
                  {data.logistics ? (
                    <>
                      <div className="logistics-head">
                        <span>
                          承运商<strong>{data.logistics.carrier}</strong>
                        </span>
                        <span className="logistics-head__sep" />
                        <span>
                          物流单号<strong>{data.logistics.tracking_number}</strong>
                        </span>
                        <span className="sample-chip" style={{ marginLeft: "auto" }}>
                          模拟物流数据
                        </span>
                      </div>
                      <div className="timeline">
                        {events.map((event, index) => {
                          const current = index === events.length - 1;
                          return (
                            <div className={`timeline__item${current ? " timeline__item--current" : ""}`} key={event.occurred_at}>
                              <span className="timeline__dot" />
                              <div className="timeline__title">{cleanEventText(event.description)}</div>
                              <div className="timeline__time">{formatShortDateTime(event.occurred_at)}</div>
                            </div>
                          );
                        })}
                      </div>
                    </>
                  ) : (
                    <div className="note">该订单暂时没有物流记录。</div>
                  )}
                </div>
              </div>

              <aside className="detail-side">
                <div className="card">
                  <h2 className="card__title">售后服务</h2>
                  {eligibility?.eligible ? (
                    <div className="service-card__state">
                      <Icon name="info" size={17} />
                      符合退款申请条件
                    </div>
                  ) : (
                    <div className="service-card__state service-card__state--muted">
                      <Icon name="info" size={17} />
                      {eligibility?.reason || "暂不支持提交退款申请"}
                    </div>
                  )}

                  <p className="service-card__note">
                    {eligibility?.eligible
                      ? "发货或签收后 7 天内可申请全额退款，提交后等待审核与结算。"
                      : "如需进一步处理，可以先向售后助手说明情况。"}
                  </p>

                  {data.latest_refund ? (
                    <div className="order-mini__row" style={{ marginBottom: 14 }}>
                      <span>最近申请</span>
                      <StatusPill tone={refundStatusTone(data.latest_refund.status)}>
                        {refundStatusLabel(data.latest_refund.status)}
                      </StatusPill>
                    </div>
                  ) : null}

                  {eligibility?.eligible ? (
                    <>
                      <button
                        className="btn btn--primary btn--block"
                        type="button"
                        onClick={() =>
                          navigate("/app", { state: { prefill: `申请 ${order.order_id} 退款` } })
                        }
                      >
                        向助手申请退款
                      </button>
                      <div className="service-card__hint">提交申请前需要你确认</div>
                    </>
                  ) : (
                    <button className="btn btn--outline btn--block" type="button" onClick={() => navigate("/app")}>
                      咨询售后助手
                    </button>
                  )}
                </div>
              </aside>
            </div>
          ) : null}
        </div>
      </div>
    </AppShell>
  );
}
