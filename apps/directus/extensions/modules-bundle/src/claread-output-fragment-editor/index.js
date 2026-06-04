import { defineComponent, h, ref, computed, watch, inject } from "vue";

/**
 * Custom Directus Interface: Claread Output Fragment Editor.
 *
 * Provides a structured form for editing the output_fragment JSONB field
 * in eval_example_lab_entries. The form dynamically switches layout
 * based on the example_type field value.
 *
 * Uses inject('values') to access sibling field values reactively,
 * as documented in Directus community and consistent with the provide/inject
 * pattern used internally by Directus v-form.
 */
export default {
  id: "claread-output-fragment-editor",
  name: "Claread Output Fragment Editor",
  icon: "data_object",
  description: "Structured editor for output_fragment JSONB with type-specific forms.",
  types: ["json", "text"],
  group: "presentation",
  options: [],
  component: defineComponent({
    props: [
      "value",
      "collection",
      "primaryKey",
      "field",
      "disabled",
      "loading",
    ],
    emits: ["input"],
    setup(props, { emit }) {
      // Inject form values from Directus v-form (reactive)
      const formValues = inject("values", ref({}));

      const parseValue = (val) => {
        if (!val) return {};
        if (typeof val === "string") {
          try { return JSON.parse(val); } catch { return {}; }
        }
        if (typeof val === "object") return val;
        return {};
      };

      const formData = ref(parseValue(props.value));

      // Derive exampleType from injected form values — this IS reactive
      const exampleType = computed(() => {
        return formValues.value?.example_type || "";
      });
      const outputFragmentLabel = computed(() => {
        return typeof formData.value?.label === "string" ? formData.value.label : "";
      });

      // Track last auto-set to avoid loops
      const lastAutoSetType = ref("");

      watch(exampleType, (newEt, oldEt) => {
        if (newEt && newEt !== oldEt && newEt !== lastAutoSetType.value) {
          lastAutoSetType.value = newEt;
          autoSetType(newEt);
        }
      });

      // Watch for external value changes
      watch(() => props.value, (newVal) => {
        const parsed = parseValue(newVal);
        if (JSON.stringify(parsed) !== JSON.stringify(formData.value)) {
          formData.value = parsed;
        }
      });

      const shouldSyncOuterLabel = computed(() => (
        exampleType.value === "grammar" || exampleType.value === "sentence_analysis"
      ));

      const syncOuterLabel = (nextLabel) => {
        if (!shouldSyncOuterLabel.value) return;
        if (!formValues.value || typeof formValues.value !== "object") return;
        if (formValues.value.label !== nextLabel) {
          formValues.value.label = nextLabel;
        }
      };

      watch([shouldSyncOuterLabel, outputFragmentLabel], ([enabled, nextLabel]) => {
        if (enabled) syncOuterLabel(nextLabel);
      }, { immediate: true });

      // Auto-set the `type` field based on example_type
      const autoSetType = (et) => {
        if (!et) return;
        const currentType = formData.value.type;
        const typeMap = {
          grammar: "grammar_note",
          sentence_analysis: "sentence_analysis",
          vocab: "vocab_highlight",
          phrase: "phrase_gloss",
          context: "context_gloss",
          translation: "translation",
        };
        const defaultType = typeMap[et];
        if (defaultType && currentType !== defaultType) {
          const validSubTypes = {
            grammar: ["grammar_note", "sentence_analysis"],
            vocab: ["vocab_highlight", "term_note", "logic_note"],
          };
          const valid = validSubTypes[et];
          if (!valid || !valid.includes(currentType)) {
            formData.value = { ...formData.value, type: defaultType };
            emitChange();
          }
        }
      };

      const fragmentType = computed(() => formData.value.type || "");

      const formLayout = computed(() => {
        const et = exampleType.value;
        const ft = fragmentType.value;

        if (et === "grammar") {
          if (ft === "sentence_analysis") return "sentence_analysis";
          return "grammar_note";
        }
        if (et === "sentence_analysis") return "sentence_analysis";
        if (et === "vocab") {
          if (ft === "term_note") return "term_note";
          if (ft === "logic_note") return "logic_note";
          return "vocab_highlight";
        }
        if (et === "phrase") return "phrase_gloss";
        if (et === "context") return "context_gloss";
        if (et === "translation") {
          if (ft === "term_note" || formData.value.term_category) return "academic_translation";
          return "translation";
        }

        // Fallback: detect from fragment type
        if (ft === "grammar_note") return "grammar_note";
        if (ft === "sentence_analysis") return "sentence_analysis";
        if (ft === "vocab_highlight") return "vocab_highlight";
        if (ft === "phrase_gloss") return "phrase_gloss";
        if (ft === "context_gloss") return "context_gloss";
        if (ft === "term_note") return "term_note";
        if (ft === "logic_note") return "logic_note";

        return "unknown";
      });

      const emitChange = () => {
        emit("input", JSON.stringify(formData.value));
      };

      const updateField = (key, value) => {
        formData.value = { ...formData.value, [key]: value };
        emitChange();
      };

      const removeField = (key) => {
        const newObj = { ...formData.value };
        delete newObj[key];
        formData.value = newObj;
        emitChange();
      };

      const addArrayItem = (key, template) => {
        const arr = [...(formData.value[key] || [])];
        arr.push(template || {});
        updateField(key, arr);
      };

      const removeArrayItem = (key, index) => {
        const arr = [...(formData.value[key] || [])];
        arr.splice(index, 1);
        updateField(key, arr);
      };

      const updateArrayItem = (key, index, itemKey, value) => {
        const arr = [...(formData.value[key] || [])];
        arr[index] = { ...arr[index], [itemKey]: value };
        updateField(key, arr);
      };

      const moveArrayItem = (key, fromIndex, direction) => {
        const arr = [...(formData.value[key] || [])];
        const toIndex = fromIndex + direction;
        if (toIndex < 0 || toIndex >= arr.length) return;
        [arr[fromIndex], arr[toIndex]] = [arr[toIndex], arr[fromIndex]];
        updateField(key, arr);
      };

      const switchType = (newType) => {
        const preserved = {};
        if (formData.value.sentence_id) preserved.sentence_id = formData.value.sentence_id;
        if (formData.value.translation_zh) preserved.translation_zh = formData.value.translation_zh;
        formData.value = { type: newType, ...preserved };
        emitChange();
      };

      // --- Style constants ---
      const labelStyle = {
        fontSize: "12px",
        fontWeight: "600",
        color: "var(--theme--foreground-subdued, #6B7280)",
        marginBottom: "4px",
        display: "block",
      };

      const fieldCodeStyle = {
        fontSize: "11px",
        fontWeight: "400",
        color: "var(--theme--primary, #4F46E5)",
        fontFamily: '"Cascadia Code", "Fira Code", "Consolas", monospace',
        marginLeft: "4px",
      };

      const inputStyle = {
        width: "100%",
        padding: "6px 10px",
        border: "1px solid var(--theme--border-color, #D1D5DB)",
        borderRadius: "4px",
        fontSize: "13px",
        background: "var(--theme--background, #FFF)",
        color: "var(--theme--foreground, #172940)",
        boxSizing: "border-box",
      };

      const textareaStyle = {
        ...inputStyle,
        fontFamily: '"Cascadia Code", "Fira Code", "Consolas", monospace',
        lineHeight: "1.5",
        resize: "vertical",
        minHeight: "60px",
      };

      const sectionStyle = {
        marginBottom: "12px",
      };

      const rowStyle = {
        display: "flex",
        gap: "8px",
        alignItems: "flex-start",
      };

      const chipStyle = (active) => ({
        display: "inline-block",
        padding: "4px 10px",
        borderRadius: "12px",
        fontSize: "12px",
        cursor: props.disabled ? "default" : "pointer",
        border: "1px solid",
        borderColor: active ? "var(--theme--primary, #4F46E5)" : "var(--theme--border-color, #D1D5DB)",
        background: active ? "var(--theme--primary-background, #EEF2FF)" : "var(--theme--background, #FFF)",
        color: active ? "var(--theme--primary, #4F46E5)" : "var(--theme--foreground-subdued, #6B7280)",
        fontWeight: active ? "600" : "400",
        marginRight: "6px",
        marginBottom: "6px",
      });

      // --- Sub-type selector ---
      const renderTypeSelector = () => {
        const et = exampleType.value;
        if (et === "grammar") {
          return h("div", { style: { marginBottom: "12px" } }, [
            h("span", { style: labelStyle }, [
              "子类型 ",
              h("span", { style: fieldCodeStyle }, "type"),
            ]),
            h("div", { style: rowStyle }, [
              h("span", {
                style: chipStyle(fragmentType.value !== "sentence_analysis"),
                onClick: () => { if (!props.disabled) switchType("grammar_note"); },
              }, "grammar_note — 语法批注"),
              h("span", {
                style: chipStyle(fragmentType.value === "sentence_analysis"),
                onClick: () => { if (!props.disabled) switchType("sentence_analysis"); },
              }, "sentence_analysis — 句子分析"),
            ]),
          ]);
        }
        if (et === "vocab") {
          return h("div", { style: { marginBottom: "12px" } }, [
            h("span", { style: labelStyle }, [
              "子类型 ",
              h("span", { style: fieldCodeStyle }, "type"),
            ]),
            h("div", { style: rowStyle }, [
              h("span", {
                style: chipStyle(fragmentType.value === "" || fragmentType.value === "vocab_highlight"),
                onClick: () => { if (!props.disabled) switchType("vocab_highlight"); },
              }, "vocab_highlight — 词汇高亮"),
              h("span", {
                style: chipStyle(fragmentType.value === "term_note"),
                onClick: () => { if (!props.disabled) switchType("term_note"); },
              }, "term_note — 术语批注"),
              h("span", {
                style: chipStyle(fragmentType.value === "logic_note"),
                onClick: () => { if (!props.disabled) switchType("logic_note"); },
              }, "logic_note — 逻辑批注"),
            ]),
          ]);
        }
        return null;
      };

      // --- Field renderers with bilingual labels ---
      const renderTextField = (key, labelZh, fieldKey, placeholder, opts = {}) => {
        return h("div", { style: { ...sectionStyle, flex: opts.flex || "1 1 auto" } }, [
          h("label", { style: labelStyle }, [
            `${labelZh} `,
            h("span", { style: fieldCodeStyle }, fieldKey),
          ]),
          h("input", {
            type: "text",
            value: formData.value[key] || "",
            disabled: props.disabled,
            placeholder: placeholder || "",
            style: inputStyle,
            onInput: (e) => updateField(key, e.target.value),
          }),
        ]);
      };

      const renderTextareaField = (key, labelZh, fieldKey, placeholder, rows = 3) => {
        return h("div", { style: sectionStyle }, [
          h("label", { style: labelStyle }, [
            `${labelZh} `,
            h("span", { style: fieldCodeStyle }, fieldKey),
          ]),
          h("textarea", {
            value: formData.value[key] || "",
            disabled: props.disabled,
            placeholder: placeholder || "",
            rows,
            style: textareaStyle,
            onInput: (e) => updateField(key, e.target.value),
          }),
        ]);
      };

      const renderSelectField = (key, labelZh, fieldKey, choices, opts = {}) => {
        return h("div", { style: { ...sectionStyle, flex: opts.flex || "1 1 auto" } }, [
          h("label", { style: labelStyle }, [
            `${labelZh} `,
            h("span", { style: fieldCodeStyle }, fieldKey),
          ]),
          h("select", {
            value: formData.value[key] || "",
            disabled: props.disabled,
            style: inputStyle,
            onChange: (e) => {
              if (e.target.value === "") {
                removeField(key);
              } else {
                updateField(key, e.target.value);
              }
            },
          }, [
            h("option", { value: "" }, opts.placeholder || "-- 选择 --"),
            ...choices.map((c) => h("option", { value: c.value }, c.text)),
          ]),
        ]);
      };

      const renderBooleanField = (key, labelZh, fieldKey) => {
        const checked = formData.value[key] === true;
        return h("div", { style: { ...sectionStyle, display: "flex", alignItems: "center", gap: "8px" } }, [
          h("input", {
            type: "checkbox",
            checked,
            disabled: props.disabled,
            style: { cursor: props.disabled ? "default" : "pointer" },
            onChange: (e) => updateField(key, e.target.checked),
          }),
          h("label", { style: { ...labelStyle, marginBottom: "0", cursor: props.disabled ? "default" : "pointer" } }, [
            `${labelZh} `,
            h("span", { style: fieldCodeStyle }, fieldKey),
          ]),
        ]);
      };

      // --- Spans editor ---
      const renderSpansEditor = () => {
        const spans = formData.value.spans || [];
        return h("div", { style: sectionStyle }, [
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } }, [
            h("label", { style: labelStyle }, [
              "高亮锚点 ",
              h("span", { style: fieldCodeStyle }, "spans"),
            ]),
            !props.disabled && h("button", {
              style: {
                fontSize: "12px", padding: "2px 8px",
                border: "1px solid var(--theme--border-color, #D1D5DB)",
                borderRadius: "4px", background: "var(--theme--background, #FFF)",
                cursor: "pointer", color: "var(--theme--primary, #4F46E5)",
              },
              onClick: () => addArrayItem("spans", { text: "" }),
            }, "+ 添加"),
          ]),
          spans.length === 0 && h("div", {
            style: { fontSize: "12px", color: "var(--theme--foreground-subdued, #6B7280)", padding: "4px 0" },
          }, "暂无 span，点击上方按钮添加"),
          ...spans.map((span, i) =>
            h("div", { style: { display: "flex", gap: "6px", alignItems: "center", marginBottom: "4px" } }, [
              h("span", { style: { fontSize: "11px", color: "var(--theme--foreground-subdued)", minWidth: "20px" } }, `${i + 1}.`),
              h("input", {
                type: "text", value: span.text || "", disabled: props.disabled,
                placeholder: "锚点文本（如 Not only、did）",
                style: { ...inputStyle, flex: "1 1 auto" },
                onInput: (e) => updateArrayItem("spans", i, "text", e.target.value),
              }),
              !props.disabled && h("button", {
                style: { fontSize: "11px", padding: "2px 6px", border: "none", background: "transparent", color: "var(--theme--danger, #DC2626)", cursor: "pointer" },
                onClick: () => removeArrayItem("spans", i),
              }, "x"),
            ]),
          ),
        ]);
      };

      // --- Chunks editor ---
      const renderChunksEditor = () => {
        const chunks = formData.value.chunks || [];
        return h("div", { style: sectionStyle }, [
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } }, [
            h("label", { style: labelStyle }, [
              "句子拆解片段 ",
              h("span", { style: fieldCodeStyle }, "chunks"),
            ]),
            !props.disabled && h("button", {
              style: {
                fontSize: "12px", padding: "2px 8px",
                border: "1px solid var(--theme--border-color, #D1D5DB)",
                borderRadius: "4px", background: "var(--theme--background, #FFF)",
                cursor: "pointer", color: "var(--theme--primary, #4F46E5)",
              },
              onClick: () => addArrayItem("chunks", { order: chunks.length + 1, label: "", text: "" }),
            }, "+ 添加"),
          ]),
          chunks.length === 0 && h("div", {
            style: { fontSize: "12px", color: "var(--theme--foreground-subdued, #6B7280)", padding: "4px 0" },
          }, "暂无 chunk，点击上方按钮添加"),
          ...chunks.map((chunk, i) =>
            h("div", {
              style: {
                border: "1px solid var(--theme--border-color, #D1D5DB)",
                borderRadius: "4px", padding: "8px", marginBottom: "6px",
                background: "var(--theme--background-subdued, #F9FAFB)",
              },
            }, [
              h("div", { style: { display: "flex", gap: "6px", alignItems: "center", marginBottom: "4px" } }, [
                h("span", { style: { fontSize: "11px", color: "var(--theme--foreground-subdued)", fontWeight: "600" } }, `#${chunk.order || i + 1}`),
                h("input", {
                  type: "text", value: chunk.label || "", disabled: props.disabled,
                  placeholder: "标签（如 主干主语、后置定语）",
                  style: { ...inputStyle, flex: "1 1 auto" },
                  onInput: (e) => updateArrayItem("chunks", i, "label", e.target.value),
                }),
                !props.disabled && h("button", {
                  style: { fontSize: "11px", padding: "2px 6px", border: "none", background: "transparent", color: "var(--theme--danger, #DC2626)", cursor: "pointer" },
                  onClick: () => removeArrayItem("chunks", i),
                }, "x"),
              ]),
              h("textarea", {
                value: chunk.text || "", disabled: props.disabled,
                placeholder: "片段文本", rows: 2,
                style: { ...textareaStyle, minHeight: "40px" },
                onInput: (e) => updateArrayItem("chunks", i, "text", e.target.value),
              }),
              !props.disabled && h("div", { style: { display: "flex", gap: "4px", marginTop: "4px" } }, [
                i > 0 && h("button", {
                  style: { fontSize: "11px", padding: "1px 6px", border: "1px solid var(--theme--border-color)", borderRadius: "3px", background: "transparent", cursor: "pointer" },
                  onClick: () => moveArrayItem("chunks", i, -1),
                }, "上移"),
                i < chunks.length - 1 && h("button", {
                  style: { fontSize: "11px", padding: "1px 6px", border: "1px solid var(--theme--border-color)", borderRadius: "3px", background: "transparent", cursor: "pointer" },
                  onClick: () => moveArrayItem("chunks", i, 1),
                }, "下移"),
              ]),
            ]),
          ),
        ]);
      };

      // --- String array editor ---
      const renderStringArrayEditor = (key, labelZh, fieldKey, placeholder) => {
        const arr = formData.value[key] || [];
        return h("div", { style: sectionStyle }, [
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } }, [
            h("label", { style: labelStyle }, [
              `${labelZh} `,
              h("span", { style: fieldCodeStyle }, fieldKey),
            ]),
            !props.disabled && h("button", {
              style: {
                fontSize: "12px", padding: "2px 8px",
                border: "1px solid var(--theme--border-color, #D1D5DB)",
                borderRadius: "4px", background: "var(--theme--background, #FFF)",
                cursor: "pointer", color: "var(--theme--primary, #4F46E5)",
              },
              onClick: () => addArrayItem(key, ""),
            }, "+ 添加"),
          ]),
          ...arr.map((item, i) =>
            h("div", { style: { display: "flex", gap: "6px", alignItems: "center", marginBottom: "4px" } }, [
              h("input", {
                type: "text", value: item || "", disabled: props.disabled,
                placeholder: placeholder || "",
                style: { ...inputStyle, flex: "1 1 auto" },
                onInput: (e) => {
                  const newArr = [...arr];
                  newArr[i] = e.target.value;
                  updateField(key, newArr);
                },
              }),
              !props.disabled && h("button", {
                style: { fontSize: "11px", padding: "2px 6px", border: "none", background: "transparent", color: "var(--theme--danger, #DC2626)", cursor: "pointer" },
                onClick: () => removeArrayItem(key, i),
              }, "x"),
            ]),
          ),
        ]);
      };

      // --- Form layouts per type ---
      const renderGrammarNoteForm = () => [
        renderTypeSelector(),
        renderSpansEditor(),
        renderTextField("label", "语法点标签", "label", "如 not only...but also 倒装结构"),
        renderTextareaField("note_zh", "中文解释", "note_zh", "语法点的中文解释，面向学生的教学说明", 4),
      ];

      const renderSentenceAnalysisForm = () => [
        renderTypeSelector(),
        renderTextField("label", "分析标签", "label", "如 过去分词后置定语 + 宾语从句"),
        renderTextareaField("analysis_zh", "中文分析", "analysis_zh", "句子结构分析的中文说明", 5),
        renderChunksEditor(),
      ];

      const renderVocabHighlightForm = () => [
        renderTypeSelector(),
        renderTextField("text", "目标词", "text", "如 adopted、pellucid"),
      ];

      const renderPhraseGlossForm = () => [
        renderTextField("text", "短语文本", "text", "如 give up、account for"),
        renderSelectField("phrase_type", "短语类型", "phrase_type", [
          { text: "phrasal_verb — 动词短语", value: "phrasal_verb" },
          { text: "collocation — 搭配", value: "collocation" },
          { text: "idiom — 习语", value: "idiom" },
        ], { placeholder: "-- 选择短语类型 --" }),
        renderTextareaField("zh", "中文释义", "zh", "短语的中文释义和用法说明", 3),
      ];

      const renderContextGlossForm = () => [
        renderTextField("text", "目标词/短语", "text", "如 address、cosmetic"),
        renderTextField("gloss", "语境义", "gloss", "如 处理；应对"),
        renderTextareaField("reason", "语境义判断理由", "reason", "为什么在这个语境下是这个意思", 3),
      ];

      const renderTranslationForm = () => [
        renderTextField("sentence_id", "句子 ID", "sentence_id", "如 s1、s2"),
        renderTextareaField("translation_zh", "中文翻译", "translation_zh", "句子的中文翻译", 4),
      ];

      const renderAcademicTranslationForm = () => [
        renderTextField("sentence_id", "句子 ID", "sentence_id", "如 s1、s2"),
        renderTextareaField("translation_zh", "中文翻译", "translation_zh", "学术翻译", 4),
        renderStringArrayEditor("translation_notes", "翻译注释", "translation_notes", "如 保留了 may be 的不确定性表达"),
      ];

      const renderTermNoteForm = () => [
        renderTypeSelector(),
        renderStringArrayEditor("sentence_ids", "句子 IDs", "sentence_ids", "如 s1"),
        renderTextField("text", "术语", "text", "如 longitudinal、mediated"),
        renderSelectField("term_category", "术语类别", "term_category", [
          { text: "technical — 专业术语", value: "technical" },
          { text: "sub_technical — 半专业术语", value: "sub_technical" },
        ]),
        renderTextField("zh", "中文释义", "zh", "如 纵向的、中介/调节"),
        renderBooleanField("zh_uncertain", "释义不确定", "zh_uncertain"),
        renderTextareaField("context_definition", "语境定义", "context_definition", "在该语境下的具体含义", 3),
        renderTextField("discipline", "学科领域", "discipline", "如 research_methodology、statistics"),
      ];

      const renderLogicNoteForm = () => [
        renderTypeSelector(),
        renderStringArrayEditor("sentence_ids", "句子 IDs", "sentence_ids", "如 s1"),
        renderSelectField("logic_type", "逻辑类型", "logic_type", [
          { text: "concession — 让步", value: "concession" },
          { text: "evidence — 证据支撑", value: "evidence" },
          { text: "contrast — 对比", value: "contrast" },
          { text: "cause_effect — 因果", value: "cause_effect" },
          { text: "condition — 条件", value: "condition" },
          { text: "purpose — 目的", value: "purpose" },
        ]),
        renderTextField("anchor_text", "逻辑标记词", "anchor_text", "如 Although、suggest that"),
        renderTextareaField("explanation", "逻辑关系说明", "explanation", "解释该逻辑关系的具体含义", 3),
        renderBooleanField("hedging_detected", "检测到模糊限制", "hedging_detected"),
        renderStringArrayEditor("hedging_words", "模糊限制词", "hedging_words", "如 suggest、may"),
      ];

      // --- Unknown type fallback ---
      const renderRawJsonEditor = () => {
        return h("div", { style: sectionStyle }, [
          h("label", { style: labelStyle }, "原始 JSON（未识别的类型，请手动编辑）"),
          h("textarea", {
            value: typeof props.value === "string" ? props.value : JSON.stringify(formData.value, null, 2),
            disabled: props.disabled, placeholder: "{}", rows: 8,
            style: { ...textareaStyle, minHeight: "120px" },
            onInput: (e) => {
              try {
                const parsed = JSON.parse(e.target.value);
                formData.value = parsed;
                emit("input", e.target.value);
              } catch {
                // Don't update on invalid JSON
              }
            },
          }),
        ]);
      };

      // --- Main render ---
      return () => {
        const layout = formLayout.value;
        let formContent;

        switch (layout) {
          case "grammar_note": formContent = renderGrammarNoteForm(); break;
          case "sentence_analysis": formContent = renderSentenceAnalysisForm(); break;
          case "vocab_highlight": formContent = renderVocabHighlightForm(); break;
          case "phrase_gloss": formContent = renderPhraseGlossForm(); break;
          case "context_gloss": formContent = renderContextGlossForm(); break;
          case "translation": formContent = renderTranslationForm(); break;
          case "academic_translation": formContent = renderAcademicTranslationForm(); break;
          case "term_note": formContent = renderTermNoteForm(); break;
          case "logic_note": formContent = renderLogicNoteForm(); break;
          default: formContent = [renderRawJsonEditor()]; break;
        }

        const typeBadge = fragmentType.value
          ? h("div", {
              style: {
                display: "inline-block", padding: "2px 8px",
                borderRadius: "10px", fontSize: "11px", fontWeight: "600",
                marginBottom: "10px",
                background: "var(--theme--primary-background, #EEF2FF)",
                color: "var(--theme--primary, #4F46E5)",
                border: "1px solid var(--theme--primary, #4F46E5)",
              },
            }, fragmentType.value)
          : null;

        return h("div", { style: { padding: "4px 0" } }, [typeBadge, ...formContent].filter(Boolean));
      };
    },
  }),
};
