<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  candidates: { type: Array, default: () => [] },
  modelProfiles: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
  datasets: { type: Array, default: () => [] },
  initialCandidateId: { type: String, default: "" },
});
const emit = defineEmits(["submit", "open-dataset-workspace"]);
const STORAGE_KEY = "claread-eval-center:workflow-lab:dataset-run-form:v1";

const form = ref({
  dataset_id: "",
  adapter_kind: "http",
  eval_purpose: "prompt_experiment",
  rag_mode: "off",
  trace_scope: "off",
  timeout_seconds: 120,
  prompt_variant_id: "",
  model_profile: "",
});
const error = ref("");

const candidateOptions = computed(() => props.candidates
  .filter((candidate) => candidate.prompt_bundle_summary?.topology_mode === "learning")
  .map((candidate) => ({
    value: candidate.variant_id,
    label: `${candidate.variant_id} / ${candidate.prompt_bundle_summary?.reading_variant || "learning"} / ${candidate.snapshot_hash || "snapshot pending"}`,
  })));

const datasetOptions = computed(() => props.datasets.map((dataset) => ({
  value: dataset.id,
  label: dataset.id,
})));
const modelOptions = computed(() => [
  { value: "", label: "使用默认模型方案" },
  ...props.modelProfiles.map((profile) => ({
    value: profile.profile_name,
    label: `${profile.profile_name} · ${profile.model_name}`,
  })),
]);

watch(
  () => props.datasets,
  (datasets) => {
    if (!form.value.dataset_id && datasets.length) {
      form.value.dataset_id = datasets[0].id;
    }
  },
  { immediate: true },
);

watch(
  () => [props.initialCandidateId, candidateOptions.value.map((option) => option.value).join("|")],
  () => {
    if (props.initialCandidateId && candidateOptions.value.some((option) => option.value === props.initialCandidateId)) {
      form.value.prompt_variant_id = props.initialCandidateId;
      return;
    }
    if (!form.value.prompt_variant_id && candidateOptions.value.length > 0) {
      form.value.prompt_variant_id = candidateOptions.value[0].value;
    }
  },
  { immediate: true },
);

watch(
  () => form.value.prompt_variant_id,
  (value) => {
    if (value) form.value.rag_mode = "off";
  },
);

watch(
  form,
  (value) => {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  },
  { deep: true },
);

const hasDataset = computed(() => form.value.dataset_id && datasetOptions.value.some((option) => option.value === form.value.dataset_id));
const canSubmit = computed(() => hasDataset.value && !props.submitting);
const inheritedCandidate = computed(() => (
  props.initialCandidateId && candidateOptions.value.some((option) => option.value === props.initialCandidateId)
    ? props.initialCandidateId
    : ""
));

try {
  const raw = window.sessionStorage.getItem(STORAGE_KEY);
  if (raw) {
    const saved = JSON.parse(raw);
    if (saved && typeof saved === "object") {
      form.value = {
        ...form.value,
        dataset_id: String(saved.dataset_id || ""),
        adapter_kind: String(saved.adapter_kind || "http"),
        eval_purpose: String(saved.eval_purpose || "prompt_experiment"),
        rag_mode: String(saved.rag_mode || "off"),
        trace_scope: String(saved.trace_scope || "off"),
        timeout_seconds: Number(saved.timeout_seconds) || 120,
        prompt_variant_id: String(saved.prompt_variant_id || ""),
        model_profile: String(saved.model_profile || ""),
      };
    }
  }
} catch {
  // ignore malformed session state
}

function submit() {
  error.value = "";
  if (!hasDataset.value) {
    error.value = "请先选择一个 dataset。";
    return;
  }
  const modelSelection = {};
  if (form.value.model_profile) {
    Object.assign(modelSelection, { default_profile: form.value.model_profile });
  }
  emit("submit", {
    execution_mode: "directus_async",
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
        <p>数据集验证</p>
        <h2>发起数据集验证</h2>
      </div>
      <span class="tag">后台执行</span>
    </header>

    <p v-if="inheritedCandidate" class="inherit-hint">
      承接自单篇验证：<strong>{{ inheritedCandidate }}</strong>
    </p>
    <p v-if="error" class="wl-error" aria-live="assertive">{{ error }}</p>
    <div v-else-if="!hasDataset" class="wl-empty-state" aria-live="polite">
      <strong>暂无可用的 dataset</strong>
      <p>先到「数据集工作区」创建 dataset，再回来发起批量验证。</p>
      <button type="button" class="secondary-cta" @click="emit('open-dataset-workspace')">去数据集工作区</button>
    </div>

    <div class="wl-form-grid">
      <label>
        <span>数据集</span>
        <select v-model="form.dataset_id" :disabled="datasetOptions.length === 0">
          <option v-if="datasetOptions.length === 0" value="">暂无可用 dataset</option>
          <option v-for="option in datasetOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </label>
      <label>
        <span>候选版本</span>
        <select v-model="form.prompt_variant_id">
          <option v-for="candidate in candidateOptions" :key="candidate.value" :value="candidate.value">
            {{ candidate.label }}
          </option>
          <option value="">— 仅作 baseline 对照 —</option>
        </select>
      </label>
      <label>
        <span>模型方案</span>
        <select v-model="form.model_profile">
          <option v-for="option in modelOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </label>
    </div>

    <details class="advanced">
      <summary>更多设置</summary>
      <div class="wl-form-grid advanced-grid">
        <label>
          <span>运行目的</span>
          <select v-model="form.eval_purpose">
            <option value="prompt_experiment">候选实验</option>
            <option value="dataset_regression">数据集回归</option>
            <option value="manual_debug">人工排障</option>
          </select>
        </label>
        <label>
          <span>执行通道</span>
          <select v-model="form.adapter_kind">
            <option value="http">http</option>
            <option value="in_process">in_process</option>
          </select>
        </label>
        <label>
          <span>检索增强</span>
          <select v-model="form.rag_mode" :disabled="Boolean(form.prompt_variant_id)">
            <option value="off">关闭</option>
            <option value="baseline">沿用 baseline</option>
            <option value="rag">强制使用 RAG</option>
            <option value="rag_fallback">RAG 失败时回退</option>
            <option value="settings">沿用运行时设置</option>
          </select>
        </label>
        <label>
          <span>调试记录</span>
          <select v-model="form.trace_scope">
            <option value="off">关闭</option>
            <option value="isolated">仅保留当前运行</option>
            <option value="inherit">沿用上游设置</option>
          </select>
        </label>
        <label>
          <span>超时（秒）</span>
          <input v-model.number="form.timeout_seconds" type="number" min="1" />
        </label>
      </div>
    </details>

    <footer class="wl-actions">
      <button type="button" :disabled="!canSubmit" @click="submit">
        {{ submitting ? "加入中..." : "发起数据集验证" }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.wl-panel {
  container-type: inline-size;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
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
label span {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.wl-panel-header h2 {
  margin: 2px 0 0;
  font-size: 18px;
  line-height: 1.45;
}

.wl-panel-header > div {
  flex: 1 1 auto;
  min-width: 0;
}

.tag {
  flex: 0 0 auto;
  align-self: flex-start;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 3px 8px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.inherit-hint {
  margin: 10px 0 0;
  border: 1px solid color-mix(in srgb, var(--theme--primary) 40%, var(--theme--border-color));
  border-radius: 8px;
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
  padding: 10px 12px;
  position: relative;
}
.inherit-hint::before {
  content: "";
  position: absolute;
  top: 12px;
  left: 12px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--theme--primary);
}
.inherit-hint {
  padding-left: 26px;
}

.inherit-hint strong {
  color: var(--theme--foreground);
}

.wl-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}

.runner-bridge-note {
  margin: 12px 0 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
}

label {
  display: grid;
  gap: 6px;
  min-width: 0;
}

input,
select,
textarea,
button {
  min-height: 36px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
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
  white-space: nowrap;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.advanced {
  border-top: 1px solid var(--theme--border-color);
  margin-top: 14px;
  padding-top: 12px;
}

.advanced summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  font-weight: 700;
}

.advanced-grid {
  margin-top: 12px;
}

.span-2 {
  grid-column: 1 / -1;
}

.wl-actions {
  margin-top: 14px;
}

.wl-error {
  margin: 12px 0 0;
  border: 1px solid var(--theme--danger);
  border-radius: 8px;
  background: var(--theme--danger-background);
  color: var(--theme--foreground);
  padding: 10px 12px;
}

.wl-empty-state {
  margin-top: 12px;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px;
  display: grid;
  gap: 8px;
}

.wl-empty-state strong {
  font-size: 13px;
}

.wl-empty-state p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
}

.secondary-cta {
  justify-self: start;
}

@container (max-width: 700px) {
  .wl-form-grid {
    grid-template-columns: 1fr;
  }

  .wl-panel-header,
  .wl-actions {
    display: grid;
  }
}

@container (max-width: 560px) {
  .wl-panel {
    padding: 14px;
  }

  .wl-panel-header h2 {
    font-size: 16px;
  }

  .wl-actions button {
    width: 100%;
  }
}
</style>
