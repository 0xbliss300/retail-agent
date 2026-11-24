import { useEffect, useState } from "react";
import { NavLink, useNavigate, useOutletContext } from "react-router-dom";
import { post, setWorkspaceAccount } from "../api.js";
import Icon, { BrandMark } from "./Icons.jsx";
import {
  loadConversations,
  subscribeConversations,
} from "../storage.js";

const NAV = [
  { to: "/app", label: "售后助手", icon: "chat", end: true },
  { to: "/app/orders", label: "我的订单", icon: "box" },
  { to: "/app/refunds", label: "退款申请", icon: "clock" },
  { to: "/app/policies", label: "售后政策", icon: "doc" },
];

export default function Sidebar() {
  const navigate = useNavigate();
  const profile = useOutletContext();
  const [conversations, setConversations] = useState(loadConversations);
  const [activeId, setActiveId] = useState(null);
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");

  useEffect(() => subscribeConversations(() => setConversations(loadConversations())), []);

  const signOut = async () => {
    setSigningOut(true);
    setLogoutError("");
    try {
      await post("/auth/logout");
      setWorkspaceAccount(null);
      navigate("/login", { replace: true });
    } catch (failure) {
      setLogoutError(failure.message);
      setSigningOut(false);
    }
  };

  const openConversation = (conversation) => {
    setActiveId(conversation.id);
    navigate(`/app?c=${conversation.id}`);
  };

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <BrandMark size={26} />
        <span className="sidebar__wordmark">RetailOps Agent</span>
      </div>

      <button className="btn btn--ghost sidebar__new" type="button" onClick={() => navigate("/app?new=1")}>
        <Icon name="plus" size={15} />
        新建对话
      </button>

      <nav className="sidebar__nav">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item${isActive ? " is-active" : ""}`}
          >
            <Icon name={item.icon} size={17} />
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar__divider" />
      <div className="sidebar__section">最近对话</div>
      <div className="sidebar__recent">
        {conversations.length === 0 ? (
          <div className="sidebar__empty">还没有对话记录</div>
        ) : (
          conversations.map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              className={`recent-item${conversation.id === activeId ? " is-active" : ""}`}
              onClick={() => openConversation(conversation)}
            >
              <Icon name="chat" size={16} />
              <span>{conversation.title}</span>
            </button>
          ))
        )}
      </div>

      {logoutError ? <p role="alert" className="form-error">{logoutError}</p> : null}
      <button className="sidebar__footer" type="button" onClick={signOut} disabled={signingOut}>
        <span className="avatar-circle">
          <Icon name="user" size={18} />
        </span>
        <span className="sidebar__user">
          <strong>{profile.name}</strong>
          <small>{signingOut ? "正在退出…" : "退出登录"}</small>
        </span>
        <Icon name="chevron-right" size={16} />
      </button>
    </aside>
  );
}
