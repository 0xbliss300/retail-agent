export const ORDER_STATUS = {
  unpaid: "未付款",
  paid: "已付款",
  shipped: "已发货",
  delivered: "已送达",
  canceled: "已取消",
  refunded: "已退款",
};

export const REFUND_STATUS = {
  pending: "待处理",
  approved: "审核通过",
  rejected: "审核拒绝",
  completed: "已完成",
  failed: "结算失败",
  withdrawn: "已撤回",
};

export const TOOL_LABEL = {
  create_refund: "提交退款申请",
  cancel_order: "取消订单",
  cancel_refund: "撤回退款申请",
};

export function formatAmount(cents) {
  if (cents === null || cents === undefined) return "—";
  return `¥${(cents / 100).toFixed(2)}`;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

export function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatShortDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatClock(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function orderStatusLabel(status) {
  return ORDER_STATUS[status] || status || "—";
}

export function refundStatusLabel(status) {
  return REFUND_STATUS[status] || status || "—";
}

export function orderStatusTone(status) {
  if (status === "unpaid" || status === "canceled") return "neutral";
  if (status === "refunded") return "blue";
  return "blue";
}

export function refundStatusTone(status) {
  if (status === "pending" || status === "failed") return "amber";
  if (status === "withdrawn" || status === "rejected") return "neutral";
  return "blue";
}

/**
 * 从聊天文本里取最近提到的订单号，用于右侧关联订单面板。
 */
export function lastOrderId(messages) {
  const pattern = /ORD-\d+/g;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const found = messages[index].text?.match(pattern);
    if (found) return found[found.length - 1];
  }
  return null;
}
