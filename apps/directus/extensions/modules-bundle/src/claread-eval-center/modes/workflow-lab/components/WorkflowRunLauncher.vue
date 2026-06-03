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

const candidateOptions = computed(() => props.candidates
  .filter((candidate) => candidate.prompt_bundle_summary?.topology_mode === "learning")
  .map((candidate) => ({
    value: candidate.variant_id,
    label: `${candidate.variant_id} / ${candidate.prompt_bundle_summary?.reading_variant || "learning"} / ${candidate.snapshot_hash || "snapshot pending"}`,
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
      throw new Error("model_selection 必须是 JSON object。");
    }
  } catch (err) {
    error.value = err?.message || "model_selection JSON 无效。";
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
        <p>数据集批跑</p>
        <h2>创建 dataset regression 运行</h2>
      </div>
      <span title="适合 Candidate 稳定后批量跑 eval dataset；快速验证一篇文章请使用单条调试。">Runner Bridge</span>
    </header>

    <p v-if="error" class="wl-error">{{ error }}</p>

    <div class="wl-form-grid">
      <label>
        <span title="必须是 evals/datasets 下已经存在的 dataset_id。本轮不提供 dataset 编辑面板。">数据集 ID</span>
        <input v-model="form.dataset_id" title="默认 article-analysis-v1。若不存在，提交时会返回 dataset_id 错误。" />
      </label>
      <label>
        <span title="选择运行方式。fake 用于快速验证链路，in_process/http 会调用真实服务。">运行适配器</span>
        <select v-model="form.adapter_kind">
          <option value="fake">fake（不调用真实 LLM）</option>
          <option value="in_process">in_process（本进程调用）</option>
          <option value="http">http（调用 services/api）</option>
        </select>
      </label>
      <label>
        <span title="用于标记这次运行的实验目的，不影响 workflow 业务逻辑。">运行目的</span>
        <select v-model="form.eval_purpose">
          <option value="prompt_experiment">Prompt 实验</option>
          <option value="dataset_regression">数据集回归</option>
          <option value="manual_debug">手动调试</option>
        </select>
      </label>
      <label>
        <span title="选择已保存且 ready_for_eval 的 Candidate。Candidate 会作为 prompt snapshot 注入本次运行。">Candidate</span>
        <select v-model="form.prompt_variant_id">
          <option value="">Baseline prompt</option>
          <option v-for="candidate in candidateOptions" :key="candidate.value" :value="candidate.value">
            {{ candidate.label }}
          </option>
        </select>
      </label>
      <label>
        <span title="当前 Candidate 与 RAG 互斥；选择 Candidate 后固定为 off。">RAG 模式</span>
        <select v-model="form.rag_mode" :disabled="Boolean(form.prompt_variant_id)">
          <option value="off">off</option>
          <option value="baseline">baseline</option>
          <option value="rag">rag</option>
          <option value="rag_fallback">rag_fallback</option>
          <option value="settings">settings</option>
        </select>
      </label>
      <label>
        <span title="控制是否记录 trace。调试真实 workflow 时可使用 isolated。">Trace</span>
        <select v-model="form.trace_scope">
          <option value="off">off</option>
          <option value="isolated">isolated</option>
          <option value="inherit">inherit</option>
        </select>
      </label>
      <label>
        <span title="单条 case 的超时时间，单位秒。">超时时间（秒）</span>
        <input v-model.number="form.timeout_seconds" type="number" min="1" />
      </label>
      <label class="span-2">
        <span title="传给 services/api 的 model_selection JSON。留空对象表示使用默认模型配置。">模型选择 JSON</span>
        <textarea v-model="form.model_selection_json" rows="3" spellcheck="false" />
      </label>
    </div>

    <footer class="wl-actions">
      <p>用于稳定候选方案的批量回归；不适合首次验证 Candidate。Manual CLI 仅保留在创建结果中用于调试。</p>
      <button type="button" :disabled="submitting" title="创建后台运行请求，页面可刷新队列状态。" @click="submit">
        {{ submitting ? "入队中..." : "加入运行队列" }}
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
