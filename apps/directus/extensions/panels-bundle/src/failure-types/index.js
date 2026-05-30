import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, formatDateTime, truncateText } from "../shared/format.js";
import { fetchSummary, DEFAULT_SUMMARY_URL } from "../shared/summary.js";
import { chip, panelShell } from "../shared/ui.js";

function renderFailureRows(rows, targetUrl) {
  const max = Math.max(1, ...rows.map((row) => row.count));

  return h(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "9px",
        minHeight: "0",
        overflow: "auto",
      },
    },
    rows.map((row) =>
      h(
        "a",
        {
          key: row.failure_code,
          href: targetUrl,
          title: `${row.failure_code} · 最近 ${formatDateTime(row.latest_queued_at)}`,
          style: {
            display: "grid",
            gridTemplateColumns: "minmax(120px, 1.1fr) 1fr 48px",
            alignItems: "center",
            gap: "8px",
            color: "inherit",
            textDecoration: "none",
            minWidth: "0",
          },
        },
        [
          h(
            "div",
            { style: { minWidth: "0" } },
            [
              h(
                "span",
                {
                  style: {
                    display: "block",
                    color: "#245CB8",
                    fontSize: "12px",
                    lineHeight: "18px",
                    fontWeight: "650",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  },
                },
                truncateText(row.failure_code, 34),
              ),
              h(
                "div",
                {
                  style: {
                    color: "var(--theme--foreground-subdued, #6B7280)",
                    fontSize: "11px",
                    lineHeight: "16px",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  },
                },
                `主 ${row.analysis_count} / Overview ${row.overview_count}`,
              ),
            ],
          ),
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
                background: "#BE123C",
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
            formatCompact(row.count),
          ),
        ],
      ),
    ),
  );
}

const FailureTypesPanel = defineComponent({
  props: {
    endpointUrl: {
      type: String,
      default: DEFAULT_SUMMARY_URL,
    },
    targetUrl: {
      type: String,
      default: "/admin/content/analysis_tasks/+?bookmark=Analysis%20Tasks%20%2F%20Failed",
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
        rows.value = Array.isArray(data?.failure_types) ? data.failure_types : [];
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => props.endpointUrl, load);

    return () =>
      panelShell(
        h("div", { style: { display: "flex", flexDirection: "column", gap: "12px", minHeight: "0" } }, [
          h("div", { style: { display: "flex", justifyContent: "space-between", gap: "8px" } }, [
            chip("失败 Top 5", rows.value.length > 0 ? "danger" : "muted"),
            chip("近 7 天", "muted"),
          ]),
          renderFailureRows(rows.value, props.targetUrl),
        ]),
        { loading: loading.value, error: error.value, empty: rows.value.length === 0 },
      );
  },
});

export default {
  id: "claread-parse-run-failure-types",
  name: "Parse Failure Types",
  icon: "report",
  description: "近 7 天失败类型分布。",
  component: FailureTypesPanel,
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
      schema: { default_value: "/admin/content/analysis_tasks/+?bookmark=Analysis%20Tasks%20%2F%20Failed" },
    },
  ],
  minWidth: 12,
  minHeight: 6,
};
