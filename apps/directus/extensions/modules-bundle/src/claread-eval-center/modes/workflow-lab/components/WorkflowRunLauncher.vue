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
      throw new Error("高级模型设置需要是 JSON 对象。");
    }
  } catch (err) {
    error.value = err?.message || "高级模型设置无法解析。";
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
        <p>批量回归</p>
        <h2>把已验证的版本加入数据集回归队列</h2>
      </div>
      <span title="这一步会创建后台运行请求，结果会进入队列与已完成列表。">进入队列</span>
    </header>

    <p class="wl-hint">
      推荐在单篇验证通过后再批量回归。默认只保留最常用的设置，其他排障项可在“更多设置”中展开。
    </p>
    <p v-if="error" class="wl-error" aria-live="assertive">{{ error }}</p>

    <div class="wl-form-grid">
      <label>
        <span title="必须是已存在的数据集。">数据集</span>
        <input v-model="form.dataset_id" title="默认 article-analysis-v1。若不存在，提交时会返回数据集错误。" />
      </label>
      <label>
        <span title="不选时使用 baseline；选择后会把候选快照注入本次运行。">候选版本</span>
        <select v-model="form.prompt_variant_id">
          <option value="">使用 baseline</option>
          <option v-for="candidate in candidateOptions" :key="candidate.value" :value="candidate.value">
            {{ candidate.label }}
          </option>
        </select>
      </label>
      <label>
        <span title="fake 适合验证链路，真实结果请选 in_process 或 http。">执行方式</span>
        <select v-model="form.adapter_kind">
          <option value="fake">fake，快速检查链路</option>
          <option value="in_process">in_process，本进程调用</option>
          <option value="http">http，调用 services/api</option>
        </select>
      </label>
    </div>

    <details class="advanced">
      <summary>更多设置</summary>
      <div class="wl-form-grid advanced-grid">
        <label>
          <span title="仅用于标记这次运行的用途。">运行目的</span>
          <select v-model="form.eval_purpose">
            <option value="prompt_experiment">候选实验</option>
            <option value="dataset_regression">数据集回归</option>
            <option value="manual_debug">人工排障</option>
          </select>
        </label>
        <label>
          <span title="仅在排查检索相关问题时需要改动。">检索增强</span>
          <select v-model="form.rag_mode" :disabled="Boolean(form.prompt_variant_id)">
            <option value="off">关闭</option>
            <option value="baseline">沿用 baseline</option>
            <option value="rag">强制使用 RAG</option>
            <option value="rag_fallback">RAG 失败时回退</option>
            <option value="settings">沿用运行时设置</option>
          </select>
        </label>
        <label>
          <span title="只在需要追踪执行细节时打开。">调试记录</span>
          <select v-model="form.trace_scope">
            <option value="off">关闭</option>
            <option value="isolated">仅保留当前运行</option>
            <option value="inherit">沿用上游设置</option>
          </select>
        </label>
        <label>
          <span title="每条 case 的最长等待时间。">超时（秒）</span>
          <input v-model.number="form.timeout_seconds" type="number" min="1" />
        </label>
        <label class="span-2">
          <span title="默认情况下不需要填写。只有在排查模型路由问题时再展开。">高级模型设置 JSON</span>
          <textarea v-model="form.model_selection_json" rows="3" spellcheck="false" />
        </label>
      </div>
    </details>

    <footer class="wl-actions">
      <p>{{ form.prompt_variant_id ? "这次回归会使用已发布候选版本。" : "这次回归会使用 baseline。" }}</p>
      <button type="button" :disabled="submitting" title="创建后台运行请求，随后可在左侧查看队列状态。" @click="submit">
        {{ submitting ? "加入中..." : "加入回归队列" }}
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
.wl-actions p,
.wl-hint,
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

.wl-panel-header > span {
  flex: 0 0 auto;
  align-self: flex-start;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 4px 8px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  white-space: nowrap;
}

.wl-hint {
  margin-top: 12px;
  line-height: 1.6;
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
