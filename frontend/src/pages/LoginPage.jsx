import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { post, setWorkspaceAccount } from "../api.js";
import Icon, { BrandMark, PackageGlyph } from "../components/Icons.jsx";
import StatusPill from "../components/StatusPill.jsx";

const TRACK = [
  { label: "已揽收", time: "1月20日 10:24", state: "done" },
  { label: "运输中", time: "1月20日 18:40", state: "done" },
  { label: "派送中", time: "", state: "current" },
  { label: "已送达", time: "", state: "" },
];

const DEMO_EMAIL = "demo@retailops.local";
const DEMO_PASSWORD = "demo1234";

export default function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState(DEMO_EMAIL);
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    if (submitting) return;
    if (!email.trim()) {
      setError("请输入邮箱地址。");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("邮箱格式不正确。");
      return;
    }
    if (!password) {
      setError("请输入密码。");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const account = await post("/auth/login", { email: email.trim(), password });
      setWorkspaceAccount(account.user_id);
      navigate("/app", { replace: true });
    } catch (failure) {
      setError(failure.message);
      setSubmitting(false);
    }
  };

  return (
    <div className="auth">
      <div className="auth__brand">
        <BrandMark size={24} />
        RetailOps Agent
      </div>

      <div className="auth__body">
        <section className="auth__hero">
          <h1 className="auth__title">售后服务，从一句话开始</h1>
          <p className="auth__subtitle">查询订单、追踪物流、处理退款</p>

          <div className="hero-chat">
            <div className="hero-chat__row hero-chat__row--user">
              <div className="hero-chat__bubble">我的订单现在到哪里了？</div>
              <span className="hero-chat__avatar">
                <Icon name="user" size={16} />
              </span>
            </div>
            <div className="hero-chat__row">
              <span className="hero-chat__avatar hero-chat__avatar--bot">
                <Icon name="chat" size={16} />
              </span>
              <div className="hero-chat__bubble hero-chat__bubble--bot">我帮你查到订单的最新状态：</div>
            </div>

            <div className="hero-order">
              <div className="hero-order__head">
                订单状态
                <StatusPill tone="blue">已发货</StatusPill>
              </div>
              <div className="hero-order__meta">订单编号：ORD-2002</div>
              <div className="hero-order__body">
                <PackageGlyph size={38} />
                <div>
                  <strong>示例订单</strong>
                  <small>数量：1</small>
                </div>
              </div>
              <div className="hero-track">
                {TRACK.map((step) => (
                  <div
                    key={step.label}
                    className={`hero-track__step${step.state ? ` hero-track__step--${step.state}` : ""}`}
                  >
                    <span className="hero-track__dot" />
                    {step.label}
                    {step.time ? <span className="hero-track__time">{step.time}</span> : null}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <form className="auth__card" onSubmit={submit}>
          <h2 className="auth__card-title">欢迎回来</h2>
          <p className="auth__card-subtitle">登录以继续你的售后对话</p>

          <label className="field">
            <span className="field__label">邮箱</span>
            <span className="field__control">
              <input
                type="text"
                value={email}
                placeholder="请输入邮箱地址"
                autoComplete="username"
                onChange={(event) => setEmail(event.target.value)}
              />
            </span>
          </label>

          <label className="field">
            <span className="field__label">密码</span>
            <span className="field__control">
              <input
                type={visible ? "text" : "password"}
                value={password}
                placeholder="请输入密码"
                autoComplete="current-password"
                onChange={(event) => setPassword(event.target.value)}
              />
              <button
                className="field__toggle"
                type="button"
                onClick={() => setVisible((value) => !value)}
                aria-label={visible ? "隐藏密码" : "显示密码"}
              >
                <Icon name={visible ? "eye-off" : "eye"} size={17} />
              </button>
            </span>
          </label>

          {error ? <div className="form-error" role="alert">{error}</div> : null}

          <button className="btn btn--primary btn--block auth__submit" type="submit" disabled={submitting}>
            {submitting ? "正在登录…" : "登录"}
          </button>

          <p className="auth__footnote">
            已填入账号 A。体验账号 B 时，输入 demo2@retailops.local，密码 demo5678。
          </p>
        </form>
      </div>

      <div className="auth__footer">RetailOps Agent</div>
    </div>
  );
}
