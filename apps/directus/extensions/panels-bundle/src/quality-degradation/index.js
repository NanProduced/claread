import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, formatPercent } from "../shared/format.js";
import { fetchSummary, DEFAULT_SUMMARY_URL, stateLabel, toneForState } from "../shared/summary.js";
import { chip, metric, panelShell } from "../shared/ui.js";

function rowCount(rows, state) {
  return rows.find((row) => row.state === state)?.count ?? 0;
}

function renderRows(rows) {
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  const max = Math.max(1, ...rows.map((row) => row.count));

  return h(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: "8px", minHeight: "0", overflow: "auto" } },
    rows.map((row) =>
      h(
        "div",
        {
          key: row.state,
          title: `${stateLabel(row.state)}: ${row.count}`,
          style: {
            display: "grid",
            gridTemplateColumns: "108px 1fr 54px",
            gap: "8px",
            alignItems: "center",
          },
        },
        [
          chip(stateLabel(row.state), toneForState(row.state)),
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
                width: `${Math.max(4, Math.round((row.count / max) * 100))}%`,
                height: "100%",
                borderRadius: "999px",
                background:
                  toneForState(row.state) === "danger"
                    ? "#BE123C"
                    : toneForState(row.state) === "warning"
                      ? "#9A5B00"
                      : toneForState(row.state) === "success"
                        ? "#11795B"
                        : "#94A3B8",
              },
            }),
          ),
          h(
            "strong",
            {
              style: {
                color: "var(--theme--foreground, #172940)",
                fontSize: "12px",
                lineHeight: "18px",
                textAlign: "right",
              },
            },
            total > 0 ? formatPercent(row.count / total) : "0%",
          ),
        ],
      ),
    ),
  );
}

const QualityDegradationPanel = defineComponent({
  props: {
    endpointUrl: {
      type: String,
      default: DEFAULT_SUMMARY_URL,
    },
    targetUrl: {
      type: String,
      default: "/admin/content/analysis_records",
    },
  },
  setup(props) {
    const loading = ref(false);
    const error = ref("");
    const rows = ref([]);

    const load = async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = await fetchSummary(props.endpointUrl);
        rows.value = Array.isArray(data?.quality?.rows) ? data.quality.rows : [];
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => props.endpointUrl, load);

    return () => {
      const total = rows.value.reduce((sum, row) => sum + row.count, 0);
      const degraded = rowCount(rows.value, "degraded_light") + rowCount(rows.value, "degraded_heavy");
      const failed = rowCount(rows.value, "failed");

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
                  gridTemplateColumns: "repeat(auto-fit, minmax(92px, 1fr))",
                  gap: "12px",
                },
              },
              [
                metric("记录", formatCompact(total), "近 7 天"),
                metric("降级", formatCompact(degraded), degraded > 0 ? "成功但需关注" : "暂无降级"),
                metric("失败态", formatCompact(failed), failed > 0 ? "不可用结果" : "暂无失败"),
              ],
            ),
            renderRows(rows.value),
          ],
        ),
        { loading: loading.value, error: error.value, empty: rows.value.length === 0 },
      );
    };
  },
});

export default {
  id: "claread-parse-run-quality-degradation",
  name: "Parse Quality Degradation",
  icon: "rule",
  description: "解析结果质量降级分布。",
  component: QualityDegradationPanel,
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
      schema: { default_value: "/admin/content/analysis_records" },
    },
  ],
  minWidth: 12,
  minHeight: 6,
};
