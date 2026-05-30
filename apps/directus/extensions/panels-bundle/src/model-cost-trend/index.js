import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, formatDayLabel, formatInteger, truncateText } from "../shared/format.js";
import { capabilityLabel, fetchSummary, DEFAULT_SUMMARY_URL, section } from "../shared/summary.js";
import { chip, metric, panelShell } from "../shared/ui.js";

const EMPTY_TREND = { days: [], top_models: [], top_capabilities: [] };

function formatDay(value) {
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? String(value || "") : formatDayLabel(date);
}

function renderTrend(days) {
  const max = Math.max(1, ...days.map((day) => day.total_tokens));

  return h(
    "div",
    {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
        gap: "6px",
        alignItems: "end",
        minHeight: "78px",
      },
    },
    days.map((day) =>
      h(
        "div",
        {
          key: day.day,
          title: `${day.day}: ${formatInteger(day.total_tokens)} tok / ${formatInteger(day.calls)} calls`,
          style: {
            display: "flex",
            flexDirection: "column",
            justifyContent: "flex-end",
            gap: "5px",
            minWidth: "0",
            height: "78px",
          },
        },
        [
          h("div", {
            style: {
              width: "100%",
              minHeight: day.total_tokens > 0 ? "8px" : "2px",
              height: `${Math.max(2, Math.round((day.total_tokens / max) * 48))}px`,
              borderRadius: "6px 6px 2px 2px",
              background: day.total_tokens > 0 ? "#245CB8" : "#D9DEE7",
            },
          }),
          h(
            "span",
            {
              style: {
                color: "var(--theme--foreground-subdued, #6B7280)",
                fontSize: "10px",
                lineHeight: "14px",
                textAlign: "center",
                whiteSpace: "nowrap",
              },
            },
            formatDay(day.day).slice(0, 5),
          ),
        ],
      ),
    ),
  );
}

function renderTopModels(rows) {
  return h(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: "8px", minWidth: "0" } },
    rows.slice(0, 5).map((row) =>
      h(
        "div",
        {
          key: row.key,
          title: `${row.label} · ${row.detail}`,
          style: {
            display: "grid",
            gridTemplateColumns: "minmax(150px, 1fr) 72px 72px",
            gap: "8px",
            alignItems: "center",
            minWidth: "0",
          },
        },
        [
          h(
            "div",
            { style: { minWidth: "0" } },
            [
              h(
                "strong",
                {
                  style: {
                    display: "block",
                    color: "var(--theme--foreground, #172940)",
                    fontSize: "12px",
                    lineHeight: "18px",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  },
                },
                truncateText(row.label, 38),
              ),
              h(
                "span",
                {
                  style: {
                    display: "block",
                    color: "var(--theme--foreground-subdued, #6B7280)",
                    fontSize: "11px",
                    lineHeight: "16px",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  },
                },
                row.detail,
              ),
            ],
          ),
          h("span", { style: numberStyle() }, `${formatCompact(row.total_tokens)} tok`),
          h("span", { style: { ...numberStyle(), textAlign: "right" } }, `${formatCompact(row.calls)} 次`),
        ],
      ),
    ),
  );
}

function renderCapabilities(rows) {
  return h(
    "div",
    { style: { display: "flex", flexWrap: "wrap", gap: "6px" } },
    rows.map((row) => chip(`${capabilityLabel(row.key)} · ${formatCompact(row.total_tokens)} tok`, "info")),
  );
}

function numberStyle() {
  return {
    color: "var(--theme--foreground-subdued, #6B7280)",
    fontSize: "12px",
    lineHeight: "18px",
    whiteSpace: "nowrap",
  };
}

const ModelCostTrendPanel = defineComponent({
  props: {
    endpointUrl: {
      type: String,
      default: DEFAULT_SUMMARY_URL,
    },
    targetUrl: {
      type: String,
      default: "/admin/content/ai_usage_events",
    },
  },
  setup(props) {
    const loading = ref(false);
    const error = ref("");
    const trend = ref(EMPTY_TREND);

    const load = async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = await fetchSummary(props.endpointUrl);
        trend.value = section(data, "model_cost_trend", EMPTY_TREND);
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => props.endpointUrl, load);

    return () => {
      const days = Array.isArray(trend.value.days) ? trend.value.days : [];
      const topModels = Array.isArray(trend.value.top_models) ? trend.value.top_models : [];
      const topCapabilities = Array.isArray(trend.value.top_capabilities) ? trend.value.top_capabilities : [];
      const totalCalls = days.reduce((sum, day) => sum + day.calls, 0);
      const totalTokens = days.reduce((sum, day) => sum + day.total_tokens, 0);
      const totalPoints = days.reduce((sum, day) => sum + day.billed_points, 0);

      return panelShell(
        h(
          "a",
          {
            href: props.targetUrl,
            style: {
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: "16px",
              minHeight: "0",
              color: "inherit",
              textDecoration: "none",
            },
          },
          [
            h("div", { style: { display: "flex", flexDirection: "column", gap: "12px", minWidth: "0" } }, [
              h(
                "div",
                {
                  style: {
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(94px, 1fr))",
                    gap: "12px",
                  },
                },
                [
                  metric("调用", formatCompact(totalCalls), "近 7 天"),
                  metric("Tokens", formatCompact(totalTokens), "parse/RAG"),
                  metric("积分", formatInteger(totalPoints), "billed_points"),
                ],
              ),
              renderTrend(days),
              renderCapabilities(topCapabilities),
            ]),
            h("div", { style: { display: "flex", flexDirection: "column", gap: "10px", minWidth: "0" } }, [
              chip("Top 模型", topModels.length ? "info" : "muted"),
              renderTopModels(topModels),
            ]),
          ],
        ),
        { loading: loading.value, error: error.value, empty: days.length === 0 && topModels.length === 0 },
      );
    };
  },
});

export default {
  id: "claread-parse-run-model-cost-trend",
  name: "Parse Model Cost Trend",
  icon: "query_stats",
  description: "近 7 天模型调用成本趋势。",
  component: ModelCostTrendPanel,
  options: [
    {
      field: "endpointUrl",
      name: "Endpoint URL",
      type: "string",
      meta: { interface: "input", width: "full" },
      schema: { default_value: DEFAULT_SUMMARY_URL },
    },
    {
      field: "targetUrl",
      name: "Target URL",
      type: "string",
      meta: { interface: "input", width: "full" },
      schema: { default_value: "/admin/content/ai_usage_events" },
    },
  ],
  minWidth: 18,
  minHeight: 7,
};
