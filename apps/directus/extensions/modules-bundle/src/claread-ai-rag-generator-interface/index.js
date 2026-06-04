import { defineComponent, h, ref, resolveComponent } from "vue";

/**
 * Custom Directus Interface: AI RAG Field Generator.
 *
 * Bound to grammar_tags, structure_signals, and retrieval_text fields of eval_example_lab_entries.
 * Provides:
 *   - A model profile selector (fetched from Claread API via Directus proxy)
 *   - An "AI Generate" button that generates all RAG fields at once
 *   - Auto-fills grammar_tags, structure_signals, teaching_goal, retrieval_text
 *   - Field-specific display (JSON editor for tags/signals, textarea for retrieval_text)
 */
export default {
  id: "claread-ai-rag-generator-interface",
  name: "Claread AI RAG Generator",
  icon: "auto_awesome",
  description: "AI-generates RAG fields (grammar_tags, structure_signals, teaching_goal, retrieval_text) for Example Lab entries.",
  types: ["text", "json"],
  group: "presentation",
  options: [
    {
      field: "modelProfilesEndpoint",
      type: "string",
      name: "Model Profiles Endpoint",
      meta: {
        width: "full",
        interface: "input",
        note: "Endpoint to fetch model profiles list (Claread API proxy)",
      },
      schema: {
        default_value: "/eval-center/article-analysis/model-profiles",
      },
    },
    {
      field: "generateEndpoint",
      type: "string",
      name: "Generate Endpoint",
      meta: {
        width: "full",
        interface: "input",
        note: "Endpoint to call for AI generation",
      },
      schema: {
        default_value: "/eval-center/example-lab/ai-generate-rag-fields",
      },
    },
  ],
  component: defineComponent({
    props: [
      "value",
      "modelProfilesEndpoint",
      "generateEndpoint",
      "collection",
      "primaryKey",
      "field",
      "values",
      "disabled",
      "loading",
    ],
    setup(props, { emit }) {
      const VButton = resolveComponent("v-button");
      const VIcon = resolveComponent("v-icon");
      const RULE_MODE_VALUE = "__rule__";

      const generating = ref(false);
      const modelProfiles = ref([]);
      const selectedModel = ref(RULE_MODE_VALUE);
      const errorMsg = ref("");
      const successMsg = ref("");

      // Fetch model profiles from Claread API proxy (same as Node Lab)
      const fetchModels = async () => {
        const endpoint = props.modelProfilesEndpoint || "/eval-center/article-analysis/model-profiles";
        try {
          const resp = await fetch(endpoint, { credentials: "include", headers: { Accept: "application/json" } });
          if (!resp.ok) return;
          const payload = await resp.json();
          const profiles = payload?.data || [];
          modelProfiles.value = profiles.map((p) => ({
            text: `${p.profile_name} · ${p.model_name}${p.annotation_route_default ? " (default)" : ""}`,
            value: p.profile_name,
          }));
        } catch {
          // Silently fail - models list is optional
        }
      };

      const getSiblingValue = (fieldName) => {
        if (props.values && typeof props.values === "object") {
          return props.values[fieldName];
        }
        return undefined;
      };

      const handleGenerate = async () => {
        const sentenceText = getSiblingValue("sentence_text");
        const outputFragment = getSiblingValue("output_fragment");
        const readingVariant = getSiblingValue("reading_variant");

        if (!sentenceText) {
          errorMsg.value = "请先填写 sentence_text";
          successMsg.value = "";
          return;
        }

        generating.value = true;
        errorMsg.value = "";
        successMsg.value = "";

        const endpoint = props.generateEndpoint || "/eval-center/example-lab/ai-generate-rag-fields";

        try {
          const body = {
            sentence_text: sentenceText,
            output_fragment: typeof outputFragment === "string" ? JSON.parse(outputFragment || "{}") : (outputFragment || {}),
            reading_variant: readingVariant || "default",
          };
          if (selectedModel.value && selectedModel.value !== RULE_MODE_VALUE) {
            body.model_profile = selectedModel.value;
          }

          const resp = await fetch(endpoint, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(body),
          });

          if (!resp.ok) {
            const errPayload = await resp.json().catch(() => ({}));
            throw new Error(errPayload?.errors?.[0]?.message || `HTTP ${resp.status}`);
          }

          const payload = await resp.json();
          const data = payload?.data;
          if (!data) throw new Error("Empty response");

          // Update current field value
          const currentField = props.field;
          if (currentField === "grammar_tags" && data.grammar_tags) {
            emit("input", JSON.stringify(data.grammar_tags));
          } else if (currentField === "structure_signals" && data.structure_signals) {
            emit("input", JSON.stringify(data.structure_signals));
          } else if (currentField === "retrieval_text" && data.retrieval_text) {
            emit("input", data.retrieval_text);
          }

          // Update sibling fields via Directus form store
          updateSiblingFields(data, currentField);

          const latency = data.latency_ms ? ` (${Math.round(data.latency_ms / 1000)}s)` : "";
          successMsg.value = generationSuccessLabel(data.generated_by, latency);
        } catch (err) {
          errorMsg.value = err.message || "生成失败";
          successMsg.value = "";
        } finally {
          generating.value = false;
        }
      };

      const generationSuccessLabel = (generatedBy, latency) => {
        if (generatedBy === "llm") return `LLM 生成成功${latency}`;
        if (generatedBy === "llm_fallback") return `LLM 调用失败，已回退规则生成${latency}`;
        return `规则生成成功${latency}`;
      };

      const resolveSiblingFormValues = () => {
        if (props.values && typeof props.values === "object") {
          return props.values;
        }

        const formEl = document.querySelector("[data-directus-form]");
        if (!formEl) return null;

        let formInstance = null;
        let el = formEl;
        while (el && !formInstance) {
          if (el.__vue__ && (el.__vue__.values || el.__vue__.editForm)) {
            formInstance = el.__vue__;
            break;
          }
          if (el.__vue_parent_component) {
            const instance = el.__vue_parent_component;
            if (instance.setupState?.values || instance.proxy?.values) {
              formInstance = instance;
              break;
            }
          }
          el = el.parentElement;
        }

        return formInstance?.values || formInstance?.proxy?.values || formInstance?.setupState?.values || null;
      };

      const updateSiblingFields = (data, currentField) => {
        const formValues = resolveSiblingFormValues();
        if (!formValues) return;

        if (currentField !== "grammar_tags" && data.grammar_tags) {
          formValues.grammar_tags = JSON.stringify(data.grammar_tags);
        }
        if (currentField !== "structure_signals" && data.structure_signals) {
          formValues.structure_signals = JSON.stringify(data.structure_signals);
        }
        if (currentField !== "retrieval_text" && data.retrieval_text) {
          formValues.retrieval_text = data.retrieval_text;
        }
        if (data.teaching_goal) {
          formValues.teaching_goal = data.teaching_goal;
        }
      };

      // Fetch models on mount
      if (typeof window !== "undefined") {
        fetchModels();
      }

      return () => {
        const children = [];
        const currentField = props.field || "retrieval_text";

        // Model selector + Generate button row
        children.push(
          h(
            "div",
            {
              style: {
                display: "flex",
                gap: "8px",
                alignItems: "flex-end",
                marginBottom: "8px",
              },
            },
            [
              h(
                "div",
                { style: { flex: "1 1 auto", minWidth: "200px" } },
                [
                  h(
                    "div",
                    {
                      style: {
                        fontSize: "12px",
                        fontWeight: "600",
                        color: "var(--theme--foreground-subdued, #6B7280)",
                        marginBottom: "4px",
                      },
                    },
                    "生成模式",
                  ),
                  h("select", {
                    value: selectedModel.value,
                    style: {
                      width: "100%",
                      padding: "6px 10px",
                      border: "1px solid var(--theme--border-color, #D1D5DB)",
                      borderRadius: "4px",
                      fontSize: "13px",
                      background: "var(--theme--background, #FFF)",
                      color: "var(--theme--foreground, #172940)",
                    },
                    onChange: (e) => {
                      selectedModel.value = e.target.value || RULE_MODE_VALUE;
                    },
                  }, [
                    h("option", { value: RULE_MODE_VALUE }, "规则模式（默认）"),
                    ...modelProfiles.value.map((p) =>
                      h("option", { value: p.value }, p.text),
                    ),
                  ]),
                ],
              ),
              h(
                VButton,
                {
                  kind: "primary",
                  secondary: true,
                  disabled: generating.value || props.disabled,
                  onClick: handleGenerate,
                },
                {
                  default: () => [
                    h(VIcon, { name: generating.value ? "hourglass_empty" : "auto_awesome", small: true }),
                    h("span", { style: { marginLeft: "6px" } }, generating.value ? "生成中..." : "AI 生成"),
                  ],
                },
              ),
            ],
          ),
        );

        // Success/error messages
        if (successMsg.value) {
          children.push(
            h(
              "div",
              {
                style: {
                  padding: "6px 10px",
                  marginBottom: "8px",
                  borderRadius: "4px",
                  fontSize: "12px",
                  background: "var(--theme--success-background, #ECFDF5)",
                  color: "var(--theme--success, #059669)",
                },
              },
              successMsg.value,
            ),
          );
        }
        if (errorMsg.value) {
          children.push(
            h(
              "div",
              {
                style: {
                  padding: "6px 10px",
                  marginBottom: "8px",
                  borderRadius: "4px",
                  fontSize: "12px",
                  background: "var(--theme--danger-background, #FEF2F2)",
                  color: "var(--theme--danger, #DC2626)",
                },
              },
              errorMsg.value,
            ),
          );
        }

        // Field-specific editor
        if (currentField === "grammar_tags" || currentField === "structure_signals") {
          // JSON array editor for tags/signals
          children.push(
            h("textarea", {
              value: props.value || "[]",
              disabled: props.disabled,
              placeholder: `${currentField}（AI 生成或手动编辑 JSON 数组）`,
              rows: 3,
              style: {
                width: "100%",
                padding: "8px 10px",
                border: "1px solid var(--theme--border-color, #D1D5DB)",
                borderRadius: "4px",
                fontSize: "12px",
                fontFamily: '"Cascadia Code", "Fira Code", "Consolas", monospace',
                lineHeight: "1.5",
                resize: "vertical",
                background: "var(--theme--background, #FFF)",
                color: "var(--theme--foreground, #172940)",
                boxSizing: "border-box",
              },
              onInput: (e) => {
                emit("input", e.target.value);
              },
            }),
          );
        } else {
          // Textarea for retrieval_text
          children.push(
            h("textarea", {
              value: props.value || "",
              disabled: props.disabled,
              placeholder: "retrieval_text（AI 生成或手动编辑）",
              rows: 6,
              style: {
                width: "100%",
                padding: "8px 10px",
                border: "1px solid var(--theme--border-color, #D1D5DB)",
                borderRadius: "4px",
                fontSize: "12px",
                fontFamily: '"Cascadia Code", "Fira Code", "Consolas", monospace',
                lineHeight: "1.5",
                resize: "vertical",
                background: "var(--theme--background, #FFF)",
                color: "var(--theme--foreground, #172940)",
                boxSizing: "border-box",
              },
              onInput: (e) => {
                emit("input", e.target.value);
              },
            }),
          );
        }

        return h(
          "div",
          {
            style: {
              display: "flex",
              flexDirection: "column",
              gap: "0",
            },
          },
          children,
        );
      };
    },
  }),
};
