<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  candidates: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
});
const emit = defineEmits(["submit"]);

const form = ref({
  dataset_id: "article-analysis-v1",
  adapter_kind: "fake",
  eval_purpose: "prompt_experiment",
  rag_mode: "off",
  trace_scope: "off",
  timeout_seconds: 120,
  prompt_variant_id: "",
  model_selection_json: "{}",
});
const error = ref("");

const candidateOptions = computed(() => props.candidates.map((candidate) => ({
  value: candidate.variant_id,
  label: `${candidate.variant_id} / ${candidate.snapshot_hash || "snapshot pending"}`,
})));

watch(
  () => form.value.prompt_variant_id,
  (value) => {
    if (value) form.value.rag_mode = "off";
  },
);

function submit() {
  error.value = "";
  let modelSelection = {};
  try {
    modelSelection = JSON.parse(form.value.model_selection_json || "{}");
    if (!modelSelection || typeof modelSelection !== "object" || Array.isArray(modelSelection)) {
      throw new Error("model_selection must be a JSON object.");
    }
  } catch (err) {
    error.value = err?.message || "Invalid model_selection JSON.";
    return;
  }
  emit("submit", {
    execution_mode: "runner_bridge",
    dataset_id: form.value.dataset_id.trim(),
    adapter_kind: form.value.adapter_kind,
    eval_purpose: form.value.eval_purpose,
    rag_mode: form.value.rag_mode,
    trace_scope: form.value.trace_scope,
    timeout_seconds: Number(form.value.timeout_seconds) || 120,
    model_selection: modelSelection,
    ...(form.value.prompt_variant_id ? { prompt_variant_id: form.value.prompt_variant_id } : {}),
  });
}
</script>

<template>
  <section class="wl-panel">
    <header class="wl-panel-header">
      <div>
        <p>Run Launcher</p>
        <h2>Create learning run</h2>
      </div>
      <span>Runner Bridge</span>
    </header>

    <p v-if="error" class="wl-error">{{ error }}</p>

    <div class="wl-form-grid">
      <label>
        <span>Dataset</span>
        <input v-model="form.dataset_id" />
      </label>
      <label>
        <span>Adapter</span>
        <select v-model="form.adapter_kind">
          <option value="fake">fake</option>
          <option value="in_process">in_process</option>
          <option value="http">http</option>
        </select>
      </label>
      <label>
        <span>Purpose</span>
        <select v-model="form.eval_purpose">
          <option value="prompt_experiment">prompt_experiment</option>
          <option value="dataset_regression">dataset_regression</option>
          <option value="manual_debug">manual_debug</option>
        </select>
      </label>
      <label>
        <span>Candidate</span>
        <select v-model="form.prompt_variant_id">
          <option value="">baseline prompt</option>
          <option v-for="candidate in candidateOptions" :key="candidate.value" :value="candidate.value">
            {{ candidate.label }}
          </option>
        </select>
      </label>
      <label>
        <span>RAG</span>
        <select v-model="form.rag_mode" :disabled="Boolean(form.prompt_variant_id)">
          <option value="off">off</option>
          <option value="baseline">baseline</option>
          <option value="rag">rag</option>
          <option value="rag_fallback">rag_fallback</option>
          <option value="settings">settings</option>
        </select>
      </label>
      <label>
        <span>Trace</span>
        <select v-model="form.trace_scope">
          <option value="off">off</option>
          <option value="isolated">isolated</option>
          <option value="inherit">inherit</option>
        </select>
      </label>
      <label>
        <span>Timeout seconds</span>
        <input v-model.number="form.timeout_seconds" type="number" min="1" />
      </label>
      <label class="span-2">
        <span>Model selection JSON</span>
        <textarea v-model="form.model_selection_json" rows="3" spellcheck="false" />
      </label>
    </div>

    <footer class="wl-actions">
      <p>Manual CLI is retained only in the created request payload for debugging.</p>
      <button type="button" :disabled="submitting" @click="submit">
        {{ submitting ? "Queueing..." : "Queue run" }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.wl-panel {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 16px;
}
.wl-panel-header,
.wl-actions {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.wl-panel-header p,
.wl-actions p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
.wl-panel-header h2 {
  margin: 2px 0 0;
  font-size: 18px;
}
.wl-panel-header > span {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 4px 8px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
.wl-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}
label {
  display: grid;
  gap: 6px;
  min-width: 0;
}
label span {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
input,
select,
textarea,
button {
  min-height: 36px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  padding: 7px 9px;
}
textarea {
  resize: vertical;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
}
button {
  cursor: pointer;
  font-weight: 700;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.span-2 {
  grid-column: 1 / -1;
}
.wl-actions {
  margin-top: 14px;
}
.wl-error {
  margin: 12px 0 0;
  color: var(--theme--danger);
}
@media (max-width: 760px) {
  .wl-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
