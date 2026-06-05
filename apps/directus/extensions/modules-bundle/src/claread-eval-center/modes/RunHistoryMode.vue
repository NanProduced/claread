<script setup>
import { useApi } from "@directus/extensions-sdk";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import JsonTreeView from "../components/JsonTreeView.vue";
import NodeProbeOutputView from "../components/NodeProbeOutputView.vue";
import ResultBlock from "../components/ResultBlock.vue";
import StatusPill from "../components/StatusPill.vue";
import WorkflowCompareReport from "./workflow-lab/components/WorkflowCompareReport.vue";
import WorkflowCompareReviewPanel from "../components/WorkflowCompareReviewPanel.vue";
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
const WORKFLOW_COMPARE_ENDPOINT = "/eval-center/workflow-lab/compares";

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

// Compare replay state (mirrors WorkflowLabMode.vue).
// Without these, the WorkflowCompareReport + WorkflowJudgePanel props blow up
// at template render time, which previously turned the whole Run History page
// into a blank screen whenever a workflow_compare record was selected.
const selectedCompareCase = ref(null);
const compareArtifacts = ref({ baseline: null, candidate: null });
const compareCaseLoading = ref(false);
const compareCaseError = ref("");

const renaming = ref(false);
const renameValue = ref("");
const renameSaving = ref(false);
const renameError = ref("");

function startRename() {
  const record = currentSource.value === "workflow" ? currentWorkflowRecord.value : currentTrial.value;
  renameValue.value = record?.custom_title || "";
  renaming.value = true;
  renameError.value = "";
}

function cancelRename() {
  renaming.value = false;
  renameValue.value = "";
  renameError.value = "";
}

async function saveRename() {
  const trimmed = renameValue.value.trim();
  if (trimmed.length > 200) {
    renameError.value = "标题不能超过 200 字。";
    return;
  }
  renameSaving.value = true;
  renameError.value = "";
  try {
    if (currentSource.value === "workflow" && currentWorkflowRecord.value?.compare_id) {
      await api.patch(
        `/eval-center/workflow-lab/run-history/${encodeURIComponent(currentWorkflowRecord.value.compare_id)}`,
        { custom_title: trimmed || null },
      );
    } else if (currentTrial.value?.trial_id) {
      await api.patch(
        `/eval-center/node-lab/run-history/${encodeURIComponent(currentTrial.value.trial_id)}`,
        { custom_title: trimmed || null },
      );
    } else {
      renameError.value = "当前记录不支持重命名。";
      renameSaving.value = false;
      return;
    }
    renaming.value = false;
    renameValue.value = "";
    // Refresh detail and list so both panes pick up the new title immediately.
    await retrySelectRecord();
    await loadRecords({ keepSelection: true });
  } catch (err) {
    renameError.value = err?.response?.data?.errors?.[0]?.message || err?.message || "重命名失败。";
  } finally {
    renameSaving.value = false;
  }
}

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
const currentTrialMeta = computed(() => currentTrialRuntimeMeta(currentTrial.value, currentResult.value));

const compareModelsSummary = computed(() => {
  if (currentTrial.value?.result_kind !== "compare_result" || !currentResult.value) return null;
  const baselineModel = currentResult.value.baseline?.model_identity?.model_name || "未记录";
  const candidateModel = currentResult.value.candidate?.model_identity?.model_name || "未记录";
  return `${baselineModel} vs ${candidateModel}`;
});

// Workflow compare performance diffs (candidate relative to baseline).
const wfLatencyDiff = computed(() => {
  const b = currentWorkflowRecord.value?.baseline_latency_seconds;
  const c = currentWorkflowRecord.value?.candidate_latency_seconds;
  if (b == null || c == null) return null;
  return Number(c) - Number(b);
});

const wfTokensDiff = computed(() => {
  const b = currentWorkflowRecord.value?.baseline_total_tokens;
  const c = currentWorkflowRecord.value?.candidate_total_tokens;
  if (b == null || c == null) return null;
  return Number(c) - Number(b);
});

function fmtDiff(diff, unit = "", positiveIsBetter = false) {
  if (diff == null) return null;
  const sign = diff > 0 ? "+" : "";
  const cls = diff === 0 ? "diff-neutral" : (positiveIsBetter === (diff > 0) ? "diff-better" : "diff-worse");
  return { text: `${sign}${diff}${unit}`, cls };
}

const wfLatencyDiffBadge = computed(() => fmtDiff(
  wfLatencyDiff.value != null ? parseFloat(wfLatencyDiff.value.toFixed(1)) : null,
  "s",
  false, // lower is better for latency
));

const wfTokensDiffBadge = computed(() => fmtDiff(wfTokensDiff.value, "", false));

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
  // Reset compare replay state when switching records so a stale selection
  // from the previous detail does not leak into the new one.
  selectedCompareCase.value = null;
  compareArtifacts.value = { baseline: null, candidate: null };
  compareCaseError.value = "";
  compareCaseLoading.value = false;
  detailLoading.value = true;
  detailError.value = "";
  try {
    detail.value = await fetchData(
      `${record.source === "workflow" ? WORKFLOW_ENDPOINT : NODE_ENDPOINT}/${encodeURIComponent(recordId(record))}`,
    );
    if (record.source === "workflow") {
      // Auto-pick the first comparison (if any) so the replay pane shows a
      // selected case on first render. Empty-state UI is preserved when the
      // report has zero comparisons.
      const firstComparison = Array.isArray(detail.value?.report?.comparisons)
        ? detail.value.report.comparisons[0] || null
        : null;
      if (firstComparison) {
        await selectCompareCase(firstComparison);
      }
    }
  } catch (err) {
    detailError.value = err?.response?.data?.errors?.[0]?.message || err?.message || "读取详情失败。";
  } finally {
    detailLoading.value = false;
  }
}

async function selectCompareCase(comparison) {
  selectedCompareCase.value = comparison || null;
  compareCaseError.value = "";
  if (!comparison?.case_id || !currentWorkflowRecord.value?.compare_id) {
    compareArtifacts.value = { baseline: null, candidate: null };
    return;
  }
  compareCaseLoading.value = true;
  try {
    const data = await fetchData(
      `${WORKFLOW_COMPARE_ENDPOINT}/${encodeURIComponent(currentWorkflowRecord.value.compare_id)}/cases/${encodeURIComponent(comparison.case_id)}`,
    );
    compareArtifacts.value = {
      baseline: data?.baseline_artifact || null,
      candidate: data?.candidate_artifact || null,
    };
  } catch (err) {
    // Recoverable: keep the page mounted and surface a scoped error.
    compareArtifacts.value = { baseline: null, candidate: null };
    compareCaseError.value = err?.response?.data?.errors?.[0]?.message || err?.message || "读取差异句证据失败。";
  } finally {
    compareCaseLoading.value = false;
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

function workflowWorkspaceLabel(_recordOrType) {
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
    const parts = [];
    const baselineBits = [];
    const candidateBits = [];
    if (record.baseline_model) baselineBits.push(`B:${record.baseline_model}`);
    if (record.baseline_latency_seconds != null) baselineBits.push(`${Number(record.baseline_latency_seconds).toFixed(1)}s`);
    if (record.baseline_total_tokens != null) {
      baselineBits.push(
        `${record.baseline_total_tokens} tok`
        + `（入 ${record.baseline_input_tokens ?? "—"} / 出 ${record.baseline_output_tokens ?? "—"}）`,
      );
    }
    if (record.candidate_model) candidateBits.push(`C:${record.candidate_model}`);
    if (record.candidate_latency_seconds != null) candidateBits.push(`${Number(record.candidate_latency_seconds).toFixed(1)}s`);
    if (record.candidate_total_tokens != null) {
      candidateBits.push(
        `${record.candidate_total_tokens} tok`
        + `（入 ${record.candidate_input_tokens ?? "—"} / 出 ${record.candidate_output_tokens ?? "—"}）`,
      );
    }
    if (baselineBits.length) parts.push(baselineBits.join(" "));
    if (candidateBits.length) parts.push(candidateBits.join(" "));
    if (parts.length) return parts.join(" · ");
    return record.display_excerpt || record.run_id || "";
  }
  const parts = [];
  if (record.model_name) parts.push(record.model_name);
  if (record.latency_seconds != null) parts.push(`${Number(record.latency_seconds).toFixed(1)}s`);
  if (record.total_tokens != null) {
    parts.push(`${record.total_tokens} tok（入 ${record.input_tokens ?? "—"} / 出 ${record.output_tokens ?? "—"}）`);
  }
  if (parts.length) return parts.join(" · ");
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
  const aggregate = runtime?.aggregate || {};
  const total = runtime?.total_tokens ?? aggregate.total_tokens;
  const input = runtime?.input_tokens ?? aggregate.input_tokens;
  const output = runtime?.output_tokens ?? aggregate.output_tokens;
  if (total == null) return "未记录";
  return `总 ${total} · 入 ${input ?? "—"} / 出 ${output ?? "—"}`;
}

function currentTrialRuntimeMeta(trial, result) {
  const runtimeSummary = result?.run?.runtime_summary || null;
  const aggregate = runtimeSummary?.aggregate || {};
  return {
    model_name: trial?.model_name || result?.run?.model_identity?.model_name || runtimeSummary?.model_name || null,
    model_profile: trial?.model_profile || result?.run?.model_identity?.profile_name || runtimeSummary?.model_profile || null,
    latency_seconds: trial?.latency_seconds != null
      ? trial.latency_seconds
      : runtimeSummary?.latency_ms != null
        ? Number(runtimeSummary.latency_ms) / 1000
        : null,
    total_tokens: trial?.total_tokens != null ? trial.total_tokens : (runtimeSummary?.total_tokens ?? aggregate.total_tokens ?? null),
    input_tokens: trial?.input_tokens != null ? trial.input_tokens : (runtimeSummary?.input_tokens ?? aggregate.input_tokens ?? null),
    output_tokens: trial?.output_tokens != null ? trial.output_tokens : (runtimeSummary?.output_tokens ?? aggregate.output_tokens ?? null),
  };
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

function formatExcerpt(text) {
  if (!text) return "";
  if (text.startsWith("[attached compare") || text.includes("hash=")) {
    return "已关联 Compare 结果 (无明文摘要)";
  }
  return text;
}

function workspaceTypeTone(record) {
  const label = workspaceLabel(record);
  if (label.includes("Workflow")) return "type-workflow";
  if (label === "Compare" || label.includes("Compare")) return "type-compare";
  if (label === "Single Run") return "type-single-run";
  return "type-default";
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
            <span class="record-type" :class="workspaceTypeTone(record)">{{ workspaceLabel(record) }}</span>
            <StatusPill :label="statusLabel(record.status)" :tone="statusTone(record.status)" />
          </span>
          <strong>{{ recordTitle(record) }}</strong>
          <span class="record-excerpt">{{ formatExcerpt(recordExcerpt(record)) }}</span>
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
            <div v-if="renaming" class="rename-row">
              <input v-model="renameValue" placeholder="输入自定义标题" :disabled="renameSaving" @keydown.enter="saveRename" @keydown.escape="cancelRename" />
              <button type="button" :disabled="renameSaving" @click="saveRename">{{ renameSaving ? "保存中..." : "保存" }}</button>
              <button type="button" :disabled="renameSaving" @click="cancelRename">取消</button>
              <span v-if="renameError" class="rename-error">{{ renameError }}</span>
            </div>
            <h2 v-else>{{ currentWorkflowRecord.display_title || currentWorkflowRecord.compare_id }} <button type="button" class="rename-trigger" title="重命名" @click="startRename">✎</button></h2>
            <p>{{ formatExcerpt(currentWorkflowRecord.display_excerpt || currentWorkflowRecord.compare_id) }}</p>
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

        <section class="facts-strip">
          <!-- Baseline card -->
          <div class="facts-card facts-baseline">
            <div class="facts-card-header">
              <span class="facts-role-badge facts-role-baseline">Baseline</span>
              <span class="facts-model-name">{{ currentWorkflowRecord.baseline_model || "—" }}</span>
            </div>
            <p v-if="currentWorkflowRecord.baseline_model_profile" class="facts-model-profile">{{ currentWorkflowRecord.baseline_model_profile }}</p>
            <dl class="facts-metrics">
              <div v-if="currentWorkflowRecord.baseline_latency_seconds != null" class="facts-metric-row">
                <dt>耗时</dt>
                <dd>
                  <span class="facts-metric-value">{{ Number(currentWorkflowRecord.baseline_latency_seconds).toFixed(1) }}s</span>
                  <span v-if="wfLatencyDiffBadge" class="facts-diff-badge" :class="wfLatencyDiffBadge.cls">{{ wfLatencyDiffBadge.text }}</span>
                </dd>
              </div>
              <div v-if="currentWorkflowRecord.baseline_total_tokens != null" class="facts-metric-row">
                <dt>Token 总量</dt>
                <dd>
                  <span class="facts-metric-value">{{ currentWorkflowRecord.baseline_total_tokens }}</span>
                  <span v-if="wfTokensDiffBadge" class="facts-diff-badge" :class="wfTokensDiffBadge.cls">{{ wfTokensDiffBadge.text }}</span>
                </dd>
              </div>
              <div v-if="currentWorkflowRecord.baseline_input_tokens != null" class="facts-metric-row facts-metric-sub">
                <dt>入 / 出</dt>
                <dd>{{ currentWorkflowRecord.baseline_input_tokens ?? "—" }} / {{ currentWorkflowRecord.baseline_output_tokens ?? "—" }}</dd>
              </div>
            </dl>
          </div>
          <!-- Candidate card -->
          <div class="facts-card facts-candidate">
            <div class="facts-card-header">
              <span class="facts-role-badge facts-role-candidate">Candidate</span>
              <span class="facts-model-name">{{ currentWorkflowRecord.candidate_model || "—" }}</span>
            </div>
            <p v-if="currentWorkflowRecord.candidate_model_profile" class="facts-model-profile">{{ currentWorkflowRecord.candidate_model_profile }}</p>
            <dl class="facts-metrics">
              <div v-if="currentWorkflowRecord.candidate_latency_seconds != null" class="facts-metric-row">
                <dt>耗时</dt>
                <dd>
                  <span class="facts-metric-value" :class="wfLatencyDiff != null ? (wfLatencyDiff > 0 ? 'val-worse' : wfLatencyDiff < 0 ? 'val-better' : '') : ''">{{ Number(currentWorkflowRecord.candidate_latency_seconds).toFixed(1) }}s</span>
                </dd>
              </div>
              <div v-if="currentWorkflowRecord.candidate_total_tokens != null" class="facts-metric-row">
                <dt>Token 总量</dt>
                <dd>
                  <span class="facts-metric-value" :class="wfTokensDiff != null ? (wfTokensDiff > 0 ? 'val-worse' : wfTokensDiff < 0 ? 'val-better' : '') : ''">{{ currentWorkflowRecord.candidate_total_tokens }}</span>
                </dd>
              </div>
              <div v-if="currentWorkflowRecord.candidate_input_tokens != null" class="facts-metric-row facts-metric-sub">
                <dt>入 / 出</dt>
                <dd>{{ currentWorkflowRecord.candidate_input_tokens ?? "—" }} / {{ currentWorkflowRecord.candidate_output_tokens ?? "—" }}</dd>
              </div>
            </dl>
          </div>
        </section>

        <section class="result-section">
          <div class="section-heading">
            <h3>Compare 摘要</h3>
            <span>{{ workflowJudgeReports.length }} 条 Judge</span>
          </div>
          <dl class="meta-grid workflow-summary-grid">
            <div>
              <dt>差异句数</dt>
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
            <span>{{ workflowCaseRows.length }} 条差异句</span>
          </div>
          <div v-if="!workflowCaseRows.length" class="empty-inline">这条 compare 还没有可回放的差异句。</div>
          <div v-else-if="compareCaseError" class="notice is-danger" role="alert">
            <span>{{ compareCaseError }}</span>
            <button v-if="selectedCompareCase" type="button" class="notice-retry" @click="selectCompareCase(selectedCompareCase)">重试</button>
          </div>
          <div v-else-if="compareCaseLoading && !compareArtifacts.baseline && !compareArtifacts.candidate" class="empty-inline">正在加载差异句证据…</div>
          <WorkflowCompareReport
            v-else
            :compare-id="currentWorkflowRecord.compare_id"
            :result="{ compare_id: currentWorkflowRecord.compare_id, report: workflowSummary, report_id: currentWorkflowRecord.report_id, created: false }"
            :selected-case-id="selectedCompareCase?.case_id || ''"
            :baseline-artifact="compareArtifacts.baseline"
            :candidate-artifact="compareArtifacts.candidate"
            @select-case="selectCompareCase"
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
            :readonly="true"
            :submitting="false"
            :prepared-sentences="compareArtifacts.baseline?.prepared_sentences || compareArtifacts.candidate?.prepared_sentences || detail?.evidence_index?.prepared_sentences || []"
            :baseline-artifact="compareArtifacts.baseline"
            :candidate-artifact="compareArtifacts.candidate"
            :comparisons="workflowSummary?.comparisons || []"
            @refresh="retrySelectRecord"
          />
        </section>

        <section v-if="currentWorkflowRecord.compare_id" class="result-section">
          <div class="section-heading">
            <h3>Compare 决策面板</h3>
            <span>记录 compare-scope 人工判断</span>
          </div>
          <WorkflowCompareReviewPanel
            :compare-id="currentWorkflowRecord.compare_id"
            :candidate-run-id="currentWorkflowRecord.candidate_run_id"
            :compare-summary="workflowSummary"
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
            <div v-if="renaming" class="rename-row">
              <input v-model="renameValue" placeholder="输入自定义标题" :disabled="renameSaving" @keydown.enter="saveRename" @keydown.escape="cancelRename" />
              <button type="button" :disabled="renameSaving" @click="saveRename">{{ renameSaving ? "保存中..." : "保存" }}</button>
              <button type="button" :disabled="renameSaving" @click="cancelRename">取消</button>
              <span v-if="renameError" class="rename-error">{{ renameError }}</span>
            </div>
            <h2 v-else>{{ currentTrial.custom_title || `${currentTrial.node_name} · ${currentTrial.reading_goal} · ${currentTrial.reading_variant}` }} <button type="button" class="rename-trigger" title="重命名" @click="startRename">✎</button></h2>
            <p>{{ formatExcerpt(currentTrial.display_excerpt || currentTrial.input_excerpt || currentTrial.input_text_hash) }}</p>
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

        <dl v-if="currentTrialMeta.model_name || currentTrialMeta.latency_seconds != null || currentTrialMeta.total_tokens != null" class="meta-grid runtime-meta">
          <!-- Single Run Mode Fields -->
          <template v-if="currentTrial.result_kind !== 'compare_result'">
            <div v-if="currentTrialMeta.model_name">
              <dt>模型</dt>
              <dd>{{ currentTrialMeta.model_name }}</dd>
            </div>
            <div v-if="currentTrialMeta.model_profile">
              <dt>Profile</dt>
              <dd>{{ currentTrialMeta.model_profile }}</dd>
            </div>
            <div v-if="currentTrialMeta.latency_seconds != null">
              <dt>耗时</dt>
              <dd>{{ Number(currentTrialMeta.latency_seconds).toFixed(1) }}s</dd>
            </div>
            <div v-if="currentTrialMeta.total_tokens != null">
              <dt>Tokens</dt>
              <dd>总 {{ currentTrialMeta.total_tokens }} · 入 {{ currentTrialMeta.input_tokens ?? "—" }} / 出 {{ currentTrialMeta.output_tokens ?? "—" }}</dd>
            </div>
          </template>

          <!-- Compare Mode Fields -->
          <template v-else>
            <div v-if="compareModelsSummary">
              <dt>对比模型</dt>
              <dd>{{ compareModelsSummary }}</dd>
            </div>
            <div v-if="currentTrialMeta.latency_seconds != null">
              <dt>最长耗时</dt>
              <dd>{{ Number(currentTrialMeta.latency_seconds).toFixed(1) }}s</dd>
            </div>
          </template>
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
              <div class="compare-side__meta">
                <div class="meta-row model-row">
                  <span class="meta-model-name">
                    {{ side.value.model_identity?.model_name || side.value.runtime_summary?.model_name || "未记录模型" }}
                  </span>
                  <span v-if="side.value.model_identity?.profile_name || side.value.runtime_summary?.model_profile" class="meta-profile-badge">
                    {{ side.value.model_identity?.profile_name || side.value.runtime_summary?.model_profile }}
                  </span>
                </div>
                <div class="meta-row stats-row">
                  <span class="meta-stat-item latency">
                    <span class="meta-icon">⏱️</span>
                    <span class="meta-label">耗时</span>
                    <span class="meta-value">{{ side.value.runtime_summary?.latency_ms != null ? (side.value.runtime_summary.latency_ms / 1000).toFixed(1) + 's' : '—' }}</span>
                  </span>
                  <span class="meta-stat-item tokens">
                    <span class="meta-icon">🪙</span>
                    <span class="meta-label">Token</span>
                    <span class="meta-value">{{ tokenSummary(side.value.runtime_summary) }}</span>
                  </span>
                </div>
              </div>
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
  grid-template-columns: minmax(320px, 0.35fr) minmax(0, 1fr);
  gap: 24px;
  min-height: 720px;
}

.history-list,
.history-detail {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  border-radius: 8px;
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
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

h2,
h3 {
  margin: 0;
  color: var(--theme--foreground);
}

.list-header h2,
.detail-header h2 {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.3;
}

.detail-header p {
  margin: 8px 0 0;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  line-height: 1.55;
}

.detail-actions,
.notice-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.icon-button {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font-size: 14px;
  transition: all 0.2s ease;
}

.icon-button:hover:not(:disabled) {
  border-color: var(--theme--primary-subdued);
  color: var(--theme--primary);
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
  background: var(--theme--background-subdued);
}

.filters label {
  display: grid;
  grid-template-columns: 80px 1fr;
  align-items: center;
  gap: 10px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 600;
}

.filters select {
  width: 100%;
  min-height: 28px;
  padding: 2px 6px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font-size: 12px;
}

.record-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  overflow-y: auto;
  flex: 1;
}

.record-row {
  display: flex;
  flex-direction: column;
  width: 100%;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid var(--theme--border-color);
  border-left: 3px solid transparent;
  border-radius: 6px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  text-align: left;
  cursor: pointer;
  transition: all 0.2s ease;
}

.record-row:hover {
  border-color: var(--theme--primary-subdued);
  background: var(--theme--background-subdued);
}

.record-row.active {
  border-color: var(--theme--primary);
  border-left-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
}

.record-topline,
.record-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.record-type {
  display: inline-flex;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 700;
  border-radius: 4px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  text-transform: uppercase;
}

.record-type.type-workflow {
  background: color-mix(in srgb, var(--theme--primary) 10%, var(--theme--background));
  border-color: color-mix(in srgb, var(--theme--primary) 35%, var(--theme--border-color));
  color: var(--theme--primary);
}

.record-type.type-compare {
  background: color-mix(in srgb, #54a7de 10%, var(--theme--background));
  border-color: color-mix(in srgb, #54a7de 35%, var(--theme--border-color));
  color: #285f8d;
}

.record-type.type-single-run {
  background: var(--theme--background-subdued);
  border-color: var(--theme--border-color);
  color: var(--theme--foreground-subdued);
}

.record-row strong {
  font-size: 13px;
  font-weight: 600;
  color: var(--theme--foreground);
}

.record-excerpt {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-meta {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.history-detail {
  padding: 24px;
  overflow-y: auto;
}

.meta-grid,
.judge-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.meta-grid div,
.judge-meta div {
  min-width: 0;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
}

dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

dd {
  margin: 4px 0 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--theme--foreground);
  overflow-wrap: anywhere;
}

.session-band {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 16px 0;
  padding: 12px 14px;
  border-radius: 6px;
  border: 1px solid var(--theme--border-color);
  border-left: 3px solid var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
}

.session-band div {
  display: grid;
  gap: 4px;
}

.session-band strong {
  font-size: 13px;
  font-weight: 600;
  color: var(--theme--foreground);
}

.session-band span {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.session-id {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.result-section {
  margin: 24px 0;
}

.section-heading {
  margin-bottom: 12px;
  border-bottom: 1px solid var(--theme--border-color);
  padding-bottom: 6px;
}

.section-heading h3 {
  font-size: 14px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.section-heading span {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
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
  border-radius: 6px;
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
  border-radius: 6px;
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
  padding: 14px;
  border-radius: 6px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
}

.compare-side__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.compare-side__meta {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.compare-side__meta .meta-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.compare-side__meta .model-row {
  font-weight: 600;
  color: var(--theme--foreground);
}

.compare-side__meta .meta-model-name {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.compare-side__meta .meta-profile-badge {
  font-size: 10px;
  font-weight: normal;
  padding: 1px 4px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--theme--primary) 8%, var(--theme--background));
  color: var(--theme--primary);
  border: 1px solid color-mix(in srgb, var(--theme--primary) 20%, var(--theme--border-color));
}

.compare-side__meta .stats-row {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.compare-side__meta .meta-stat-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.compare-side__meta .meta-icon {
  font-size: 12px;
  opacity: 0.8;
}

.compare-side__meta .meta-label {
  font-weight: bold;
  text-transform: uppercase;
  font-size: 9px;
  letter-spacing: 0.05em;
  opacity: 0.7;
}

.compare-side__meta .meta-value {
  color: var(--theme--foreground);
  font-weight: 500;
}

.judge-list {
  display: grid;
  gap: 10px;
}

.empty-state,
.empty-inline,
.notice {
  padding: 16px;
  border-radius: 6px;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

.empty-inline {
  border: 1px dashed var(--theme--border-color);
  background: var(--theme--background-subdued);
  text-align: center;
}

.notice.is-danger {
  color: var(--theme--danger);
  border: 1px solid color-mix(in srgb, var(--theme--danger) 25%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 6%, var(--theme--background));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.notice.is-warning {
  margin-top: 14px;
  border: 1px solid color-mix(in srgb, #d97706 35%, var(--theme--border-color));
  color: #b45309;
  background: color-mix(in srgb, #d97706 8%, var(--theme--background));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.notice-retry {
  border: 1px solid color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--danger);
  font-size: 11px;
  font-weight: 700;
  padding: 4px 12px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.notice-retry:hover {
  background: color-mix(in srgb, var(--theme--danger) 12%, var(--theme--background));
}

.danger-button {
  border: 1px solid color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--danger);
  font-size: 11px;
  font-weight: 700;
  padding: 6px 12px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.danger-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.danger-button:hover:not(:disabled) {
  background: color-mix(in srgb, var(--theme--danger) 10%, var(--theme--background));
}

.rename-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0;
}

.rename-row input {
  flex: 1;
  min-height: 32px;
  border: 1px solid var(--theme--primary);
  border-radius: 4px;
  padding: 4px 10px;
  font: inherit;
  font-size: 15px;
  background: var(--theme--background);
  color: var(--theme--foreground);
}

.rename-row button {
  min-height: 32px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.rename-row button:first-of-type {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.rename-error {
  color: var(--theme--danger);
  font-size: 11px;
}

.rename-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  cursor: pointer;
  vertical-align: middle;
  margin-left: 6px;
  opacity: 0.7;
  transition: opacity 0.15s;
}

.rename-trigger:hover {
  color: var(--theme--primary);
  border-color: var(--theme--primary);
  opacity: 1;
}

/* ── Workflow Compare facts panel ─────────────────────────── */
.facts-strip {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.facts-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued, var(--theme--background));
}

.facts-candidate {
  border-color: color-mix(in srgb, var(--theme--primary) 28%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
}

.facts-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.facts-role-badge {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.facts-role-baseline {
  background: color-mix(in srgb, var(--theme--foreground-subdued) 12%, transparent);
  color: var(--theme--foreground-subdued);
}

.facts-role-candidate {
  background: color-mix(in srgb, var(--theme--primary) 14%, transparent);
  color: var(--theme--primary);
}

.facts-model-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--theme--foreground);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.facts-model-profile {
  margin: -4px 0 0;
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.facts-metrics {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin: 0;
}

.facts-metric-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.facts-metric-row dt {
  flex-shrink: 0;
  width: 64px;
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.facts-metric-row dd {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 0;
}

.facts-metric-value {
  font-size: 13px;
  font-weight: 700;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  color: var(--theme--foreground);
}

.facts-metric-value.val-better {
  color: var(--theme--success);
}

.facts-metric-value.val-worse {
  color: var(--theme--danger);
}

.facts-metric-sub .facts-metric-value,
.facts-metric-sub dt,
.facts-metric-sub dd {
  font-size: 11px;
  font-weight: 400;
  color: var(--theme--foreground-subdued);
}

.facts-diff-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  white-space: nowrap;
  border: 1px solid transparent;
}

.facts-diff-badge.diff-better {
  color: var(--theme--success);
  background: color-mix(in srgb, var(--theme--success) 12%, transparent);
  border-color: color-mix(in srgb, var(--theme--success) 30%, transparent);
}

.facts-diff-badge.diff-worse {
  color: var(--theme--danger);
  background: color-mix(in srgb, var(--theme--danger) 12%, transparent);
  border-color: color-mix(in srgb, var(--theme--danger) 30%, transparent);
}

.facts-diff-badge.diff-neutral {
  color: var(--theme--foreground-subdued);
  background: color-mix(in srgb, var(--theme--foreground-subdued) 10%, transparent);
  border-color: var(--theme--border-color);
}

.runtime-meta {
  margin-top: 0;
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
