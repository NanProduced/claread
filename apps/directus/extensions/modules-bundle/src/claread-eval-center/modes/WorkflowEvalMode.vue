<script setup>
import { computed, onMounted, ref } from "vue";
import ResultBlock from "../components/ResultBlock.vue";

const api = useApi();
const emit = defineEmits(["open-run-history"]);

const mode = ref("preset");
const executionMode = ref("manual");

const presetId = ref("article-analysis-baseline-fake");
const customRunId = ref("");

const adapterKind = ref("fake");
const datasetId = ref("article-analysis-v1");
const evalPurpose = ref("prompt_experiment");
const ragMode = ref("off");
const traceScope = ref("off");
const timeoutSeconds = ref(120);
const promptVariantId = ref("");
const modelSelectionJson = ref("{}");

const submitting = ref(false);
const submitResult = ref(null);
const submitError = ref(null);
const requestRows = ref([]);
const requestLoading = ref(false);
const requestError = ref(null);
const requestStatusFilter = ref("all");
const promptVariants = ref([]);
const promptVariantsLoading = ref(false);
const expandedRequestId = ref("");

const presetOptions = ref([
  { text: "Baseline Fake（无 LLM，验证流程）", value: "article-analysis-baseline-fake" },
  { text: "No-Few-Shot Fake（无 LLM，关闭 few-shot）", value: "article-analysis-no-few-shot-fake" },
  { text: "Smoke Fake（冒烟测试）", value: "smoke-fake" },
]);

const executionOptions = [
  { text: "Manual CLI", value: "manual" },
  { text: "Runner Bridge Queue", value: "runner_bridge" },
];

const requestStatusOptions = [
  { text: "All", value: "all" },
  { text: "Queued", value: "queued" },
  { text: "Running", value: "running" },
  { text: "Succeeded", value: "succeeded" },
  { text: "Failed", value: "failed" },
  { text: "Cancelled", value: "cancelled" },
];

const adapterOptions = [
  { text: "Fake（无 LLM 调用，快速验证流程）", value: "fake" },
  { text: "In-Process（真实 LLM 调用，需 API Key）", value: "in_process" },
  { text: "HTTP services/api", value: "http" },
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

const traceScopeOptions = [
  { text: "Off", value: "off" },
  { text: "Isolated", value: "isolated" },
  { text: "Inherit", value: "inherit" },
];

const canSubmit = computed(() => {
  if (mode.value === "preset") return presetId.value && !submitting.value;
  return datasetId.value && !submitting.value && !promptVariantRagConflict.value;
});

const selectedPromptVariant = computed(() => (
  promptVariants.value.find((item) => item.variant_id === promptVariantId.value)
));

const promptVariantRagConflict = computed(() => Boolean(promptVariantId.value && ragMode.value !== "off"));

onMounted(() => {
  void loadConfigPresets();
  void loadPromptVariants();
  void loadRequests();
});

async function loadConfigPresets() {
  try {
    const resp = await api.get("/eval-center/workflow-runs/config-presets");
    const data = resp?.data?.data || resp?.data || [];
    if (Array.isArray(data) && data.length > 0) {
      presetOptions.value = data.map((item) => ({
        text: item.id,
        value: item.id,
      }));
      if (!presetOptions.value.some((item) => item.value === presetId.value)) {
        presetId.value = presetOptions.value[0].value;
      }
    }
  } catch {
    // Keep built-in presets if the endpoint is unavailable.
  }
}

async function loadPromptVariants() {
  promptVariantsLoading.value = true;
  try {
    const resp = await api.get("/eval-center/prompt-variants/ready");
    const data = resp?.data?.data || resp?.data || [];
    promptVariants.value = Array.isArray(data) ? data : [];
    if (promptVariantId.value && !promptVariants.value.some((item) => item.variant_id === promptVariantId.value)) {
      promptVariantId.value = "";
    }
  } catch {
    promptVariants.value = [];
  } finally {
    promptVariantsLoading.value = false;
  }
}

async function submitRequest() {
  submitting.value = true;
  submitResult.value = null;
  submitError.value = null;

  const payload = { execution_mode: executionMode.value };
  if (mode.value === "preset") {
    payload.preset_id = presetId.value;
  } else {
    let modelSelection = {};
    try {
      modelSelection = JSON.parse(modelSelectionJson.value || "{}");
      if (!modelSelection || typeof modelSelection !== "object" || Array.isArray(modelSelection)) {
        throw new Error("model_selection must be a JSON object.");
      }
    } catch (err) {
      submitError.value = err?.message || "Invalid model_selection JSON.";
      submitting.value = false;
      return;
    }
    payload.dataset_id = datasetId.value;
    payload.adapter_kind = adapterKind.value;
    payload.eval_purpose = evalPurpose.value;
    payload.rag_mode = ragMode.value;
    payload.trace_scope = traceScope.value;
    payload.model_selection = modelSelection;
    payload.timeout_seconds = Number(timeoutSeconds.value) || 120;
    if (promptVariantId.value) {
      payload.prompt_variant_id = promptVariantId.value;
    }
  }

  if (customRunId.value.trim()) {
    payload.run_id = customRunId.value.trim();
  }

  try {
    const resp = await api.post("/eval-center/workflow-runs/requests", payload);
    submitResult.value = resp?.data?.data || resp?.data;
    await loadRequests();
  } catch (err) {
    const errData = err?.response?.data;
    submitError.value = errData?.errors?.map((e) => e.message).join("; ") || err.message;
  } finally {
    submitting.value = false;
  }
}

async function loadRequests() {
  requestLoading.value = true;
  requestError.value = null;
  try {
    const resp = await api.get("/eval-center/workflow-runs/requests", {
      params: {
        status: requestStatusFilter.value,
        limit: 30,
      },
    });
    const data = resp?.data?.data || resp?.data || [];
    requestRows.value = Array.isArray(data) ? data : [];
  } catch (err) {
    const errData = err?.response?.data;
    requestError.value = errData?.errors?.map((e) => e.message).join("; ") || err.message;
  } finally {
    requestLoading.value = false;
  }
}

async function cancelRequest(row) {
  if (!row?.id || !["queued", "running"].includes(row.status)) return;
  const detail = row.status === "running"
    ? "The worker process is not killed; it will stop writing completion after heartbeat detects cancellation."
    : "The request will not be claimed by a worker.";
  const ok = window.confirm(`Cancel ${row.status} eval request ${row.run_id}?\n\n${detail}`);
  if (!ok) return;
  requestError.value = null;
  try {
    await api.post(`/eval-center/workflow-runs/requests/${encodeURIComponent(row.id)}/cancel`);
    await loadRequests();
  } catch (err) {
    const errData = err?.response?.data;
    requestError.value = errData?.errors?.map((e) => e.message).join("; ") || err.message;
  }
}

async function retryRequest(row) {
  if (!row?.id || !["failed", "cancelled"].includes(row.status)) return;
  const ok = window.confirm(
    `Retry eval request ${row.run_id} as a new run?\n\nA new run_id will be generated. Existing artifacts will not be modified.`,
  );
  if (!ok) return;
  requestError.value = null;
  try {
    await api.post(
      `/eval-center/workflow-runs/requests/${encodeURIComponent(row.id)}/retry`,
      { retry_reason: "manual_retry_from_runner_queue" },
    );
    requestStatusFilter.value = "all";
    await loadRequests();
  } catch (err) {
    const errData = err?.response?.data;
    requestError.value = errData?.errors?.map((e) => e.message).join("; ") || err.message;
  }
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function requestStatusClass(status) {
  return {
    queued: "pending",
    running: "running",
    succeeded: "success",
    failed: "failed",
    cancelled: "cancelled",
  }[status] || "pending";
}

function requestErrorSummary(row) {
  if (!row?.error) return "";
  return [row.error.code, row.error.message].filter(Boolean).join(": ");
}

function promptVariantLabel(item) {
  if (!item) return "";
  return `${item.variant_id} · ${item.snapshot_hash || "snapshot"}`;
}

function openRunHistory(row) {
  const runId = row?.artifact_run_id || row?.run_id;
  if (!runId) return;
  emit("open-run-history", runId);
}

function toggleRequestDetails(row) {
  expandedRequestId.value = expandedRequestId.value === row.id ? "" : row.id;
}

function attemptSummary(row) {
  const attemptNo = Number(row?.attempt_no || 1);
  if (attemptNo <= 1 && !row?.source_request_id) return "";
  return `Attempt ${attemptNo}${row?.source_request_id ? " · retry" : ""}`;
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

    <div class="control-grid">
      <div class="control-group">
        <label>Execution Mode</label>
        <select v-model="executionMode">
          <option v-for="opt in executionOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
        </select>
        <small>Manual only generates YAML. Runner Bridge only queues a request; an external worker still performs execution.</small>
      </div>
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
        <small v-if="promptVariantRagConflict" class="error-text">
          Prompt variant snapshot v1 requires RAG Mode Off.
        </small>
      </div>

      <div class="control-group">
        <label>Prompt Variant</label>
        <select v-model="promptVariantId" :disabled="promptVariantsLoading">
          <option value="">None</option>
          <option v-for="variant in promptVariants" :key="variant.variant_id" :value="variant.variant_id">
            {{ promptVariantLabel(variant) }}
          </option>
        </select>
        <small v-if="selectedPromptVariant">
          Ready snapshot {{ selectedPromptVariant.snapshot_hash }} will be embedded in the request.
        </small>
        <small v-else-if="promptVariantsLoading">Loading ready snapshots...</small>
        <small v-else>Only ready_for_eval workflow_eval snapshots are selectable.</small>
      </div>

      <div class="control-group">
        <label>Trace Scope</label>
        <select v-model="traceScope">
          <option v-for="opt in traceScopeOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
        </select>
        <small>Use Off unless you intentionally need LangSmith trace isolation.</small>
      </div>

      <div class="control-group">
        <label>Timeout（秒）</label>
        <input v-model.number="timeoutSeconds" type="number" min="10" max="600" />
      </div>

      <div class="control-group full-width">
        <label>Model Selection JSON</label>
        <textarea v-model="modelSelectionJson" rows="4" spellcheck="false" />
        <small>Leave as {} to use the default annotation_generation model route.</small>
      </div>

      <div class="control-group">
        <label>自定义 Run ID（可选）</label>
        <input v-model="customRunId" type="text" placeholder="留空自动生成" />
        <small>仅允许字母、数字、连字符和点号。已存在的 run_id 会被拒绝。</small>
      </div>

      <div class="control-group full-width">
        <div class="warning-banner">
          <strong>注意：</strong>Prompt Variant 会作为 eval-only snapshot 嵌入 request，不会修改业务 prompt YAML；v1 要求 RAG Mode 为 Off。
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
          <div class="result-row" v-if="submitResult.prompt_variant_id || submitResult.config?.prompt_variant_id">
            <span class="result-label">Prompt Variant</span>
            <code>{{ submitResult.prompt_variant_id || submitResult.config?.prompt_variant_id }}</code>
          </div>
          <div class="result-row">
            <span class="result-label">Execution</span>
            <code>{{ submitResult.execution_mode || "manual" }}</code>
          </div>
          <div class="result-row" v-if="submitResult.runner_bridge_request">
            <span class="result-label">Bridge</span>
            <code>{{ submitResult.runner_bridge_request.status }}</code>
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

    <section class="request-queue">
      <div class="queue-heading">
        <div>
          <h3>Runner Bridge Queue</h3>
          <p>Recent workflow eval requests created by Directus. Execution is performed by the external eval worker.</p>
        </div>
        <div class="queue-actions">
          <select v-model="requestStatusFilter" @change="loadRequests">
            <option v-for="opt in requestStatusOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
          </select>
          <button class="copy-btn" type="button" :disabled="requestLoading" @click="loadRequests">
            {{ requestLoading ? "Refreshing" : "Refresh" }}
          </button>
        </div>
      </div>

      <div v-if="requestError" class="submit-error">
        <strong>Queue error: </strong>{{ requestError }}
      </div>

      <div class="queue-table-wrap">
        <table class="queue-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Adapter</th>
              <th>Worker</th>
              <th>Heartbeat</th>
              <th>Artifact</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!requestLoading && requestRows.length === 0">
              <td colspan="7" class="empty-cell">No workflow eval requests found.</td>
            </tr>
            <tr v-for="row in requestRows" :key="row.id">
              <td>
                <div class="run-cell">
                  <code>{{ row.run_id }}</code>
                  <span>{{ row.dataset_id }}</span>
                  <small v-if="attemptSummary(row)" class="attempt-text">
                    {{ attemptSummary(row) }}
                  </small>
                  <small v-if="row.prompt_variant_id" class="variant-text">
                    Variant {{ row.prompt_variant_id }}
                  </small>
                  <small>{{ formatDate(row.date_created) }}</small>
                  <small v-if="requestErrorSummary(row)" class="error-text">
                    {{ requestErrorSummary(row) }}
                  </small>
                </div>
              </td>
              <td>
                <span class="verdict-pill" :class="requestStatusClass(row.status)">
                  {{ row.status }}
                </span>
              </td>
              <td>{{ row.adapter_kind }}</td>
              <td>{{ row.lease_owner || "-" }}</td>
              <td>{{ formatDate(row.heartbeat_at || row.lease_until) }}</td>
              <td>
                <code v-if="row.artifact_path">{{ row.artifact_path }}</code>
                <span v-else-if="row.expected_artifact_path" class="expected-artifact">
                  Expected {{ row.expected_artifact_path }}
                </span>
                <span v-else>-</span>
              </td>
              <td>
                <button
                  v-if="['queued', 'running'].includes(row.status)"
                  class="cancel-btn"
                  type="button"
                  @click="cancelRequest(row)"
                >
                  Cancel
                </button>
                <button
                  v-else-if="['failed', 'cancelled'].includes(row.status)"
                  class="retry-btn"
                  type="button"
                  @click="retryRequest(row)"
                >
                  Retry
                </button>
                <button
                  v-else-if="row.status === 'succeeded'"
                  class="open-btn"
                  type="button"
                  @click="openRunHistory(row)"
                >
                  Open
                </button>
                <button class="details-btn" type="button" @click="toggleRequestDetails(row)">
                  {{ expandedRequestId === row.id ? "Hide" : "Details" }}
                </button>
              </td>
            </tr>
            <tr v-for="row in requestRows.filter((item) => item.id === expandedRequestId)" :key="`${row.id}-details`">
              <td colspan="7">
                <div class="request-details">
                  <div>
                    <span>Request</span>
                    <code>{{ row.id }}</code>
                  </div>
                  <div>
                    <span>Attempt</span>
                    <code>{{ row.attempt_no || 1 }} / {{ row.max_attempts || 1 }}</code>
                  </div>
                  <div>
                    <span>Source</span>
                    <code>{{ row.source_request_id || "-" }}</code>
                  </div>
                  <div>
                    <span>Lease</span>
                    <code>{{ row.lease_owner || "-" }} · {{ formatDate(row.lease_until) }}</code>
                  </div>
                  <div>
                    <span>Started / Finished</span>
                    <code>{{ formatDate(row.started_at) }} · {{ formatDate(row.finished_at) }}</code>
                  </div>
                  <div>
                    <span>Config</span>
                    <code>{{ row.config_summary?.config_file || "-" }}</code>
                  </div>
                  <div v-if="row.retry_reason">
                    <span>Retry Reason</span>
                    <code>{{ row.retry_reason }}</code>
                  </div>
                  <div v-if="requestErrorSummary(row)">
                    <span>Error</span>
                    <code>{{ requestErrorSummary(row) }}</code>
                  </div>
                </div>
              </td>
            </tr>
            <tr v-if="requestLoading">
              <td colspan="7" class="empty-cell">Loading requests...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
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
.control-group input,
.control-group textarea {
  padding: 8px 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 13px;
}

.control-group select:focus,
.control-group input:focus,
.control-group textarea:focus {
  border-color: var(--theme--primary);
  outline: none;
}

.control-group textarea {
  resize: vertical;
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

.verdict-pill.running {
  background: var(--theme--primary-background);
  color: var(--theme--primary);
}

.verdict-pill.success {
  background: var(--theme--success-background);
  color: var(--theme--foreground);
}

.verdict-pill.failed {
  background: var(--theme--danger-background);
  color: var(--theme--danger);
}

.verdict-pill.cancelled {
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
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

.request-queue {
  margin-top: 28px;
  padding-top: 20px;
  border-top: 1px solid var(--theme--border-color);
}

.queue-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.queue-heading h3 {
  margin: 0 0 4px;
  font-size: 15px;
}

.queue-heading p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.queue-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.queue-actions select {
  padding: 6px 8px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 12px;
}

.queue-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
}

.queue-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 820px;
  font-size: 12px;
}

.queue-table th,
.queue-table td {
  padding: 10px;
  border-bottom: 1px solid var(--theme--border-color);
  text-align: left;
  vertical-align: top;
}

.queue-table th {
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}

.queue-table tr:last-child td {
  border-bottom: 0;
}

.run-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 180px;
}

.run-cell span,
.run-cell small,
.muted {
  color: var(--theme--foreground-subdued);
}

.error-text {
  color: var(--theme--danger);
  max-width: 280px;
  overflow-wrap: anywhere;
}

.expected-artifact {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.attempt-text {
  color: var(--theme--primary);
}

.variant-text {
  color: var(--theme--foreground-subdued);
}

.empty-cell {
  color: var(--theme--foreground-subdued);
  text-align: center;
}

.cancel-btn {
  padding: 4px 10px;
  border: 1px solid var(--theme--danger);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--danger);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.cancel-btn:hover {
  background: var(--theme--danger-background);
}

.retry-btn {
  padding: 4px 10px;
  border: 1px solid var(--theme--primary);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--primary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.retry-btn:hover {
  background: var(--theme--primary-background);
}

.open-btn,
.details-btn {
  padding: 4px 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  margin-left: 6px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.open-btn {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.open-btn:hover,
.details-btn:hover {
  background: var(--theme--background-subdued);
}

.request-details {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background-subdued);
  padding: 10px;
}

.request-details div {
  min-width: 0;
}

.request-details span,
.request-details code {
  display: block;
  overflow-wrap: anywhere;
}

.request-details span {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}

.request-details code {
  margin-top: 2px;
  font-size: 12px;
}

@media (max-width: 720px) {
  .control-grid {
    grid-template-columns: 1fr;
  }

  .queue-heading {
    flex-direction: column;
  }

  .queue-actions {
    width: 100%;
    justify-content: space-between;
  }

  .request-details {
    grid-template-columns: 1fr;
  }
}
</style>
