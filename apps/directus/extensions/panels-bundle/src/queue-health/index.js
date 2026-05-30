import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, formatDurationSeconds } from "../shared/format.js";
import { fetchSummary, DEFAULT_SUMMARY_URL, section } from "../shared/summary.js";
import { chip, metric, panelShell } from "../shared/ui.js";

const EMPTY_QUEUE = { lanes: [] };

function totals(queue) {
  return (Array.isArray(queue.lanes) ? queue.lanes : []).reduce(
    (acc, lane) => ({
      queued: acc.queued + Number(lane.queued || 0),
      running: acc.running + Number(lane.running || 0),
      finalizing: acc.finalizing + Number(lane.finalizing || 0),
      stale: acc.stale + Number(lane.stale || 0),
      oldestQueued: Math.max(acc.oldestQueued, Number(lane.oldest_queued_wait_seconds || 0)),
      oldestActive: Math.max(acc.oldestActive, Number(lane.oldest_active_run_seconds || 0)),
    }),
    { queued: 0, running: 0, finalizing: 0, stale: 0, oldestQueued: 0, oldestActive: 0 },
  );
}

function renderLaneRows(queue) {
  const lanes = Array.isArray(queue.lanes) ? queue.lanes : [];

  return h(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: "8px" } },
    lanes.map((lane) =>
      h(
        "div",
        {
          key: lane.lane,
          style: {
            display: "grid",
            gridTemplateColumns: "86px repeat(4, minmax(0, 1fr))",
            gap: "8px",
            alignItems: "center",
            minWidth: "0",
          },
        },
        [
          chip(lane.lane === "overview" ? "Overview" : "主解析", lane.stale > 0 ? "danger" : "muted"),
          h("span", { style: cellStyle() }, `排队 ${formatCompact(lane.queued)}`),
          h("span", { style: cellStyle() }, `运行 ${formatCompact(lane.running)}`),
          h("span", { style: cellStyle() }, `收尾 ${formatCompact(lane.finalizing)}`),
          h("span", { style: cellStyle(lane.stale > 0) }, `卡死 ${formatCompact(lane.stale)}`),
        ],
      ),
    ),
  );
}

function cellStyle(danger = false) {
  return {
    color: danger ? "#BE123C" : "var(--theme--foreground-subdued, #6B7280)",
    fontSize: "12px",
    lineHeight: "18px",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  };
}

const QueueHealthPanel = defineComponent({
  props: {
    endpointUrl: {
      type: String,
      default: DEFAULT_SUMMARY_URL,
    },
    targetUrl: {
      type: String,
      default: "/admin/content/analysis_tasks",
    },
  },
  setup(props) {
    const loading = ref(false);
    const error = ref("");
    const queue = ref(EMPTY_QUEUE);

    const load = async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = await fetchSummary(props.endpointUrl);
        queue.value = section(data, "queue", EMPTY_QUEUE);
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => props.endpointUrl, load);

    return () => {
      const total = totals(queue.value);
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
                metric("排队", formatCompact(total.queued), total.oldestQueued > 0 ? `最久 ${formatDurationSeconds(total.oldestQueued)}` : "暂无排队"),
                metric("运行", formatCompact(total.running + total.finalizing), total.oldestActive > 0 ? `最久 ${formatDurationSeconds(total.oldestActive)}` : "暂无运行"),
                metric("卡死", formatCompact(total.stale), total.stale > 0 ? "超过 5 分钟" : "暂无卡死"),
              ],
            ),
            renderLaneRows(queue.value),
          ],
        ),
        { loading: loading.value, error: error.value },
      );
    };
  },
});

export default {
  id: "claread-parse-run-queue-health",
  name: "Parse Queue Health",
  icon: "pending_actions",
  description: "解析队列与卡死任务状态。",
  component: QueueHealthPanel,
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
      schema: { default_value: "/admin/content/analysis_tasks" },
    },
  ],
  minWidth: 12,
  minHeight: 6,
};
