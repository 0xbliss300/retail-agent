import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams, useLocation } from "react-router-dom";
import AppShell from "../components/AppShell.jsx";
import Icon, { PackageGlyph } from "../components/Icons.jsx";
import MessageText from "../components/MessageText.jsx";
import StatusPill from "../components/StatusPill.jsx";
import { get, post, streamChat } from "../api.js";
import {
  formatAmount,
  formatClock,
  lastOrderId,
  orderStatusLabel,
  orderStatusTone,
} from "../format.js";
import {
  createConversationId,
  loadActiveConversationId,
  loadConversations,
  saveActiveConversationId,
  saveConversations,
  titleFromMessage,
} from "../storage.js";

const SUGGESTIONS = ["查询我的订单", "查看 ORD-2002 的物流", "申请 ORD-2002 退款"];

function newId() {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

function toolTitle(tool) {
  if (tool === "cancel_order") return "确认取消订单";
  if (tool === "cancel_refund") return "确认撤回退款申请";
  return "请确认退款申请";
}

function decisionLabels(tool) {
  if (tool === "cancel_order") return { approve: "确认取消订单", reject: "保留订单" };
  if (tool === "cancel_refund") return { approve: "确认撤回", reject: "保留申请" };
  return { approve: "确认提交", reject: "暂不申请" };
}

function flowSteps(tool) {
  if (tool === "cancel_order") {
    return [
      { label: "核对取消信息", state: "active" },
      { label: "确认取消", state: "" },
      { label: "查看处理结果", state: "" },
    ];
  }
  if (tool === "cancel_refund") {
    return [
      { label: "核对撤回信息", state: "active" },
      { label: "确认撤回", state: "" },
      { label: "等待处理结果", state: "" },
    ];
  }
  return [
    { label: "核对申请信息", state: "active" },
    { label: "确认提交申请", state: "" },
    { label: "等待审核与结算", state: "" },
  ];
}

export default function WorkspacePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();

  const [conversationId, setConversationId] = useState(() => createConversationId());
  const [messages, setMessages] = useState([]);
  const [session, setSession] = useState(null);
  const [pending, setPending] = useState(null);
  const [order, setOrder] = useState(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [banner, setBanner] = useState("");

  const scrollRef = useRef(null);
  const bootRef = useRef(false);
  const activeIdRef = useRef(conversationId);
  activeIdRef.current = conversationId;

  const persist = useCallback((nextMessages, nextSession) => {
    const texts = nextMessages.filter((message) => message.text);
    if (texts.length === 0) return;
    if (nextMessages.some((message) => message.streaming)) return;
    const firstUser = texts.find((message) => message.role === "user");
    const record = {
      id: activeIdRef.current,
      title: titleFromMessage(firstUser?.text),
      sessionId: nextSession?.sessionId ?? null,
      threadId: nextSession?.threadId ?? null,
      messages: texts.slice(-40),
      updatedAt: Date.now(),
    };
    const list = loadConversations().filter((item) => item.id !== record.id);
    saveConversations([record, ...list]);
    saveActiveConversationId(record.id);
  }, []);

  useEffect(() => {
    persist(messages, session);
  }, [messages, session, persist]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, pending]);

  const startNew = useCallback(() => {
    const id = createConversationId();
    setConversationId(id);
    setMessages([]);
    setSession(null);
    setPending(null);
    setOrder(null);
    setInput("");
    setBanner("");
    saveActiveConversationId(id);
  }, []);

  const restore = useCallback(async (record) => {
    setConversationId(record.id);
    setMessages(record.messages ?? []);
    setSession(record.sessionId ? { sessionId: record.sessionId, threadId: record.threadId } : null);
    setPending(null);
    saveActiveConversationId(record.id);
    if (!record.sessionId) return;
    try {
      const result = await post("/recover", {
        session_id: record.sessionId,
        thread_id: record.threadId,
      });
      if (result.state === "awaiting_confirmation" && result.confirmation_id) {
        setPending({
          confirmation_id: result.confirmation_id,
          tool: result.tool,
          order_id: result.order_id,
          summary: result.summary,
          expires_at: result.expires_at,
        });
      }
    } catch {
      // 会话已过期时保持静默，下次发送会重新开始。
    }
  }, []);

  useEffect(() => {
    if (bootRef.current) return;
    bootRef.current = true;
    const targetId = searchParams.get("c");
    if (targetId) {
      const found = loadConversations().find((item) => item.id === targetId);
      if (found) {
        restore(found);
        return;
      }
    }
    if (searchParams.get("new")) {
      startNew();
      return;
    }
    const activeId = loadActiveConversationId();
    const active = loadConversations().find((item) => item.id === activeId);
    if (active) restore(active);
  }, [restore, searchParams, startNew]);

  // 侧栏切换对话时不会重新挂载组件，这里跟随查询参数切换。
  useEffect(() => {
    const targetId = searchParams.get("c");
    if (!targetId || targetId === activeIdRef.current) return;
    const found = loadConversations().find((item) => item.id === targetId);
    if (found) restore(found);
  }, [restore, searchParams]);

  useEffect(() => {
    const createNew = searchParams.get("new");
    if (!createNew) return;
    startNew();
    navigate("/app", { replace: true });
  }, [navigate, searchParams, startNew]);

  useEffect(() => {
    const prefill = location.state?.prefill;
    if (!prefill) return;
    setInput(prefill);
    navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  const orderId = useMemo(
    () => pending?.order_id || lastOrderId(messages.filter((message) => !message.streaming)),
    [messages, pending],
  );

  useEffect(() => {
    if (!orderId) {
      setOrder(null);
      return undefined;
    }
    let alive = true;
    get(`/orders/${orderId}`)
      .then((data) => alive && setOrder(data))
      .catch(() => alive && setOrder(null));
    return () => {
      alive = false;
    };
  }, [orderId]);

  const appendMessage = (message) => setMessages((current) => [...current, message]);

  const send = async (raw) => {
    const text = raw.trim();
    if (!text || sending) return;

    const assistantId = newId();
    appendMessage({ id: newId(), role: "user", text });
    appendMessage({ id: assistantId, role: "assistant", text: "", streaming: true });
    setInput("");
    setSending(true);
    setBanner("");

    let currentSession = session;
    try {
      let done;
      try {
        done = await streamChat({
          message: text,
          sessionId: currentSession?.sessionId,
          threadId: currentSession?.threadId,
          onToken: (token) =>
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId ? { ...message, text: message.text + token } : message,
              ),
            ),
        });
      } catch (error) {
        if (error.code === "CONVERSATION_NOT_FOUND" && currentSession) {
          setBanner("上一段对话已过期，已为你开始新的对话。");
          currentSession = null;
          setSession(null);
          done = await streamChat({
            message: text,
            onToken: (token) =>
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantId ? { ...message, text: message.text + token } : message,
                ),
              ),
          });
        } else {
          throw error;
        }
      }

      setSession({ sessionId: done.session_id, threadId: done.thread_id });
      setPending(done.pending_action ?? null);
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? { ...message, text: done.reply || message.text, streaming: false }
            : message,
        ),
      );
      if (done.warning) setBanner(done.warning);
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                streaming: false,
                error: true,
                text: message.text || error.message || "回复失败，请稍后重试。",
              }
            : message,
        ),
      );
    } finally {
      setSending(false);
    }
  };

  const decide = async (decision) => {
    if (!session || !pending || deciding) return;
    setDeciding(true);
    setBanner("");
    try {
      const result = await post("/confirm", {
        session_id: session.sessionId,
        thread_id: session.threadId,
        confirmation_id: pending.confirmation_id,
        decision,
      });
      setSession({ sessionId: result.session_id, threadId: result.thread_id });
      setPending(result.pending_action ?? null);
      appendMessage({ id: newId(), role: "assistant", text: result.reply });
      if (result.warning) setBanner(result.warning);
    } catch (error) {
      setBanner(error.message || "确认失败，请稍后重试。");
    } finally {
      setDeciding(false);
    }
  };

  const labels = decisionLabels(pending?.tool);
  const steps = flowSteps(pending?.tool);

  return (
    <AppShell>
      <div className="chat-layout">
        <section className="chat-col">
          <header className="chat-head">
            <h1 className="chat-head__title">售后助手</h1>
            <p className="chat-head__subtitle">查询订单与物流，处理售后申请</p>
          </header>

          <div className="chat-scroll" ref={scrollRef}>
            <div className="chat-sample">
              <span className="sample-chip">示例对话</span>
            </div>

            {messages.length === 0 ? (
              <>
                <div className="msg-row">
                  <span className="msg-avatar">
                    <Icon name="chat" size={16} />
                  </span>
                  <div className="bubble bubble--assistant">
                    你好，我是售后助手。可以帮你查询订单、跟踪物流，也可以发起退款或取消申请。涉及资金与订单状态的操作，都会先请你确认。
                  </div>
                </div>
                <div className="msg-row" style={{ paddingLeft: 40, gap: 8 }}>
                  {SUGGESTIONS.map((item) => (
                    <button
                      key={item}
                      className="btn btn--outline btn--sm"
                      type="button"
                      onClick={() => send(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </>
            ) : (
              messages.map((message) =>
                message.role === "user" ? (
                  <div className="msg-row msg-row--user" key={message.id}>
                    <div className="bubble bubble--user">{message.text}</div>
                    <span className="msg-avatar msg-avatar--user">
                      <Icon name="user" size={16} />
                    </span>
                  </div>
                ) : (
                  <div className="msg-row" key={message.id}>
                    <span className="msg-avatar">
                      <Icon name="chat" size={16} />
                    </span>
                    <div className={`bubble ${message.error ? "bubble--error" : "bubble--assistant"}`}>
                      {message.text ? <MessageText text={message.text} /> : null}
                      {message.streaming ? <span className="bubble__cursor" /> : null}
                    </div>
                  </div>
                ),
              )
            )}

            {pending ? (
              <div className="confirm-card">
                <div className="confirm-card__head">
                  <span className="confirm-card__title">{toolTitle(pending.tool)}</span>
                  <StatusPill tone="amber">待确认</StatusPill>
                </div>
                <div className="confirm-card__rows">
                  <div className="kv">
                    <span className="kv__label">订单编号</span>
                    <span className="kv__value">{pending.order_id}</span>
                  </div>
                  <div className="kv">
                    <span className="kv__label">当前状态</span>
                    <span className="kv__value">{order ? orderStatusLabel(order.order.status) : "—"}</span>
                  </div>
                  <div className="kv">
                    <span className="kv__label">实付金额</span>
                    <span className="kv__value">{order ? formatAmount(order.order.paid_amount_cents) : "—"}</span>
                  </div>
                  <div className="kv">
                    <span className="kv__label">操作说明</span>
                    <span className="kv__value">{pending.summary}</span>
                  </div>
                </div>
                <p className="confirm-card__note">确认后才会提交到服务端，并重新校验订单状态与金额。</p>
                <div className="confirm-card__actions">
                  <button
                    className="btn btn--outline btn--sm"
                    type="button"
                    disabled={deciding}
                    onClick={() => decide("reject")}
                  >
                    {labels.reject}
                  </button>
                  <button
                    className="btn btn--primary btn--sm"
                    type="button"
                    disabled={deciding}
                    onClick={() => decide("approve")}
                  >
                    {deciding ? "处理中…" : labels.approve}
                  </button>
                </div>
                <div className="confirm-card__foot">
                  有效期至 {formatClock(pending.expires_at)}，过期后需要重新发起。
                </div>
              </div>
            ) : null}
          </div>

          {banner ? (
            <div style={{ padding: "0 26px 8px" }} className="note">
              <Icon name="info" size={15} />
              {banner}
            </div>
          ) : null}

          <div className="composer">
            <form
              className="composer__box"
              onSubmit={(event) => {
                event.preventDefault();
                send(input);
              }}
            >
              <input
                value={input}
                placeholder="输入你的问题，例如：我的退款进度如何？"
                onChange={(event) => setInput(event.target.value)}
                disabled={sending}
              />
              <button className="composer__send" type="submit" disabled={sending || !input.trim()} aria-label="发送">
                {sending ? <span className="spinner" /> : <Icon name="send" size={18} />}
              </button>
            </form>
            <div className="composer__hint">重要操作将在你确认后执行</div>
          </div>
        </section>

        <aside className="context">
          <h2 className="context__title">关联订单</h2>
          {order ? (
            <>
              <div className="order-mini">
                <div className="order-mini__top">
                  <span className="order-mini__id">{order.order.order_id}</span>
                  <StatusPill tone={orderStatusTone(order.order.status)}>
                    {orderStatusLabel(order.order.status)}
                  </StatusPill>
                </div>
                <div className="order-mini__glyph">
                  <PackageGlyph size={54} />
                </div>
                <div className="order-mini__row">
                  <span>实付金额</span>
                  <strong>{formatAmount(order.order.paid_amount_cents)}</strong>
                </div>
                <div className="order-mini__row">
                  <span>退款资格</span>
                  <strong className={order.refund_eligibility?.eligible ? "ok" : ""}>
                    {order.refund_eligibility?.eligible
                      ? "符合申请条件"
                      : order.refund_eligibility?.reason || "—"}
                  </strong>
                </div>
              </div>

              {pending ? (
                <>
                  <div className="context__divider" />
                  <h2 className="context__title" style={{ fontSize: 15 }}>
                    处理流程
                  </h2>
                  <div className="flow">
                    {steps.map((step) => (
                      <div
                        key={step.label}
                        className={`flow__step${step.state ? ` flow__step--${step.state}` : ""}`}
                      >
                        <span className="flow__dot" />
                        {step.label}
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
            </>
          ) : (
            <div className="empty">发送一条消息后，这里会显示关联订单与处理流程。</div>
          )}
        </aside>
      </div>
    </AppShell>
  );
}
