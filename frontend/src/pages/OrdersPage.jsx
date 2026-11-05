import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AppShell from "../components/AppShell.jsx";
import Icon from "../components/Icons.jsx";
import Pager from "../components/Pager.jsx";
import StatusPill from "../components/StatusPill.jsx";
import { get } from "../api.js";
import { formatAmount, formatDateTime, orderStatusLabel, orderStatusTone } from "../format.js";

const PAGE_SIZE = 10;

export default function OrdersPage() {
  const navigate = useNavigate();
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setError("");
    get(`/orders?limit=${PAGE_SIZE}&offset=${offset}`)
      .then((result) => alive && setData(result))
      .catch((problem) => alive && setError(problem.message));
    return () => {
      alive = false;
    };
  }, [offset]);

  const orders = data?.orders ?? [];

  return (
    <AppShell>
      <div className="page">
        <header className="page__head">
          <div>
            <h1 className="page__title">我的订单</h1>
            <p className="page__subtitle">查看订单状态，继续你的售后服务</p>
          </div>
        </header>

        <div className="page__body">
          <div className="page__center-chip">
            <span className="sample-chip">示例数据</span>
          </div>

          {error ? <div className="empty">{error}</div> : null}

          {!error ? (
            <>
              <div className="table-card">
                <table className="table">
                  <thead>
                    <tr>
                      <th>订单编号</th>
                      <th>订单状态</th>
                      <th>实付金额</th>
                      <th>付款时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((order) => (
                      <tr key={order.order_id}>
                        <td>{order.order_id}</td>
                        <td>
                          <StatusPill tone={orderStatusTone(order.status)}>
                            {orderStatusLabel(order.status)}
                          </StatusPill>
                        </td>
                        <td>{formatAmount(order.paid_amount_cents)}</td>
                        <td>{formatDateTime(order.paid_at)}</td>
                        <td>
                          <Link className="table__link" to={`/app/orders/${order.order_id}`}>
                            查看详情
                          </Link>
                        </td>
                      </tr>
                    ))}
                    {orders.length === 0 && !error ? (
                      <tr>
                        <td className="table__empty" colSpan={5}>
                          当前没有可展示的订单。
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

              <div className="table-foot">
                <span>按订单编号排序 ↑</span>
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
          <span>不知道如何处理？向售后助手提问</span>
          <button className="btn btn--primary btn--sm" type="button" onClick={() => navigate("/app")}>
            咨询助手
          </button>
        </div>
      </div>
    </AppShell>
  );
}
