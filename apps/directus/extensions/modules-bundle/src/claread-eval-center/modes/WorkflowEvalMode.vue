<script setup>
import { computed, ref } from "vue";
import ResultBlock from "../components/ResultBlock.vue";

const api = useApi();

const mode = ref("preset");

const presetId = ref("article-analysis-baseline-fake");
const customRunId = ref("");

const adapterKind = ref("fake");
const datasetId = ref("article-analysis-v1");
const evalPurpose = ref("prompt_experiment");
const ragMode = ref("off");
const timeoutSeconds = ref(120);

const submitting = ref(false);
const submitResult = ref(null);
const submitError = ref(null);

const presetOptions = [
  { text: "Baseline Fake（无 LLM，验证流程）", value: "article-analysis-baseline-fake" },
  { text: "No-Few-Shot Fake（无 LLM，关闭 few-shot）", value: "article-analysis-no-few-shot-fake" },
  { text: "Smoke Fake（冒烟测试）", value: "smoke-fake" },
];

const adapterOptions = [
  { text: "Fake（无 LLM 调用，快速验证流程）", value: "fake" },
  { text: "In-Process（真实 LLM 调用，需 API Key）", value: "in_process" },
];

const purposeOptions = [
  { text: "数据集回归", value: "dataset_regression" },
  { text: "Prompt 实验", value: "prompt_experiment" },
  { text: "手动调试", value: "manual_debug" },
];

const ragOptions = [
  { text: "Off（无 RAG）", value: "off" },
  { text: "Baseline", value: "baseline" },
  { text: "RAG", value: "rag" },
  { text: "RAG Fallback", value: "rag_fallback" },
  { text: "Settings", value: "settings" },
];

const canSubmit = computed(() => {
  if (mode.value === "preset") return presetId.value && !submitting.value;
  return datasetId.value && !submitting.value;
});

async function submitRequest() {
  submitting.value = true;
  submitResult.value = null;
  submitError.value = null;

  const payload = {};
  if (mode.value === "preset") {
    payload.preset_id = presetId.value;
  } else {
    payload.dataset_id = datasetId.value;
    payload.adapter_kind = adapterKind.value;
    payload.eval_purpose = evalPurpose.value;
    payload.rag_mode = ragMode.value;
    payload.timeout_seconds = Number(timeoutSeconds.value) || 120;
  }

  if (customRunId.value.trim()) {
    payload.run_id = customRunId.value.trim();
  }

  try {
    const resp = await api.post("/eval-center/workflow-runs/requests", payload);
    submitResult.value = resp?.data?.data || resp?.data;
  } catch (err) {
    const errData = err?.response?.data;
    submitError.value = errData?.errors?.map((e) => e.message).join("; ") || err.message;
  } finally {
    submitting.value = false;
  }
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text);
}
</script>

<template>
  <section class="workflow-eval">
    <div class="section-heading">
      <h2>创建 Workflow Eval Run</h2>
      <p>生成 eval run 配置。此页面不会自动运行 eval，也不会生成 run artifact。你需要手动保存 YAML 并在终端执行 CLI 命令。</p>
    </div>

    <div class="mode-tabs">
      <button class="mode-tab" :class="{ 'is-active': mode === 'preset' }" type="button" @click="mode = 'preset'">使用 Config Preset</button>
      <button class="mode-tab" :class="{ 'is-active': mode === 'custom' }" type="button" @click="mode = 'custom'">自定义配置</button>
    </div>

    <div v-if="mode === 'preset'" class="control-grid">
      <div class="control-group">
        <label>Config Preset</label>
        <select v-model="presetId">
          <option v-for="opt in presetOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
        </select>
        <small>从已有 run-configs 中选择。Preset 的 adapter_kind、dataset_id 等已预设。</small>
      </div>

      <div class="control-group">
        <label>自定义 Run ID（可选）</label>
        <input v-model="customRunId" type="text" placeholder="留空自动生成" />
        <small>仅允许字母、数字、连字符和点号。已存在的 run_id 会被拒绝。</small>
      </div>
    </div>

    <div v-else class="control-grid">
      <div class="control-group">
        <label>Adapter</label>
        <select v-model="adapterKind">
          <option v-for="opt in adapterOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
        </select>
        <small v-if="adapterKind === 'fake'">Fake adapter 不调用 LLM，仅验证流程和 schema。</small>
        <small v-else>In-Process adapter 会调用真实 LLM，需要 API Key 且产生 token 费用。</small>
      </div>

      <div class="control-group">
        <label>Dataset</label>
        <input v-model="datasetId" type="text" placeholder="article-analysis-v1" />
        <small>当前可用数据集：article-analysis-v1</small>
      </div>

      <div class="control-group">
        <label>Eval Purpose</label>
        <select v-model="evalPurpose">
          <option v-for="opt in purposeOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
        </select>
      </div>

      <div class="control-group">
        <label>RAG Mode</label>
        <select v-model="ragMode">
          <option v-for="opt in ragOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
        </select>
      </div>

      <div class="control-group">
        <label>Timeout（秒）</label>
        <input v-model.number="timeoutSeconds" type="number" min="10" max="600" />
      </div>

      <div class="control-group">
        <label>自定义 Run ID（可选）</label>
        <input v-model="customRunId" type="text" placeholder="留空自动生成" />
        <small>仅允许字母、数字、连字符和点号。已存在的 run_id 会被拒绝。</small>
      </div>

      <div class="control-group full-width">
        <div class="warning-banner">
          <strong>注意：</strong>自定义配置暂不支持 prompt_variant_id。如需测试 prompt variant，请使用 "No-Few-Shot Fake" preset 或直接编辑 YAML。
        </div>
      </div>
    </div>

    <div class="action-bar">
      <button class="submit-btn" :disabled="!canSubmit" type="button" @click="submitRequest">
        {{ submitting ? "生成中…" : "生成 Run Config" }}
      </button>
    </div>

    <div v-if="submitError" class="submit-error">
      <strong>生成失败：</strong>{{ submitError }}
    </div>

    <div v-if="submitResult" class="submit-result">
      <ResultBlock title="Run Config 生成结果" :open="true">
        <div class="result-summary">
          <div class="result-row">
            <span class="result-label">Status</span>
            <span class="verdict-pill pending">{{ submitResult.status }}</span>
          </div>
          <div class="result-row">
            <span class="result-label">Run ID</span>
            <code>{{ submitResult.run_id }}</code>
          </div>
          <div class="result-row" v-if="submitResult.preset_id">
            <span class="result-label">Preset</span>
            <code>{{ submitResult.preset_id }}</code>
          </div>
        </div>

        <div class="steps-section">
          <p class="steps-heading"><strong>手动执行步骤：</strong></p>
          <ol class="steps-list">
            <li>
              <strong>保存 YAML 配置</strong>：将下方 YAML 内容保存为
              <code>{{ submitResult.config_file }}</code>
              <button class="copy-btn" type="button" @click="copyToClipboard(submitResult.yaml_content)">复制 YAML</button>
            </li>
            <li>
              <strong>执行 CLI 命令</strong>：
              <div class="cli-box">
                <code>{{ submitResult.recommended_cli_command }}</code>
                <button class="copy-btn" type="button" @click="copyToClipboard(submitResult.recommended_cli_command)">复制</button>
              </div>
            </li>
            <li>
              <strong>查看结果</strong>：执行完成后，回到 Eval Center → 运行历史 → 刷新。
            </li>
          </ol>
        </div>

        <div class="pending-warning">
          ⚠️ {{ submitResult.message }}
        </div>

        <details class="yaml-preview" open>
          <summary>YAML 配置内容</summary>
          <pre>{{ submitResult.yaml_content }}</pre>
        </details>

        <details v-if="submitResult.config" class="config-preview">
          <summary>Config Object（自定义配置）</summary>
          <pre>{{ JSON.stringify(submitResult.config, null, 2) }}</pre>
        </details>
      </ResultBlock>
    </div>
  </section>
</template>

<style scoped>
.workflow-eval {
  max-width: 860px;
}

.section-heading h2 {
  margin: 0 0 4px;
  font-size: 18px;
}

.section-heading p {
  margin: 0 0 16px;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

.mode-tabs {
  display: flex;
  gap: 0;
  margin-bottom: 16px;
  border-bottom: 2px solid var(--theme--border-color);
}

.mode-tab {
  padding: 8px 16px;
  border: 0;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  background: transparent;
  color: var(--theme--foreground-subdued);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.mode-tab.is-active {
  border-bottom-color: var(--theme--primary);
  color: var(--theme--primary);
}

.control-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 20px;
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.control-group.full-width {
  grid-column: 1 / -1;
}

.control-group label {
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground-subdued);
}

.control-group select,
.control-group input {
  padding: 8px 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 13px;
}

.control-group select:focus,
.control-group input:focus {
  border-color: var(--theme--primary);
  outline: none;
}

.control-group small {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.warning-banner {
  padding: 10px 14px;
  border: 1px solid var(--theme--warning-background);
  border-radius: 4px;
  background: var(--theme--warning-background);
  font-size: 12px;
  color: var(--theme--foreground);
}

.action-bar {
  margin-bottom: 20px;
}

.submit-btn {
  padding: 10px 24px;
  border: 0;
  border-radius: 4px;
  background: var(--theme--primary);
  color: var(--theme--background);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.submit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.submit-error {
  padding: 12px 16px;
  border: 1px solid var(--theme--danger);
  border-radius: 4px;
  background: var(--theme--danger-background);
  color: var(--theme--foreground);
  font-size: 13px;
  margin-bottom: 16px;
}

.submit-result {
  margin-top: 16px;
}

.result-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}

.result-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.result-label {
  min-width: 100px;
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground-subdued);
}

.verdict-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.verdict-pill.pending {
  background: var(--theme--warning-background);
  color: var(--theme--foreground);
}

.steps-section {
  margin-bottom: 16px;
}

.steps-heading {
  margin: 0 0 8px;
  font-size: 13px;
}

.steps-list {
  margin: 0;
  padding-left: 24px;
  font-size: 13px;
  line-height: 1.8;
}

.steps-list li {
  margin-bottom: 8px;
}

.cli-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background-subdued);
  margin-top: 6px;
}

.cli-box code {
  flex: 1;
  font-size: 13px;
  word-break: break-all;
}

.pending-warning {
  padding: 10px 14px;
  border: 1px solid var(--theme--warning-background);
  border-radius: 4px;
  background: var(--theme--warning-background);
  font-size: 12px;
  color: var(--theme--foreground);
  margin-bottom: 16px;
}

.copy-btn {
  flex: 0 0 auto;
  padding: 4px 12px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.copy-btn:hover {
  background: var(--theme--background-subdued);
}

.yaml-preview,
.config-preview {
  margin-top: 12px;
}

.yaml-preview summary,
.config-preview summary {
  cursor: pointer;
  color: var(--theme--primary);
  font-size: 12px;
  font-weight: 700;
}

.yaml-preview pre,
.config-preview pre {
  margin: 8px 0 0;
  padding: 12px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background-subdued);
  font-size: 12px;
  overflow-x: auto;
}

@media (max-width: 720px) {
  .control-grid {
    grid-template-columns: 1fr;
  }
}
</style>
