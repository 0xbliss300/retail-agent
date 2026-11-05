import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import Icon, { BrandMark } from "./Icons.jsx";
import {
  loadConversations,
  loadProfile,
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
  const [profile, setProfile] = useState(loadProfile);
  const [conversations, setConversations] = useState(loadConversations);
  const [activeId, setActiveId] = useState(null);

  useEffect(() => subscribeConversations(() => setConversations(loadConversations())), []);
  useEffect(() => {
    const refresh = () => setProfile(loadProfile());
    window.addEventListener("storage", refresh);
    return () => window.removeEventListener("storage", refresh);
  }, []);

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

      <div className="sidebar__footer" onClick={() => navigate("/login")} role="button" tabIndex={0}>
        <span className="avatar-circle">
          <Icon name="user" size={18} />
        </span>
        <span className="sidebar__user">
          <strong>{profile.name || "小林"}</strong>
          <small>个人账号</small>
        </span>
        <Icon name="chevron-right" size={16} />
      </div>
    </aside>
  );
}
