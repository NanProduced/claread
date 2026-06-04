<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useApi } from "@directus/extensions-sdk";

const props = defineProps({
  compareId: { type: String, default: "" },
  rubrics: { type: Array, default: () => [] },
  modelProfiles: { type: Array, default: () => [] },
  requests: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["queue", "refresh"]);

const api = useApi();

const rubricId = ref("");
const adapterKind = ref("llm");
const judgeModelProfile = ref("");

const modelProfileOptions = computed(() => (props.modelProfiles || []).map((profile) => ({
  value: profile.profile_name,
  label: `${profile.profile_name} · ${profile.model_name || profile.profile_name}`,
  modelName: profile.model_name || profile.profile_name,
})));

const compareRequests = computed(() => props.requests.filter((item) => item.compare_id === props.compareId));

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
      if (req.compare_id === props.compareId && req.status === "succeeded" && !requestState[req.id || req.judge_run_id]) {
        void ensureResult(req);
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
    for (const req of props.requests) {
      if (req.compare_id === props.compareId && req.status === "succeeded") {
        void ensureResult(req);
      }
    }
  },
);

const TERMINAL_STATUSES = new Set(["succeeded", "complete", "failed", "total_failure", "cancelled", "errored"]);

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

</script>

<template>
  <section class="judge-panel">
    <header>
      <div>
        <p>Judge 评审</p>
        <h3>Compare 级评审</h3>
      </div>
      <button type="button" title="刷新当前 compare 的 judge request 状态。" @click="emit('refresh')">刷新</button>
    </header>

    <p class="judge-disclaimer">
      本面板锚定 <strong>workflow_compare</strong>，展示 compare-scope 评审请求与结果摘要。
      当前默认提供 <strong>本地规则 Judge</strong>，用于快速给出可回看的 compare 结论；最终裁决仍需人工复核。
    </p>

    <div class="judge-form">
      <label>
        <span title="选择 compare-level pairwise judge 使用的 rubric。">Rubric</span>
        <select v-model="rubricId" :disabled="disabled">
          <option v-for="rubric in rubrics" :key="rubric.id" :value="rubric.id">
            {{ rubricLabel(rubric) }}
          </option>
        </select>
      </label>
      <label>
        <span title="真实 Judge 会调用 OpenAI-compatible 模型；fake 仅用于调试 deterministic 包装链路。">Judge 适配器</span>
        <select v-model="adapterKind" :disabled="disabled">
          <option value="llm">llm，调用真实 Judge</option>
          <option value="fake">fake，本地规则 Judge（立即执行）</option>
        </select>
      </label>
      <label v-if="adapterKind === 'llm'">
        <span title="真实 Judge 使用的模型配置。当前通过安全模型列表选择模型名；底层调用仍依赖 judge 环境变量中的 base_url / api_key。">Judge 模型</span>
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

    <p class="judge-help">
      当前 compare-level judge 默认评审这次 compare 中全部发生变化的句子级 case。
      不再暴露额外的句子数限制选项，避免把单篇 judge 复杂化。
    </p>

    <div class="judge-requests">
      <p v-if="compareRequests.length === 0">当前 compare 暂无 judge request。</p>
      <article
        v-for="request in compareRequests"
        v-else
        :key="request.id || request.judge_run_id"
        class="judge-request"
      >
        <header>
          <div class="judge-head-main">
            <strong>{{ request.judge_run_id }}</strong>
            <span class="status-pill" :class="`is-${statusTone(request.status)}`">{{ statusLabel(request.status) }}</span>
          </div>
          <small class="judge-meta">{{ request.rubric_id }} / {{ request.judge_adapter_kind }}</small>
        </header>

        <div v-if="requestState[request.id || request.judge_run_id]?.loading" class="result-loading">正在读取评审结果…</div>
        <div v-else-if="requestState[request.id || request.judge_run_id]?.error" class="result-error">{{ requestState[request.id || request.judge_run_id].error }}</div>
        <div
          v-else-if="requestState[request.id || request.judge_run_id]?.result?.summary"
          class="result-summary"
        >
          <div>
            <dt>总 case</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.total_cases ?? "—" }}</dd>
          </div>
          <div>
            <dt>候选更优</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.candidate_preferred ?? requestState[request.id || request.judge_run_id].result.summary.passed ?? 0 }}</dd>
          </div>
          <div>
            <dt>Baseline 更优</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.baseline_preferred ?? requestState[request.id || request.judge_run_id].result.summary.failed ?? 0 }}</dd>
          </div>
          <div>
            <dt>持平</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.tie ?? 0 }}</dd>
          </div>
          <div>
            <dt>需复查</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.needs_review ?? 0 }}</dd>
          </div>
          <div>
            <dt>异常</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.errored ?? 0 }}</dd>
          </div>
        </div>
        <section
          v-if="requestCases(request).length"
          class="case-results-block"
        >
          <header class="case-results-head">
            <strong>逐 case 结果</strong>
            <small>这批 fake judge 先把 deterministic compare 信号物化成可回看的 compare 结论。</small>
          </header>
          <ol class="case-results-list">
            <li
              v-for="caseResult in requestCases(request)"
              :key="`${request.id || request.judge_run_id}-${caseResult.case_id}`"
              class="case-result-card"
            >
              <div class="case-result-top">
                <div class="case-result-id">{{ caseResult.case_id }}</div>
                <span :class="`status-pill is-${caseVerdictTone(caseResult.verdict)}`">
                  {{ caseVerdictLabel(caseResult.verdict) }}
                </span>
              </div>
              <p class="case-result-summary">{{ caseResult.summary || "暂无摘要。" }}</p>
              <dl class="case-result-metrics">
                <div>
                  <dt>Baseline 结构/轻微</dt>
                  <dd>{{ caseResult.baseline_hard_failures ?? 0 }}/{{ caseResult.baseline_soft_failures ?? 0 }}</dd>
                </div>
                <div>
                  <dt>候选 结构/轻微</dt>
                  <dd>{{ caseResult.candidate_hard_failures ?? 0 }}/{{ caseResult.candidate_soft_failures ?? 0 }}</dd>
                </div>
                <div>
                  <dt>倾向</dt>
                  <dd>{{ caseResult.preferred_side || "无" }}</dd>
                </div>
                <div>
                  <dt>分数</dt>
                  <dd>{{ caseResult.overall_score ?? "—" }}</dd>
                </div>
              </dl>
              <ul v-if="Array.isArray(caseResult.reasons) && caseResult.reasons.length" class="case-result-reasons">
                <li v-for="reason in caseResult.reasons" :key="`${caseResult.case_id}-${reason}`">{{ reason }}</li>
              </ul>
            </li>
          </ol>
        </section>
        <div v-else-if="request.status !== 'succeeded' && request.status !== 'complete'" class="result-pending">
          {{ request.status === 'queued' ? '等待中，评审启动后会自动加载摘要。' : '评审进行中，完成后会显示摘要。' }}
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
.judge-disclaimer,
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
.judge-disclaimer {
  margin-top: 8px;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  padding: 8px 10px;
  line-height: 1.55;
}
.judge-disclaimer strong {
  color: var(--theme--foreground);
}
.judge-form {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(160px, 0.9fr) minmax(90px, 0.6fr) auto;
  margin-top: 12px;
}
.judge-help {
  margin: 10px 0 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
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
  gap: 10px;
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

.case-results-head strong,
.case-results-head small {
  display: block;
}

.case-results-head small {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.5;
}

.case-results-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}

.case-result-card {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  background: var(--theme--background-subdued);
  padding: 10px;
  display: grid;
  gap: 8px;
}

.case-result-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.case-result-id {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground-subdued);
}

.case-result-summary {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--theme--foreground);
}

.case-result-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
  background: var(--theme--background);
}

.case-result-metrics div {
  background: var(--theme--background);
  padding: 8px 10px;
}

.case-result-metrics dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}

.case-result-metrics dd {
  margin: 4px 0 0;
  font-size: 13px;
  font-weight: 700;
  overflow-wrap: anywhere;
}

.case-result-reasons {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
}

.case-result-reasons li {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 999px;
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  line-height: 1.4;
  padding: 2px 8px;
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
  .case-result-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
