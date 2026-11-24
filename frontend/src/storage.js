const CONVERSATIONS_KEY = "retailops.conversations";
const ACTIVE_KEY = "retailops.activeConversation";
const ACCOUNT_KEY = "retailops.account";

const listeners = new Set();

function notify() {
  listeners.forEach((listener) => listener());
}

function read(key, fallback) {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    // 浏览器禁用本地存储时保持内存状态即可。
    return false;
  }
}

export function setConversationAccount(userId) {
  try {
    // 旧版历史没有账号归属，不能恢复到登录后的工作台。
    for (const key of ["retailops.profile", CONVERSATIONS_KEY, ACTIVE_KEY]) {
      window.localStorage.removeItem(key);
    }
    if (userId && read(ACCOUNT_KEY, null) === userId) return;
    window.sessionStorage.removeItem(CONVERSATIONS_KEY);
    window.sessionStorage.removeItem(ACTIVE_KEY);
    write(ACCOUNT_KEY, userId);
  } catch {
    // 浏览器禁用存储时，页面仍可使用当前对话。
  }
}

export function loadConversations() {
  return read(CONVERSATIONS_KEY, []);
}

export function saveConversations(conversations) {
  const saved = write(CONVERSATIONS_KEY, conversations.slice(0, 6));
  if (saved) notify();
  return saved;
}

export function loadActiveConversationId() {
  return read(ACTIVE_KEY, null);
}

export function saveActiveConversationId(id) {
  write(ACTIVE_KEY, id);
}

export function subscribeConversations(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function createConversationId() {
  return `conv_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export function titleFromMessage(text) {
  const clean = (text || "").replace(/\s+/g, " ").trim();
  if (!clean) return "新对话";
  return clean.length > 14 ? `${clean.slice(0, 14)}…` : clean;
}
