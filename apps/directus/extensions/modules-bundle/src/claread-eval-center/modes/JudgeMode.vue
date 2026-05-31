<script setup>
import { useApi } from "@directus/extensions-sdk";
import { computed, onMounted, ref } from "vue";
import ResultBlock from "../components/ResultBlock.vue";

const api = useApi();
const runsEndpoint = "/eval-center/runs";

const loadingRuns = ref(false);
const loadingDetail = ref(false);
const loadingRequests = ref(false);
const submitting = ref(false);
const error = ref("");
const requestError = ref("");

const runs = ref([]);
const selectedRunId = ref("");
const selectedRunDetail = ref(null);
const rubrics = ref([]);
const selectedRubricId = ref("");
const judgeAdapterKind = ref("fake");
const judgeMaxCases = ref(50);
const requestStatusFilter = ref("all");
const requestRows = ref([]);
const selectedJudgeReport = ref(null);

const requestStatusOptions = [
  { text: "All", value: "all" },
  { text: "Queued", value: "queued" },
  { text: "Running", value: "running" },
  { text: "Succeeded", value: "succeeded" },
  { text: "Failed", value: "failed" },
  { text: "Cancelled", value: "cancelled" },
];

const runOptions = computed(() =>
  runs.value.map((run) => ({
    text: `${run.run_id} · ${run.dataset_id || "no dataset"} · ${run.total_cases || 0} cases`,
    value: run.run_id,
  })),
);

const selectedRunSummary = computed(() => selectedRunDetail.value?.summary || null);
const judgeReports = computed(() => selectedRunDetail.value?.judge_reports || []);
const canQueueJudge = computed(
  () => Boolean(selectedRunId.value && selectedRubricId.value && !submitting.value),
);

onMounted(() => {
  void refreshAll();
});

async function refreshAll() {
  await Promise.all([loadRubrics(), loadRuns()]);
}

async function fetchJson(url) {
  const response = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.errors?.[0]?.message || `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return payload?.data !== undefined ? payload.data : payload;
}

async function loadRuns() {
  loadingRuns.value = true;
  error.value = "";
  try {
    const data = await fetchJson(`${runsEndpoint}?limit=100`);
    runs.value = Array.isArray(data?.runs) ? data.runs : [];
    if (!selectedRunId.value && runs.value.length) {
      selectedRunId.value = runs.value[0].run_id;
    }
    if (selectedRunId.value) {
      await loadSelectedRun();
    }
  } catch (err) {
    error.value = err?.message || "读取 workflow runs 失败。";
  } finally {
    loadingRuns.value = false;
  }
}

async function loadRubrics() {
  try {
    const resp = await api.get("/eval-center/judge/rubrics");
    const data = resp?.data?.data || resp?.data || [];
    rubrics.value = Array.isArray(data) ? data : [];
    if (!selectedRubricId.value && rubrics.value.length) {
      selectedRubricId.value = rubrics.value[0].id;
    }
    if (selectedRubricId.value && !rubrics.value.some((rubric) => rubric.id === selectedRubricId.value)) {
      selectedRubricId.value = rubrics.value[0]?.id || "";
    }
  } catch (err) {
    requestError.value = err?.response?.data?.errors?.map((item) => item.message).join("; ") || err.message;
  }
}

async function selectRun() {
  selectedJudgeReport.value = null;
  await loadSelectedRun();
}

async function loadSelectedRun() {
  if (!selectedRunId.value) return;
  loadingDetail.value = true;
  error.value = "";
  try {
    const [detail, reports] = await Promise.all([
      fetchJson(`${runsEndpoint}/${encodeURIComponent(selectedRunId.value)}`),
      fetchJson(`${runsEndpoint}/${encodeURIComponent(selectedRunId.value)}/judge`).catch(() => []),
    ]);
    const safeReports = Array.isArray(reports) ? reports : [];
    selectedRunDetail.value = {
      ...detail,
      summary: {
        ...detail.summary,
        judge_report_count: safeReports.length,
      },
      judge_reports: safeReports,
    };
    await loadRequests();
  } catch (err) {
    selectedRunDetail.value = null;
    error.value = err?.message || "读取 run detail 失败。";
  } finally {
    loadingDetail.value = false;
  }
}

async function loadRequests() {
  loadingRequests.value = true;
  requestError.value = "";
  try {
    const resp = await api.get("/eval-center/judge/requests", {
      params: {
        status: requestStatusFilter.value,
        run_id: selectedRunId.value || undefined,
        limit: 50,
      },
    });
    const data = resp?.data?.data || resp?.data || [];
    requestRows.value = Array.isArray(data) ? data : [];
  } catch (err) {
    requestError.value = err?.response?.data?.errors?.map((item) => item.message).join("; ") || err.message;
  } finally {
    loadingRequests.value = false;
  }
}

async function queueJudgeRequest() {
  if (!canQueueJudge.value) return;
  submitting.value = true;
  requestError.value = "";
  try {
    await api.post("/eval-center/judge/requests", {
      run_id: selectedRunId.value,
      rubric_id: selectedRubricId.value,
      judge_adapter_kind: judgeAdapterKind.value,
      config_json: {
        source: "judge_mode",
        max_concurrency: 1,
        max_cases: Number(judgeMaxCases.value) || 50,
      },
    });
    requestStatusFilter.value = "all";
    await loadRequests();
  } catch (err) {
    requestError.value = err?.response?.data?.errors?.map((item) => item.message).join("; ") || err.message;
  } finally {
    submitting.value = false;
  }
}

async function cancelJudgeRequest(row) {
  if (!row?.id || !["queued", "running"].includes(row.status)) return;
  const ok = window.confirm(
    `Cancel judge request ${row.judge_run_id}?\n\nThe worker is not killed; completion write-back is guarded by status and lease owner.`,
  );
  if (!ok) return;
  requestError.value = "";
  try {
    await api.post(`/eval-center/judge/requests/${encodeURIComponent(row.id)}/cancel`);
    await loadRequests();
  } catch (err) {
    requestError.value = err?.response?.data?.errors?.map((item) => item.message).join("; ") || err.message;
  }
}

async function retryJudgeRequest(row) {
  if (!row?.id || !["failed", "cancelled"].includes(row.status)) return;
  const ok = window.confirm(
    `Retry judge request ${row.judge_run_id} as a new judge run?\n\nA new judge_run_id will be generated. Existing judge artifacts will not be modified.`,
  );
  if (!ok) return;
  requestError.value = "";
  try {
    await api.post(`/eval-center/judge/requests/${encodeURIComponent(row.id)}/retry`, {
      retry_reason: "manual retry from Judge mode",
    });
    requestStatusFilter.value = "all";
    await loadRequests();
  } catch (err) {
    requestError.value = err?.response?.data?.errors?.map((item) => item.message).join("; ") || err.message;
  }
}

async function selectJudgeReport(judgeRunId) {
  if (!selectedRunId.value || !judgeRunId) return;
  loadingDetail.value = true;
  error.value = "";
  try {
    selectedJudgeReport.value = await fetchJson(
      `${runsEndpoint}/${encodeURIComponent(selectedRunId.value)}/judge/${encodeURIComponent(judgeRunId)}`,
    );
  } catch (err) {
    selectedJudgeReport.value = null;
    error.value = err?.message || "读取 judge report 失败。";
  } finally {
    loadingDetail.value = false;
  }
}

async function refreshSelectedArtifacts() {
  await loadSelectedRun();
}

function dash(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function formatJson(value) {
  if (value == null) return "";
  return JSON.stringify(value, null, 2);
}

function requestErrorSummary(row) {
  if (!row?.error) return "";
  return [row.error.code, row.error.message].filter(Boolean).join(": ");
}

function statusClass(status) {
  return {
    queued: "is-review",
    running: "is-running",
    succeeded: "is-win",
    failed: "is-loss",
    cancelled: "is-muted",
    pass: "is-win",
    fail: "is-loss",
    needs_review: "is-review",
    error: "is-loss",
  }[status] || "is-review";
}
</script>

<template>
  <section class="judge-mode">
    <div class="section-heading">
      <div>
        <h2>LLM-as-a-Judge</h2>
        <span>创建 judge request、查看 worker 写回状态和已生成 report。Judge verdict 只作为 evidence。</span>
      </div>
      <button class="secondary-btn" type="button" :disabled="loadingRuns" @click="refreshAll">
        {{ loadingRuns ? "Refreshing" : "Refresh" }}
      </button>
    </div>

    <p v-if="error" class="error-message">{{ error }}</p>
    <p v-if="requestError" class="error-message">{{ requestError }}</p>

    <div class="judge-controls">
      <label>
        <span>Source Run</span>
        <select v-model="selectedRunId" :disabled="loadingRuns" @change="selectRun">
          <option v-if="!runOptions.length" value="">No workflow runs</option>
          <option v-for="run in runOptions" :key="run.value" :value="run.value">
            {{ run.text }}
          </option>
        </select>
      </label>
      <label>
        <span>Rubric</span>
        <select v-model="selectedRubricId">
          <option v-if="!rubrics.length" value="">No rubrics</option>
          <option v-for="rubric in rubrics" :key="rubric.id" :value="rubric.id">
            {{ rubric.id }}@{{ rubric.version || "unknown" }}
          </option>
        </select>
      </label>
      <label>
        <span>Adapter</span>
        <select v-model="judgeAdapterKind">
          <option value="fake">Fake</option>
          <option value="llm">LLM</option>
        </select>
      </label>
      <label>
        <span>Max Cases</span>
        <input v-model.number="judgeMaxCases" type="number" min="1" max="1000" step="1">
      </label>
      <button class="primary-btn" type="button" :disabled="!canQueueJudge" @click="queueJudgeRequest">
        {{ submitting ? "Queueing" : "Queue Judge" }}
      </button>
    </div>

    <div v-if="selectedRunSummary" class="summary-grid">
      <div>
        <span>Dataset</span>
        <strong>{{ dash(selectedRunSummary.dataset_id) }}</strong>
      </div>
      <div>
        <span>Purpose</span>
        <strong>{{ dash(selectedRunSummary.eval_purpose) }}</strong>
      </div>
      <div>
        <span>Cases</span>
        <strong>{{ selectedRunSummary.total_cases }}</strong>
        <small>{{ selectedRunSummary.case_artifact_count }} artifacts</small>
      </div>
      <div>
        <span>Deterministic Failed</span>
        <strong>{{ dash(selectedRunSummary.failed) }}</strong>
      </div>
      <div>
        <span>Hard Failures</span>
        <strong>{{ dash(selectedRunSummary.hard_failure_count) }}</strong>
      </div>
      <div>
        <span>Judge Reports</span>
        <strong>{{ selectedRunSummary.judge_report_count || 0 }}</strong>
      </div>
    </div>

    <section class="judge-panel">
      <div class="panel-heading">
        <div>
          <h3>Judge Request Queue</h3>
          <span>Directus 只创建和取消 request；模型调用由 evals judge worker 执行。</span>
        </div>
        <div class="panel-actions">
          <select v-model="requestStatusFilter" @change="loadRequests">
            <option v-for="opt in requestStatusOptions" :key="opt.value" :value="opt.value">{{ opt.text }}</option>
          </select>
          <button class="secondary-btn" type="button" :disabled="loadingRequests" @click="loadRequests">
            {{ loadingRequests ? "Loading" : "Refresh Queue" }}
          </button>
        </div>
      </div>

      <div class="request-table">
        <div class="request-row request-head">
          <span>Judge Run</span>
          <span>Status</span>
          <span>Adapter</span>
          <span>Updated</span>
          <span>Artifact</span>
          <span>Action</span>
        </div>
        <div v-if="!loadingRequests && requestRows.length === 0" class="empty-row">
          No judge requests found for the selected run.
        </div>
        <div v-for="request in requestRows" :key="request.id" class="request-row">
          <span>
            <strong>{{ request.judge_run_id }}</strong>
            <small>{{ request.rubric_id }}@{{ request.rubric_version }}</small>
            <small>{{ request.run_id }}</small>
            <small v-if="requestErrorSummary(request)" class="error-text">
              {{ requestErrorSummary(request) }}
            </small>
          </span>
          <span class="status-pill" :class="statusClass(request.status)">
            {{ request.status }}
            <small v-if="request.attempt_no > 1">attempt {{ request.attempt_no }}</small>
          </span>
          <span>{{ request.judge_adapter_kind }}</span>
          <span>{{ formatDate(request.finished_at || request.heartbeat_at || request.date_updated) }}</span>
          <span>{{ dash(request.artifact_path || request.expected_artifact_path) }}</span>
          <span class="row-actions">
            <button
              v-if="['queued', 'running'].includes(request.status)"
              type="button"
              @click="cancelJudgeRequest(request)"
            >
              Cancel
            </button>
            <button
              v-else-if="['failed', 'cancelled'].includes(request.status)"
              type="button"
              @click="retryJudgeRequest(request)"
            >
              Retry
            </button>
          </span>
        </div>
      </div>
    </section>

    <section class="judge-panel">
      <div class="panel-heading">
        <div>
          <h3>Judge Reports</h3>
          <span>读取 `evals/runs/&lt;run_id&gt;/judge/&lt;judge_run_id&gt;/report.json`。</span>
        </div>
        <button class="secondary-btn" type="button" :disabled="loadingDetail" @click="refreshSelectedArtifacts">
          Refresh Reports
        </button>
      </div>

      <div v-if="judgeReports.length" class="report-list">
        <button
          v-for="report in judgeReports"
          :key="report.judge_run_id"
          type="button"
          :class="{ 'is-active': selectedJudgeReport?.summary?.judge_run_id === report.judge_run_id }"
          @click="selectJudgeReport(report.judge_run_id)"
        >
          <strong>{{ report.judge_run_id }}</strong>
          <small>{{ report.total_cases }} cases · {{ report.passed }} pass · {{ report.failed }} fail</small>
        </button>
      </div>
      <p v-else class="muted-line">当前 run 暂无 judge report artifact。</p>
    </section>

    <ResultBlock title="Selected Judge Report" :open="Boolean(selectedJudgeReport)">
      <template v-if="selectedJudgeReport">
        <div class="summary-grid compact">
          <div>
            <span>Judge Run</span>
            <strong>{{ selectedJudgeReport.summary.judge_run_id }}</strong>
          </div>
          <div>
            <span>Rubric</span>
            <strong>{{ selectedJudgeReport.summary.rubric_id }}</strong>
            <small>{{ selectedJudgeReport.summary.rubric_version }}</small>
          </div>
          <div>
            <span>Cases</span>
            <strong>{{ selectedJudgeReport.summary.total_cases }}</strong>
          </div>
          <div>
            <span>Pass / Fail</span>
            <strong>{{ selectedJudgeReport.summary.passed }} / {{ selectedJudgeReport.summary.failed }}</strong>
          </div>
          <div>
            <span>Needs Review</span>
            <strong>{{ selectedJudgeReport.summary.needs_review }}</strong>
          </div>
          <div>
            <span>Average Score</span>
            <strong>{{ dash(selectedJudgeReport.summary.average_score) }}</strong>
          </div>
        </div>

        <div class="case-summary-table">
          <div class="case-summary-row case-summary-head">
            <span>Case</span>
            <span>Verdict</span>
            <span>Score</span>
            <span>Reasons</span>
          </div>
          <div
            v-for="item in selectedJudgeReport.report.case_summaries || []"
            :key="item.case_id"
            class="case-summary-row"
          >
            <span>{{ item.case_id }}</span>
            <span class="status-pill" :class="statusClass(item.verdict)">{{ item.verdict }}</span>
            <span>{{ dash(item.score) }}</span>
            <span>{{ item.reasons?.join("; ") || "—" }}</span>
          </div>
        </div>

        <ResultBlock title="Judge Report JSON" :open="false">
          <pre>{{ formatJson(selectedJudgeReport) }}</pre>
        </ResultBlock>
      </template>
      <p v-else class="muted-line">选择一个 judge report 查看明细。</p>
    </ResultBlock>
  </section>
</template>

<style scoped>
.judge-mode {
  max-width: 1180px;
}

.section-heading,
.panel-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.section-heading h2,
.panel-heading h3 {
  margin: 0 0 4px;
}

.section-heading span,
.panel-heading span,
.muted-line {
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

.error-message,
.error-text {
  color: var(--theme--danger);
  font-size: 13px;
}

.judge-controls {
  display: grid;
  grid-template-columns: minmax(220px, 1.4fr) minmax(180px, 1.1fr) minmax(120px, 0.7fr) minmax(90px, 0.5fr) auto;
  gap: 12px;
  align-items: end;
  margin-bottom: 18px;
}

.judge-controls label {
  display: grid;
  gap: 4px;
}

.judge-controls span {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.judge-controls select,
.judge-controls input,
.panel-actions select {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 12px;
  padding: 7px 8px;
}

.primary-btn,
.secondary-btn,
.request-row button,
.report-list button {
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  padding: 7px 10px;
}

.primary-btn {
  border-color: var(--theme--primary);
  background: var(--theme--primary);
  color: var(--theme--background);
  font-weight: 700;
}

.primary-btn:disabled,
.secondary-btn:disabled {
  cursor: default;
  opacity: 0.55;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 18px;
}

.summary-grid.compact {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}

.summary-grid div {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px;
}

.summary-grid span,
.summary-grid small {
  display: block;
  overflow: hidden;
  color: var(--theme--foreground-subdued);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.summary-grid strong {
  display: block;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.judge-panel {
  border-top: 1px solid var(--theme--border-color);
  padding: 18px 0;
}

.panel-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.request-table,
.case-summary-table {
  display: grid;
  gap: 6px;
}

.request-row {
  display: grid;
  grid-template-columns:
    minmax(180px, 1.2fr) minmax(90px, 0.6fr) minmax(70px, 0.5fr) minmax(120px, 0.8fr)
    minmax(200px, 1.2fr) minmax(70px, 0.5fr);
  gap: 10px;
  align-items: center;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px;
  font-size: 12px;
}

.request-head,
.case-summary-head {
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}

.request-row span,
.case-summary-row span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.request-row small,
.report-list small {
  display: block;
  overflow: hidden;
  color: var(--theme--foreground-subdued);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-row {
  border: 1px dashed var(--theme--border-color);
  border-radius: 6px;
  padding: 14px;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  text-align: center;
}

.row-actions {
  display: flex;
  gap: 6px;
}

.status-pill {
  display: inline-flex;
  width: fit-content;
  max-width: 100%;
  border-radius: 999px;
  background: var(--theme--background-subdued);
  padding: 2px 8px;
  font-weight: 700;
}

.status-pill.is-win {
  background: var(--theme--success-background);
}

.status-pill.is-loss {
  background: var(--theme--danger-background);
}

.status-pill.is-review,
.status-pill.is-running {
  background: var(--theme--warning-background);
}

.status-pill.is-muted {
  color: var(--theme--foreground-subdued);
}

.report-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.report-list button {
  max-width: 280px;
  text-align: left;
}

.report-list button.is-active {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.case-summary-row {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) minmax(90px, 0.6fr) minmax(60px, 0.4fr) minmax(240px, 1.8fr);
  gap: 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px;
  font-size: 12px;
}

pre {
  max-height: 420px;
  overflow: auto;
  margin: 12px 0 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background-subdued);
  padding: 12px;
  color: var(--theme--foreground);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 1100px) {
  .judge-controls,
  .summary-grid,
  .summary-grid.compact,
  .request-row,
  .case-summary-row {
    grid-template-columns: 1fr;
  }

  .section-heading,
  .panel-heading {
    flex-direction: column;
  }
}
</style>
