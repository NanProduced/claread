import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, formatInteger, toNumber } from "../shared/format.js";
import { buildItemsPath, fetchJson } from "../shared/query.js";
import { chip, metric, panelShell } from "../shared/ui.js";

const CAPABILITY_LABELS = {
  analysis_full: "主解析",
  analysis_overview_hint: "Overview",
  rag_embedding: "RAG Embedding",
  rag_rerank: "RAG Rerank",
};

const DEFAULT_CAPABILITIES = [
  "analysis_full",
  "analysis_overview_hint",
  "rag_embedding",
  "rag_rerank",
];

function resolveBreakdownConfig(mode) {
  if (mode === "model") {
    return {
      groupBy: ["model_provider", "model_name"],
      label: "模型",
      labelWidth: "minmax(180px, 1.4fr)",
      rowKey(row) {
        return `${row.model_provider || "unknown"}:${row.model_name || "unknown"}`;
      },
      rowLabel(row) {
        return row.model_name || row.model_provider || "未记录模型";
      },
      rowDetail(row) {
        return row.model_provider || "provider 未记录";
      },
      tone: "muted",
    };
  }

  if (mode === "provider") {
    return {
      groupBy: ["model_provider"],
      label: "Provider",
      labelWidth: "minmax(140px, 1.2fr)",
      rowKey(row) {
        return row.model_provider || "unknown";
      },
      rowLabel(row) {
        return row.model_provider || "未记录 provider";
      },
      rowDetail() {
        return "";
      },
      tone: "muted",
    };
  }

  return {
    groupBy: ["capability_code"],
    label: "Capability",
    labelWidth: "96px",
    rowKey(row) {
      return row.capability_code || "unknown";
    },
    rowLabel(row) {
      return CAPABILITY_LABELS[row.capability_code] ?? row.capability_code ?? "unknown";
    },
    rowDetail() {
      return "";
    },
    tone: "info",
  };
}

function normalizeAggregate(rows) {
  const row = Array.isArray(rows) ? rows[0] : null;
  return {
    count: toNumber(row?.count),
    totalTokens: toNumber(row?.sum?.total_tokens),
    billedPoints: toNumber(row?.sum?.billed_points),
  };
}

function normalizeSplit(rows, config) {
  return (Array.isArray(rows) ? rows : []).map((row) => ({
    key: config.rowKey(row),
    label: config.rowLabel(row),
    detail: config.rowDetail(row),
    tone: config.tone,
    count: toNumber(row.count),
    totalTokens: toNumber(row.sum?.total_tokens),
    billedPoints: toNumber(row.sum?.billed_points),
  })).sort((left, right) => right.totalTokens - left.totalTokens || right.count - left.count);
}

function renderSplit(rows, config) {
  return h(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "8px",
      },
    },
    rows.map((row) =>
      h(
        "div",
        {
          key: row.key,
          style: {
            display: "grid",
            gridTemplateColumns: `${config.labelWidth} 72px 1fr 1fr`,
            gap: "8px",
            alignItems: "center",
            minWidth: "0",
          },
        },
        [
          h(
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
              chip(row.label, row.tone),
              row.detail
                ? h(
                    "span",
                    {
                      style: {
                        color: "var(--theme--foreground-subdued, #6B7280)",
                        fontSize: "11px",
                        lineHeight: "16px",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      },
                    },
                    row.detail,
                  )
                : null,
            ].filter(Boolean),
          ),
          h(
            "span",
            {
              style: {
                color: "var(--theme--foreground-subdued, #6B7280)",
                fontSize: "12px",
                lineHeight: "18px",
                whiteSpace: "nowrap",
              },
            },
            `${formatCompact(row.count)} 次`,
          ),
          h(
            "span",
            {
              style: {
                color: "var(--theme--foreground-subdued, #6B7280)",
                fontSize: "12px",
                lineHeight: "18px",
                whiteSpace: "nowrap",
              },
            },
            `${formatCompact(row.totalTokens)} tok`,
          ),
          h(
            "span",
            {
              style: {
                color: "var(--theme--foreground-subdued, #6B7280)",
                fontSize: "12px",
                lineHeight: "18px",
                textAlign: "right",
                whiteSpace: "nowrap",
              },
            },
            `${formatInteger(row.billedPoints)} pts`,
          ),
        ],
      ),
    ),
  );
}

const UsageTotalPanel = defineComponent({
  props: {
    targetUrl: {
      type: String,
      default: "/admin/content/ai_usage_events",
    },
    breakdownMode: {
      type: String,
      default: "capability",
    },
  },
  setup(props) {
    const loading = ref(false);
    const error = ref("");
    const total = ref({ count: 0, totalTokens: 0, billedPoints: 0 });
    const split = ref([]);

    const load = async () => {
      loading.value = true;
      error.value = "";

      const filter = {
        capability_code: {
          _in: DEFAULT_CAPABILITIES,
        },
      };
      const config = resolveBreakdownConfig(props.breakdownMode);

      try {
        const [aggregateRows, splitRows] = await Promise.all([
          fetchJson(
            buildItemsPath("ai_usage_events", {
              aggregate: {
                count: "*",
                sum: ["total_tokens", "billed_points"],
              },
              filter,
            }),
          ),
          fetchJson(
            buildItemsPath("ai_usage_events", {
              aggregate: {
                count: "*",
                sum: ["total_tokens", "billed_points"],
              },
              groupBy: config.groupBy,
              filter,
            }),
          ),
        ]);

        total.value = normalizeAggregate(aggregateRows);
        split.value = normalizeSplit(splitRows, config);
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => props.breakdownMode, load);

    return () => {
      const config = resolveBreakdownConfig(props.breakdownMode);
      return panelShell(
        h(
          "a",
          {
            href: props.targetUrl,
            style: {
              display: "flex",
              flexDirection: "column",
              gap: "12px",
              minHeight: "0",
              color: "inherit",
              textDecoration: "none",
            },
          },
          [
            h(
              "div",
              {
                style: {
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
                  gap: "12px",
                },
              },
              [
                metric("调用", formatCompact(total.value.count), "解析相关"),
                metric("Tokens", formatCompact(total.value.totalTokens), "total_tokens"),
                metric("积分", formatInteger(total.value.billedPoints), "billed_points"),
              ],
            ),
            renderSplit(split.value, config),
          ],
        ),
        {
          loading: loading.value,
          error: error.value,
          empty: total.value.count === 0,
        },
      );
    };
  },
});

export default {
  id: "claread-parse-run-usage-total",
  name: "Parse Usage Total",
  icon: "toll",
  description: "解析相关 AI usage 汇总。",
  component: UsageTotalPanel,
  options: [
    {
      field: "targetUrl",
      name: "Target URL",
      type: "string",
      meta: {
        interface: "input",
        width: "full",
      },
      schema: {
        default_value: "/admin/content/ai_usage_events",
      },
    },
    {
      field: "breakdownMode",
      name: "Breakdown Mode",
      type: "string",
      meta: {
        interface: "select-dropdown",
        width: "full",
        options: {
          choices: [
            { text: "Capability", value: "capability" },
            { text: "Model", value: "model" },
            { text: "Provider", value: "provider" },
          ],
        },
      },
      schema: {
        default_value: "capability",
      },
    },
  ],
  minWidth: 8,
  minHeight: 5,
};
