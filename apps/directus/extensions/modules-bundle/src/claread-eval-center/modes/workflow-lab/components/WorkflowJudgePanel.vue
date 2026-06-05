<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useApi } from "@directus/extensions-sdk";
import WorkflowSentenceCompareNotebook from "./WorkflowSentenceCompareNotebook.vue";

const props = defineProps({
  compareId: { type: String, default: "" },
  rubrics: { type: Array, default: () => [] },
  modelProfiles: { type: Array, default: () => [] },
  requests: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false },
  preparedSentences: { type: Array, default: () => [] },
  baselineArtifact: { type: Object, default: null },
  candidateArtifact: { type: Object, default: null },
  /** Persisted compare report comparisons — passed through to notebook */
  comparisons: { type: Array, default: () => [] },
});
const emit = defineEmits(["queue", "refresh"]);

const api = useApi();

const rubricId = ref("");
const adapterKind = ref("llm");
const judgeModelProfile = ref("");
const filterMode = ref("all");

const modelProfileOptions = computed(() => (props.modelProfiles || []).map((profile) => ({
  value: profile.profile_name,
  label: `${profile.profile_name} · ${profile.model_name || profile.profile_name}`,
  modelName: profile.model_name || profile.profile_name,
})));

const compareRequests = computed(() => {
  const reqs = props.requests.filter((item) => item.compare_id === props.compareId);
  return reqs.sort((a, b) => new Date(b.date_created || 0).getTime() - new Date(a.date_created || 0).getTime());
});

const currentRequest = computed(() => {
  if (!compareRequests.value.length) return null;
  // Always show the most recent request — never silently substitute
  // an older succeeded result.  This prevents the UX confusion where
  // a newly-queued judge is hidden behind an old valid one.
  return compareRequests.value[0];
});

const historyRequests = computed(() => {
  if (!currentRequest.value) return [];
  return compareRequests.value.filter(r => (r.id || r.judge_run_id) !== (currentRequest.value.id || currentRequest.value.judge_run_id));
});

const requestState = reactive({});

function requestResult(request) {
  return requestState[request.id || request.judge_run_id]?.result || null;
}

function requestCases(request) {
  const cases = requestResult(request)?.case_results?.cases;
  return Array.isArray(cases) ? cases : [];
}

function caseVerdictTone(verdict) {
  if (verdict === "candidate_preferred") return "success";
  if (verdict === "baseline_preferred") return "danger";
  if (verdict === "needs_review") return "warning";
  return "neutral";
}

function caseVerdictLabel(verdict) {
  const map = {
    candidate_preferred: "候选更优",
    baseline_preferred: "Baseline 更优",
    tie: "持平",
    needs_review: "需复查",
  };
  return map[verdict] || verdict || "未知";
}

function statusTone(status) {
  if (status === "succeeded" || status === "complete") return "success";
  if (status === "failed" || status === "total_failure" || status === "cancelled" || status === "errored") return "danger";
  if (status === "queued" || status === "running" || status === "partial_failure") return "warning";
  return "neutral";
}

function statusLabel(status) {
  const map = {
    succeeded: "已完成",
    complete: "已完成",
    failed: "失败",
    cancelled: "已取消",
    errored: "执行异常",
    total_failure: "全部失败",
    partial_failure: "部分失败",
    queued: "排队中",
    running: "运行中",
  };
  return map[status] || status || "未知";
}

async function ensureResult(request) {
  if (!request || !request.compare_id || !request.judge_run_id) return;
  const key = request.id || request.judge_run_id;
  if (requestState[key]?.result || requestState[key]?.loading) return;
  requestState[key] = { ...(requestState[key] || {}), loading: true, error: "" };
  try {
    const url = `/eval-center/workflow-lab/compares/${encodeURIComponent(request.compare_id)}/judge/${encodeURIComponent(request.judge_run_id)}`;
    const response = await api.get(url);
    const data = response?.data?.data ?? response?.data ?? null;
    requestState[key] = { ...requestState[key], loading: false, result: data };
  } catch (err) {
    requestState[key] = { ...requestState[key], loading: false, error: err?.response?.data?.errors?.[0]?.message || err?.message || "读取评审结果失败。" };
  }
}

function resultHref(request) {
  if (!request?.compare_id || !request?.judge_run_id) return null;
  return `/eval-center/workflow-lab/compares/${encodeURIComponent(request.compare_id)}/judge/${encodeURIComponent(request.judge_run_id)}`;
}

watch(
  () => props.requests,
  (requests) => {
    for (const req of requests) {
      if (req.compare_id === props.compareId) {
        const key = req.id || req.judge_run_id;
        // Fetch result for any terminal request we haven't loaded yet,
        // AND always fetch the current (most recent) request if it's
        // terminal — even if it failed.
        const isTerminal = req.status === "succeeded" || req.status === "partial_failure"
          || req.status === "failed" || req.status === "total_failure"
          || req.status === "cancelled" || req.status === "errored" || req.status === "complete";
        if (isTerminal && !requestState[key]) {
          void ensureResult(req);
        }
      }
    }
  },
  { immediate: true },
);

watch(
  () => props.compareId,
  () => {
    for (const key of Object.keys(requestState)) {
      delete requestState[key];
    }
    const terminalSet = new Set(["succeeded", "partial_failure", "failed", "total_failure", "cancelled", "errored", "complete"]);
    for (const req of props.requests) {
      if (req.compare_id === props.compareId && terminalSet.has(req.status)) {
        void ensureResult(req);
      }
    }
  },
);

const TERMINAL_STATUSES = new Set(["succeeded", "complete", "partial_failure", "failed", "total_failure", "cancelled", "errored"]);

let pollTimer = null;

function startPolling() {
  stopPolling();
  pollTimer = window.setInterval(() => {
    const hasActive = compareRequests.value.some((r) => !TERMINAL_STATUSES.has(r.status));
    if (hasActive) {
      emit("refresh");
    } else {
      stopPolling();
    }
  }, 5000);
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

watch(
  () => [props.requests, props.compareId],
  () => {
    const hasActive = compareRequests.value.some((r) => !TERMINAL_STATUSES.has(r.status));
    if (hasActive && !pollTimer) {
      startPolling();
    } else if (!hasActive) {
      stopPolling();
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  stopPolling();
  for (const key of Object.keys(requestState)) {
    delete requestState[key];
  }
});

watch(
  () => props.rubrics,
  (rubrics) => {
    if (!rubricId.value && rubrics.length) rubricId.value = rubrics[0].id;
  },
  { immediate: true },
);

watch(
  () => props.modelProfiles,
  (profiles) => {
    if (!judgeModelProfile.value && Array.isArray(profiles) && profiles.length) {
      judgeModelProfile.value = profiles[0].profile_name || "";
    }
  },
  { immediate: true },
);

function queue() {
  if (!props.compareId || !rubricId.value || props.disabled) return;
  const selectedModel = modelProfileOptions.value.find((item) => item.value === judgeModelProfile.value) || null;
  emit("queue", {
    compare_id: props.compareId,
    rubric_id: rubricId.value,
    judge_adapter_kind: adapterKind.value,
    config_json: {
      source: "workflow_compare",
      max_concurrency: 1,
      judger_model_profile: adapterKind.value === "llm" ? judgeModelProfile.value || null : null,
      judger_model_name: adapterKind.value === "llm" ? selectedModel?.modelName || null : null,
    },
  });
}

function rubricLabel(rubric) {
  if (!rubric) return "—";
  return rubric.title || rubric.id;
}

/** Build judge overlay Map for the notebook component */
const judgeOverlayMap = computed(() => {
  if (!currentRequest.value) return null;
  const cases = requestCases(currentRequest.value);
  if (!cases.length) return null;
  const map = new Map();
  for (const c of cases) {
    map.set(String(c.case_id), {
      verdict: c.verdict,
      summary: c.summary || "",
      reasons: Array.isArray(c.reasons) ? c.reasons : [],
      status: c.status,
      error: c.error || null,
      baseline_hard_failures: c.baseline_hard_failures ?? 0,
      baseline_soft_failures: c.baseline_soft_failures ?? 0,
      candidate_hard_failures: c.candidate_hard_failures ?? 0,
      candidate_soft_failures: c.candidate_soft_failures ?? 0,
    });
  }
  return map;
});

const currentResult = computed(() => {
  if (!currentRequest.value) return null;
  return requestState[currentRequest.value.id || currentRequest.value.judge_run_id];
});
</script>

<template>
  <section class="judge-panel">
    <header>
      <div>
        <p>Judge 评审</p>
        <h3>当前评审结论</h3>
      </div>
      <button v-if="!readonly" type="button" title="刷新评审状态。" @click="emit('refresh')">刷新</button>
    </header>

    <div v-if="!readonly" class="judge-form">
      <label>
        <span title="选择评审使用的 rubric。">Rubric</span>
        <select v-model="rubricId" :disabled="disabled">
          <option v-for="rubric in rubrics" :key="rubric.id" :value="rubric.id">
            {{ rubricLabel(rubric) }}
          </option>
        </select>
      </label>
      <label>
        <span title="适配器类型。">Judge 适配器</span>
        <select v-model="adapterKind" :disabled="disabled">
          <option value="llm">llm，调用真实 Judge</option>
        </select>
      </label>
      <label v-if="adapterKind === 'llm'">
        <span title="真实 Judge 使用的模型配置。">Judge 模型</span>
        <select v-model="judgeModelProfile" :disabled="disabled">
          <option v-for="profile in modelProfileOptions" :key="profile.value" :value="profile.value">
            {{ profile.label }}
          </option>
        </select>
      </label>
      <button type="button" :disabled="disabled || submitting || !compareId || !rubricId" @click="queue">
        {{ submitting ? "入队中" : "发起 Judge" }}
      </button>
    </div>

    <div class="judge-requests">
      <p v-if="!currentRequest">暂无评审结论。</p>

      <!-- Current Request -->
      <article
        v-if="currentRequest"
        class="judge-request current-judge"
      >
        <header>
          <div class="judge-head-main">
            <span class="status-pill" :class="`is-${statusTone(currentRequest.status)}`">{{ statusLabel(currentRequest.status) }}</span>
            <span class="judge-meta-light" v-if="!readonly">{{ currentRequest.judge_run_id }}</span>
          </div>
        </header>

        <div v-if="currentResult?.loading" class="result-loading">正在读取评审结果…</div>
        <div v-else-if="currentRequest.status === 'failed' || currentRequest.status === 'errored'" class="result-error">
          评审失败。{{ currentResult?.error || '请检查日志或重新发起。' }}
        </div>
        <div v-else-if="currentResult?.error" class="result-error">{{ currentResult.error }}</div>

        <div
          v-else-if="currentResult?.result?.summary"
          class="result-summary"
        >
          <div>
            <dt>Judge 模型</dt>
            <dd>{{ currentResult.result.summary.judge_model_name || "—" }}</dd>
          </div>
          <div>
            <dt>Judge Profile</dt>
            <dd>{{ currentResult.result.summary.judge_model_profile || "—" }}</dd>
          </div>
          <div>
            <dt>Judge 耗时</dt>
            <dd>{{ currentResult.result.summary.judge_latency_seconds != null ? `${Number(currentResult.result.summary.judge_latency_seconds).toFixed(1)}s` : "—" }}</dd>
          </div>
          <div>
            <dt>Judge Tokens</dt>
            <dd>
              {{
                currentResult.result.summary.judge_total_tokens != null
                  ? `${currentResult.result.summary.judge_total_tokens}（入 ${currentResult.result.summary.judge_input_tokens ?? "—"} / 出 ${currentResult.result.summary.judge_output_tokens ?? "—"}）`
                  : "—"
              }}
            </dd>
          </div>
          <div>
            <dt>差异句数</dt>
            <dd>{{ currentResult.result.summary.total_cases ?? "—" }}</dd>
          </div>
          <div>
            <dt>候选更优</dt>
            <dd>{{ currentResult.result.summary.candidate_preferred ?? currentResult.result.summary.passed ?? 0 }}</dd>
          </div>
          <div>
            <dt>Baseline 更优</dt>
            <dd>{{ currentResult.result.summary.baseline_preferred ?? currentResult.result.summary.failed ?? 0 }}</dd>
          </div>
          <div>
            <dt>持平</dt>
            <dd>{{ currentResult.result.summary.tie ?? 0 }}</dd>
          </div>
          <div>
            <dt>需复查</dt>
            <dd>{{ currentResult.result.summary.needs_review ?? 0 }}</dd>
          </div>
        </div>

        <!-- Sentence notebook with judge overlay -->
        <section
          v-if="requestCases(currentRequest).length"
          class="case-results-block"
        >
          <header class="case-results-head">
            <strong>逐句评审结果</strong>
            <div class="filter-bar">
              <button type="button" :class="{ active: filterMode === 'all' }" @click="filterMode = 'all'">全部</button>
              <button type="button" :class="{ active: filterMode === 'judged' }" @click="filterMode = 'judged'">仅评审</button>
            </div>
          </header>

          <WorkflowSentenceCompareNotebook
            :baseline-artifact="baselineArtifact"
            :candidate-artifact="candidateArtifact"
            :prepared-sentences="preparedSentences"
            :comparisons="comparisons"
            :judge-overlay="judgeOverlayMap"
            :filter-mode="filterMode"
            empty-text="暂无逐句评审数据。"
          />

          <!-- Debug info: collapsed -->
          <details class="debug-details">
            <summary>调试信号</summary>
            <ol class="debug-list">
              <li
                v-for="caseResult in requestCases(currentRequest)"
                :key="`${currentRequest.id || currentRequest.judge_run_id}-${caseResult.case_id}`"
                class="debug-item"
              >
                <span class="debug-sid">{{ caseResult.case_id }}</span>
                <span :class="`status-pill is-${caseVerdictTone(caseResult.verdict)}`">{{ caseVerdictLabel(caseResult.verdict) }}</span>
                <span class="debug-signal" title="Baseline 硬/软失败">B: {{ caseResult.baseline_hard_failures ?? 0 }}/{{ caseResult.baseline_soft_failures ?? 0 }}</span>
                <span class="debug-signal" title="Candidate 硬/软失败">C: {{ caseResult.candidate_hard_failures ?? 0 }}/{{ caseResult.candidate_soft_failures ?? 0 }}</span>
              </li>
            </ol>
          </details>
        </section>
        <div v-else-if="currentRequest.status !== 'succeeded' && currentRequest.status !== 'complete' && currentRequest.status !== 'failed' && currentRequest.status !== 'errored'" class="result-pending">
          {{ currentRequest.status === 'queued' ? '等待中，评审启动后会自动加载摘要。' : '评审进行中，完成后会显示摘要。' }}
        </div>
      </article>

      <!-- History Requests -->
      <details v-if="historyRequests.length > 0" class="history-requests-details">
        <summary>历史评审记录 ({{ historyRequests.length }})</summary>
        <div class="history-requests-list">
          <article
            v-for="request in historyRequests"
            :key="request.id || request.judge_run_id"
            class="judge-request history-judge"
          >
            <header>
              <div class="judge-head-main">
                <span class="status-pill" :class="`is-${statusTone(request.status)}`">{{ statusLabel(request.status) }}</span>
                <small class="judge-meta">{{ request.rubric_id }} / {{ request.judge_adapter_kind }}</small>
              </div>
            </header>

            <div v-if="requestState[request.id || request.judge_run_id]?.result?.summary" class="result-summary-small">
              <span>差异句数: {{ requestState[request.id || request.judge_run_id].result.summary.total_cases ?? "—" }}</span>
              <span>候选优: {{ requestState[request.id || request.judge_run_id].result.summary.candidate_preferred ?? requestState[request.id || request.judge_run_id].result.summary.passed ?? 0 }}</span>
              <span>Baseline优: {{ requestState[request.id || request.judge_run_id].result.summary.baseline_preferred ?? requestState[request.id || request.judge_run_id].result.summary.failed ?? 0 }}</span>
            </div>

            <a
              v-if="resultHref(request) && request.status !== 'queued' && request.status !== 'running'"
              class="result-link"
              :href="resultHref(request)"
              target="_blank"
              rel="noopener"
            >查看完整结果 →</a>
          </article>
        </div>
      </details>
    </div>
  </section>
</template>

<style scoped>
.judge-panel {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 12px;
}
header,
.judge-form {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
header p,
label span,
.result-pending,
.result-loading,
.result-error {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
header h3 {
  margin: 2px 0 0;
  font-size: 14px;
}
.judge-form {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(160px, 0.9fr) minmax(90px, 0.6fr) auto;
  margin-top: 12px;
}
label {
  display: grid;
  gap: 5px;
}
button,
select,
input {
  min-height: 34px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  padding: 6px 8px;
}
button {
  cursor: pointer;
  font-weight: 700;
}
button:disabled,
select:disabled,
input:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.judge-requests {
  display: grid;
  gap: 12px;
  margin-top: 12px;
}
.judge-request {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 10px 12px;
  display: grid;
  gap: 8px;
}
.current-judge {
  border-color: var(--theme--border-color);
  background: var(--theme--background);
}
.history-judge {
  border-color: var(--theme--border-color-subdued);
  background: var(--theme--background-subdued);
}
.history-requests-details {
  margin-top: 4px;
}
.history-requests-details summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 8px;
}
.history-requests-list {
  display: grid;
  gap: 8px;
  padding-left: 8px;
  border-left: 2px solid var(--theme--border-color-subdued);
}
.judge-request header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}
.judge-head-main {
  display: flex;
  align-items: center;
  gap: 8px;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  background: var(--theme--background);
}
.status-pill.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}
.status-pill.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}
.status-pill.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}
.status-pill.is-neutral {
  color: var(--theme--foreground-subdued);
}
.judge-meta {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}
.judge-meta-light {
  color: var(--theme--foreground-subdued);
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 11px;
}
.case-results-block {
  display: grid;
  gap: 10px;
}
.case-results-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}
.case-results-head strong {
  display: block;
}
.filter-bar {
  display: flex;
  gap: 4px;
}
.filter-bar button {
  min-height: 28px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.filter-bar button.active {
  background: var(--theme--primary);
  color: var(--theme--background);
  border-color: var(--theme--primary);
}
.result-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
  background: var(--theme--background-subdued);
}
.result-summary div {
  background: var(--theme--background);
  padding: 8px 10px;
}
.result-summary dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}
.result-summary dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.result-summary-small {
  display: flex;
  gap: 12px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
.result-loading,
.result-error,
.result-pending {
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  padding: 8px 10px;
  background: var(--theme--background-subdued);
  line-height: 1.55;
}
.result-error {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}
.result-link {
  align-self: flex-start;
  color: var(--theme--primary);
  font-size: 12px;
  font-weight: 700;
  text-decoration: none;
}
.result-link:hover {
  text-decoration: underline;
}

/* Debug details */
.debug-details {
  margin-top: 4px;
}
.debug-details summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
.debug-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
  margin-top: 6px;
}
.debug-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 4px;
  background: var(--theme--background-subdued);
  font-size: 11px;
}
.debug-sid {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}
.debug-signal {
  color: var(--theme--foreground-subdued);
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 10px;
}

@media (max-width: 860px) {
  .judge-form {
    grid-template-columns: 1fr;
  }
  .result-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .case-results-head {
    display: grid;
  }
}
</style>
