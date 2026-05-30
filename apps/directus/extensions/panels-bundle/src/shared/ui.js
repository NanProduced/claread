import { h } from "vue";

export const PANEL_COLORS = {
  foreground: "var(--theme--foreground, #172940)",
  subdued: "var(--theme--foreground-subdued, #6B7280)",
  border: "var(--theme--border-color-subdued, #E3E7EE)",
  background: "var(--theme--background, #FFFFFF)",
  page: "var(--theme--background-subdued, #F5F7FA)",
};

export const STATUS_META = {
  failed: { label: "失败", tone: "danger", order: 1 },
  running: { label: "运行中", tone: "info", order: 2 },
  finalizing: { label: "收尾中", tone: "info", order: 3 },
  queued: { label: "排队中", tone: "warning", order: 4 },
  succeeded: { label: "成功", tone: "success", order: 5 },
  cancelled: { label: "已取消", tone: "muted", order: 6 },
  expired: { label: "已过期", tone: "muted", order: 7 },
};

const TONES = {
  muted: {
    background: "#F8FAFC",
    border: "#CBD5E1",
    color: "#475569",
  },
  info: {
    background: "#EEF5FF",
    border: "#BFDBFE",
    color: "#245CB8",
  },
  success: {
    background: "#ECFDF3",
    border: "#A7F3D0",
    color: "#11795B",
  },
  warning: {
    background: "#FFF7E8",
    border: "#FCD34D",
    color: "#9A5B00",
  },
  danger: {
    background: "#FFF1F2",
    border: "#FECDD3",
    color: "#BE123C",
  },
};

export function statusLabel(value) {
  return STATUS_META[value]?.label ?? String(value || "未记录");
}

export function statusTone(value) {
  return STATUS_META[value]?.tone ?? "muted";
}

export function sortStatuses(rows) {
  return [...rows].sort((left, right) => {
    const leftOrder = STATUS_META[left.status]?.order ?? 99;
    const rightOrder = STATUS_META[right.status]?.order ?? 99;
    return leftOrder - rightOrder || String(left.status).localeCompare(String(right.status));
  });
}

export function panelShell(children, { loading, error, empty } = {}) {
  return h(
    "div",
    {
      style: {
        height: "100%",
        minHeight: "0",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        padding: "12px",
        color: PANEL_COLORS.foreground,
        overflow: "hidden",
      },
    },
    [
      loading ? stateText("正在加载...") : null,
      error ? errorBox(error) : null,
      !loading && !error && empty ? stateText("当前没有可展示的数据。") : null,
      !loading && !error && !empty ? children : null,
    ].filter(Boolean),
  );
}

export function stateText(text) {
  return h(
    "div",
    {
      style: {
        color: PANEL_COLORS.subdued,
        fontSize: "13px",
        lineHeight: "20px",
      },
    },
    text,
  );
}

export function errorBox(message) {
  return h(
    "div",
    {
      style: {
        padding: "10px 12px",
        borderRadius: "8px",
        border: "1px solid #FECDD3",
        background: "#FFF1F2",
        color: "#BE123C",
        fontSize: "12px",
        lineHeight: "18px",
      },
    },
    `读取失败：${message}`,
  );
}

export function metric(label, value, detail) {
  return h(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "2px",
        minWidth: "0",
      },
    },
    [
      h(
        "span",
        {
          style: {
            color: PANEL_COLORS.subdued,
            fontSize: "11px",
            lineHeight: "16px",
            whiteSpace: "nowrap",
          },
        },
        label,
      ),
      h(
        "strong",
        {
          style: {
            color: PANEL_COLORS.foreground,
            fontSize: "22px",
            lineHeight: "28px",
            fontWeight: "700",
            whiteSpace: "nowrap",
          },
        },
        value,
      ),
      detail
        ? h(
            "span",
            {
              style: {
                color: PANEL_COLORS.subdued,
                fontSize: "11px",
                lineHeight: "16px",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              },
            },
            detail,
          )
        : null,
    ].filter(Boolean),
  );
}

export function chip(text, tone = "muted") {
  const colors = TONES[tone] ?? TONES.muted;
  return h(
    "span",
    {
      style: {
        display: "inline-flex",
        alignItems: "center",
        minHeight: "22px",
        padding: "0 8px",
        borderRadius: "999px",
        border: `1px solid ${colors.border}`,
        background: colors.background,
        color: colors.color,
        fontSize: "11px",
        lineHeight: "16px",
        fontWeight: "650",
        whiteSpace: "nowrap",
      },
    },
    text,
  );
}

export function link(text, href, title = undefined) {
  return h(
    "a",
    {
      href,
      title,
      style: {
        color: "#245CB8",
        textDecoration: "none",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      },
    },
    text,
  );
}
