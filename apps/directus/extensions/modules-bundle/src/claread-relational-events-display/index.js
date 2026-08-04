import { defineComponent, h, onMounted, ref, watch } from "vue";
import { renderMappedBadge } from "../shared/enum-display.js";
import { formatDateTime } from "../shared/format.js";

function normalizeItems(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => item && typeof item === "object");
}

// DATA-LEGACY-IDENTITY-EXIT: the legacy analysis_task / analysis_overview_task
// event-fetch branches are gone; this display renders directly provided items only.

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

      const load = () => {
        items.value = normalizeItems(props.value);
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
