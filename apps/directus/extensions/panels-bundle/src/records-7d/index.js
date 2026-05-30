import { defineComponent, h, onMounted, ref, watch } from "vue";

import { formatCompact, formatDayLabel, toNumber } from "../shared/format.js";
import { buildItemsPath, fetchJson } from "../shared/query.js";
import { metric, panelShell } from "../shared/ui.js";

const DAY_MS = 24 * 60 * 60 * 1000;

function dayKey(date) {
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()].join("-");
}

function buildLastSevenDays() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(today.getTime() - (6 - index) * DAY_MS);
    return {
      key: dayKey(date),
      label: formatDayLabel(date),
      count: 0,
    };
  });
}

function normalizeRows(rows) {
  const days = buildLastSevenDays();
  const byKey = new Map(days.map((day) => [day.key, day]));

  for (const row of Array.isArray(rows) ? rows : []) {
    const key = [row.created_at_year, row.created_at_month, row.created_at_day].join("-");
    const target = byKey.get(key);
    if (target) target.count = toNumber(row.count);
  }

  return days;
}

function renderBars(days) {
  const max = Math.max(1, ...days.map((day) => day.count));

  return h(
    "div",
    {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
        gap: "6px",
        alignItems: "end",
        minHeight: "86px",
      },
    },
    days.map((day) =>
      h(
        "div",
        {
          key: day.key,
          style: {
            display: "flex",
            flexDirection: "column",
            justifyContent: "flex-end",
            gap: "5px",
            minWidth: "0",
            height: "86px",
          },
          title: `${day.label}: ${day.count}`,
        },
        [
          h(
            "div",
            {
              style: {
                width: "100%",
                minHeight: day.count > 0 ? "8px" : "2px",
                height: `${Math.max(2, Math.round((day.count / max) * 54))}px`,
                borderRadius: "6px 6px 2px 2px",
                background: day.count > 0 ? "#245CB8" : "#D9DEE7",
              },
            },
          ),
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
            day.label.slice(0, 5),
          ),
        ],
      ),
    ),
  );
}

const RecordsPanel = defineComponent({
  props: {
    showHeader: {
      type: Boolean,
      default: false,
    },
    targetUrl: {
      type: String,
      default: "/admin/content/analysis_records",
    },
  },
  setup(props) {
    const loading = ref(false);
    const error = ref("");
    const days = ref(buildLastSevenDays());

    const load = async () => {
      loading.value = true;
      error.value = "";

      try {
        const data = await fetchJson(
          buildItemsPath("analysis_records", {
            aggregate: { count: "*" },
            groupBy: ["year(created_at)", "month(created_at)", "day(created_at)"],
            filter: {
              created_at: { _gte: "$NOW(-7 days)" },
              deleted_at: { _null: true },
            },
          }),
        );
        days.value = normalizeRows(data);
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : "unknown error";
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => props.targetUrl, load);

    return () => {
      const total = days.value.reduce((sum, day) => sum + day.count, 0);
      const latest = days.value[days.value.length - 1]?.count ?? 0;

      return panelShell(
        h(
          "a",
          {
            href: props.targetUrl,
            style: {
              display: "flex",
              flexDirection: "column",
              gap: "12px",
              color: "inherit",
              textDecoration: "none",
              minHeight: "0",
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
                metric("近 7 天记录", formatCompact(total), "排除 deleted"),
                metric("今天", formatCompact(latest), "按 created_at"),
              ],
            ),
            renderBars(days.value),
          ],
        ),
        { loading: loading.value, error: error.value },
      );
    };
  },
});

export default {
  id: "claread-parse-run-records-7d",
  name: "Parse Records 7D",
  icon: "monitoring",
  description: "最近 7 天解析记录数和日粒度趋势。",
  component: RecordsPanel,
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
        default_value: "/admin/content/analysis_records",
      },
    },
  ],
  minWidth: 8,
  minHeight: 5,
};
