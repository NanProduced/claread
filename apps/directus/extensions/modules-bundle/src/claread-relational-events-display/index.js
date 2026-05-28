import { defineComponent, h, onMounted, ref, watch } from "vue";
import { renderMappedBadge } from "../shared/enum-display.js";
import { formatDateTime } from "../shared/format.js";

function normalizeItems(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => item && typeof item === "object");
}

const QUERY_CONFIG = {
  analysis_tasks: {
    path: (primaryKey) =>
      `/items/analysis_task_events?fields=id,event_type,created_at&sort=created_at&filter[task_id][_eq]=${encodeURIComponent(primaryKey)}`,
  },
  analysis_overview_tasks: {
    path: (primaryKey) =>
      `/items/analysis_overview_task_events?fields=id,event_type,created_at&sort=created_at&filter[task_id][_eq]=${encodeURIComponent(primaryKey)}`,
  },
};

export default {
  id: "claread-relational-events-display",
  name: "Claread Relational Events Display",
  icon: "timeline",
  description: "在任务详情页中用中文标签展示事件流。",
  types: ["alias"],
  group: "relational",
  localTypes: ["o2m"],
  relational: true,
  component: defineComponent({
    props: ["value", "collection", "primaryKey"],
    setup(props) {
      const items = ref(normalizeItems(props.value));

      const load = async () => {
        const directValue = normalizeItems(props.value);
        if (directValue.length > 0) {
          items.value = directValue;
          return;
        }

        const config = QUERY_CONFIG[props.collection];
        if (!config || !props.primaryKey) {
          items.value = [];
          return;
        }

        try {
          const response = await fetch(config.path(props.primaryKey), {
            credentials: "include",
            headers: {
              Accept: "application/json",
            },
          });

          if (!response.ok) {
            items.value = [];
            return;
          }

          const payload = await response.json();
          items.value = normalizeItems(payload?.data ?? []);
        } catch {
          items.value = [];
        }
      };

      onMounted(load);
      watch(() => [props.value, props.collection, props.primaryKey], load, { deep: true });

      return () => {
        if (items.value.length === 0) {
          return h(
            "div",
            {
              style: {
                color: "#64748B",
                fontSize: "12px",
                lineHeight: "18px",
                padding: "8px 0",
              },
            },
            "暂无事件",
          );
        }

        return h(
          "div",
          {
            style: {
              display: "flex",
              flexDirection: "column",
              gap: "8px",
              padding: "4px 0",
            },
          },
          items.value.map((item) =>
            h(
              "div",
              {
                style: {
                  border: "1px solid #E2E8F0",
                  borderRadius: "12px",
                  padding: "12px 14px",
                  background: "#FFFFFF",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "12px",
                  flexWrap: "wrap",
                },
              },
              [
                renderMappedBadge(item.event_type, "event_type", true),
                h(
                  "span",
                  {
                    style: {
                      color: "#64748B",
                      fontSize: "12px",
                      lineHeight: "18px",
                      whiteSpace: "nowrap",
                    },
                  },
                  formatDateTime(item.created_at),
                ),
              ],
            ),
          ),
        );
      };
    },
  }),
};
