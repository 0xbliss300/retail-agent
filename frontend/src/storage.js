const PROFILE_KEY = "retailops.profile";
const CONVERSATIONS_KEY = "retailops.conversations";
const ACTIVE_KEY = "retailops.activeConversation";

const listeners = new Set();

function notify() {
  listeners.forEach((listener) => listener());
}

function read(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 浏览器禁用本地存储时保持内存状态即可。
  }
}

export function loadProfile() {
  return read(PROFILE_KEY, { name: "小林", email: "" });
}

export function saveProfile(profile) {
  write(PROFILE_KEY, profile);
  notify();
}

export function loadConversations() {
  return read(CONVERSATIONS_KEY, []);
}

export function saveConversations(conversations) {
  write(CONVERSATIONS_KEY, conversations.slice(0, 6));
  notify();
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
