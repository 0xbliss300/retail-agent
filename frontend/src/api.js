// 开发时经 Vite 代理走 /api；构建后由 FastAPI 直接托管，接口在根路径。
const BASE = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "/api" : "");

async function toError(response) {
  let detail = null;
  try {
    detail = (await response.json())?.detail;
  } catch {
    // 非 JSON 错误响应直接使用默认文案。
  }
  const isObject = detail !== null && typeof detail === "object";
  const error = new Error(isObject && detail.message ? detail.message : "请求失败，请稍后重试。");
  error.code = isObject ? detail.error : undefined;
  error.status = response.status;
  return error;
}

export async function get(path) {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) throw await toError(response);
  return response.json();
}

export async function post(path, body) {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) throw await toError(response);
  return response.json();
}

/**
 * 逐块读取 SSE，把 token 交给回调；返回 done 事件里的完整结果。
 */
export async function streamChat({ message, sessionId, threadId, onToken }) {
  const response = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId ?? null,
      thread_id: threadId ?? null,
    }),
  });
  if (!response.ok) throw await toError(response);
  if (!response.body) throw new Error("当前浏览器不支持流式响应。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done = null;

  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");

      const eventName = /^event: (.+)$/m.exec(raw)?.[1];
      const dataLine = /^data: (.*)$/m.exec(raw)?.[1];
      if (!eventName || !dataLine) continue;

      const payload = JSON.parse(dataLine);
      if (eventName === "token") {
        onToken(payload.text);
      } else if (eventName === "done") {
        done = payload;
      } else if (eventName === "error") {
        const error = new Error(payload.message || "模型调用失败，请稍后重试。");
        error.code = payload.error;
        throw error;
      }
    }
  }

  return done;
}
