import { h } from "vue";

const TONES = {
  info: {
    background: "#DBEAFE",
    color: "#1D4ED8",
    borderColor: "#93C5FD",
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
    background: "#F1F5F9",
    color: "#475569",
    borderColor: "#CBD5E1",
  },
  default: {
    background: "#F8FAFC",
    color: "#0F172A",
    borderColor: "#CBD5E1",
  },
};

const VARIANT_MAPS = {
  client_source_from_id: {
    web: { label: "Web", tone: "info" },
    task: { label: "小程序", tone: "success" },
    unknown: { label: "未知", tone: "muted" },
  },
  usage_scope: {
    user_billed: { label: "用户计费", tone: "info" },
    system_internal: { label: "系统内部", tone: "muted" },
    anonymous_trial: { label: "匿名试用", tone: "warning" },
    eval_debug: { label: "评测调试", tone: "warning" },
  },
  billing_mode: {
    user_points: { label: "积分计费", tone: "info" },
    internal_only: { label: "内部结算", tone: "muted" },
    trial: { label: "试用", tone: "warning" },
    no_charge: { label: "不计费", tone: "muted" },
  },
  capability_code: {
    analysis_full: { label: "全文解析", tone: "info" },
    analysis_overview_hint: { label: "概览提示派生", tone: "warning" },
    dict_ai_lookup: { label: "词典 AI 查询", tone: "info" },
    reader_ask: { label: "阅读问答", tone: "info" },
    daily_reader_pipeline: { label: "每日精读流水线", tone: "muted" },
    daily_reader_scoring: { label: "每日精读评分", tone: "muted" },
  },
  event_type: {
    task_submitted: { label: "已提交", tone: "info" },
    task_started: { label: "已开始", tone: "info" },
    task_finalizing: { label: "结果收口中", tone: "warning" },
    task_succeeded: { label: "已完成", tone: "success" },
    task_failed: { label: "已失败", tone: "danger" },
    task_cancelled: { label: "已取消", tone: "muted" },
    task_requeued: { label: "已重试入队", tone: "warning" },
    task_recovered_succeeded: { label: "恢复完成", tone: "success" },
  },
};

function normalizeString(value) {
  if (value == null) return "";
  return String(value).trim();
}

export function inferClientSource(clientRecordId) {
  const raw = normalizeString(clientRecordId);
  if (!raw) {
    return {
      key: "unknown",
      raw,
      ...VARIANT_MAPS.client_source_from_id.unknown,
    };
  }

  const prefix = raw.includes("-") ? raw.split("-")[0] : raw;
  const mapped =
    VARIANT_MAPS.client_source_from_id[prefix] ?? VARIANT_MAPS.client_source_from_id.unknown;
  return {
    key: prefix,
    raw,
    ...mapped,
  };
}

export function getEnumPresentation(value, variant) {
  if (variant === "client_source_from_id") {
    return inferClientSource(value);
  }

  const raw = normalizeString(value);
  const mapped = VARIANT_MAPS[variant]?.[raw];
  if (mapped) return { key: raw, raw, ...mapped };

  return {
    key: raw || "unknown",
    raw,
    label: raw || "NULL",
    tone: raw ? "default" : "muted",
  };
}

export function renderBadge({ label, tone, raw, showRaw = true }) {
  const toneStyle = TONES[tone] ?? TONES.default;

  return h(
    "div",
    {
      title: raw || label,
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
        label,
      ),
      showRaw
        ? h(
            "code",
            {
              style: {
                color: "#64748B",
                fontSize: "11px",
                lineHeight: "16px",
                whiteSpace: "nowrap",
              },
            },
            raw || "NULL",
          )
        : null,
    ].filter(Boolean),
  );
}

export function renderMappedBadge(value, variant, showRaw = true) {
  const presentation = getEnumPresentation(value, variant);
  return renderBadge({
    label: presentation.label,
    tone: presentation.tone,
    raw: presentation.raw,
    showRaw,
  });
}
