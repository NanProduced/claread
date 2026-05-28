import { defineComponent, h, onMounted, ref, watch } from "vue";
import { renderMappedBadge } from "../shared/enum-display.js";
import { resolveContext } from "../shared/context.js";
import { formatDateTime, truncateText } from "../shared/format.js";

export default {
  id: "claread-record-context-interface",
  name: "Claread Record Context Interface",
  icon: "description",
  description: "在详情页展示关联记录标题、来源端和原文预览。",
  types: ["string", "uuid", "text"],
  group: "presentation",
  options: [
    {
      field: "target",
      type: "string",
      name: "Target",
      meta: {
        width: "full",
        interface: "select-dropdown",
        options: {
          choices: [{ text: "Analysis Record", value: "record" }],
        },
      },
    },
    {
      field: "preview_length",
      type: "integer",
      name: "Preview Length",
      meta: {
        width: "half",
        interface: "input",
      },
      schema: {
        default_value: 240,
      },
    },
  ],
  component: defineComponent({
    props: ["value", "target", "preview_length"],
    setup(props) {
      const context = ref(null);

      const load = async () => {
        context.value = await resolveContext(props.value, props.target ?? "record");
      };

      onMounted(load);
      watch(() => [props.value, props.target], load, { deep: true });

      return () => {
        const record = context.value;
        const sourceText = record?.source_text || "";
        const preview = truncateText(sourceText, Number(props.preview_length) || 240);

        return h(
          "div",
          {
            style: {
              display: "flex",
              flexDirection: "column",
              gap: "12px",
              padding: "4px 0 12px",
            },
          },
          [
            h(
              "div",
              {
                style: {
                  border: "1px solid #E2E8F0",
                  borderRadius: "16px",
                  padding: "16px",
                  background: "#FFFFFF",
                  display: "flex",
                  flexDirection: "column",
                  gap: "10px",
                },
              },
              [
                h(
                  "div",
                  {
                    style: {
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "baseline",
                      gap: "12px",
                      flexWrap: "wrap",
                    },
                  },
                  [
                    h(
                      "strong",
                      {
                        style: {
                          color: "#0F172A",
                          fontSize: "14px",
                          lineHeight: "22px",
                        },
                      },
                      record?.title || "加载中",
                    ),
                    record?.client_record_id
                      ? renderMappedBadge(record.client_record_id, "client_source_from_id", false)
                      : null,
                  ].filter(Boolean),
                ),
                h(
                  "div",
                  {
                    style: {
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                      gap: "10px 16px",
                    },
                  },
                  [
                    ["Record ID", record?.id || ""],
                    ["Client Record ID", record?.client_record_id || "未记录"],
                    ["最近打开", formatDateTime(record?.last_opened_at)],
                  ].map(([label, text]) =>
                    h("div", { style: { display: "flex", flexDirection: "column", gap: "2px" } }, [
                      h(
                        "span",
                        {
                          style: {
                            color: "#64748B",
                            fontSize: "11px",
                            lineHeight: "16px",
                          },
                        },
                        label,
                      ),
                      h(
                        "span",
                        {
                          style: {
                            color: "#0F172A",
                            fontSize: "12px",
                            lineHeight: "18px",
                            wordBreak: "break-word",
                          },
                        },
                        text,
                      ),
                    ]),
                  ),
                ),
              ],
            ),
            h(
              "div",
              {
                style: {
                  border: "1px solid #E2E8F0",
                  borderRadius: "16px",
                  padding: "16px",
                  background: "#FFFFFF",
                  display: "flex",
                  flexDirection: "column",
                  gap: "8px",
                },
              },
              [
                h(
                  "strong",
                  {
                    style: {
                      color: "#0F172A",
                      fontSize: "13px",
                      lineHeight: "20px",
                    },
                  },
                  "原文预览",
                ),
                h(
                  "div",
                  {
                    style: {
                      color: "#0F172A",
                      fontSize: "13px",
                      lineHeight: "22px",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    },
                  },
                  sourceText ? preview : "空文本",
                ),
                sourceText.length > preview.length
                  ? h("details", { style: { color: "#334155" } }, [
                      h(
                        "summary",
                        {
                          style: {
                            cursor: "pointer",
                            fontSize: "12px",
                            lineHeight: "18px",
                            color: "#475569",
                          },
                        },
                        "展开完整原文",
                      ),
                      h(
                        "pre",
                        {
                          style: {
                            margin: "10px 0 0",
                            padding: "14px",
                            borderRadius: "12px",
                            background: "#0F172A",
                            color: "#E2E8F0",
                            fontSize: "12px",
                            lineHeight: "18px",
                            overflowX: "auto",
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                          },
                        },
                        sourceText,
                      ),
                    ])
                  : null,
              ].filter(Boolean),
            ),
          ],
        );
      };
    },
  }),
};

