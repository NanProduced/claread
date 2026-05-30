import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, formatPercent } from "../shared/format.js";
import { fetchSummary, DEFAULT_SUMMARY_URL, section } from "../shared/summary.js";
import { chip, metric, panelShell } from "../shared/ui.js";

const EMPTY_HEALTH = {
  total: 0,
  succeeded: 0,
  failed: 0,
  success_rate: 0,
  active: 0,
  stale: 0,
};

function healthTone(health) {
  if (health.stale > 0 || health.failed > 0) return "danger";
  if (health.active > 0) return "warning";
  return "success";
}

const HealthOverviewPanel = defineComponent({
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
    const health = ref(EMPTY_HEALTH);

    const load = async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = await fetchSummary(props.endpointUrl);
        health.value = section(data, "health", EMPTY_HEALTH);
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
            h("div", { style: { display: "flex", justifyContent: "space-between", gap: "8px" } }, [
              chip(health.value.stale > 0 ? "有卡死任务" : "运行健康", healthTone(health.value)),
              chip(`近 ${health.value.window_days || 7} 天`, "muted"),
            ]),
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
                metric("成功率", formatPercent(health.value.success_rate), `${formatCompact(health.value.succeeded)} / ${formatCompact(health.value.total)}`),
                metric("失败", formatCompact(health.value.failed), health.value.failed > 0 ? "需要排查" : "暂无失败"),
                metric("Active", formatCompact(health.value.active), "queued/running/finalizing"),
                metric("Stale", formatCompact(health.value.stale), health.value.stale > 0 ? "超过 5 分钟" : "暂无卡死"),
              ],
            ),
          ],
        ),
        { loading: loading.value, error: error.value },
      );
  },
});

export default {
  id: "claread-parse-run-health-overview",
  name: "Parse Health Overview",
  icon: "monitor_heart",
  description: "解析链路健康总览。",
  component: HealthOverviewPanel,
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
