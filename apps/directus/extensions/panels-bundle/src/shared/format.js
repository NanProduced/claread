export function toNumber(value) {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function formatCompact(value) {
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(toNumber(value));
}

export function formatInteger(value) {
  return new Intl.NumberFormat("en-US").format(toNumber(value));
}

export function formatDateTime(value) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);

  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function shortId(value, length = 8) {
  const raw = value == null ? "" : String(value);
  return raw.length <= length ? raw : raw.slice(0, length);
}

export function truncateText(value, maxLength = 88) {
  const raw = value == null ? "" : String(value);
  return raw.length <= maxLength ? raw : `${raw.slice(0, maxLength)}...`;
}

export function formatDayLabel(date) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function formatPercent(value) {
  return `${Math.round(toNumber(value) * 100)}%`;
}

export function formatDurationSeconds(value) {
  const seconds = toNumber(value);
  if (seconds <= 0) return "0 秒";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`;
  return `${(seconds / 86400).toFixed(1)} 天`;
}
