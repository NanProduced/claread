<script setup>
import { useApi } from "@directus/extensions-sdk";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import JsonTreeView from "../components/JsonTreeView.vue";
import NodeProbeOutputView from "../components/NodeProbeOutputView.vue";
import ResultBlock from "../components/ResultBlock.vue";
import ReviewNotesPanel from "../components/ReviewNotesPanel.vue";
import StatusPill from "../components/StatusPill.vue";
import WorkflowCompareReport from "./workflow-lab/components/WorkflowCompareReport.vue";
import WorkflowJudgePanel from "./workflow-lab/components/WorkflowJudgePanel.vue";
import {
  formatDateTime,
  shortId,
  statusLabel,
  statusTone,
} from "../composables/useEvalFormatting";

const api = useApi();
const emit = defineEmits(["compare-run"]);

const props = defineProps({
  initialRunId: { type: String, default: "" },
  initialSource: { type: String, default: "all" },
});

const NODE_ENDPOINT = "/eval-center/node-lab/run-history";
const WORKFLOW_ENDPOINT = "/eval-center/workflow-lab/run-history";

const POLL_INTERVAL_MS = 7000;
const LIVE_STATUSES = new Set(["queued", "running"]);
const SOURCE_VALUES = new Set(["all", "node_lab", "workflow"]);
const WORKFLOW_WORKSPACE_VALUES = new Set(["all", "workflow_compare"]);

const loading = ref(false);
const detailLoading = ref(false);
const deleting = ref(false);
const error = ref("");
const detailError = ref("");
const records = ref([]);
const selectedRecordKey = ref("");
const pendingDeleteKey = ref("");
const detail = ref(null);
const sourceFilter = ref("all");
const nodeFilters = ref({
  workspace_type: "all",
  session_scope: "all",
  node_name: "all",
});
const workflowFilters = ref({
  workspace_type: "all",
});
const isPollingActive = ref(false);
let pollHandle = null;
let visibilityListenerBound = false;

const filteredTitle = computed(() => {
  const sourceLabel = sourceFilter.value === "workflow"
    ? "Workflow"
    : sourceFilter.value === "node_lab"
      ? "Node Lab"
      : "全部来源";
  const workspace = nodeFilters.value.workspace_type === "single_run"
    ? "Single Run"
    : nodeFilters.value.workspace_type === "baseline_compare"
      ? "Baseline Compare"
      : "全部类型";
  const scope = nodeFilters.value.session_scope === "standalone"
    ? "Standalone"
    : nodeFilters.value.session_scope === "session"
      ? "Session"
      : "全部来源";
  const workflowScope = workflowFilters.value.workspace_type === "workflow_compare"
    ? "Workflow Compare"
    : "Workflow 全部类型";
  if (sourceFilter.value === "workflow") {
    return `${sourceLabel} / ${workflowScope}`;
  }
  if (sourceFilter.value === "node_lab") {
    return `${sourceLabel} / ${workspace} / ${scope}`;
  }
  return `${sourceLabel} / ${workspace} / ${scope} / ${workflowScope}`;
});

const selectedRecord = computed(() => (
  records.value.find((record) => recordKeyOf(record) === selectedRecordKey.value) || null
));

const currentSource = computed(() => detail.value?.source || selectedRecord.value?.source || sourceFilter.value);
const currentTrial = computed(() => (
  currentSource.value === "node_lab"
    ? detail.value?.trial || selectedRecord.value || null
    : null
));
const currentResult = computed(() => (
  currentSource.value === "node_lab"
    ? detail.value?.result || null
    : null
));
const judgeRequests = computed(() => (
  currentSource.value === "node_lab"
    ? detail.value?.judge_requests || []
    : []
));
const currentSession = computed(() => (
  currentSource.value === "node_lab"
    ? detail.value?.session || null
    : null
));
const currentWorkflowRecord = computed(() => (
  currentSource.value === "workflow"
    ? detail.value?.record || selectedRecord.value || null
    : null
));
const workflowSummary = computed(() => (
  currentSource.value === "workflow"
    ? detail.value?.report || null
    : null
));
const workflowCaseArtifacts = computed(() => (
  currentSource.value === "workflow" && Array.isArray(detail.value?.report?.comparisons)
    ? detail.value.report.comparisons
    : []
));
const workflowJudgeReports = computed(() => (
  currentSource.value === "workflow" && Array.isArray(detail.value?.compare_judge_requests)
    ? detail.value.compare_judge_requests
    : []
));
const workflowFullCaseArtifacts = computed(() => []);
const workflowSingleRunArtifact = computed(() => null);
const workflowCompareReports = computed(() => []);

const resultKindLabel = computed(() => {
  if (currentSource.value === "workflow") return workflowWorkspaceLabel(currentWorkflowRecord.value);
  const kind = currentTrial.value?.result_kind;
  if (kind === "single_run_result") return "Single Run";
  if (kind === "compare_result") return "Baseline Compare";
  return "Result";
});

const workflowCaseRows = computed(() => [...workflowCaseArtifacts.value].sort((a, b) => {
  const severityDelta = workflowCaseSeverityRank(b) - workflowCaseSeverityRank(a);
  if (severityDelta !== 0) return severityDelta;
  return String(a.case_id || "").localeCompare(String(b.case_id || ""));
}));

watch(
  () => [props.initialSource, props.initialRunId],
  ([source, runId]) => {
    const normalizedSource = normalizeSource(source);
    if (normalizedSource) {
      sourceFilter.value = normalizedSource;
    }
    if (runId) {
      void selectRecordBySource(normalizedSource === "all" ? "workflow" : normalizedSource, runId);
    }
  },
);

onMounted(async () => {
  sourceFilter.value = normalizeSource(props.initialSource);
  await loadRecords();
  if (props.initialRunId) {
    await selectRecordBySource(sourceFilter.value === "all" ? "workflow" : sourceFilter.value, props.initialRunId);
  } else if (!selectedRecordKey.value && records.value.length) {
    await selectRecord(records.value[0]);
  }
  startPolling();
  bindVisibilityListener();
});

onBeforeUnmount(() => {
  stopPolling();
  unbindVisibilityListener();
});

async function fetchData(url) {
  const response = await api.get(url);
  return response?.data?.data ?? response?.data;
}

function normalizeSource(value) {
  return SOURCE_VALUES.has(String(value || "")) ? String(value || "") : "all";
}

function normalizeWorkflowWorkspace(value) {
  return WORKFLOW_WORKSPACE_VALUES.has(String(value || "")) ? String(value || "") : "all";
}

function buildNodeQuery() {
  const params = new URLSearchParams();
  params.set("limit", "80");
  for (const [key, value] of Object.entries(nodeFilters.value)) {
    if (value && value !== "all") params.set(key, value);
  }
  return params.toString();
}

function recordId(record) {
  if (!record) return "";
  return record.source === "workflow"
    ? String(record.compare_id || record.record_id || "")
    : String(record.trial_id || record.record_id || "");
}

function recordKeyOf(record) {
  return `${record?.source || "unknown"}:${recordId(record)}`;
}

async function fetchNodeRecords() {
  const data = await fetchData(`${NODE_ENDPOINT}?${buildNodeQuery()}`);
  const items = Array.isArray(data?.records) ? data.records : [];
  return items.map((record) => ({ ...record, source: "node_lab" }));
}

async function fetchWorkflowRecords() {
  const data = await fetchData(`${WORKFLOW_ENDPOINT}?limit=80`);
  const items = Array.isArray(data?.records) ? data.records : [];
  return items.map((record) => ({ ...record, source: "workflow" }));
}

function applyRecordFilters(items) {
  return items.filter((record) => {
    if (record?.source === "workflow") {
      return workflowFilters.value.workspace_type === "all"
        || record.workspace_type === workflowFilters.value.workspace_type;
    }
    return true;
  });
}

async function loadRecords(options = {}) {
  loading.value = true;
  error.value = "";
  try {
    const loaders = [];
    if (sourceFilter.value !== "workflow") loaders.push(fetchNodeRecords());
    if (sourceFilter.value !== "node_lab") loaders.push(fetchWorkflowRecords());
    const loadedGroups = await Promise.all(loaders);
    records.value = applyRecordFilters(loadedGroups
      .flat()
      .sort((a, b) => {
        const aTime = new Date(a.date_created || a.created_at || 0).getTime();
        const bTime = new Date(b.date_created || b.created_at || 0).getTime();
        return bTime - aTime;
      }));
    if (!options.keepSelection) {
      selectedRecordKey.value = "";
      detail.value = null;
    }
    if (selectedRecordKey.value && !records.value.some((record) => recordKeyOf(record) === selectedRecordKey.value)) {
      selectedRecordKey.value = "";
      detail.value = null;
    }
    if (!selectedRecordKey.value && records.value.length) {
      await selectRecord(records.value[0]);
    }
  } catch (err) {
    error.value = err?.response?.data?.errors?.[0]?.message || err?.message || "读取 Run History 失败。";
  } finally {
    loading.value = false;
  }
}

async function selectRecordBySource(source, recordIdValue) {
  const normalizedSource = normalizeSource(source);
  if (!recordIdValue) return;
  const searchSources = normalizedSource === "all" ? ["node_lab", "workflow"] : [normalizedSource];
  for (const src of searchSources) {
    const existing = records.value.find((item) => item.source === src && recordId(item) === recordIdValue);
    if (existing) {
      await selectRecord(existing);
      return;
    }
  }
  detailLoading.value = true;
  detailError.value = "";
  const fetchSource = normalizedSource === "all" ? "workflow" : normalizedSource;
  try {
    detail.value = await fetchData(
      `${fetchSource === "workflow" ? WORKFLOW_ENDPOINT : NODE_ENDPOINT}/${encodeURIComponent(recordIdValue)}`,
    );
    selectedRecordKey.value = `${fetchSource}:${recordIdValue}`;
  } catch (err) {
    detailError.value = err?.response?.data?.errors?.[0]?.message || err?.message || "读取详情失败。";
  } finally {
    detailLoading.value = false;
  }
}

async function selectRecord(recordOrKey) {
  const record = typeof recordOrKey === "string"
    ? records.value.find((item) => recordKeyOf(item) === recordOrKey) || null
    : recordOrKey;
  if (!record) return;
  pendingDeleteKey.value = "";
  selectedRecordKey.value = recordKeyOf(record);
  detailLoading.value = true;
  detailError.value = "";
  try {
    detail.value = await fetchData(
      `${record.source === "workflow" ? WORKFLOW_ENDPOINT : NODE_ENDPOINT}/${encodeURIComponent(recordId(record))}`,
    );
  } catch (err) {
    detailError.value = err?.response?.data?.errors?.[0]?.message || err?.message || "读取详情失败。";
  } finally {
    detailLoading.value = false;
  }
}

function deleteTargetKey() {
  if (currentSource.value === "workflow") {
    return currentWorkflowRecord.value?.compare_id
      ? `workflow:${currentWorkflowRecord.value.compare_id}`
      : "";
  }
  return currentTrial.value?.trial_id ? `node_lab:${currentTrial.value.trial_id}` : "";
}

function promptDeleteSelectedRecord() {
  pendingDeleteKey.value = deleteTargetKey();
}

function cancelDeleteSelectedRecord() {
  pendingDeleteKey.value = "";
}

async function deleteSelectedRecord() {
  const source = currentSource.value;
  const key = deleteTargetKey();
  if (!key || pendingDeleteKey.value !== key) return;
  const workflowCompareId = currentWorkflowRecord.value?.compare_id;
  const nodeTrialId = currentTrial.value?.trial_id;
  deleting.value = true;
  detailError.value = "";
  try {
    if (source === "workflow" && workflowCompareId) {
      await api.delete(`${WORKFLOW_ENDPOINT}/${encodeURIComponent(workflowCompareId)}`);
    } else if (source === "node_lab" && nodeTrialId) {
      await api.delete(`/eval-center/node-lab/trials/${encodeURIComponent(nodeTrialId)}`);
    } else {
      throw new Error("当前记录不支持删除。");
    }
    pendingDeleteKey.value = "";
    selectedRecordKey.value = "";
    detail.value = null;
    await loadRecords({ keepSelection: false });
  } catch (err) {
    detailError.value = err?.response?.data?.errors?.[0]?.message || err?.message || "删除历史记录失败。";
  } finally {
    deleting.value = false;
  }
}

function setSource(value) {
  sourceFilter.value = normalizeSource(value);
  pendingDeleteKey.value = "";
  void loadRecords({ keepSelection: false });
}

function setNodeFilter(key, value) {
  nodeFilters.value = { ...nodeFilters.value, [key]: value };
  void loadRecords({ keepSelection: true });
}

function setWorkflowFilter(key, value) {
  workflowFilters.value = {
    ...workflowFilters.value,
    [key]: key === "workspace_type" ? normalizeWorkflowWorkspace(value) : value,
  };
  void loadRecords({ keepSelection: true });
}

function retryLoadRecords() {
  void loadRecords({ keepSelection: true });
}

function retrySelectRecord() {
  if (selectedRecordKey.value) {
    void selectRecord(selectedRecordKey.value);
  }
}

function hasLiveRecords() {
  return records.value.some((record) => LIVE_STATUSES.has(record.status));
}

async function pollOnce() {
  if (!isPollingActive.value) return;
  if (loading.value) return;
  if (!hasLiveRecords()) return;
  try {
    await loadRecords({ keepSelection: true });
  } catch {
    // Polling 失败静默,下一次 interval 继续
  }
}

function startPolling() {
  if (pollHandle) return;
  isPollingActive.value = true;
  pollHandle = window.setInterval(() => {
    void pollOnce();
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  isPollingActive.value = false;
  if (pollHandle) {
    window.clearInterval(pollHandle);
    pollHandle = null;
  }
}

function handleVisibilityChange() {
  if (document.hidden) {
    stopPolling();
  } else {
    startPolling();
    void pollOnce();
  }
}

function bindVisibilityListener() {
  if (visibilityListenerBound) return;
  if (typeof document === "undefined") return;
  document.addEventListener("visibilitychange", handleVisibilityChange);
  visibilityListenerBound = true;
}

function unbindVisibilityListener() {
  if (!visibilityListenerBound) return;
  if (typeof document === "undefined") return;
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  visibilityListenerBound = false;
}

function workspaceLabel(record) {
  if (record?.source === "workflow") return workflowWorkspaceLabel(record);
  if (record?.result_kind === "single_run_result") return "Single Run";
  if (record?.workspace_type === "baseline_compare") return "Compare";
  return record?.workspace_type || "Result";
}

function workflowWorkspaceLabel(recordOrType) {
  const workspaceType = typeof recordOrType === "string"
    ? recordOrType
    : recordOrType?.workspace_type;
  if (workspaceType === "workflow_compare") return "Workflow Compare";
  return "Workflow Compare";
}

function sourceLabel(record) {
  if (record?.source === "workflow") {
    return "Workflow Lab · Compare";
  }
  if (record?.session_id) return record.session_title || record.session_id;
  return "Standalone";
}

function recordTitle(record) {
  if (record?.source === "workflow") {
    return record.display_title || `${record.prompt_variant_id || "baseline"} · compare`;
  }
  return `${record.node_name} · ${record.reading_goal} · ${record.reading_variant}`;
}

function recordExcerpt(record) {
  if (record?.source === "workflow") {
    return record.display_excerpt || record.run_id || "";
  }
  return record.display_excerpt || record.input_excerpt || record.input_text_hash || "";
}

function workflowDatasetValue(record) {
  return record?.compare_id || "未记录";
}

function runOutput(result) {
  return result?.run?.node_output || null;
}

function compareSides(result) {
  if (!result) return [];
  return [
    { key: "baseline", label: "Baseline", value: result.baseline || null },
    { key: "candidate", label: "Candidate", value: result.candidate || null },
  ].filter((item) => item.value);
}

function tokenSummary(runtime) {
  const total = runtime?.aggregate?.total_tokens;
  if (total == null) return "未记录";
  return `${total} tokens`;
}

function workflowCaseSeverityRank(item) {
  if (!item) return 0;
  if (item.error) return 5;
  if ((item.hard_failures || 0) > 0) return 4;
  if ((item.soft_failures || 0) > 0) return 3;
  if ((item.warning_count || 0) > 0) return 2;
  if ((item.drop_count || 0) > 0) return 1;
  return 0;
}

function openWorkflowCompare(report) {
  if (!report?.baseline_run_id || !report?.candidate_run_id) return;
  emit("compare-run", {
    baseline_run_id: report.baseline_run_id,
    candidate_run_id: report.candidate_run_id,
  });
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
  <section class="run-history">
    <aside class="history-list">
      <div class="list-header">
        <div>
          <p class="eyebrow">Run History</p>
          <h2>{{ filteredTitle }}</h2>
        </div>
        <button class="icon-button" type="button" :disabled="loading" aria-label="刷新历史记录" title="刷新" @click="loadRecords({ keepSelection: true })">
          <span aria-hidden="true">↻</span>
        </button>
      </div>

      <div class="filters">
        <label>
          <span>数据源</span>
          <select :value="sourceFilter" @change="setSource($event.target.value)">
            <option value="all">全部</option>
            <option value="node_lab">Node Lab</option>
            <option value="workflow">Workflow Lab</option>
          </select>
        </label>
        <label v-if="sourceFilter !== 'workflow'">
          <span>类型</span>
          <select :value="nodeFilters.workspace_type" @change="setNodeFilter('workspace_type', $event.target.value)">
            <option value="all">全部</option>
            <option value="single_run">Single Run</option>
            <option value="baseline_compare">Baseline Compare</option>
          </select>
        </label>
        <label v-if="sourceFilter !== 'workflow'">
          <span>来源</span>
          <select :value="nodeFilters.session_scope" @change="setNodeFilter('session_scope', $event.target.value)">
            <option value="all">全部</option>
            <option value="standalone">Standalone</option>
            <option value="session">Session</option>
          </select>
        </label>
        <label v-if="sourceFilter !== 'workflow'">
          <span>Node</span>
          <select :value="nodeFilters.node_name" @change="setNodeFilter('node_name', $event.target.value)">
            <option value="all">全部</option>
            <option value="grammar">Grammar</option>
            <option value="vocabulary">Vocabulary</option>
            <option value="translation">Translation</option>
          </select>
        </label>
        <label v-if="sourceFilter !== 'node_lab'">
          <span>Workflow 类型</span>
          <select :value="workflowFilters.workspace_type" @change="setWorkflowFilter('workspace_type', $event.target.value)">
            <option value="all">全部</option>
            <option value="workflow_compare">Workflow Compare</option>
          </select>
        </label>
      </div>

      <div v-if="error" class="notice is-danger" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="notice-retry" @click="retryLoadRecords">重试</button>
      </div>
      <div v-else-if="loading && !records.length" class="empty-state">正在读取历史记录...</div>
      <div v-else-if="!records.length" class="empty-state">暂无匹配记录。</div>

      <div class="record-list">
        <button
          v-for="record in records"
          :key="recordKeyOf(record)"
          type="button"
          class="record-row"
          :class="{ active: recordKeyOf(record) === selectedRecordKey }"
          @click="selectRecord(record)"
        >
          <span class="record-topline">
            <span class="record-type">{{ workspaceLabel(record) }}</span>
            <StatusPill :label="statusLabel(record.status)" :tone="statusTone(record.status)" />
          </span>
          <strong>{{ recordTitle(record) }}</strong>
          <span class="record-excerpt">{{ recordExcerpt(record) }}</span>
          <span class="record-meta">
            <span>{{ sourceLabel(record) }}</span>
            <span>{{ formatDateTime(record.date_created || record.created_at) }}</span>
          </span>
        </button>
      </div>
    </aside>

    <main class="history-detail">
      <div v-if="detailLoading" class="empty-state">正在读取详情...</div>
      <div v-else-if="detailError" class="notice is-danger" role="alert">
        <span>{{ detailError }}</span>
        <button type="button" class="notice-retry" @click="retrySelectRecord">重试</button>
      </div>
      <div v-else-if="!currentTrial && !currentWorkflowRecord" class="empty-state">请选择一条历史记录。</div>
      <template v-else-if="currentSource === 'workflow' && currentWorkflowRecord">
        <header class="detail-header">
          <div>
            <p class="eyebrow">{{ resultKindLabel }}</p>
            <h2>{{ currentWorkflowRecord.display_title || currentWorkflowRecord.compare_id }}</h2>
            <p>{{ currentWorkflowRecord.display_excerpt || currentWorkflowRecord.compare_id }}</p>
          </div>
          <div class="detail-actions">
            <StatusPill :label="statusLabel(currentWorkflowRecord.status)" :tone="statusTone(currentWorkflowRecord.status)" size="large" />
            <button type="button" class="danger-button" :disabled="deleting" @click="promptDeleteSelectedRecord">删除记录</button>
          </div>
        </header>

        <div
          v-if="pendingDeleteKey === deleteTargetKey()"
          class="notice is-warning"
          role="alert"
        >
          <span>删除后会移除这条 compare 对应的 artifact、compare judge 文件、compare review notes，以及它私有依赖的底层双跑 run artifact。</span>
          <div class="notice-actions">
            <button type="button" class="notice-retry" @click="cancelDeleteSelectedRecord">取消</button>
            <button type="button" class="danger-button" :disabled="deleting" @click="deleteSelectedRecord">
              {{ deleting ? "删除中..." : "确认删除" }}
            </button>
          </div>
        </div>

        <dl class="meta-grid">
          <div>
            <dt>Compare</dt>
            <dd>{{ currentWorkflowRecord.compare_id }}</dd>
          </div>
          <div>
            <dt>类型</dt>
            <dd>{{ workflowWorkspaceLabel(currentWorkflowRecord) }}</dd>
          </div>
          <div>
            <dt>Prompt Variant</dt>
            <dd>{{ currentWorkflowRecord.prompt_variant_id || "baseline" }}</dd>
          </div>
          <div>
            <dt>Input Hash</dt>
            <dd>{{ currentWorkflowRecord.input_hash || "未记录" }}</dd>
          </div>
          <div>
            <dt>Baseline Run</dt>
            <dd>{{ currentWorkflowRecord.baseline_run_id || "未记录" }}</dd>
          </div>
          <div>
            <dt>Candidate Run</dt>
            <dd>{{ currentWorkflowRecord.candidate_run_id || "未记录" }}</dd>
          </div>
        </dl>

        <section class="result-section">
          <div class="section-heading">
            <h3>Compare 摘要</h3>
            <span>{{ workflowJudgeReports.length }} 条 Judge</span>
          </div>
          <dl class="meta-grid workflow-summary-grid">
            <div>
              <dt>总 Cases</dt>
              <dd>{{ workflowSummary?.total_cases ?? 0 }}</dd>
            </div>
            <div>
              <dt>更好</dt>
              <dd>{{ workflowSummary?.wins ?? 0 }}</dd>
            </div>
            <div>
              <dt>变差</dt>
              <dd>{{ workflowSummary?.losses ?? 0 }}</dd>
            </div>
            <div>
              <dt>持平</dt>
              <dd>{{ workflowSummary?.ties ?? 0 }}</dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd>{{ currentWorkflowRecord.source_kind || "未记录" }}</dd>
            </div>
          </dl>
        </section>

        <section class="result-section">
          <div class="section-heading">
            <h3>Compare 报告</h3>
            <span>{{ workflowCaseRows.length }} 条 Case</span>
          </div>
          <WorkflowCompareReport
            :compare-id="currentWorkflowRecord.compare_id"
            :result="{ compare_id: currentWorkflowRecord.compare_id, report: workflowSummary, report_id: currentWorkflowRecord.report_id, created: false }"
            :selected-case-id="''"
            :baseline-artifact="null"
            :candidate-artifact="null"
          />
        </section>

        <section class="result-section">
          <div class="section-heading">
            <h3>Compare Judge</h3>
            <span>{{ workflowJudgeReports.length ? "compare-level" : "暂无 Judge" }}</span>
          </div>
          <WorkflowJudgePanel
            :compare-id="currentWorkflowRecord.compare_id"
            :requests="workflowJudgeReports"
            :rubrics="[]"
            :disabled="true"
            :submitting="false"
            @refresh="retrySelectRecord"
          />
        </section>

        <section v-if="currentWorkflowRecord.compare_id" class="result-section">
          <div class="section-heading">
            <h3>人工 Review</h3>
            <span>只读 / 补充</span>
          </div>
          <ReviewNotesPanel
            title="Workflow Compare Review"
            target-type="workflow_compare"
            :target-id="currentWorkflowRecord.compare_id"
            :run-id="currentWorkflowRecord.candidate_run_id"
            scope-note="这类 note 挂在 workflow_compare 上，用于回看阶段补充 compare-scope 人工结论。"
          />
        </section>

        <ResultBlock title="完整结果 JSON" :open="false">
          <JsonTreeView
            :value="{ compare: detail?.compare, report: workflowSummary, evidence_index: detail?.evidence_index, baseline_run_summary: detail?.baseline_run_summary, candidate_run_summary: detail?.candidate_run_summary }"
            empty-text="暂无 result artifact。"
          />
        </ResultBlock>
      </template>
      <template v-else>
        <header class="detail-header">
          <div>
            <p class="eyebrow">{{ resultKindLabel }}</p>
            <h2>{{ currentTrial.node_name }} · {{ currentTrial.reading_goal }} · {{ currentTrial.reading_variant }}</h2>
            <p>{{ currentTrial.display_excerpt || currentTrial.input_excerpt || currentTrial.input_text_hash }}</p>
          </div>
          <div class="detail-actions">
            <StatusPill :label="statusLabel(currentTrial.status)" :tone="statusTone(currentTrial.status)" size="large" />
            <button type="button" class="danger-button" :disabled="deleting" @click="promptDeleteSelectedRecord">删除记录</button>
          </div>
        </header>

        <div
          v-if="pendingDeleteKey === deleteTargetKey()"
          class="notice is-warning"
          role="alert"
        >
          <span>删除后会移除这条 trial 对应的 artifact、本地 judge 文件，以及关联的 Node Lab 数据库记录。</span>
          <div class="notice-actions">
            <button type="button" class="notice-retry" @click="cancelDeleteSelectedRecord">取消</button>
            <button type="button" class="danger-button" :disabled="deleting" @click="deleteSelectedRecord">
              {{ deleting ? "删除中..." : "确认删除" }}
            </button>
          </div>
        </div>

        <dl class="meta-grid">
          <div>
            <dt>Trial</dt>
            <dd>{{ currentTrial.trial_id }}</dd>
          </div>
          <div>
            <dt>来源</dt>
            <dd>{{ sourceLabel(currentTrial) }}</dd>
          </div>
          <div>
            <dt>Prompt</dt>
            <dd>{{ shortId(currentTrial.baseline_snapshot_hash || "baseline", 16) }}</dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd>{{ formatDateTime(currentTrial.date_created) }}</dd>
          </div>
        </dl>

        <section v-if="currentSession" class="session-band">
          <div>
            <strong>{{ currentSession.title }}</strong>
            <span>{{ currentSession.status }} · {{ currentSession.goal || "未记录目标" }}</span>
          </div>
          <span class="session-id">{{ currentSession.session_id }}</span>
        </section>

        <section v-if="currentTrial.result_kind === 'single_run_result'" class="result-section">
          <div class="section-heading">
            <h3>Single Run 输出</h3>
            <span>{{ tokenSummary(currentResult?.run?.runtime_summary) }}</span>
          </div>
          <NodeProbeOutputView
            :node-name="currentTrial.node_name"
            :output="runOutput(currentResult)"
            :prepared-sentences="currentResult?.run?.prepared_sentences || []"
            :quick-validation="currentResult?.run?.quick_validation || null"
            empty-text="这条记录没有结构化输出。"
          />
        </section>

        <section v-else class="result-section">
          <div class="section-heading">
            <h3>Compare 输出</h3>
            <span>{{ judgeRequests.length }} 条 Judge</span>
          </div>
          <div class="compare-grid">
            <article v-for="side in compareSides(currentResult)" :key="side.key" class="compare-side">
              <div class="compare-side__header">
                <strong>{{ side.label }}</strong>
                <StatusPill :label="statusLabel(side.value.status)" :tone="statusTone(side.value.status)" />
              </div>
              <p>{{ side.value.model_identity?.model_name || "未记录模型" }} · {{ tokenSummary(side.value.runtime_summary) }}</p>
              <NodeProbeOutputView
                :node-name="currentTrial.node_name"
                :output="side.value.node_output || null"
                :prepared-sentences="side.value.prepared_sentences || []"
                :quick-validation="side.value.quick_validation || null"
                empty-text="这侧没有结构化输出。"
              />
            </article>
          </div>
        </section>

        <section class="result-section">
          <div class="section-heading">
            <h3>Judge 结果</h3>
            <span>{{ judgeRequests.length ? "只读" : "暂无 Judge" }}</span>
          </div>
          <div v-if="!judgeRequests.length" class="empty-inline">这条记录还没有 Judge request。</div>
          <div v-else class="judge-list">
            <ResultBlock
              v-for="request in judgeRequests"
              :key="request.judge_request_id"
              :title="`${request.judge_request_id} · ${statusLabel(request.status)}`"
              :open="request.status === 'succeeded'"
            >
              <dl class="judge-meta">
                <div>
                  <dt>Mode</dt>
                  <dd>{{ request.judge_config_snapshot_json?.judge_mode || "未记录" }}</dd>
                </div>
                <div>
                  <dt>Preset</dt>
                  <dd>{{ request.judge_config_snapshot_json?.preset_id || "未记录" }}</dd>
                </div>
                <div>
                  <dt>时间</dt>
                  <dd>{{ formatDateTime(request.finished_at || request.date_updated) }}</dd>
                </div>
              </dl>
              <JsonTreeView :value="request.result || request.artifacts || request.error_json" empty-text="暂无 Judge artifact。" />
            </ResultBlock>
          </div>
        </section>

        <ResultBlock title="完整结果 JSON" :open="false">
          <JsonTreeView :value="currentResult" empty-text="暂无 result artifact。" />
        </ResultBlock>
      </template>
    </main>
  </section>
</template>

<style scoped>
.run-history {
  display: grid;
  grid-template-columns: minmax(320px, 0.38fr) minmax(0, 1fr);
  gap: 16px;
  min-height: 720px;
}

.history-list,
.history-detail {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
}

.history-list {
  display: flex;
  flex-direction: column;
}

.list-header,
.detail-header,
.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.list-header {
  padding: 16px;
  border-bottom: 1px solid var(--theme--border-color);
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

h2,
h3 {
  margin: 0;
  color: var(--theme--foreground);
}

.list-header h2,
.detail-header h2 {
  font-size: 18px;
  line-height: 1.3;
}

.detail-header p {
  margin: 8px 0 0;
  color: var(--theme--foreground-subdued);
  line-height: 1.5;
}

.detail-actions,
.notice-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.icon-button {
  width: 34px;
  height: 34px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
}

.icon-button:disabled {
  cursor: default;
  opacity: 0.5;
}

.filters {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--theme--border-color);
}

.filters label {
  display: grid;
  gap: 5px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.filters select {
  width: 100%;
  min-height: 34px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground);
}

.record-list {
  overflow: auto;
}

.record-row {
  display: grid;
  width: 100%;
  gap: 7px;
  padding: 14px 16px;
  border: 0;
  border-bottom: 1px solid var(--theme--border-color);
  background: transparent;
  color: var(--theme--foreground);
  text-align: left;
  cursor: pointer;
}

.record-row.active {
  background: color-mix(in srgb, var(--theme--primary) 10%, transparent);
  box-shadow: inset 3px 0 0 var(--theme--primary);
}

.record-topline,
.record-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.record-type,
.record-meta,
.record-excerpt {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.record-excerpt {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-detail {
  padding: 18px;
  overflow: auto;
}

.meta-grid,
.judge-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
}

.meta-grid div,
.judge-meta div {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
}

dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}

.session-band {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 16px 0;
  padding: 12px;
  border: 1px solid var(--theme--border-color);
  background: color-mix(in srgb, var(--theme--primary) 7%, transparent);
}

.session-band div {
  display: grid;
  gap: 4px;
}

.session-band span {
  color: var(--theme--foreground-subdued);
}

.session-id {
  font-family: var(--theme--fonts--monospace--font-family);
  font-size: 12px;
}

.result-section {
  margin: 18px 0;
}

.section-heading {
  margin-bottom: 10px;
}

.section-heading span {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.workflow-summary-grid {
  margin-top: 0;
}

.workflow-compare-list {
  display: grid;
  gap: 10px;
}

.workflow-compare-item {
  display: grid;
  gap: 5px;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground);
  text-align: left;
  cursor: pointer;
}

.workflow-compare-item span {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.workflow-case-table-wrap {
  overflow: auto;
  border: 1px solid var(--theme--border-color);
}

.workflow-case-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 820px;
}

.workflow-case-table th,
.workflow-case-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--theme--border-color);
  text-align: left;
  vertical-align: top;
}

.workflow-case-table th {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
  background: var(--theme--background-subdued);
}

.compare-side {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--theme--border-color);
}

.compare-side__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.compare-side p {
  margin: 0 0 10px;
  color: var(--theme--foreground-subdued);
}

.judge-list {
  display: grid;
  gap: 10px;
}

.empty-state,
.empty-inline,
.notice {
  padding: 16px;
  color: var(--theme--foreground-subdued);
}

.empty-inline {
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
}

.notice.is-danger {
  color: var(--theme--danger);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.notice.is-warning {
  margin-top: 14px;
  border: 1px solid color-mix(in srgb, #d18d00 35%, var(--theme--border-color));
  color: #8b5a00;
  background: color-mix(in srgb, #d18d00 8%, var(--theme--background));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.notice-retry {
  border: 1px solid color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: var(--theme--background);
  color: var(--theme--danger);
  font-size: 12px;
  font-weight: 600;
  padding: 4px 12px;
  cursor: pointer;
}

.notice-retry:hover {
  background: color-mix(in srgb, var(--theme--danger) 12%, var(--theme--background));
}

.danger-button {
  border: 1px solid color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: var(--theme--background);
  color: var(--theme--danger);
  font-size: 12px;
  font-weight: 700;
  padding: 6px 12px;
  cursor: pointer;
}

.danger-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.danger-button:hover:not(:disabled) {
  background: color-mix(in srgb, var(--theme--danger) 10%, var(--theme--background));
}

@media (max-width: 1100px) {
  .run-history,
  .compare-grid,
  .meta-grid,
  .judge-meta {
    grid-template-columns: 1fr;
  }

  .detail-actions,
  .notice-actions,
  .notice.is-warning,
  .notice.is-danger {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
