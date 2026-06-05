<template>
  <div class="rag-gen">
    <section class="generator-card">
      <header class="card-header">
        <div class="title-wrap">
          <div class="title-row">
            <v-icon name="auto_awesome" small class="title-icon" />
            <span class="title">AI Generate</span>
          </div>
          <p class="subtitle">生成 grammar_tags、retrieval_text 和派生元数据，不改写 few-shot 本体。</p>
        </div>
        <span v-if="lastResult" class="status-pill" :class="isFallback ? 'status-pill-fallback' : 'status-pill-ok'">
          {{ isFallback ? "Fallback" : "LLM OK" }}
        </span>
      </header>

      <div class="control-grid">
        <label class="control-block">
          <span class="control-label">Model Profile</span>
          <v-select
            v-model="modelProfile"
            :items="modelSelectOptions"
            item-text="text"
            item-value="value"
            placeholder="Model profile"
            class="model-picker"
          />
        </label>

        <div class="action-block">
          <v-button
            :disabled="!canGenerate || generating"
            :loading="generating"
            class="generate-btn"
            @click="generate"
          >
            Generate
          </v-button>
        </div>
      </div>
    </section>

    <div v-if="prereqMessage" class="inline-hint">
      <v-icon name="info_outline" x-small />
      <span>{{ prereqMessage }}</span>
    </div>

    <v-notice v-if="error" type="danger" dense>{{ error }}</v-notice>

    <section v-if="lastResult" class="result-card">
      <header class="result-head">
        <div class="result-title-wrap">
          <div class="result-eyebrow">Latest Run</div>
          <div class="result-title">{{ isFallback ? "Rule fallback applied" : "LLM generation completed" }}</div>
        </div>

        <span v-if="confidenceLabel" class="confidence-pill" :class="'confidence-' + lastResult.confidence">
          {{ confidenceLabel }}
        </span>
      </header>

      <div class="stats-grid">
        <div v-if="generatedByLabel" class="stat-card">
          <span class="stat-label">Mode</span>
          <span class="stat-value">{{ generatedByLabel }}</span>
        </div>
        <div v-if="lastResult.derived_by" class="stat-card">
          <span class="stat-label">Derived By</span>
          <span class="stat-value">{{ lastResult.derived_by }}</span>
        </div>
        <div v-if="lastResult.profile_name" class="stat-card">
          <span class="stat-label">Profile</span>
          <span class="stat-value">{{ lastResult.profile_name }}</span>
        </div>
        <div v-if="lastResult.model_name" class="stat-card">
          <span class="stat-label">Model</span>
          <span class="stat-value">{{ lastResult.model_name }}</span>
        </div>
        <div v-if="lastResult.latency_ms !== undefined && lastResult.latency_ms !== null" class="stat-card">
          <span class="stat-label">Latency</span>
          <span class="stat-value">{{ lastResult.latency_ms }} ms</span>
        </div>
        <div v-if="inputTokens !== null" class="stat-card">
          <span class="stat-label">Input</span>
          <span class="stat-value">{{ inputTokens }}</span>
        </div>
        <div v-if="outputTokens !== null" class="stat-card">
          <span class="stat-label">Output</span>
          <span class="stat-value">{{ outputTokens }}</span>
        </div>
        <div v-if="totalTokens !== null" class="stat-card">
          <span class="stat-label">Total</span>
          <span class="stat-value">{{ totalTokens }}</span>
        </div>
      </div>

      <v-notice v-if="isFallback" type="warning" dense class="fallback-notice">
        本次未拿到 LLM 结构化结果，当前展示的是 rule-engine fallback，因此不会有字段理由或 token 用量。
      </v-notice>

      <div v-if="lastResult.reasoning || lastResult.fallback_reason" class="detail-actions">
        <button v-if="lastResult.reasoning" class="detail-btn" type="button" @click="showReasoning = !showReasoning">
          <v-icon :name="showReasoning ? 'expand_less' : 'expand_more'" x-small />
          <span>字段理由 / Rationale</span>
        </button>
        <button
          v-if="lastResult.fallback_reason"
          class="detail-btn detail-btn-warning"
          type="button"
          @click="showFallbackReason = !showFallbackReason"
        >
          <v-icon :name="showFallbackReason ? 'expand_less' : 'expand_more'" x-small />
          <span>Fallback 原因</span>
        </button>
      </div>

      <div v-if="lastResult.reasoning && showReasoning" class="detail-panel">
        <pre class="detail-body">{{ lastResult.reasoning }}</pre>
      </div>

      <div v-if="lastResult.fallback_reason && showFallbackReason" class="detail-panel">
        <pre class="detail-body detail-body-warning">{{ lastResult.fallback_reason }}</pre>
      </div>
    </section>
  </div>
</template>

<script>
import { computed, inject, nextTick, onMounted, ref, watch } from "vue";

const STORAGE_PREFIX = "claread:example-lab:ai-rag";
const GENERATED_FRAGMENT_KEY = "__ai_rag_generated";

function parseJsonValue(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return parsed === null || parsed === undefined ? fallback : parsed;
    } catch {
      return fallback;
    }
  }
  return value;
}

function readSessionState(key) {
  if (typeof window === "undefined" || !window.sessionStorage || !key) return null;
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeSessionState(key, value) {
  if (typeof window === "undefined" || !window.sessionStorage || !key) return;
  try {
    if (!value) {
      window.sessionStorage.removeItem(key);
      return;
    }
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore storage failures.
  }
}

function unwrapInjectedValue(value) {
  if (value && typeof value === "object" && "value" in value) return value.value;
  return value;
}

function stringifyEditorValue(value) {
  if (typeof value === "string") return value;
  if (value === undefined) return "";
  return JSON.stringify(value, null, 2);
}

function emitFieldValue(emit, field, value) {
  if (typeof emit !== "function" || !field) return;
  emit("setFieldValue", field, value);
  emit("setFieldValue", { field, value });
}

function syncTextareaField(root, value) {
  const textarea = root?.querySelector("textarea.sans-serif, textarea");
  if (!textarea) return false;

  const nextValue = typeof value === "string" ? value : stringifyEditorValue(value);
  if (textarea.value === nextValue) return true;

  textarea.value = nextValue;
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function syncCodeMirrorField(root, value) {
  const editor = root?.querySelector(".CodeMirror")?.CodeMirror;
  if (!editor || typeof editor.setValue !== "function") return false;

  const nextValue = stringifyEditorValue(value);
  if (editor.getValue?.() === nextValue) return true;

  editor.setValue(nextValue);
  if (typeof editor.save === "function") editor.save();
  return true;
}

function syncFieldEditor(field, value) {
  if (typeof document === "undefined" || !field || value === undefined) return;

  const root = document.querySelector(`[data-field="${field}"]`);
  if (!root) return;

  if (Array.isArray(value)) {
    if (syncCodeMirrorField(root, value)) return;
    syncTextareaField(root, value);
    return;
  }

  syncTextareaField(root, value);
}

export default {
  props: ["value", "collection", "primaryKey", "field", "disabled", "loading"],
  emits: ["input", "setFieldValue"],
  setup(props, { emit }) {
    const values = inject("values", ref({}));
    const api = inject("api", null);
    const primaryKeyRef = inject("primaryKey", ref(null));
    const collectionRef = inject("collection", ref("eval_example_lab_entries"));

    const generating = ref(false);
    const error = ref(null);
    const lastResult = ref(null);
    const modelProfile = ref(null);
    const modelProfiles = ref([]);
    const showReasoning = ref(false);
    const showFallbackReason = ref(false);

    const sentenceText = computed(() => String(values?.value?.sentence_text || ""));
    const outputFragment = computed(() => {
      const parsed = parseJsonValue(values?.value?.output_fragment, {});
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
      return parsed;
    });
    const readingVariant = computed(() => String(values?.value?.reading_variant || "intermediate_reading"));
    const exampleType = computed(() => String(values?.value?.example_type || ""));
    const exampleId = computed(() => String(values?.value?.example_id || "draft"));
    const collectionName = computed(() => props.collection || unwrapInjectedValue(collectionRef) || "eval_example_lab_entries");
    const itemPrimaryKey = computed(() => props.primaryKey ?? unwrapInjectedValue(primaryKeyRef) ?? null);

    const isRagEligible = computed(() => exampleType.value === "grammar" || exampleType.value === "sentence_analysis");

    const canGenerate = computed(() =>
      isRagEligible.value
      && sentenceText.value.trim().length > 0
      && outputFragment.value
      && typeof outputFragment.value === "object"
      && !Array.isArray(outputFragment.value)
      && String(outputFragment.value.type || "").trim().length > 0
      && String(modelProfile.value || "").trim().length > 0
    );

    const prereqMessage = computed(() => {
      if (!isRagEligible.value) return "Select example_type = grammar or sentence_analysis to enable AI generation.";
      if (!sentenceText.value.trim()) return "Fill sentence text before generating.";
      if (!String(outputFragment.value?.type || "").trim()) return "Choose an output fragment type before generating.";
      if (!String(modelProfile.value || "").trim()) return "Choose a model profile before generating.";
      return "";
    });

    const storageKey = computed(() => {
      const collection = collectionName.value;
      const recordId = itemPrimaryKey.value || exampleId.value || "draft";
      return `${STORAGE_PREFIX}:${collection}:${recordId}`;
    });

    const modelSelectOptions = computed(() =>
      modelProfiles.value.map((profile) => {
        const badges = [];
        if (profile.annotation_route_default) badges.push("annotation-default");
        if (profile.default_profile) badges.push("global-default");
        const suffix = badges.length ? ` [${badges.join(", ")}]` : "";
        return {
          text: `${profile.profile_name} (${profile.model_name || profile.provider})${suffix}`,
          value: profile.profile_name,
        };
      }),
    );

    const confidenceLabel = computed(() => {
      const value = String(lastResult.value?.confidence || "").toLowerCase();
      if (!value) return "";
      if (value === "high") return "High confidence";
      if (value === "medium") return "Medium confidence";
      if (value === "low") return "Low confidence";
      return value;
    });

    const usage = computed(() => lastResult.value?.usage || null);
    const generatedByLabel = computed(() => {
      const value = String(lastResult.value?.generated_by || "");
      if (value === "llm") return "LLM";
      if (value === "llm_fallback") return "LLM fallback";
      return value;
    });
    const inputTokens = computed(() => {
      const current = usage.value;
      if (!current || typeof current !== "object") return null;
      if (current.prompt_tokens !== undefined) return current.prompt_tokens;
      if (current.input_tokens !== undefined) return current.input_tokens;
      return null;
    });
    const outputTokens = computed(() => {
      const current = usage.value;
      if (!current || typeof current !== "object") return null;
      if (current.completion_tokens !== undefined) return current.completion_tokens;
      if (current.output_tokens !== undefined) return current.output_tokens;
      return null;
    });
    const totalTokens = computed(() => {
      const current = usage.value;
      if (!current || typeof current !== "object") return null;
      if (current.total_tokens !== undefined) return current.total_tokens;
      return null;
    });
    const isFallback = computed(() => String(lastResult.value?.generated_by || "") === "llm_fallback");

    function restoreSessionState() {
      const stored = readSessionState(storageKey.value);
      if (!stored) return;
      if (stored.modelProfile && !modelProfile.value) modelProfile.value = stored.modelProfile;
      if (stored.lastResult) lastResult.value = stored.lastResult;
    }

    async function loadModelProfiles() {
      if (!api) {
        error.value = "Directus API is unavailable in this interface.";
        return;
      }

      try {
        const response = await api.get("/eval-center/example-lab/model-profiles");
        const profiles = Array.isArray(response?.data?.data) ? response.data.data : [];
        modelProfiles.value = profiles;

        if (!modelProfile.value && profiles.length > 0) {
          const preferred =
            profiles.find((item) => item.annotation_route_default)
            || profiles.find((item) => item.default_profile)
            || profiles[0];
          modelProfile.value = preferred?.profile_name || null;
        }
      } catch (requestError) {
        error.value =
          requestError?.response?.data?.errors?.[0]?.message
          || requestError?.message
          || "Failed to load model profiles.";
      }
    }

    function updateSiblingFields(data) {
      const formValues = values?.value;
      const nextValues = {
        grammar_tags: data.grammar_tags,
        retrieval_text: data.retrieval_text,
        derived_by: data.derived_by,
      };

      for (const [field, value] of Object.entries(nextValues)) {
        if (value === undefined) continue;
        emitFieldValue(emit, field, value);
        if (formValues && typeof formValues === "object") {
          formValues[field] = value;
        }
      }

      const nextFragment = {
        ...outputFragment.value,
        [GENERATED_FRAGMENT_KEY]: Object.fromEntries(
          Object.entries(nextValues).filter(([, value]) => value !== undefined),
        ),
      };
      emitFieldValue(emit, "output_fragment", nextFragment);
      if (formValues && typeof formValues === "object") {
        formValues.output_fragment = nextFragment;
      }

      void nextTick(() => {
        window.setTimeout(() => {
          for (const [field, value] of Object.entries(nextValues)) {
            syncFieldEditor(field, value);
          }
        }, 0);
      });
    }

    async function generate() {
      if (!canGenerate.value || generating.value) return;
      if (!api) {
        error.value = "Directus API is unavailable in this interface.";
        return;
      }

      generating.value = true;
      error.value = null;
      showReasoning.value = false;
      showFallbackReason.value = false;

      try {
        const payload = {
          sentence_text: sentenceText.value,
          output_fragment: outputFragment.value,
          reading_variant: readingVariant.value,
          model_profile: modelProfile.value,
        };

        const response = await api.post("/eval-center/example-lab/ai-generate-rag-fields", payload);
        const data = response?.data?.data;
        if (!data || typeof data !== "object") {
          throw new Error("Empty response from AI generation API.");
        }

        lastResult.value = data;
        updateSiblingFields(data);
      } catch (requestError) {
        error.value =
          requestError?.response?.data?.errors?.[0]?.message
          || requestError?.message
          || "Failed to generate AI RAG fields.";
      } finally {
        generating.value = false;
      }
    }

    watch(storageKey, () => {
      restoreSessionState();
    }, { immediate: true });

    watch([lastResult, modelProfile], () => {
      writeSessionState(storageKey.value, {
        modelProfile: modelProfile.value || null,
        lastResult: lastResult.value || null,
      });
    }, { deep: true });

    onMounted(() => {
      restoreSessionState();
      void loadModelProfiles();
    });

    return {
      canGenerate,
      confidenceLabel,
      error,
      generate,
      generating,
      inputTokens,
      isFallback,
      lastResult,
      modelProfile,
      modelSelectOptions,
      outputTokens,
      prereqMessage,
      showFallbackReason,
      showReasoning,
      totalTokens,
      generatedByLabel,
    };
  },
};
</script>

<style scoped>
.rag-gen {
  display: grid;
  gap: 12px;
}

.generator-card,
.result-card {
  border: 1px solid var(--theme--border-color, #d9e1ea);
  border-radius: 12px;
  background: var(--theme--background-page, #fff);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.05);
}

.generator-card {
  padding: 16px;
  background:
    linear-gradient(135deg, rgba(59, 130, 246, 0.08), rgba(16, 185, 129, 0.04)),
    var(--theme--background-subdued, #f8fafc);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.title-wrap {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title-icon {
  color: var(--theme--primary, #2563eb);
}

.title {
  font-size: 14px;
  font-weight: 700;
  color: var(--theme--foreground, #111827);
  letter-spacing: -0.01em;
}

.subtitle {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--theme--foreground-subdued, #64748b);
}

.status-pill,
.confidence-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.status-pill-ok,
.confidence-high {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.status-pill-fallback,
.confidence-medium {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.confidence-low {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
}

.control-grid {
  margin-top: 14px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: end;
}

.control-block {
  display: grid;
  gap: 6px;
}

.control-label {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--theme--foreground-subdued, #64748b);
}

.model-picker {
  width: 100%;
}

.action-block {
  display: flex;
  align-items: end;
  justify-content: flex-end;
}

.generate-btn {
  min-width: 120px;
}

.inline-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(148, 163, 184, 0.08);
  color: var(--theme--foreground-subdued, #64748b);
  font-size: 12px;
  line-height: 1.45;
}

.result-card {
  padding: 16px;
  display: grid;
  gap: 14px;
}

.result-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.result-title-wrap {
  min-width: 0;
}

.result-eyebrow {
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--theme--foreground-subdued, #64748b);
}

.result-title {
  margin-top: 3px;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.35;
  color: var(--theme--foreground, #111827);
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 10px;
}

.stat-card {
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--theme--background-subdued, #f8fafc);
  border: 1px solid rgba(148, 163, 184, 0.16);
}

.stat-label {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--theme--foreground-subdued, #64748b);
}

.stat-value {
  font-size: 12.5px;
  line-height: 1.4;
  font-weight: 600;
  color: var(--theme--foreground, #111827);
  font-variant-numeric: tabular-nums;
  word-break: break-word;
}

.fallback-notice {
  margin: 0;
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid rgba(59, 130, 246, 0.14);
  background: rgba(59, 130, 246, 0.08);
  color: var(--theme--primary, #2563eb);
  border-radius: 999px;
  padding: 7px 12px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.detail-btn:hover {
  opacity: 0.9;
}

.detail-btn-warning {
  border-color: rgba(245, 158, 11, 0.18);
  background: rgba(245, 158, 11, 0.1);
  color: #b45309;
}

.detail-panel {
  margin-top: -4px;
}

.detail-body {
  margin: 0;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: var(--theme--background-subdued, #fafafb);
  color: var(--theme--foreground, #334155);
  font-size: 12.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}

.detail-body-warning {
  border-color: rgba(245, 158, 11, 0.18);
  background: rgba(255, 251, 235, 0.9);
}

@media (max-width: 720px) {
  .card-header,
  .result-head {
    flex-direction: column;
    align-items: stretch;
  }

  .control-grid {
    grid-template-columns: 1fr;
  }

  .action-block {
    justify-content: stretch;
  }

  .generate-btn {
    width: 100%;
  }
}
</style>
