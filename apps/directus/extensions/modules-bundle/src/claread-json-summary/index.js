import { h } from "vue";

function normalizeJson(value) {
  if (value == null || value === "") return null;
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }

  return value;
}

function compactBytes(characters) {
  if (characters < 1024) return `${characters} B`;
  if (characters < 1024 * 1024) return `${(characters / 1024).toFixed(1)} KB`;
  return `${(characters / (1024 * 1024)).toFixed(1)} MB`;
}

function countArrayEntriesByKey(input, targetKey) {
  let total = 0;

  function visit(value) {
    if (!value || typeof value !== "object") return;

    if (!Array.isArray(value) && Array.isArray(value[targetKey])) {
      total += value[targetKey].length;
    }

    const children = Array.isArray(value) ? value : Object.values(value);
    for (const child of children) {
      if (child && typeof child === "object") {
        visit(child);
      }
    }
  }

  visit(input);
  return total;
}

function buildSummary(value, summaryKind) {
  const normalized = normalizeJson(value);

  if (normalized == null) {
    return {
      heading: "Empty JSON",
      subheading: "值为空",
      chips: [],
      title: "raw: NULL",
    };
  }

  const serialized =
    typeof normalized === "string" ? normalized : JSON.stringify(normalized);
  const topLevelKeys =
    normalized && typeof normalized === "object" && !Array.isArray(normalized)
      ? Object.keys(normalized)
      : [];

  const chips = [`${compactBytes(serialized.length)}`];

  if (summaryKind === "render_scene") {
    const metricConfig = [
      ["paragraphs", "段落"],
      ["sentences", "句子"],
      ["blocks", "块"],
      ["inline_annotations", "标注"],
      ["sentence_entries", "讲解"],
      ["warnings", "告警"],
    ];

    for (const [key, label] of metricConfig) {
      const count = countArrayEntriesByKey(normalized, key);
      if (count > 0) {
        chips.push(`${label} ${count}`);
      }
    }
  }

  if (summaryKind === "page_state" && normalized && typeof normalized === "object") {
    if (normalized.pageState) {
      chips.push(`页面 ${normalized.pageState}`);
    }

    if (normalized.derived && typeof normalized.derived === "object") {
      chips.push(`derived ${Object.keys(normalized.derived).length}`);
      if (normalized.derived.overview_hint) {
        chips.push("含 overview_hint");
      }
    }
  }

  if (summaryKind === "generic" && topLevelKeys.length > 0) {
    chips.push(`keys ${topLevelKeys.length}`);
  }

  const preview =
    topLevelKeys.length > 0 ? topLevelKeys.slice(0, 5).join(", ") : Array.isArray(normalized) ? "array" : typeof normalized;

  return {
    heading:
      summaryKind === "render_scene"
        ? "Render Scene"
        : summaryKind === "page_state"
          ? "Page State"
          : "JSON Summary",
    subheading: preview,
    chips,
    title: serialized.length > 320 ? `${serialized.slice(0, 320)}…` : serialized,
  };
}

function renderJsonSummary(value, summaryKind) {
  const summary = buildSummary(value, summaryKind ?? "generic");

  return h(
    "div",
    {
      title: summary.title,
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "6px",
        minWidth: "0",
        padding: "8px 0",
      },
    },
    [
      h(
        "div",
        {
          style: {
            display: "flex",
            justifyContent: "space-between",
            gap: "8px",
            alignItems: "baseline",
            flexWrap: "wrap",
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
            summary.heading,
          ),
          h(
            "span",
            {
              style: {
                color: "#64748B",
                fontSize: "11px",
                lineHeight: "16px",
              },
            },
            summary.subheading,
          ),
        ],
      ),
      h(
        "div",
        {
          style: {
            display: "flex",
            flexWrap: "wrap",
            gap: "6px",
          },
        },
        summary.chips.map((chip) =>
          h(
            "span",
            {
              style: {
                display: "inline-flex",
                alignItems: "center",
                minHeight: "22px",
                padding: "0 8px",
                borderRadius: "999px",
                border: "1px solid #CBD5E1",
                background: "#F8FAFC",
                color: "#334155",
                fontSize: "11px",
                lineHeight: "16px",
                whiteSpace: "nowrap",
              },
            },
            chip,
          ),
        ),
      ),
    ],
  );
}

export default {
  id: "claread-json-summary",
  name: "Claread JSON Summary",
  icon: "data_object",
  description: "将大 JSON 压缩成结构摘要，避免详情页被原始内容淹没。",
  types: ["json"],
  options: [
    {
      field: "summary_kind",
      type: "string",
      name: "Summary Kind",
      meta: {
        width: "full",
        interface: "select-dropdown",
        options: {
          choices: [
            { text: "Render Scene", value: "render_scene" },
            { text: "Page State", value: "page_state" },
            { text: "Generic", value: "generic" },
          ],
        },
      },
    },
  ],
  component: function JsonSummaryDisplay({ value, summary_kind }) {
    return renderJsonSummary(value, summary_kind);
  },
};
