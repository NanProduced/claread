import { defineComponent, h, ref } from "vue";

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

function countItemsBy(items, selector) {
  const counts = new Map();

  for (const item of Array.isArray(items) ? items : []) {
    const key = selector(item);
    if (!key) continue;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  return counts;
}

function getMarkBreakdown(normalized) {
  const inlineMarks = Array.isArray(normalized?.inline_marks) ? normalized.inline_marks : [];
  const sentenceEntries = Array.isArray(normalized?.sentence_entries) ? normalized.sentence_entries : [];

  const markTypeCounts = countItemsBy(inlineMarks, (item) => item?.annotation_type ?? item?.annotationType);
  const entryTypeCounts = countItemsBy(sentenceEntries, (item) => item?.entry_type ?? item?.entryType);

  return {
    inlineMarks,
    sentenceEntries,
    vocab: [
      ["词汇", markTypeCounts.get("vocab_highlight") ?? 0],
      ["短语", markTypeCounts.get("phrase_gloss") ?? 0],
      ["语境", markTypeCounts.get("context_gloss") ?? 0],
    ],
    grammar: [
      ["语法旁注", markTypeCounts.get("grammar_note") ?? 0],
      ["句子拆解", entryTypeCounts.get("sentence_analysis") ?? 0],
    ],
  };
}

async function copyText(text) {
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function buildSummary(value, summaryKind) {
  const normalized = normalizeJson(value);

  if (normalized == null) {
    return {
      heading: "空数据",
      eyebrow: "快速预览",
      descriptor: "当前字段为空。",
      metrics: [{ label: "大小", value: "0 B" }],
      pills: [],
      diagnostics: [],
      preview: "NULL",
    };
  }

  const serialized =
    typeof normalized === "string" ? normalized : JSON.stringify(normalized, null, 2);
  const topLevelKeys =
    normalized && typeof normalized === "object" && !Array.isArray(normalized)
      ? Object.keys(normalized)
      : [];

  const metrics = [{ label: "大小", value: compactBytes(serialized.length) }];
  const pills = [];
  const diagnostics = [];

  if (summaryKind === "render_scene") {
    const request = normalized?.request && typeof normalized.request === "object" ? normalized.request : {};
    const article = normalized?.article && typeof normalized.article === "object" ? normalized.article : {};
    const breakdown = getMarkBreakdown(normalized);
    const paragraphCount =
      Array.isArray(article.paragraphs) ? article.paragraphs.length : countArrayEntriesByKey(normalized, "paragraphs");
    const sentenceCount = countArrayEntriesByKey(normalized, "sentence_ids") || countArrayEntriesByKey(normalized, "sentences");
    const explanationCount = breakdown.sentenceEntries.length || countArrayEntriesByKey(normalized, "sentence_entries");
    const warningCount = countArrayEntriesByKey(normalized, "warnings");
    const annotationCount = breakdown.inlineMarks.length || countArrayEntriesByKey(normalized, "inline_marks");

    metrics.push(
      { label: "段落", value: `${paragraphCount}` },
      { label: "句子", value: `${sentenceCount}` },
      { label: "讲解", value: `${explanationCount}` },
      { label: "告警", value: `${warningCount}` },
    );

    if (annotationCount > 0) {
      metrics.push({ label: "标注", value: `${annotationCount}` });
    }

    if (request.source_type) pills.push({ text: `来源 ${request.source_type}`, tone: "info" });
    if (request.reading_goal) pills.push({ text: `目标 ${request.reading_goal}`, tone: "neutral" });
    if (request.reading_variant) pills.push({ text: `变体 ${request.reading_variant}`, tone: "neutral" });
    if (normalized.user_facing_state) pills.push({ text: "含用户态快照", tone: "accent" });

    diagnostics.push(
      {
        label: "结构模块",
        value: topLevelKeys.length > 0 ? `${topLevelKeys.length} 个顶层键` : "无对象结构",
      },
      {
        label: "词汇类标注",
        value: breakdown.vocab.map(([label, count]) => `${label} ${count}`).join(" / "),
      },
      {
        label: "语法类标注",
        value: breakdown.grammar.map(([label, count]) => `${label} ${count}`).join(" / "),
      },
      {
        label: "Translations",
        value: normalized.translations ? "存在" : "缺失",
      },
      {
        label: "Inline Marks",
        value: normalized.inline_marks ? "存在" : "缺失",
      },
      {
        label: "Raw 模块",
        value: topLevelKeys.slice(0, 4).join(" / ") || typeof normalized,
      },
    );

    return {
      heading: "Render Scene",
      eyebrow: "快速预览",
      descriptor: "用于首屏快速确认结构规模、来源上下文和明显异常，不替代后续 JSON Inspector。",
      metrics,
      pills,
      diagnostics,
      preview: serialized,
    };
  }

  if (summaryKind === "page_state" && normalized && typeof normalized === "object") {
    metrics.push({
      label: "页面状态",
      value: normalized.pageState ? `${normalized.pageState}` : "未标记",
    });

    if (normalized.derived && typeof normalized.derived === "object") {
      metrics.push({ label: "Derived", value: `${Object.keys(normalized.derived).length}` });
      if (normalized.derived.overview_hint) pills.push({ text: "含 overview_hint", tone: "accent" });
    }

    diagnostics.push(
      {
        label: "结构模块",
        value: topLevelKeys.length > 0 ? `${topLevelKeys.length} 个顶层键` : "无对象结构",
      },
      {
        label: "顶层键",
        value: topLevelKeys.slice(0, 4).join(" / ") || "无",
      },
    );

    return {
      heading: "Page State",
      eyebrow: "快速预览",
      descriptor: "用于确认页面状态、derived 派生信息和 overview_hint 是否已回写。",
      metrics,
      pills,
      diagnostics,
      preview: serialized,
    };
  }

  if (topLevelKeys.length > 0) {
    metrics.push({ label: "顶层键", value: `${topLevelKeys.length}` });
  }

  diagnostics.push({
    label: "结构类型",
    value: Array.isArray(normalized) ? "array" : typeof normalized,
  });

  return {
    heading: "JSON",
    eyebrow: "快速预览",
    descriptor: "通用 JSON 摘要，优先回答大小与结构形态。",
    metrics,
    pills,
    diagnostics,
    preview: serialized,
  };
}

function pill(text, tone = "neutral") {
  const palette = {
    neutral: {
      background: "#F8FAFC",
      border: "#CBD5E1",
      color: "#334155",
    },
    info: {
      background: "#EFF6FF",
      border: "#BFDBFE",
      color: "#1D4ED8",
    },
    accent: {
      background: "#ECFDF5",
      border: "#A7F3D0",
      color: "#047857",
    },
  };

  const colors = palette[tone] ?? palette.neutral;

  return h(
    "span",
    {
      style: {
        display: "inline-flex",
        alignItems: "center",
        minHeight: "26px",
        padding: "0 11px",
        borderRadius: "999px",
        border: `1px solid ${colors.border}`,
        background: colors.background,
        color: colors.color,
        fontSize: "12px",
        lineHeight: "18px",
        whiteSpace: "nowrap",
      },
    },
    text,
  );
}

function metricTile(label, value) {
  return h(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "5px",
        minWidth: "0",
        padding: "12px 14px",
        borderRadius: "14px",
        background: "#FFFFFF",
        border: "1px solid #E2E8F0",
      },
    },
    [
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
        "strong",
        {
          style: {
            color: "#0F172A",
            fontSize: "18px",
            lineHeight: "24px",
          },
        },
        value,
      ),
    ],
  );
}

function diagnosticRow(label, value) {
  return h(
    "div",
    {
      style: {
        display: "flex",
        justifyContent: "space-between",
        gap: "16px",
        padding: "8px 0",
        borderBottom: "1px solid #E2E8F0",
      },
    },
    [
      h(
        "span",
        {
          style: {
            color: "#64748B",
            fontSize: "12px",
            lineHeight: "18px",
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
            textAlign: "right",
            wordBreak: "break-word",
          },
        },
        value,
      ),
    ],
  );
}

export default {
  id: "claread-json-summary-interface",
  name: "Claread JSON Summary Interface",
  icon: "data_object",
  description: "在详情页提供结构快速预览，并保留完整 JSON 展开。",
  types: ["json"],
  group: "presentation",
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
  component: defineComponent({
    props: ["value", "summary_kind"],
    setup(props) {
      const copyState = ref("");

      const handleCopy = async () => {
        const summary = buildSummary(props.value, props.summary_kind ?? "generic");

        try {
          await copyText(summary.preview);
          copyState.value = "已复制";
        } catch {
          copyState.value = "复制失败";
        }

        setTimeout(() => {
          if (copyState.value) copyState.value = "";
        }, 1500);
      };

      return () => {
        const summary = buildSummary(props.value, props.summary_kind ?? "generic");

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
                  border: "1px solid #D9E2EC",
                  borderRadius: "18px",
                  overflow: "hidden",
                  background: "linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%)",
                  display: "flex",
                  flexDirection: "column",
                },
              },
              [
                h(
                  "div",
                  {
                    style: {
                      display: "flex",
                      justifyContent: "space-between",
                      gap: "16px",
                      alignItems: "flex-start",
                      flexWrap: "wrap",
                      padding: "16px 18px 12px",
                      borderBottom: "1px solid #E2E8F0",
                    },
                  },
                  [
                    h("div", { style: { display: "flex", flexDirection: "column", gap: "4px", flex: "1 1 420px" } }, [
                      h(
                        "span",
                        {
                          style: {
                            color: "#64748B",
                            fontSize: "11px",
                            lineHeight: "16px",
                            letterSpacing: "0.04em",
                          },
                        },
                        summary.eyebrow,
                      ),
                      h(
                        "strong",
                        {
                          style: {
                            color: "#0F172A",
                            fontSize: "18px",
                            lineHeight: "26px",
                          },
                        },
                        summary.heading,
                      ),
                      h(
                        "span",
                        {
                          style: {
                            color: "#475569",
                            fontSize: "12px",
                            lineHeight: "18px",
                          },
                        },
                        summary.descriptor,
                      ),
                    ]),
                    h(
                      "div",
                      {
                        style: {
                          display: "flex",
                          flexDirection: "column",
                          gap: "8px",
                          alignItems: "flex-end",
                        },
                      },
                      [
                        summary.diagnostics.length > 0
                          ? h(
                              "span",
                              {
                                style: {
                                  color: "#64748B",
                                  fontSize: "12px",
                                  lineHeight: "18px",
                                  whiteSpace: "nowrap",
                                },
                              },
                              summary.diagnostics[0]?.value ?? "",
                            )
                          : null,
                        h(
                          "button",
                          {
                            type: "button",
                            onClick: handleCopy,
                            style: {
                              border: "1px solid #CBD5E1",
                              background: "#FFFFFF",
                              color: "#0F172A",
                              borderRadius: "10px",
                              padding: "6px 10px",
                              fontSize: "12px",
                              lineHeight: "18px",
                              cursor: "pointer",
                            },
                          },
                          copyState.value || "复制 JSON",
                        ),
                      ].filter(Boolean),
                    ),
                  ],
                ),
                h(
                  "div",
                  {
                    style: {
                      display: "flex",
                      flexDirection: "column",
                      gap: "14px",
                      padding: "16px 18px 18px",
                    },
                  },
                  [
                    h(
                      "div",
                      {
                        style: {
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(118px, 1fr))",
                          gap: "10px",
                        },
                      },
                      summary.metrics.map((item) => metricTile(item.label, item.value)),
                    ),
                    summary.pills.length > 0
                      ? h(
                          "div",
                          {
                            style: {
                              display: "flex",
                              gap: "8px",
                              flexWrap: "wrap",
                            },
                          },
                          summary.pills.map((item) => pill(item.text, item.tone)),
                        )
                      : null,
                    summary.diagnostics.length > 0
                      ? h(
                          "div",
                          {
                            style: {
                              display: "flex",
                              flexDirection: "column",
                              gap: "0",
                              padding: "2px 0 0",
                            },
                          },
                          summary.diagnostics.map((item, index) =>
                            h(
                              "div",
                              {
                                style: index === summary.diagnostics.length - 1 ? { borderBottom: "none" } : null,
                              },
                              [diagnosticRow(item.label, item.value)],
                            ),
                          ),
                        )
                      : null,
                  ].filter(Boolean),
                ),
              ],
            ),
            h("details", { style: { color: "#334155" } }, [
              h(
                "summary",
                {
                  style: {
                    cursor: "pointer",
                    fontSize: "12px",
                    lineHeight: "18px",
                    color: "#475569",
                    userSelect: "none",
                  },
                },
                "展开完整 JSON",
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
                summary.preview,
              ),
            ]),
          ],
        );
      };
    },
  }),
};
