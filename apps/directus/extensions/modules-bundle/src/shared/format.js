export function shortId(value, length = 8) {
  const raw = value == null ? "" : String(value);
  if (raw.length <= length) return raw;
  return raw.slice(0, length);
}

export function formatDateTime(value) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function truncateText(value, maxLength = 240) {
  const raw = value == null ? "" : String(value);
  if (raw.length <= maxLength) return raw;
  return `${raw.slice(0, maxLength)}…`;
}

