import { defineComponent, h, onMounted, ref } from "vue";

import { formatDateTime, shortId, truncateText } from "../shared/format.js";
import { fetchJson } from "../shared/query.js";
import { chip, link, panelShell } from "../shared/ui.js";

function laneLabel(lane) {
  if (lane === "analysis") return "主解析";
  if (lane === "overview") return "Overview";
  return lane || "未知";
}

function renderRows(rows) {
  return h(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        minHeight: "0",
        overflow: "auto",
        border: "1px solid var(--theme--border-color-subdued, #E3E7EE)",
        borderRadius: "8px",
        background: "var(--theme--background, #FFFFFF)",
      },
    },
    rows.map((row, index) =>
      h(
        "div",
        {
          key: `${row.lane}:${row.task_id}`,
          style: {
            display: "grid",
            gridTemplateColumns: "84px minmax(110px, 1.4fr) minmax(96px, 1fr) 88px",
            gap: "10px",
            alignItems: "center",
            padding: "9px 10px",
            borderTop: index === 0 ? "none" : "1px solid var(--theme--border-color-subdued, #E3E7EE)",
            minWidth: "0",
          },
        },
        [
          chip(laneLabel(row.lane), row.lane === "analysis" ? "danger" : "warning"),
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
              link(
                row.record_title || `Record ${shortId(row.record_id)}`,
                row.inspector_url || row.detail_url,
                row.record_title || row.record_id,
              ),
              h(
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
                row.client_record_id || row.record_id || "未关联记录",
              ),
            ],
          ),
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
              link(row.failure_code || "failed", row.detail_url, row.failure_code || row.task_id),
              h(
                "span",
                {
                  title: row.failure_message || "",
                  style: {
                    color: "var(--theme--foreground-subdued, #6B7280)",
                    fontSize: "11px",
                    lineHeight: "16px",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  },
                },
                truncateText(row.failure_message || "无失败信息"),
              ),
            ],
          ),
          h(
            "span",
            {
              style: {
                color: "var(--theme--foreground-subdued, #6B7280)",
                fontSize: "11px",
                lineHeight: "16px",
                textAlign: "right",
                whiteSpace: "nowrap",
              },
            },
            formatDateTime(row.queued_at),
          ),
        ],
      ),
    ),
  );
}

const RecentFailuresPanel = defineComponent({
  props: {
    endpointUrl: {
      type: String,
      default: "/parse-run-observability/recent-failures?limit=5",
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
        const data = await fetchJson(props.endpointUrl);
        rows.value = Array.isArray(data) ? data : [];
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);

    return () =>
      panelShell(renderRows(rows.value), {
        loading: loading.value,
        error: error.value,
        empty: rows.value.length === 0,
      });
  },
});

export default {
  id: "claread-parse-run-recent-failures",
  name: "Recent Parse Failures",
  icon: "error",
  description: "合并展示最近失败的主解析和 Overview 任务。",
  component: RecentFailuresPanel,
  options: [
    {
      field: "endpointUrl",
      name: "Endpoint URL",
      type: "string",
      meta: {
        interface: "input",
        width: "full",
      },
      schema: {
        default_value: "/parse-run-observability/recent-failures?limit=5",
      },
    },
  ],
  minWidth: 12,
  minHeight: 6,
};
