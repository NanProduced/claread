<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useApi } from "@directus/extensions-sdk";

const props = defineProps({
  runId: { type: String, default: "" },
  rubrics: { type: Array, default: () => [] },
  requests: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["queue", "refresh"]);

const api = useApi();

const rubricId = ref("");
const adapterKind = ref("llm");
const maxCases = ref(30);

const runRequests = computed(() => props.requests.filter((item) => item.run_id === props.runId));

const requestState = reactive({});

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
  if (!request || !request.run_id || !request.judge_run_id) return;
  const key = request.id || request.judge_run_id;
  if (requestState[key]?.result || requestState[key]?.loading) return;
  requestState[key] = { ...(requestState[key] || {}), loading: true, error: "" };
  try {
    const url = `/eval-center/runs/${encodeURIComponent(request.run_id)}/judge/${encodeURIComponent(request.judge_run_id)}`;
    const response = await api.get(url);
    const data = response?.data?.data ?? response?.data ?? null;
    requestState[key] = { ...requestState[key], loading: false, result: data };
  } catch (err) {
    requestState[key] = { ...requestState[key], loading: false, error: err?.response?.data?.errors?.[0]?.message || err?.message || "读取评审结果失败。" };
  }
}

function resultHref(request) {
  if (!request?.run_id || !request?.judge_run_id) return null;
  return `/eval-center/runs/${encodeURIComponent(request.run_id)}/judge/${encodeURIComponent(request.judge_run_id)}`;
}

watch(
  () => props.requests,
  (requests) => {
    for (const req of requests) {
      if (req.run_id === props.runId && req.status === "succeeded" && !requestState[req.id || req.judge_run_id]) {
        void ensureResult(req);
      }
    }
  },
  { immediate: true },
);

watch(
  () => props.runId,
  () => {
    for (const key of Object.keys(requestState)) {
      delete requestState[key];
    }
    for (const req of props.requests) {
      if (req.run_id === props.runId && req.status === "succeeded") {
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
    const hasActive = runRequests.value.some((r) => !TERMINAL_STATUSES.has(r.status));
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
  () => [props.requests, props.runId],
  () => {
    const hasActive = runRequests.value.some((r) => !TERMINAL_STATUSES.has(r.status));
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

function queue() {
  if (!props.runId || !rubricId.value || props.disabled) return;
  emit("queue", {
    run_id: props.runId,
    rubric_id: rubricId.value,
    judge_adapter_kind: adapterKind.value,
    config_json: {
      source: "workflow_lab",
      max_concurrency: 1,
      max_cases: Number(maxCases.value) || 30,
    },
  });
}

function rubricLabel(rubric) {
  if (!rubric) return "—";
  return rubric.title || rubric.id;
}

function judgePassRate(summary) {
  if (!summary) return "—";
  const total = Number(summary.total_cases ?? 0);
  if (total === 0) return "—";
  const passed = Number(summary.passed ?? 0);
  return `${Math.round((passed / total) * 100)}%`;
}
</script>

<template>
  <section class="judge-panel">
    <header>
      <div>
        <p>Judge 评审</p>
        <h3>Run 级评审（仅展示请求与结果）</h3>
      </div>
      <button type="button" title="刷新当前 run 的 judge request 状态。" @click="emit('refresh')">刷新</button>
    </header>

    <p class="judge-disclaimer">
      本面板只展示后端 judge 的请求与结果摘要。<strong>不生成任何裁决文案</strong>，结论以人工 review 为准。
    </p>

    <div class="judge-form">
      <label>
        <span title="选择 run-level judge 使用的 rubric。Compare-level pairwise judge 后续单独设计。">Rubric</span>
        <select v-model="rubricId" :disabled="disabled">
          <option v-for="rubric in rubrics" :key="rubric.id" :value="rubric.id">
            {{ rubricLabel(rubric) }}
          </option>
        </select>
      </label>
      <label>
              <span title="当前 Workflow Lab 只暴露真实 llm judge。">Judge 适配器</span>
        <select v-model="adapterKind" :disabled="disabled">
          <option value="llm">llm，调用真实 judge</option>
        </select>
      </label>
      <label>
        <span title="限制 judge 最多读取的 case 数，避免输入过大。">最大 case 数</span>
        <input v-model.number="maxCases" type="number" min="1" :disabled="disabled" />
      </label>
      <button type="button" :disabled="disabled || submitting || !runId || !rubricId" @click="queue">
        {{ submitting ? "入队中" : "发起 Judge" }}
      </button>
    </div>

    <div class="judge-requests">
      <p v-if="runRequests.length === 0">当前 run 暂无 judge request。</p>
      <article
        v-for="request in runRequests"
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
            <dt>通过</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.passed ?? 0 }}</dd>
          </div>
          <div>
            <dt>失败</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.failed ?? 0 }}</dd>
          </div>
          <div>
            <dt>需复查</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.needs_review ?? 0 }}</dd>
          </div>
          <div>
            <dt>异常</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.errored ?? 0 }}</dd>
          </div>
          <div>
            <dt>均分</dt>
            <dd>{{ requestState[request.id || request.judge_run_id].result.summary.average_score ?? "—" }}</dd>
          </div>
          <div>
            <dt>通过率</dt>
            <dd>{{ judgePassRate(requestState[request.id || request.judge_run_id].result.summary) }}</dd>
          </div>
        </div>
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
}
</style>
