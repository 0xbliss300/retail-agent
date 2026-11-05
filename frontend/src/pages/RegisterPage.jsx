import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Icon, { BrandMark, PackageGlyph } from "../components/Icons.jsx";
import StatusPill from "../components/StatusPill.jsx";
import { saveProfile } from "../storage.js";

const FEATURES = [
  { icon: "box", label: "订单与物流，一问即知" },
  { icon: "clock", label: "退款进度，清晰可查" },
  { icon: "check", label: "重要操作，由你确认" },
];

export default function RegisterPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm: "" });
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState("");

  const update = (key) => (event) => setForm((value) => ({ ...value, [key]: event.target.value }));

  const submit = (event) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setError("请输入昵称。");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) {
      setError("邮箱格式不正确。");
      return;
    }
    if (form.password.length < 6) {
      setError("密码至少 6 位。");
      return;
    }
    if (form.password !== form.confirm) {
      setError("两次输入的密码不一致。");
      return;
    }
    setError("");
    saveProfile({ name: form.name.trim(), email: form.email.trim() });
    navigate("/app");
  };

  return (
    <div className="auth">
      <div className="auth__brand">
        <BrandMark size={24} />
        RetailOps Agent
      </div>

      <div className="auth__body">
        <section className="auth__hero">
          <h1 className="auth__title">让售后处理更简单</h1>
          <p className="auth__subtitle">从查询订单，到确认每一步操作</p>

          <div className="hero-features">
            {FEATURES.map((feature) => (
              <div className="hero-feature" key={feature.label}>
                <Icon name={feature.icon} size={20} />
                {feature.label}
              </div>
            ))}
          </div>

          <div className="hero-sample">
            <PackageGlyph size={36} />
            <div style={{ flex: 1 }}>
              <div className="hero-sample__id">ORD-1001</div>
              <div className="hero-sample__meta">示例订单</div>
            </div>
            <StatusPill tone="blue">已发货</StatusPill>
          </div>
        </section>

        <form className="auth__card" onSubmit={submit}>
          <h2 className="auth__card-title">创建账号</h2>
          <p className="auth__card-subtitle">开启你的售后服务对话</p>

          <label className="field">
            <span className="field__label">昵称</span>
            <span className="field__control">
              <input type="text" value={form.name} placeholder="请输入昵称" onChange={update("name")} />
            </span>
          </label>

          <label className="field">
            <span className="field__label">邮箱</span>
            <span className="field__control">
              <input type="text" value={form.email} placeholder="请输入邮箱地址" onChange={update("email")} />
            </span>
          </label>

          <label className="field">
            <span className="field__label">密码</span>
            <span className="field__control">
              <input
                type={visible ? "text" : "password"}
                value={form.password}
                placeholder="设置密码"
                onChange={update("password")}
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

          <label className="field">
            <span className="field__label">确认密码</span>
            <span className="field__control">
              <input
                type={visible ? "text" : "password"}
                value={form.confirm}
                placeholder="再次输入密码"
                onChange={update("confirm")}
              />
            </span>
          </label>

          {error ? <div className="form-error">{error}</div> : null}

          <button className="btn btn--primary btn--block auth__submit" type="submit">
            注册
          </button>

          <p className="auth__switch">
            已有账号？
            <Link to="/login">立即登录</Link>
          </p>
          <p className="auth__footnote">演示环境：账号信息仅保存在本机，不发送到服务端。</p>
        </form>
      </div>

      <div className="auth__footer">RetailOps Agent</div>
    </div>
  );
}
