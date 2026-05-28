import { h } from "vue";
import { truncateText } from "../shared/format.js";

export default {
  id: "claread-text-preview-interface",
  name: "Claread Text Preview Interface",
  icon: "article",
  description: "在详情页显示长文本折叠预览，并支持展开查看全文。",
  types: ["text", "string"],
  group: "presentation",
  options: [
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
  component: function TextPreviewInterface({ value, preview_length }) {
    const raw = value == null ? "" : String(value);
    const preview = truncateText(raw, Number(preview_length) || 240);

    return h(
      "div",
      {
        style: {
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
              border: "1px solid #E2E8F0",
              borderRadius: "16px",
              padding: "16px",
              background: "#FFFFFF",
              color: "#0F172A",
              fontSize: "13px",
              lineHeight: "22px",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            },
          },
          raw ? preview : "空文本",
        ),
        raw.length > preview.length
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
                raw,
              ),
            ])
          : null,
      ].filter(Boolean),
    );
  },
};

