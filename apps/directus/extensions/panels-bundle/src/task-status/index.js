import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, toNumber } from "../shared/format.js";
import { buildItemsPath, fetchJson } from "../shared/query.js";
import { chip, metric, panelShell, sortStatuses, statusLabel, statusTone } from "../shared/ui.js";

function normalizeRows(rows) {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => ({
      status: row.status || "unknown",
      count: toNumber(row.count),
    }))
    .filter((row) => row.count > 0);
}

function renderRows(rows) {
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  const sorted = sortStatuses(rows);

  return h(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        minHeight: "0",
        overflow: "auto",
      },
    },
    sorted.map((row) => {
      const percent = total > 0 ? Math.round((row.count / total) * 100) : 0;
      return h(
        "div",
        {
          key: row.status,
          style: {
            display: "grid",
            gridTemplateColumns: "96px 1fr 54px",
            gap: "8px",
            alignItems: "center",
          },
        },
        [
          chip(statusLabel(row.status), statusTone(row.status)),
          h(
            "div",
            {
              style: {
                height: "8px",
                borderRadius: "999px",
                background: "#EEF2F7",
                overflow: "hidden",
              },
            },
            [
              h("div", {
                style: {
                  width: `${percent}%`,
                  height: "100%",
                  borderRadius: "999px",
                  background:
                    statusTone(row.status) === "danger"
                      ? "#BE123C"
                      : statusTone(row.status) === "success"
                        ? "#11795B"
                        : statusTone(row.status) === "warning"
                          ? "#9A5B00"
                          : "#245CB8",
                },
              }),
            ],
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
            `${row.count}`,
          ),
        ],
      );
    }),
  );
}

const TaskStatusPanel = defineComponent({
  props: {
    collection: {
      type: String,
      default: "analysis_tasks",
    },
    laneLabel: {
      type: String,
      default: "主解析任务",
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
        const data = await fetchJson(
          buildItemsPath(props.collection, {
            aggregate: { count: "*" },
            groupBy: ["status"],
          }),
        );
        rows.value = normalizeRows(data);
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => [props.collection, props.laneLabel, props.targetUrl], load);

    return () => {
      const total = rows.value.reduce((sum, row) => sum + row.count, 0);
      const failed = rows.value.find((row) => row.status === "failed")?.count ?? 0;

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
                metric(props.laneLabel, formatCompact(total), "全部状态"),
                metric("失败", formatCompact(failed), failed > 0 ? "需要排查" : "暂无失败"),
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
  id: "claread-parse-run-task-status",
  name: "Parse Task Status",
  icon: "donut_large",
  description: "解析任务状态分布，可配置主任务或 Overview 任务。",
  component: TaskStatusPanel,
  options: [
    {
      field: "collection",
      name: "Collection",
      type: "string",
      meta: {
        interface: "select-dropdown",
        width: "full",
        options: {
          choices: [
            { text: "analysis_tasks", value: "analysis_tasks" },
            { text: "analysis_overview_tasks", value: "analysis_overview_tasks" },
          ],
        },
      },
      schema: {
        default_value: "analysis_tasks",
      },
    },
    {
      field: "laneLabel",
      name: "Lane Label",
      type: "string",
      meta: {
        interface: "input",
        width: "half",
      },
      schema: {
        default_value: "主解析任务",
      },
    },
    {
      field: "targetUrl",
      name: "Target URL",
      type: "string",
      meta: {
        interface: "input",
        width: "half",
      },
      schema: {
        default_value: "/admin/content/analysis_tasks",
      },
    },
  ],
  minWidth: 8,
  minHeight: 5,
};
