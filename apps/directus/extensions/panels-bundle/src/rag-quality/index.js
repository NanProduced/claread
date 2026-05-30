import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, formatPercent, toNumber, truncateText } from "../shared/format.js";
import {
  DEFAULT_SUMMARY_URL,
  fetchSummary,
  ragDropReasonLabel,
  ragFallbackReasonLabel,
  ragOutputTypeLabel,
  section,
} from "../shared/summary.js";
import { chip, metric, panelShell } from "../shared/ui.js";

const EMPTY_RAG_QUALITY = {
  overview: {
    snapshots: 0,
    output_type_count: 0,
    rag_hits: 0,
    fallback_count: 0,
    low_confidence_count: 0,
    hit_rate: 0,
  },
  by_output_type: [],
  fallback_reasons: [],
  drop_reasons: [],
};

function formatAverage(value) {
  const numeric = toNumber(value);
  return numeric >= 10 ? numeric.toFixed(0) : numeric.toFixed(1);
}

function renderOutputRows(rows) {
  const max = Math.max(1, ...rows.map((row) => toNumber(row.output_type_count)));

  return h(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: "8px", minWidth: "0" } },
    rows.map((row) => {
      const total = toNumber(row.output_type_count);
      const fallback = toNumber(row.fallback_count);
      const hitRate = toNumber(row.hit_rate);

      return h(
        "div",
        {
          key: row.output_type,
          title: `${row.output_type}: ${formatPercent(hitRate)} hit / ${fallback} fallback`,
          style: {
            display: "grid",
            gridTemplateColumns: "102px minmax(90px, 1fr) 58px 74px",
            gap: "8px",
            alignItems: "center",
            minWidth: "0",
          },
        },
        [
          chip(ragOutputTypeLabel(row.output_type), fallback > 0 ? "warning" : "success"),
          h(
            "div",
            {
              style: {
                height: "8px",
                borderRadius: "999px",
                background: "#F1F5F9",
                overflow: "hidden",
              },
            },
            h("div", {
              style: {
                width: `${Math.max(4, Math.round((total / max) * 100))}%`,
                height: "100%",
                borderRadius: "999px",
                background: fallback > 0 ? "#9A5B00" : "#11795B",
              },
            }),
          ),
          h("span", { style: cellStyle() }, formatPercent(hitRate)),
          h("span", { style: { ...cellStyle(fallback > 0), textAlign: "right" } }, `${formatCompact(fallback)} 回退`),
        ],
      );
    }),
  );
}

function renderReasonRows(rows, labelFn, emptyText) {
  if (!rows.length) {
    return h("div", { style: mutedTextStyle() }, emptyText);
  }

  return h(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: "6px", minWidth: "0" } },
    rows.slice(0, 5).map((row) =>
      h(
        "div",
        {
          key: row.key,
          title: row.key,
          style: {
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) 44px",
            gap: "8px",
            alignItems: "center",
            minWidth: "0",
          },
        },
        [
          h(
            "span",
            {
              style: {
                ...mutedTextStyle(),
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              },
            },
            truncateText(labelFn(row.key), 34),
          ),
          h("strong", { style: { ...cellStyle(), textAlign: "right" } }, formatCompact(row.count)),
        ],
      ),
    ),
  );
}

function renderLatencyFacts(rows) {
  const avg = rows.reduce(
    (acc, row) => {
      acc.ann += toNumber(row.avg_ann_hit_count);
      acc.rerank += toNumber(row.avg_rerank_hit_count);
      acc.selected += toNumber(row.avg_selected_examples);
      acc.embeddingMs += toNumber(row.avg_embedding_latency_ms);
      acc.rerankMs += toNumber(row.avg_rerank_latency_ms);
      return acc;
    },
    { ann: 0, rerank: 0, selected: 0, embeddingMs: 0, rerankMs: 0 },
  );
  const divisor = Math.max(1, rows.length);

  return h(
    "div",
    {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(88px, 1fr))",
        gap: "10px",
      },
    },
    [
      smallFact("ANN", formatAverage(avg.ann / divisor)),
      smallFact("Rerank", formatAverage(avg.rerank / divisor)),
      smallFact("选中", formatAverage(avg.selected / divisor)),
      smallFact("Embedding", `${formatAverage(avg.embeddingMs / divisor)} ms`),
      smallFact("Rerank Lat.", `${formatAverage(avg.rerankMs / divisor)} ms`),
    ],
  );
}

function smallFact(label, value) {
  return h(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: "1px", minWidth: "0" } },
    [
      h("span", { style: mutedTextStyle() }, label),
      h("strong", { style: cellStyle() }, value),
    ],
  );
}

function cellStyle(danger = false) {
  return {
    color: danger ? "#BE123C" : "var(--theme--foreground, #172940)",
    fontSize: "12px",
    lineHeight: "18px",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  };
}

function mutedTextStyle() {
  return {
    color: "var(--theme--foreground-subdued, #6B7280)",
    fontSize: "11px",
    lineHeight: "16px",
  };
}

const RagQualityPanel = defineComponent({
  props: {
    endpointUrl: {
      type: String,
      default: DEFAULT_SUMMARY_URL,
    },
    targetUrl: {
      type: String,
      default: "/admin/content/analysis_debug_snapshots/+?bookmark=Debug%20Snapshots%20%2F%20RAG",
    },
  },
  setup(props) {
    const loading = ref(false);
    const error = ref("");
    const quality = ref(EMPTY_RAG_QUALITY);

    const load = async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = await fetchSummary(props.endpointUrl);
        quality.value = section(data, "rag_quality", EMPTY_RAG_QUALITY);
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => props.endpointUrl, load);

    return () => {
      const overview = quality.value.overview ?? EMPTY_RAG_QUALITY.overview;
      const rows = Array.isArray(quality.value.by_output_type) ? quality.value.by_output_type : [];
      const fallbackReasons = Array.isArray(quality.value.fallback_reasons) ? quality.value.fallback_reasons : [];
      const dropReasons = Array.isArray(quality.value.drop_reasons) ? quality.value.drop_reasons : [];
      const avgRerankHits =
        rows.reduce((sum, row) => sum + toNumber(row.avg_rerank_hit_count), 0) / Math.max(1, rows.length);

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
                  gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
                  gap: "12px",
                },
              },
              [
                metric("命中率", formatPercent(overview.hit_rate), `${formatCompact(overview.rag_hits)} / ${formatCompact(overview.output_type_count)}`),
                metric("Fallback", formatCompact(overview.fallback_count), overview.fallback_count > 0 ? "需要关注" : "暂无回退"),
                metric("低置信度", formatCompact(overview.low_confidence_count), "low_confidence"),
                metric("Rerank 命中", formatAverage(avgRerankHits), `${formatCompact(overview.snapshots)} snapshots`),
              ],
            ),
            renderOutputRows(rows),
            renderLatencyFacts(rows),
            h(
              "div",
              {
                style: {
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                  gap: "14px",
                  minWidth: "0",
                },
              },
              [
                h("div", { style: { display: "flex", flexDirection: "column", gap: "6px", minWidth: "0" } }, [
                  chip("Fallback Reasons", fallbackReasons.length ? "warning" : "muted"),
                  renderReasonRows(fallbackReasons, ragFallbackReasonLabel, "暂无 fallback reason。"),
                ]),
                h("div", { style: { display: "flex", flexDirection: "column", gap: "6px", minWidth: "0" } }, [
                  chip("Drop Reasons", dropReasons.length ? "info" : "muted"),
                  renderReasonRows(dropReasons, ragDropReasonLabel, "暂无淘汰项。"),
                ]),
              ],
            ),
          ],
        ),
        { loading: loading.value, error: error.value, empty: overview.snapshots === 0 && rows.length === 0 },
      );
    };
  },
});

export default {
  id: "claread-parse-run-rag-quality",
  name: "Parse RAG Quality",
  icon: "hub",
  description: "RAG 检索质量、fallback 和淘汰原因。",
  component: RagQualityPanel,
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
      schema: { default_value: "/admin/content/analysis_debug_snapshots/+?bookmark=Debug%20Snapshots%20%2F%20RAG" },
    },
  ],
  minWidth: 12,
  minHeight: 8,
};
