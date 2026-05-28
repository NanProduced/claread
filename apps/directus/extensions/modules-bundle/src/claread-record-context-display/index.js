import { defineComponent, h, onMounted, ref, watch } from "vue";
import { renderMappedBadge } from "../shared/enum-display.js";
import { formatDateTime, shortId } from "../shared/format.js";
import { resolveContext } from "../shared/context.js";

function titleText(context) {
  if (!context) return "加载中";
  if (context.type === "record") return context.title || shortId(context.id);
  return context.record?.title || `任务 ${shortId(context.id)}`;
}

function secondaryText(context) {
  if (!context) return "";
  if (context.type === "record") {
    return context.id || "";
  }
  const parts = [];
  if (context.status) parts.push(context.status);
  if (context.id) parts.push(shortId(context.id, 12));
  return parts.join(" · ");
}

export default {
  id: "claread-record-context-display",
  name: "Claread Record Context Display",
  icon: "preview",
  description: "在列表或关系字段中补充文章标题、来源端和关联上下文。",
  types: ["string", "uuid", "text", "alias"],
  options: [
    {
      field: "target",
      type: "string",
      name: "Target",
      meta: {
        width: "full",
        interface: "select-dropdown",
        options: {
          choices: [
            { text: "Analysis Record", value: "record" },
            { text: "Analysis Task", value: "analysis_task" },
            { text: "Analysis Overview Task", value: "analysis_overview_task" },
          ],
        },
      },
    },
  ],
  component: defineComponent({
    props: ["value", "target"],
    setup(props) {
      const context = ref(null);

      const load = async () => {
        context.value = await resolveContext(props.value, props.target ?? "record");
      };

      onMounted(load);
      watch(() => [props.value, props.target], load, { deep: true });

      return () =>
        h(
          "div",
          {
            style: {
              display: "flex",
              flexDirection: "column",
              gap: "4px",
              minWidth: "0",
            },
          },
          [
            h(
              "div",
              {
                style: {
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  flexWrap: "wrap",
                  minWidth: "0",
                },
              },
              [
                h(
                  "strong",
                  {
                    style: {
                      color: "#0F172A",
                      fontSize: "12px",
                      lineHeight: "18px",
                    },
                  },
                  titleText(context.value),
                ),
                context.value?.client_source
                  ? renderMappedBadge(context.value.client_source.raw, "client_source_from_id", false)
                  : context.value?.record?.client_source
                    ? renderMappedBadge(
                        context.value.record.client_source.raw,
                        "client_source_from_id",
                        false,
                      )
                    : null,
              ].filter(Boolean),
            ),
            h(
              "span",
              {
                style: {
                  color: "#64748B",
                  fontSize: "11px",
                  lineHeight: "16px",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                },
                title:
                  context.value?.type === "record"
                    ? `${context.value?.client_record_id || ""}\n最近打开: ${formatDateTime(context.value?.last_opened_at)}`
                    : context.value?.id || "",
              },
              secondaryText(context.value),
            ),
          ],
        );
    },
  }),
};

