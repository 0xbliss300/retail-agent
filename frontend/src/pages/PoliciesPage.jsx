import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import AppShell from "../components/AppShell.jsx";
import Icon from "../components/Icons.jsx";
import { get } from "../api.js";

export default function PoliciesPage() {
  const navigate = useNavigate();
  const [policies, setPolicies] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    get("/policies")
      .then((result) => alive && setPolicies(result.policies))
      .catch((problem) => alive && setError(problem.message));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <AppShell>
      <div className="page">
        <header className="page__head">
          <div>
            <h1 className="page__title">售后政策</h1>
            <p className="page__subtitle">了解取消订单与退款申请的适用条件</p>
            <p className="note" style={{ marginTop: 6 }}>
              本项目模拟业务政策
            </p>
          </div>
        </header>

        <div className="page__body page__body--wide">
          {error ? <div className="empty">{error}</div> : null}

          {policies ? (
            <>
              <div className="policy-grid">
                <section className="policy-card">
                  <div className="policy-card__head">
                    <h2 className="policy-card__title">
                      <Icon name="box" size={20} />
                      取消订单
                    </h2>
                    <span className="sample-chip">示例数据</span>
                  </div>
                  <ul className="policy-list">
                    {(policies.cancellation ?? []).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </section>

                <section className="policy-card">
                  <div className="policy-card__head">
                    <h2 className="policy-card__title">
                      <Icon name="refresh" size={20} />
                      退款申请
                    </h2>
                    <span className="sample-chip">示例数据</span>
                  </div>
                  <ul className="policy-list">
                    {(policies.refund ?? []).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </section>
              </div>

              <div className="callout" style={{ marginTop: 20 }}>
                <Icon name="info" size={17} />
                申请提交成功，不代表退款已到账。
              </div>

              <section className="policy-extra">
                <Icon name="doc" size={20} />
                <div>
                  <div className="policy-extra__title">撤回申请</div>
                  <p>{(policies.withdraw ?? []).join("")}</p>
                </div>
              </section>

              <div className="policy-actions">
                <button className="btn btn--primary" type="button" onClick={() => navigate("/app")}>
                  <Icon name="chat" size={16} />
                  咨询售后助手
                </button>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </AppShell>
  );
}
