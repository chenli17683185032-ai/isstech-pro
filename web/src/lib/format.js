export function formatDateTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "--";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const statusLabels = {
  ready: "就绪",
  validated: "已校验",
  extracted: "待确认",
  needs_review: "需复核",
  pending: "待处理",
  confirmed: "已确认",
  rejected: "已拒绝",
  not_proposed: "未建议",
  succeeded: "成功",
  failed: "失败",
  running: "运行中",
};

export function statusLabel(value) {
  return statusLabels[value] || value || "未知";
}
