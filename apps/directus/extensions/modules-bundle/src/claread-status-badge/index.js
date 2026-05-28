import { h } from "vue";

const TONE_STYLES = {
  info: {
    background: "#DBEAFE",
    color: "#1D4ED8",
    borderColor: "#93C5FD",
  },
  progress: {
    background: "#E0E7FF",
    color: "#4338CA",
    borderColor: "#A5B4FC",
  },
  success: {
    background: "#DCFCE7",
    color: "#166534",
    borderColor: "#86EFAC",
  },
  warning: {
    background: "#FEF3C7",
    color: "#92400E",
    borderColor: "#FCD34D",
  },
  danger: {
    background: "#FEE2E2",
    color: "#B91C1C",
    borderColor: "#FCA5A5",
  },
  muted: {
    background: "#F3F4F6",
    color: "#4B5563",
    borderColor: "#D1D5DB",
  },
  default: {
    background: "#F8FAFC",
    color: "#0F172A",
    borderColor: "#CBD5E1",
  },
};

const STATUS_MAPS = {
  analysis_status: {
    queued: { label: "排队中", tone: "info" },
    running: { label: "进行中", tone: "progress" },
    finalizing: { label: "收尾中", tone: "progress" },
    ready: { label: "已就绪", tone: "success" },
    partial: { label: "部分完成", tone: "warning" },
    failed: { label: "失败", tone: "danger" },
    deleted: { label: "已删除", tone: "muted" },
    cancelled: { label: "已取消", tone: "muted" },
    expired: { label: "已过期", tone: "muted" },
  },
  user_facing_state: {
    normal: { label: "正常", tone: "success" },
    degraded_light: { label: "轻度降级", tone: "warning" },
    failed: { label: "失败", tone: "danger" },
  },
  task_status: {
    queued: { label: "排队中", tone: "info" },
    running: { label: "执行中", tone: "progress" },
    finalizing: { label: "收尾中", tone: "progress" },
    succeeded: { label: "成功", tone: "success" },
    failed: { label: "失败", tone: "danger" },
    cancelled: { label: "已取消", tone: "muted" },
    expired: { label: "已过期", tone: "muted" },
  },
  usage_status: {
    queued: { label: "排队中", tone: "info" },
    running: { label: "执行中", tone: "progress" },
    succeeded: { label: "成功", tone: "success" },
    failed: { label: "失败", tone: "danger" },
    cancelled: { label: "已取消", tone: "muted" },
    expired: { label: "已过期", tone: "muted" },
  },
};

function inferVariant(field, collection) {
  if (field === "analysis_status") return "analysis_status";
  if (field === "user_facing_state") return "user_facing_state";
  if (field === "status" && collection === "ai_usage_events") return "usage_status";
  if (field === "status") return "task_status";
  return "analysis_status";
}

function renderBadge({ value, field, collection, variant }) {
  const rawValue = value == null || value === "" ? "NULL" : String(value);
  const resolvedVariant = variant || inferVariant(field, collection);
  const mapping = STATUS_MAPS[resolvedVariant]?.[rawValue] ?? {
    label: rawValue,
    tone: "default",
  };
  const toneStyle = TONE_STYLES[mapping.tone] ?? TONE_STYLES.default;
  const title = `raw: ${rawValue}\ndomain: ${resolvedVariant}`;

  return h(
    "div",
    {
      title,
      style: {
        display: "inline-flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "8px",
        minWidth: "0",
      },
    },
    [
      h(
        "span",
        {
          style: {
            display: "inline-flex",
            alignItems: "center",
            minHeight: "24px",
            padding: "0 10px",
            borderRadius: "999px",
            border: `1px solid ${toneStyle.borderColor}`,
            background: toneStyle.background,
            color: toneStyle.color,
            fontWeight: "700",
            fontSize: "12px",
            lineHeight: "20px",
            whiteSpace: "nowrap",
          },
        },
        mapping.label,
      ),
      h(
        "code",
        {
          style: {
            color: "#64748B",
            fontSize: "11px",
            lineHeight: "16px",
            whiteSpace: "nowrap",
          },
        },
        rawValue,
      ),
    ],
  );
}

export default {
  id: "claread-status-badge",
  name: "Claread Status Badge",
  icon: "flag",
  description: "以中文 badge 展示 Claread 状态字段，并保留原始枚举值。",
  types: ["string"],
  options: [
    {
      field: "variant",
      type: "string",
      name: "Status Domain",
      meta: {
        width: "full",
        interface: "select-dropdown",
        options: {
          choices: [
            { text: "Analysis Status", value: "analysis_status" },
            { text: "User Facing State", value: "user_facing_state" },
            { text: "Task Status", value: "task_status" },
            { text: "Usage Status", value: "usage_status" },
          ],
        },
      },
    },
  ],
  component: function StatusBadgeDisplay({ value, field, collection, variant }) {
    return renderBadge({ value, field, collection, variant });
  },
};
