<script setup>
import { computed, onMounted, ref, watch } from "vue";
import CandidatePanel from "./components/CandidatePanel.vue";
import CaseEvidenceInspector from "./components/CaseEvidenceInspector.vue";
import WorkflowCompareBuilder from "./components/WorkflowCompareBuilder.vue";
import WorkflowCompareReport from "./components/WorkflowCompareReport.vue";
import WorkflowJudgePanel from "./components/WorkflowJudgePanel.vue";
import WorkflowRunDetail from "./components/WorkflowRunDetail.vue";
import WorkflowRunLauncher from "./components/WorkflowRunLauncher.vue";
import WorkflowRunQueue from "./components/WorkflowRunQueue.vue";
import WorkflowSingleRunLauncher from "./components/WorkflowSingleRunLauncher.vue";
import WorkflowSingleRunResult from "./components/WorkflowSingleRunResult.vue";
import { useWorkflowLabApi } from "./composables/useWorkflowLabApi.js";
import { dash, formatRunIdentity } from "./composables/workflowLabFormatting.js";

const props = defineProps({
  initialBaselineRunId: { type: String, default: "" },
  initialCandidateRunId: { type: String, default: "" },
});
const emit = defineEmits(["open-run-history"]);

const workflowApi = useWorkflowLabApi();
const STORAGE_KEY = "claread-eval-center:workflow-lab:v2";
const WORKFLOW_PROMPT_BUNDLE_SCHEMA = "workflow-prompt-bundle-v1";
const WORKFLOW_AGENT_ORDER = ["vocabulary", "grammar", "translation", "repair"];
const WORKFLOW_AGENT_LABELS = {
  vocabulary: "词汇",
  grammar: "语法",
  translation: "翻译",
  repair: "修复",
};

const WORKSPACES = [
  { id: "single_run", label: "单篇验证", desc: "先跑一篇文章，确认当前版本是否值得继续批量回归。" },
  { id: "dataset_runs", label: "批量回归", desc: "把版本加入队列，查看回归进度、结果和 case 证据。" },
  { id: "compare_judge", label: "对比与评审", desc: "比较 baseline 与候选版本，并查看双侧证据和 judge 请求。" },
  { id: "candidate", label: "候选版本", desc: "从 baseline 派生一个候选版本，编辑后发布到运行入口。" },
];

const activeWorkspace = ref("single_run");
const activeCompareTab = ref("compare");
const error = ref("");
const message = ref("");

const runs = ref([]);
const runsLoading = ref(false);
const selectedRunId = ref("");
const selectedRunDetail = ref(null);
const runDetailLoading = ref(false);
const requests = ref([]);
const requestsLoading = ref(false);
const runSubmitting = ref(false);

const singleRunSubmitting = ref(false);
const singleRunResult = ref(null);
const modelProfiles = ref([]);

const selectedCaseId = ref("");
const selectedCaseArtifact = ref(null);
const caseLoading = ref(false);

const baselineRunId = ref("");
const candidateRunId = ref("");
const compareLoading = ref(false);
const compareResult = ref(null);
const selectedCompareCase = ref(null);
const compareArtifacts = ref({ baseline: null, candidate: null });
const compareCaseLoading = ref(false);

const readyCandidates = ref([]);
const candidateDrafts = ref([]);
const candidateLoading = ref(false);
const candidateSaving = ref(false);
const candidatePreviewing = ref(false);
const candidateError = ref("");
const candidateMessage = ref("");
const selectedDraftId = ref("");
const candidatePreview = ref(null);
const candidateForm = ref(emptyCandidateForm());
const candidateBaselineJson = ref(JSON.stringify(emptyCandidateForm()));

const rubrics = ref([]);
const judgeRequests = ref([]);
const judgeSubmitting = ref(false);

const activeWorkspaceMeta = computed(() => WORKSPACES.find((item) => item.id === activeWorkspace.value) || WORKSPACES[0]);
const datasetRuns = computed(() => runs.value.filter((run) => (run.learning_case_count || 0) > 0));
const compareRuns = computed(() => runs.value.filter((run) => (run.learning_case_count || 0) > 0 && run.has_report));
const publishedCandidateCount = computed(() => readyCandidates.value.length);
const draftCandidateCount = computed(() => candidateDrafts.value.filter((item) => item.status !== "ready_for_eval").length);
const currentCompareJudgeRunId = computed(() => compareResult.value?.report?.candidate_run_id || candidateRunId.value || "");
const candidateDirty = computed(() => JSON.stringify(candidateForm.value) !== candidateBaselineJson.value);

watch(
  () => [props.initialBaselineRunId, props.initialCandidateRunId],
  ([baseline, candidate]) => {
    if (baseline) baselineRunId.value = baseline;
    if (candidate) candidateRunId.value = candidate;
    if (baseline || candidate) activeWorkspace.value = "compare_judge";
  },
  { immediate: true },
);

watch(activeWorkspace, (workspace) => {
  window.sessionStorage.setItem(STORAGE_KEY, workspace);
});

watch(currentCompareJudgeRunId, async (runId) => {
  if (activeWorkspace.value === "compare_judge" && activeCompareTab.value === "judge" && runId) {
    await loadJudgeRequests(runId);
  }
});

onMounted(async () => {
  const persistedWorkspace = window.sessionStorage.getItem(STORAGE_KEY);
  if (persistedWorkspace && WORKSPACES.some((item) => item.id === persistedWorkspace)) {
    activeWorkspace.value = persistedWorkspace;
  }
  await Promise.all([
    loadRuns(),
    loadRequests(),
    loadCandidates({ syncSelection: true }),
    loadRubrics(),
    loadModelProfiles(),
  ]);
  if (currentCompareJudgeRunId.value && activeWorkspace.value === "compare_judge" && activeCompareTab.value === "judge") {
    await loadJudgeRequests(currentCompareJudgeRunId.value);
  } else {
    await loadJudgeRequests();
  }
});

function emptyCandidateForm() {
  return {
    variant_id: "",
    status: "draft",
    scope: "workflow_eval",
    few_shot_mode: "baseline",
    notes: "",
    reading_goal: "daily_reading",
    reading_variant: "intermediate_reading",
    topology_mode: "learning",
    prompt_version: null,
    prompt_profile: null,
    agents: emptyAgentMap(),
    baseline_agents: emptyAgentMap(),
  };
}

function emptyAgentMap() {
  return Object.fromEntries(WORKFLOW_AGENT_ORDER.map((agentName) => [
    agentName,
    normalizeAgentLayer(agentName),
  ]));
}

function normalizeAgentLayer(agentName, layer = {}) {
  const value = layer && typeof layer === "object" && !Array.isArray(layer) ? layer : {};
  return {
    agent_name: value.agent_name || agentName,
    label: value.label || WORKFLOW_AGENT_LABELS[agentName] || agentName,
    instructions: String(value.instructions || ""),
    policy_name: value.policy_name || (agentName === "repair" ? null : agentName),
    policy_focus: value.policy_focus || null,
    policy_variant: value.policy_variant || null,
    policy_lines: Array.isArray(value.policy_lines) ? value.policy_lines.map((line) => String(line || "")) : [],
    examples: Array.isArray(value.examples)
      ? value.examples.filter((entry) => entry && typeof entry === "object" && !Array.isArray(entry)).map((entry) => ({
        example_type: entry.example_type || "grammar",
        sentence_text: entry.sentence_text || "",
        output_fragment: entry.output_fragment || "",
      }))
      : [],
    prompt_template: String(value.prompt_template || ""),
  };
}

function normalizeAgentMap(agents) {
  const raw = agents && typeof agents === "object" && !Array.isArray(agents) ? agents : {};
  return Object.fromEntries(WORKFLOW_AGENT_ORDER.map((agentName) => [
    agentName,
    normalizeAgentLayer(agentName, raw[agentName]),
  ]));
}

function candidateFormFromBundle(bundle, current = {}) {
  const agents = normalizeAgentMap(bundle?.agents);
  return {
    ...emptyCandidateForm(),
    variant_id: current.variant_id || "",
    status: current.status || "draft",
    scope: "workflow_eval",
    few_shot_mode: bundle?.few_shot_mode || current.few_shot_mode || "baseline",
    notes: current.notes || "",
    reading_goal: bundle?.reading_goal || current.reading_goal || "daily_reading",
    reading_variant: bundle?.reading_variant || current.reading_variant || "intermediate_reading",
    topology_mode: bundle?.topology_mode || "learning",
    prompt_version: bundle?.prompt_version || null,
    prompt_profile: bundle?.prompt_profile || null,
    agents,
    baseline_agents: normalizeAgentMap(bundle?.baseline_agents || bundle?.agents),
  };
}

function candidateFormFromDraft(draft) {
  const manifest = draft?.manifest_json && typeof draft.manifest_json === "object" ? draft.manifest_json : {};
  if (manifest.schema_version === WORKFLOW_PROMPT_BUNDLE_SCHEMA) {
    return {
      ...emptyCandidateForm(),
      variant_id: draft.variant_id || manifest.variant_id || "",
      status: draft.status || "draft",
      scope: draft.scope || "workflow_eval",
      few_shot_mode: manifest.few_shot_mode || draft.few_shot_mode || "baseline",
      notes: draft.notes || manifest.description || "",
      reading_goal: manifest.reading_goal || "daily_reading",
      reading_variant: manifest.reading_variant || "intermediate_reading",
      topology_mode: manifest.topology_mode || "learning",
      prompt_version: manifest.prompt_version || null,
      prompt_profile: manifest.prompt_profile || null,
      agents: normalizeAgentMap(manifest.agents),
      baseline_agents: normalizeAgentMap(manifest.baseline_agents || manifest.agents),
    };
  }
  return {
    ...emptyCandidateForm(),
    variant_id: draft.variant_id || "",
    status: draft.status || "draft",
    scope: draft.scope || "workflow_eval",
    few_shot_mode: draft.few_shot_mode || "baseline",
    notes: draft.notes || "",
  };
}

function workflowBundleManifest(form) {
  return {
    schema_version: WORKFLOW_PROMPT_BUNDLE_SCHEMA,
    variant_id: form.variant_id.trim(),
    target: "article_analysis",
    description: form.notes || "",
    reading_goal: form.reading_goal || "daily_reading",
    reading_variant: form.reading_variant || "intermediate_reading",
    prompt_version: form.prompt_version || null,
    prompt_profile: form.prompt_profile || null,
    topology_mode: "learning",
    few_shot_mode: form.few_shot_mode || "baseline",
    agents: normalizeAgentMap(form.agents),
    baseline_agents: normalizeAgentMap(form.baseline_agents),
  };
}

function policiesFromAgents(agents) {
  const policies = {};
  for (const layer of Object.values(normalizeAgentMap(agents))) {
    if (!layer.policy_name || !layer.policy_focus) continue;
    if (!policies[layer.policy_name]) policies[layer.policy_name] = {};
    policies[layer.policy_name][layer.policy_focus] = {
      [layer.policy_variant || "default"]: layer.policy_lines.filter((line) => line.trim()),
      default: layer.policy_lines.filter((line) => line.trim()),
    };
  }
  return policies;
}

function examplesFromAgents(agents, readingVariant) {
  const examples = {};
  for (const layer of Object.values(normalizeAgentMap(agents))) {
    const cleanExamples = layer.examples.filter((entry) => entry.sentence_text && entry.output_fragment);
    if (cleanExamples.length === 0) continue;
    examples[layer.agent_name] = {
      [layer.policy_variant || readingVariant || "default"]: cleanExamples,
      default: cleanExamples,
    };
  }
  return examples;
}

function setError(err, fallback) {
  error.value = workflowApi.directusError(err, fallback);
}

async function loadRuns(options = {}) {
  runsLoading.value = true;
  error.value = "";
  try {
    runs.value = await workflowApi.listRuns(100);
    if (!options.keepSelection && !selectedRunId.value) {
      const firstRun = datasetRuns.value[0];
      if (firstRun) await selectRun(firstRun.run_id, { silentWorkspace: true });
    }
  } catch (err) {
    setError(err, "Failed to load workflow runs.");
  } finally {
    runsLoading.value = false;
  }
}

async function loadRequests() {
  requestsLoading.value = true;
  try {
    requests.value = await workflowApi.listRunRequests("all");
  } catch (err) {
    setError(err, "Failed to load workflow run requests.");
  } finally {
    requestsLoading.value = false;
  }
}

async function selectRun(runId, options = {}) {
  if (!runId) return;
  selectedRunId.value = runId;
  if (!options.silentWorkspace) activeWorkspace.value = "dataset_runs";
  runDetailLoading.value = true;
  selectedCaseId.value = "";
  selectedCaseArtifact.value = null;
  try {
    selectedRunDetail.value = await workflowApi.getRunDetail(runId);
    const firstLearningCase = (selectedRunDetail.value?.case_artifacts || []).find(
      (item) => item?.workflow_identity?.topology_mode === "learning" || item?.schema_identity?.topology_mode === "learning",
    );
    if (firstLearningCase) {
      await selectCase(firstLearningCase.case_id, runId);
    }
    await loadJudgeRequests(runId);
  } catch (err) {
    selectedRunDetail.value = null;
    setError(err, "Failed to load run detail.");
  } finally {
    runDetailLoading.value = false;
  }
}

async function selectCase(caseId, runId = selectedRunId.value) {
  if (!caseId || !runId) return;
  selectedCaseId.value = caseId;
  caseLoading.value = true;
  try {
    selectedCaseArtifact.value = await workflowApi.getCaseArtifact(runId, caseId);
  } catch (err) {
    selectedCaseArtifact.value = null;
    setError(err, "Failed to load case artifact.");
  } finally {
    caseLoading.value = false;
  }
}

async function submitRun(payload) {
  runSubmitting.value = true;
  error.value = "";
  message.value = "";
  try {
    const created = await workflowApi.createRunRequest(payload);
    message.value = `已加入运行队列：${created?.run_id || ""}`.trim();
    activeWorkspace.value = "dataset_runs";
    await Promise.all([loadRequests(), loadRuns({ keepSelection: true })]);
  } catch (err) {
    setError(err, "Failed to queue workflow run.");
  } finally {
    runSubmitting.value = false;
  }
}

async function submitSingleRun(payload) {
  singleRunSubmitting.value = true;
  singleRunResult.value = null;
  error.value = "";
  message.value = "";
  try {
    singleRunResult.value = await workflowApi.runSingleWorkflow(payload);
    activeWorkspace.value = "single_run";
    message.value = singleRunResult.value?.prompt_identity?.prompt_variant_id
      ? `Single Run 完成：${singleRunResult.value.prompt_identity.prompt_variant_id}`
      : "Baseline Single Run 完成。";
  } catch (err) {
    setError(err, "Failed to run workflow single run.");
  } finally {
    singleRunSubmitting.value = false;
  }
}

async function cancelRequest(row) {
  if (!row?.id) return;
  const ok = window.confirm(`Cancel workflow request ${row.run_id}?`);
  if (!ok) return;
  try {
    await workflowApi.cancelRunRequest(row.id);
    await loadRequests();
  } catch (err) {
    setError(err, "Failed to cancel workflow request.");
  }
}

async function retryRequest(row) {
  if (!row?.id) return;
  try {
    await workflowApi.retryRunRequest(row.id, { retry_reason: "workflow_lab_retry" });
    await loadRequests();
  } catch (err) {
    setError(err, "Failed to retry workflow request.");
  }
}

async function createCompare() {
  compareLoading.value = true;
  compareArtifacts.value = { baseline: null, candidate: null };
  error.value = "";
  message.value = "";
  try {
    compareResult.value = await workflowApi.createCompare({
      baseline_run_id: baselineRunId.value,
      candidate_run_id: candidateRunId.value,
    });
    message.value = compareResult.value.created ? "对比报告已生成。" : "已读取已有对比报告。";
    activeWorkspace.value = "compare_judge";
    activeCompareTab.value = "compare";
    const firstComparison = compareResult.value.report?.comparisons?.[0];
    if (firstComparison) await selectCompareCase(firstComparison);
    if (compareResult.value?.report?.candidate_run_id) {
      await loadJudgeRequests(compareResult.value.report.candidate_run_id);
    }
    await loadRuns({ keepSelection: true });
  } catch (err) {
    setError(err, "Failed to generate compare report.");
  } finally {
    compareLoading.value = false;
  }
}

async function selectCompareCase(comparison) {
  selectedCompareCase.value = comparison;
  compareCaseLoading.value = true;
  compareArtifacts.value = { baseline: null, candidate: null };
  try {
    const report = compareResult.value?.report;
    const [baselineArtifact, candidateArtifact] = await Promise.all([
      comparison?.case_id && report?.baseline_run_id
        ? workflowApi.getCaseArtifact(report.baseline_run_id, comparison.case_id)
        : Promise.resolve(null),
      comparison?.case_id && report?.candidate_run_id
        ? workflowApi.getCaseArtifact(report.candidate_run_id, comparison.case_id)
        : Promise.resolve(null),
    ]);
    compareArtifacts.value = {
      baseline: baselineArtifact,
      candidate: candidateArtifact,
    };
  } catch (err) {
    compareArtifacts.value = { baseline: null, candidate: null };
    setError(err, "Failed to load compare evidence.");
  } finally {
    compareCaseLoading.value = false;
  }
}

async function loadCandidates(options = {}) {
  candidateLoading.value = true;
  candidateError.value = "";
  try {
    const [ready, drafts] = await Promise.all([
      workflowApi.listReadyCandidates(),
      workflowApi.listCandidateDrafts(),
    ]);
    readyCandidates.value = ready;
    candidateDrafts.value = drafts;
    if (options.syncSelection !== false) {
      if (selectedDraftId.value) {
        const refreshed = drafts.find((draft) => draft.id === selectedDraftId.value);
        if (refreshed) {
          applyDraftSelection(refreshed, { silentWorkspace: true });
        } else if (drafts.length) {
          applyDraftSelection(drafts[0], { silentWorkspace: true });
        } else {
          selectedDraftId.value = "";
        }
      } else if (drafts.length) {
        applyDraftSelection(drafts[0], { silentWorkspace: true });
      }
    }
  } catch (err) {
    candidateError.value = workflowApi.directusError(err, "Failed to load candidates.");
  } finally {
    candidateLoading.value = false;
  }
}

function confirmDiscardCandidateChanges() {
  if (!candidateDirty.value) return true;
  return window.confirm("当前 Candidate 有未保存修改。继续操作会丢失这些修改，是否继续？");
}

async function refreshCandidates() {
  const syncSelection = !candidateDirty.value;
  await loadCandidates({ syncSelection });
  if (!syncSelection) {
    candidateMessage.value = "列表已刷新，当前未保存编辑已保留。";
  }
}

function newCandidate() {
  if (!confirmDiscardCandidateChanges()) return;
  selectedDraftId.value = "";
  candidatePreview.value = null;
  candidateMessage.value = "";
  candidateError.value = "";
  candidateForm.value = emptyCandidateForm();
  candidateBaselineJson.value = JSON.stringify(candidateForm.value);
  activeWorkspace.value = "candidate";
}

function applyDraftSelection(draft, options = {}) {
  selectedDraftId.value = draft.id || "";
  candidatePreview.value = null;
  candidateMessage.value = "";
  candidateError.value = "";
  candidateForm.value = candidateFormFromDraft(draft);
  candidateBaselineJson.value = JSON.stringify(candidateForm.value);
  if (!options.silentWorkspace) activeWorkspace.value = "candidate";
}

function selectDraft(draft) {
  if (!confirmDiscardCandidateChanges()) return;
  applyDraftSelection(draft);
}

function candidatePayload(extra = {}) {
  const manifest = workflowBundleManifest(candidateForm.value);
  return {
    variant_id: manifest.variant_id,
    target: "article_analysis",
    status: candidateForm.value.status,
    scope: "workflow_eval",
    few_shot_mode: candidateForm.value.few_shot_mode,
    notes: candidateForm.value.notes,
    policies_json: policiesFromAgents(manifest.agents),
    examples_json: examplesFromAgents(manifest.agents, manifest.reading_variant),
    manifest_json: manifest,
    ...extra,
  };
}

async function createCandidateFromBaseline() {
  candidateLoading.value = true;
  candidateError.value = "";
  candidateMessage.value = "";
  candidatePreview.value = null;
  selectedDraftId.value = "";
  try {
    const bundle = await workflowApi.loadBaselineBundle({
      reading_goal: candidateForm.value.reading_goal || "daily_reading",
      reading_variant: candidateForm.value.reading_variant || "intermediate_reading",
      few_shot_mode: candidateForm.value.few_shot_mode || "baseline",
    });
    candidateForm.value = candidateFormFromBundle(bundle, candidateForm.value);
    candidateMessage.value = "已从 baseline prompt 创建完整 Workflow Candidate 草稿。";
    activeWorkspace.value = "candidate";
  } catch (err) {
    candidateError.value = workflowApi.directusError(err, "Failed to load baseline prompt bundle.");
  } finally {
    candidateLoading.value = false;
  }
}

async function previewCandidate() {
  candidatePreviewing.value = true;
  candidateError.value = "";
  candidateMessage.value = "";
  try {
    candidatePreview.value = await workflowApi.previewCandidate(candidatePayload());
    return candidatePreview.value;
  } catch (err) {
    candidateError.value = workflowApi.directusError(err, "Failed to preview candidate.");
    return null;
  } finally {
    candidatePreviewing.value = false;
  }
}

async function persistCandidate(targetStatus, successMessage) {
  candidateSaving.value = true;
  candidateError.value = "";
  candidateMessage.value = "";
  try {
    const preview = await previewCandidate();
    if (!preview) return;
    const payload = candidatePayload({
      status: targetStatus,
      manifest_json: preview.manifest_json,
      snapshot_hash: preview.snapshot_hash,
    });
    const saved = await workflowApi.saveCandidateDraft(payload, selectedDraftId.value);
    if (saved?.id) selectedDraftId.value = saved.id;
    candidateForm.value = { ...candidateForm.value, status: targetStatus };
    candidateMessage.value = successMessage;
    await loadCandidates({ syncSelection: true });
  } catch (err) {
    candidateError.value = workflowApi.directusError(err, "Failed to save candidate.");
  } finally {
    candidateSaving.value = false;
  }
}

async function saveCandidateDraft() {
  await persistCandidate("draft", "Candidate 草稿已保存。");
}

async function publishCandidate() {
  await persistCandidate("ready_for_eval", "Candidate 已发布，可在 Single Run / Dataset Run 中选择。");
}

async function unpublishCandidate() {
  await persistCandidate("draft", "Candidate 已撤回发布，运行入口已隐藏该版本。");
}

async function loadRubrics() {
  try {
    rubrics.value = await workflowApi.listRubrics();
  } catch (err) {
    setError(err, "Failed to load judge rubrics.");
  }
}

async function loadModelProfiles() {
  try {
    modelProfiles.value = await workflowApi.listModelProfiles();
  } catch (err) {
    setError(err, "Failed to load model profiles.");
  }
}

async function loadJudgeRequests(runId = "") {
  try {
    judgeRequests.value = await workflowApi.listJudgeRequests(runId ? { run_id: runId } : {});
  } catch (err) {
    setError(err, "Failed to load judge requests.");
  }
}

async function queueJudge(payload) {
  judgeSubmitting.value = true;
  error.value = "";
  try {
    await workflowApi.createJudgeRequest(payload);
    await loadJudgeRequests(payload.run_id);
  } catch (err) {
    setError(err, "Failed to queue judge request.");
  } finally {
    judgeSubmitting.value = false;
  }
}

const contextFacts = computed(() => {
  if (activeWorkspace.value === "candidate") {
    return [
      { label: "已发布", value: String(publishedCandidateCount.value) },
      { label: "草稿", value: String(draftCandidateCount.value) },
      { label: "当前状态", value: candidateForm.value.status === "ready_for_eval" ? "已发布" : "草稿" },
    ];
  }
  if (activeWorkspace.value === "dataset_runs") {
      return [
        { label: "队列", value: String(requests.value.length) },
        { label: "已完成", value: String(datasetRuns.value.length) },
        { label: "当前 Run", value: dash(selectedRunId.value, "未选择") },
      ];
    }
    if (activeWorkspace.value === "compare_judge") {
      return [
        { label: "Baseline", value: dash(baselineRunId.value, "未选择") },
        { label: "候选版本", value: dash(candidateRunId.value, "未选择") },
        { label: "当前 Case", value: dash(selectedCompareCase.value?.case_id, "未选择") },
      ];
    }
  return [
    { label: "已发布候选版本", value: String(publishedCandidateCount.value) },
    { label: "模型方案", value: String(modelProfiles.value.length) },
    { label: "调试结果", value: singleRunResult.value ? "已生成" : "未运行" },
  ];
});

function runCaption(run) {
  return `${formatRunIdentity(run)} · ${dash(run.created_at, "unknown time")}`;
}
</script>

<template>
  <section class="workflow-lab">
    <header class="context-bar">
      <div>
        <p>Learning Workflow Lab</p>
        <h1>{{ activeWorkspaceMeta.label }}</h1>
        <p class="workspace-desc">{{ activeWorkspaceMeta.desc }}</p>
      </div>
      <dl class="context-grid">
        <div v-for="fact in contextFacts" :key="fact.label">
          <dt>{{ fact.label }}</dt>
          <dd>{{ fact.value }}</dd>
        </div>
      </dl>
    </header>

    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-if="message" class="notice success">{{ message }}</p>

    <nav class="workspace-nav" aria-label="Workflow Lab workspaces">
      <button
        v-for="workspace in WORKSPACES"
        :key="workspace.id"
        type="button"
        :class="{ active: activeWorkspace === workspace.id }"
        @click="activeWorkspace = workspace.id"
      >
        <strong>{{ workspace.label }}</strong>
        <small>{{ workspace.desc }}</small>
      </button>
    </nav>

    <CandidatePanel
      v-if="activeWorkspace === 'candidate'"
      v-model:form="candidateForm"
      :drafts="candidateDrafts"
      :ready-candidates="readyCandidates"
      :selected-id="selectedDraftId"
      :preview="candidatePreview"
      :loading="candidateLoading"
      :saving="candidateSaving"
      :previewing="candidatePreviewing"
      :error="candidateError"
      :message="candidateMessage"
      @refresh="refreshCandidates"
      @new="newCandidate"
      @create-from-baseline="createCandidateFromBaseline"
      @select="selectDraft"
      @preview="previewCandidate"
      @save-draft="saveCandidateDraft"
      @publish="publishCandidate"
      @unpublish="unpublishCandidate"
    />

    <div v-else-if="activeWorkspace === 'single_run'" class="workspace-layout single-run-layout">
      <div class="main-stack">
        <WorkflowSingleRunLauncher
          :candidates="readyCandidates"
          :model-profiles="modelProfiles"
          :submitting="singleRunSubmitting"
          @submit="submitSingleRun"
        />
      </div>
      <div class="side-stack sticky-pane">
        <WorkflowSingleRunResult
          :result="singleRunResult"
          :loading="singleRunSubmitting"
        />
      </div>
    </div>

    <div v-else-if="activeWorkspace === 'dataset_runs'" class="workspace-layout dataset-layout">
      <aside class="side-stack">
        <WorkflowRunQueue
          :requests="requests"
          :loading="requestsLoading"
          :selected-run-id="selectedRunId"
          @refresh="loadRequests"
          @select-run="selectRun"
          @cancel="cancelRequest"
          @retry="retryRequest"
        />

        <section class="run-list">
          <header>
            <strong>已完成 runs</strong>
            <button type="button" :disabled="runsLoading" @click="loadRuns({ keepSelection: true })">
              {{ runsLoading ? "刷新中" : "刷新" }}
            </button>
          </header>
          <button
            v-for="run in datasetRuns"
            :key="run.run_id"
            type="button"
            :class="{ active: run.run_id === selectedRunId }"
            @click="selectRun(run.run_id)"
          >
            <span>{{ run.run_id }}</span>
            <small>{{ runCaption(run) }}</small>
          </button>
          <p v-if="!runsLoading && datasetRuns.length === 0">暂无可展示的 learning run。</p>
        </section>
      </aside>

      <main class="main-stack">
        <details class="launcher-shell">
          <summary>创建新的批量回归任务</summary>
          <WorkflowRunLauncher
            :candidates="readyCandidates"
            :submitting="runSubmitting"
            @submit="submitRun"
          />
        </details>
        <WorkflowRunDetail
          :detail="selectedRunDetail"
          :loading="runDetailLoading"
          :selected-case-id="selectedCaseId"
          :rubrics="rubrics"
          :judge-requests="judgeRequests"
          :judge-submitting="judgeSubmitting"
          @select-case="selectCase"
          @queue-judge="queueJudge"
          @refresh-judge="loadJudgeRequests(selectedRunId)"
        />
        <CaseEvidenceInspector
          :artifact="selectedCaseArtifact"
          :loading="caseLoading"
        />
      </main>
    </div>

    <div v-else class="workspace-layout compare-layout">
      <main class="main-stack">
        <WorkflowCompareBuilder
          v-model:baseline-run-id="baselineRunId"
          v-model:candidate-run-id="candidateRunId"
          :runs="compareRuns"
          :loading="compareLoading"
          @compare="createCompare"
          @select-run="selectRun"
        />

        <section class="compare-tabs">
          <button type="button" :class="{ active: activeCompareTab === 'compare' }" @click="activeCompareTab = 'compare'">差异报告</button>
          <button type="button" :class="{ active: activeCompareTab === 'judge' }" @click="activeCompareTab = 'judge'; if (currentCompareJudgeRunId) loadJudgeRequests(currentCompareJudgeRunId);">Judge 请求</button>
        </section>

        <WorkflowCompareReport
          v-if="activeCompareTab === 'compare'"
          :result="compareResult"
          :selected-case-id="selectedCompareCase?.case_id || ''"
          @select-case="selectCompareCase"
        />

        <section v-else class="judge-workspace">
          <header class="judge-workspace-header">
            <div>
              <p>Judge 请求</p>
              <h2>{{ currentCompareJudgeRunId || "请先生成一条差异报告" }}</h2>
            </div>
            <span>Judge 仍按候选 run 维度发起，这里只增强可读性，不改后端模型。</span>
          </header>

          <WorkflowJudgePanel
            :run-id="currentCompareJudgeRunId"
            :rubrics="rubrics"
            :requests="judgeRequests"
            :submitting="judgeSubmitting"
            :disabled="!currentCompareJudgeRunId"
            @queue="queueJudge"
            @refresh="loadJudgeRequests(currentCompareJudgeRunId)"
          />
        </section>
      </main>

      <aside class="side-stack sticky-pane">
        <CaseEvidenceInspector
          :baseline-artifact="compareArtifacts.baseline"
          :candidate-artifact="compareArtifacts.candidate"
          :compare-case="selectedCompareCase"
          :loading="compareCaseLoading"
        />
      </aside>
    </div>
  </section>
</template>

<style scoped>
.workflow-lab {
  display: grid;
  gap: 16px;
}

.context-bar {
  display: grid;
  grid-template-columns: minmax(320px, 1.05fr) minmax(520px, 1.45fr);
  gap: 16px;
  align-items: stretch;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 18px;
}

.context-bar p,
.workspace-desc,
.workspace-nav small,
.notice,
.run-list small,
.run-list p,
.judge-workspace-header span {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.context-bar h1 {
  margin: 2px 0 0;
  font-size: 24px;
}

.workspace-desc {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.55;
}

.context-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.context-grid div {
  display: grid;
  align-content: start;
  gap: 6px;
  min-width: 0;
  background: var(--theme--background-subdued);
  padding: 10px;
}

dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}

dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}

.notice {
  border-radius: 8px;
  padding: 10px 12px;
  line-height: 1.55;
}

.notice.error {
  background: var(--theme--danger-background);
}

.notice.success {
  background: var(--theme--success-background);
}

.workspace-nav {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.workspace-nav button,
.run-list button,
.compare-tabs button {
  min-height: 64px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  padding: 10px 12px;
  text-align: left;
}

.workspace-nav button.active,
.run-list button.active,
.compare-tabs button.active {
  border-color: var(--theme--primary);
  background: var(--theme--background-subdued);
}

.workspace-nav strong {
  display: block;
}

.workspace-nav small {
  display: block;
  margin-top: 6px;
  line-height: 1.5;
}

.workspace-layout {
  display: grid;
  gap: 16px;
  align-items: start;
}

.single-run-layout {
  grid-template-columns: minmax(520px, 0.78fr) minmax(420px, 1fr);
}

.dataset-layout {
  grid-template-columns: minmax(320px, 0.38fr) minmax(0, 1fr);
}

.compare-layout {
  grid-template-columns: minmax(0, 1fr) minmax(420px, 0.42fr);
}

.main-stack,
.side-stack {
  display: grid;
  gap: 14px;
  min-width: 0;
}

.sticky-pane {
  position: sticky;
  top: 24px;
  min-width: 0;
}

.run-list,
.compare-tabs,
.judge-workspace,
.launcher-shell {
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 14px;
}

.run-list header,
.judge-workspace-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.run-list button {
  display: block;
  width: 100%;
  margin-top: 8px;
  min-height: 56px;
}

.run-list span,
.run-list small {
  display: block;
  overflow-wrap: anywhere;
}

.compare-tabs {
  display: flex;
  gap: 8px;
}

.launcher-shell summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  font-weight: 700;
}

.launcher-shell :deep(.wl-panel) {
  margin-top: 12px;
}

.compare-tabs button {
  flex: 1;
}

.judge-workspace-header h2 {
  margin: 2px 0 0;
  font-size: 18px;
}

@media (max-width: 1240px) {
  .context-bar,
  .single-run-layout,
  .dataset-layout,
  .compare-layout {
    grid-template-columns: 1fr;
  }

  .sticky-pane {
    position: static;
  }
}

@media (max-width: 760px) {
  .workspace-nav,
  .context-grid {
    grid-template-columns: 1fr;
  }
}
</style>
