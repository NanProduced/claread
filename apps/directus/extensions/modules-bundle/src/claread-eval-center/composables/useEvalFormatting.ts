/**
 * Eval Center 通用格式化工具。
 *
 * 取代 RunHistoryMode.vue 与 useNodeLabFormatting.ts 之前各自实现的
 * formatDateTime / shortId / statusLabel / statusTone 副本。所有
 * eval-center 子模块都应该从这里 import,避免重复实现之间的飘移。
 */

export type StatusTone = "success" | "warning" | "danger" | "attention" | "active" | "neutral";

const STATUS_LABELS: Readonly<Record<string, string>> = {
  succeeded: "成功",
  failed: "失败",
  timeout: "超时",
  cancelled: "已取消",
  queued: "排队中",
  running: "运行中",
  complete: "完整完成",
  partial_failure: "部分失败",
  total_failure: "全部失败",
  drafting: "草稿中",
  active: "进行中",
  paused: "已暂停",
  reviewed: "已复盘",
  archived: "已归档",
  unreviewed: "未评审",
};

const SUCCESS_TONES: ReadonlySet<string> = new Set(["succeeded", "complete", "reviewed"]);
const WARNING_TONES: ReadonlySet<string> = new Set(["partial_failure", "paused", "queued", "running", "active"]);
const DANGER_TONES: ReadonlySet<string> = new Set(["failed", "total_failure", "cancelled"]);
const ATTENTION_TONES: ReadonlySet<string> = new Set(["timeout"]);

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "未记录";
  return STATUS_LABELS[status] || status;
}

export function statusTone(status: string | null | undefined): StatusTone {
  if (!status) return "neutral";
  if (SUCCESS_TONES.has(status)) return "success";
  if (WARNING_TONES.has(status)) return "warning";
  if (DANGER_TONES.has(status)) return "danger";
  if (ATTENTION_TONES.has(status)) return "attention";
  return "neutral";
}

export type ShortIdSide = "end" | "start";

/**
 * 截断字符串用作短 id 显示。null / undefined / 空字符串返回 "—"
 * (与 useNodeLabFormatting.ts 旧版一致,避免 UI 出现空 cell)。
 */
export function shortId(value: string | null | undefined, length: number = 8, side: ShortIdSide = "end"): string {
  const normalized = String(value ?? "").trim();
  if (!normalized) return "—";
  if (normalized.length <= length) return normalized;
  return side === "start" ? normalized.slice(0, length) : normalized.slice(-length);
}

/**
 * 用 zh-CN locale 格式化 ISO 时间。无值 / 不可解析时分别返回 "未记录" / 原值。
 * 在浏览器 Intl 不支持时回退到 toLocaleString。
 */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  if (!value) return "未记录";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  } catch {
    return date.toLocaleString();
  }
}
