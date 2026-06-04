<script setup>
import { computed, onMounted, ref, watch } from "vue";
import CandidatePanel from "./components/CandidatePanel.vue";
import CaseEvidenceInspector from "./components/CaseEvidenceInspector.vue";
import WorkflowCompareBuilder from "./components/WorkflowCompareBuilder.vue";
import WorkflowCompareReport from "./components/WorkflowCompareReport.vue";
import WorkflowJudgePanel from "./components/WorkflowJudgePanel.vue";
import WorkflowDatasetWorkspace from "./components/WorkflowDatasetWorkspace.vue";
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
const STORAGE_STATE_KEY = "claread-eval-center:workflow-lab:state:v1";
const WORKFLOW_PROMPT_BUNDLE_SCHEMA = "workflow-prompt-bundle-v1";
const WORKFLOW_AGENT_ORDER = ["vocabulary", "grammar", "translation", "repair"];
const WORKFLOW_AGENT_LABELS = {
  vocabulary: "词汇",
  grammar: "语法",
  translation: "翻译",
  repair: "修复",
};

const WORKSPACES = [
  {
    id: "candidate",
    label: "候选版本",
    desc: "从 baseline 派生候选版本，编辑后发布到验证入口。",
    nextHint: "完成后到「单篇验证」实测。",
  },
  {
    id: "single_run",
    label: "单篇验证",
    desc: "用一篇文章快速验证候选版本是否值得进入数据集验证。",
    nextHint: "通过后到「数据集验证」批量跑。",
  },
  {
    id: "datasets",
    label: "数据集工作区",
    desc: "创建 dataset，并把当前单篇验证沉淀成可批量复跑的 case。",
    nextHint: "准备好 dataset 后到「数据集验证」发起 runs。",
  },
  {
    id: "dataset_runs",
    label: "数据集验证",
    desc: "把候选版本加入数据集验证队列，阅读逐 case 证据。",
    nextHint: "完成后到「对比与证据」与 baseline 对比。",
  },
  {
    id: "compare_judge",
    label: "对比与证据",
    desc: "比较 baseline run 与 candidate run，查看 case 级别差异和 judge 评审。",
    nextHint: "形成决策后回「候选版本」迭代。",
  },
];
const NEXT_WORKSPACE_BY_ID = {
  candidate: "single_run",
  single_run: "datasets",
  datasets: "dataset_runs",
  dataset_runs: "compare_judge",
  compare_judge: "candidate",
};

const activeWorkspace = ref("candidate");
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
const availableDatasets = ref([]);
const datasetsLoading = ref(false);
const datasetCreating = ref(false);
const datasetCaseSaving = ref(false);

const singleRunSubmitting = ref(false);
const singleRunHistorySaving = ref(false);
const singleRunResult = ref(null);
const lastSingleRunRequest = ref(null);
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

// 跨 workspace CTA 路由:从候选版本发布后跳到单篇验证并预选 candidate
const pendingSingleRunCandidateId = ref("");
const pendingDatasetCandidateId = ref("");

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
const nextWorkspaceMeta = computed(() => WORKSPACES.find((item) => item.id === NEXT_WORKSPACE_BY_ID[activeWorkspace.value]) || null);
const datasetRuns = computed(() => runs.value.filter((run) => run.mode !== "workflow_single_run" && (run.learning_case_count || 0) > 0 && !isFakeRun(run)));
const compareRuns = computed(() => runs.value.filter((run) => run.mode !== "workflow_single_run" && (run.learning_case_count || 0) > 0 && run.has_report && !isFakeRun(run)));
const publishedCandidateCount = computed(() => readyCandidates.value.length);
const draftCandidateCount = computed(() => candidateDrafts.value.filter((item) => item.status !== "ready_for_eval").length);
const currentCompareJudgeRunId = computed(() => compareResult.value?.report?.candidate_run_id || candidateRunId.value || "");
const candidateDirty = computed(() => JSON.stringify(candidateForm.value) !== candidateBaselineJson.value);

const compareCaseCoverage = computed(() => {
  const comparisons = compareResult.value?.report?.comparisons;
  return Array.isArray(comparisons) ? comparisons.length : 0;
});
const compareDatasetId = computed(() => {
  return compareResult.value?.report?.dataset_id
    || candidateRun.value?.dataset_id
    || baselineRun.value?.dataset_id
    || "";
});
const baselineRun = computed(() => compareRuns.value.find((run) => run.run_id === baselineRunId.value) || null);
const candidateRun = computed(() => compareRuns.value.find((run) => run.run_id === candidateRunId.value) || null);
const persistedWorkflowState = computed(() => ({
  activeWorkspace: activeWorkspace.value,
  activeCompareTab: activeCompareTab.value,
  selectedDraftId: selectedDraftId.value,
  candidateForm: candidateForm.value,
  candidateBaselineJson: candidateBaselineJson.value,
  singleRunResult: singleRunResult.value,
  lastSingleRunRequest: lastSingleRunRequest.value,
  pendingSingleRunCandidateId: pendingSingleRunCandidateId.value,
  pendingDatasetCandidateId: pendingDatasetCandidateId.value,
  selectedRunId: selectedRunId.value,
  selectedCaseId: selectedCaseId.value,
  selectedCaseArtifact: selectedCaseArtifact.value,
  baselineRunId: baselineRunId.value,
  candidateRunId: candidateRunId.value,
  compareResult: compareResult.value,
  compareArtifacts: compareArtifacts.value,
  selectedCompareCase: selectedCompareCase.value ? { case_id: selectedCompareCase.value.case_id } : null,
}));

const unifiedRunsList = computed(() => {
  const map = new Map();
  // 先用 artifact runs 填底，覆盖所有"只有本地 artifact、没有 request 行"的历史 run
  for (const run of datasetRuns.value) {
    map.set(run.run_id, {
      run_id: run.run_id,
      status: run.has_report ? "succeeded" : "unknown",
      dataset_id: run.dataset_id || null,
      prompt_variant_id: run.prompt_variant_id || null,
      learning_case_count: run.learning_case_count || 0,
      has_report: Boolean(run.has_report),
      created_at: run.created_at || null,
      cancelable: false,
      retryable: false,
      request_id: null,
      source: "artifact",
    });
  }
  // 再 overlay requests，覆盖 in-flight / 已知状态的 run，并补 cancel/retry 能力
  for (const req of requests.value) {
    if (isFakeRun({ run_id: req.run_id, adapter_kind: req.adapter_kind })) continue;
    const existing = map.get(req.run_id) || {};
    map.set(req.run_id, {
      run_id: req.run_id,
      status: req.status || existing.status || "unknown",
      dataset_id: req.dataset_id || existing.dataset_id || null,
      prompt_variant_id: req.prompt_variant_id || existing.prompt_variant_id || null,
      learning_case_count: existing.learning_case_count || 0,
      has_report: existing.has_report || req.status === "succeeded" || req.status === "complete",
      created_at: req.date_created || req.created_at || existing.created_at || null,
      cancelable: Boolean(req.cancelable),
      retryable: Boolean(req.retryable),
      request_id: req.id || null,
      source: existing.source ? "request+artifact" : "request",
    });
  }
  return Array.from(map.values())
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
});

function isFakeRun(run) {
  if (!run) return false;
  const id = String(run.run_id || "");
  if (id.startsWith("ui-fake-") || id.startsWith("smoke-fake-")) return true;
  return run.adapter_kind === "fake";
}

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

watch(
  persistedWorkflowState,
  (value) => {
    window.sessionStorage.setItem(STORAGE_STATE_KEY, JSON.stringify(value));
  },
  { deep: true },
);

watch(currentCompareJudgeRunId, async (runId) => {
  if (activeWorkspace.value === "compare_judge" && activeCompareTab.value === "judge" && runId) {
    await loadJudgeRequests(runId);
  }
});

onMounted(async () => {
  const persistedWorkspace = window.sessionStorage.getItem(STORAGE_KEY);
  let restoredState = null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_STATE_KEY);
    restoredState = raw ? JSON.parse(raw) : null;
  } catch {
    restoredState = null;
  }
  if (persistedWorkspace && WORKSPACES.some((item) => item.id === persistedWorkspace)) {
    activeWorkspace.value = persistedWorkspace;
  }
  if (restoredState && typeof restoredState === "object") {
    activeCompareTab.value = restoredState.activeCompareTab || activeCompareTab.value;
    selectedDraftId.value = restoredState.selectedDraftId || "";
    candidateBaselineJson.value = restoredState.candidateBaselineJson || candidateBaselineJson.value;
    singleRunResult.value = restoredState.singleRunResult || null;
    lastSingleRunRequest.value = restoredState.lastSingleRunRequest || null;
    pendingSingleRunCandidateId.value = restoredState.pendingSingleRunCandidateId || "";
    pendingDatasetCandidateId.value = restoredState.pendingDatasetCandidateId || "";
    baselineRunId.value = restoredState.baselineRunId || "";
    candidateRunId.value = restoredState.candidateRunId || "";
    selectedCaseId.value = restoredState.selectedCaseId || "";
    selectedCaseArtifact.value = restoredState.selectedCaseArtifact || null;
    compareResult.value = restoredState.compareResult || null;
    compareArtifacts.value = restoredState.compareArtifacts || { baseline: null, candidate: null };
    selectedRunId.value = restoredState.selectedRunId || "";
  }
  await Promise.all([
    loadRuns(),
    loadRequests(),
    loadCandidates({ syncSelection: true }),
    loadRubrics(),
    loadModelProfiles(),
    loadDatasets(),
  ]);
  if (restoredState?.candidateForm && typeof restoredState.candidateForm === "object") {
    candidateForm.value = {
      ...emptyCandidateForm(),
      ...restoredState.candidateForm,
    };
  }
  if (restoredState?.selectedRunId && unifiedRunsList.value.some((row) => row.run_id === restoredState.selectedRunId)) {
    await selectRun(restoredState.selectedRunId, { silentWorkspace: true, skipFirstCase: Boolean(restoredState.selectedCaseArtifact) });
  }
  if (currentCompareJudgeRunId.value && activeWorkspace.value === "compare_judge" && activeCompareTab.value === "judge") {
    await loadJudgeRequests(currentCompareJudgeRunId.value);
  } else {
    await loadJudgeRequests();
  }
});

async function loadDatasets() {
  datasetsLoading.value = true;
  try {
    availableDatasets.value = await workflowApi.listDatasets();
  } catch (err) {
    setError(err, "Failed to load datasets.");
  } finally {
    datasetsLoading.value = false;
  }
}

function emptyCandidateForm() {
  return {
    variant_id: "",
    status: "draft",
    scope: "workflow_lab",
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
    scope: "workflow_lab",
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
      scope: draft.scope || "workflow_lab",
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
    scope: draft.scope || "workflow_lab",
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
    const lines = layer.policy_lines.filter((line) => line.trim());
    const variantKey = layer.policy_variant || "default";
    policies[layer.policy_name][layer.policy_focus] = {
      [variantKey]: lines,
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

function onGoToSingleRun(variantId) {
  // 从候选版本页(发布后 CTA)切到单篇验证;null = "留在候选版本",什么都不做
  if (variantId === null) return;
  if (variantId && activeWorkspace.value === "candidate") {
    pendingSingleRunCandidateId.value = variantId;
  }
  activeWorkspace.value = "single_run";
}

function onGoToDatasetRuns() {
  const candidateId = singleRunResult.value?.prompt_identity?.prompt_variant_id || "";
  if (candidateId) pendingDatasetCandidateId.value = candidateId;
  if (availableDatasets.value.length === 0) {
    message.value = "先在「数据集工作区」创建一个 dataset，再回来发起批量验证。";
    activeWorkspace.value = "datasets";
    return;
  }
  activeWorkspace.value = "dataset_runs";
}

function onGoToCandidate() {
  activeWorkspace.value = "candidate";
}

function onGoToDatasets() {
  activeWorkspace.value = "datasets";
}

function goToNextWorkspace() {
  if (nextWorkspaceMeta.value?.id) {
    activeWorkspace.value = nextWorkspaceMeta.value.id;
  }
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
  const selectedRow = unifiedRunsList.value.find((row) => row.run_id === runId) || null;
  try {
    if (selectedRow && ["queued", "running", "failed", "cancelled"].includes(selectedRow.status) && !selectedRow.has_report) {
      selectedRunDetail.value = {
        summary: {
          run_id: selectedRow.run_id,
          dataset_id: selectedRow.dataset_id,
          prompt_variant_id: selectedRow.prompt_variant_id,
          rag_mode: selectedRow.config_summary?.rag_mode || null,
          topology_mode: "learning",
          status: selectedRow.status,
          learning_case_count: selectedRow.learning_case_count || 0,
          total_cases: selectedRow.learning_case_count || 0,
        },
        case_artifacts: [],
        pending_message: selectedRow.status === "failed" || selectedRow.status === "cancelled"
          ? "这条运行请求没有成功落盘，所以当前没有可读取的 eval artifact。请先查看请求状态并决定是否重试。"
          : "这条运行请求仍在排队或后台执行中，artifact 还没有落盘。等 Directus 完成写盘后，这里才会出现 case 证据。",
      };
      await loadJudgeRequests(runId);
      return;
    }
    selectedRunDetail.value = await workflowApi.getRunDetail(runId);
    const firstLearningCase = options.skipFirstCase
      ? null
      : (selectedRunDetail.value?.case_artifacts || []).find(
        (item) => item?.workflow_identity?.topology_mode === "learning" || item?.schema_identity?.topology_mode === "learning",
      );
    if (firstLearningCase && !options.skipFirstCase) {
      await selectCase(firstLearningCase.case_id, runId);
    }
    await loadJudgeRequests(runId);
  } catch (err) {
    if (selectedRow && !selectedRow.has_report && String(workflowApi.directusError(err, "")).includes("Eval artifact not found")) {
      selectedRunDetail.value = {
        summary: {
          run_id: selectedRow.run_id,
          dataset_id: selectedRow.dataset_id,
          prompt_variant_id: selectedRow.prompt_variant_id,
          rag_mode: selectedRow.config_summary?.rag_mode || null,
          topology_mode: "learning",
          status: selectedRow.status,
          learning_case_count: selectedRow.learning_case_count || 0,
          total_cases: selectedRow.learning_case_count || 0,
        },
        case_artifacts: [],
        pending_message: "当前 run 还没有产出可读取的 eval artifact。请等待后台执行完成并写盘后再打开详情。",
      };
    } else {
      selectedRunDetail.value = null;
      setError(err, "Failed to load run detail.");
    }
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
  lastSingleRunRequest.value = payload;
  error.value = "";
  message.value = "";
  try {
    singleRunResult.value = await workflowApi.runSingleWorkflow(payload);
    pendingDatasetCandidateId.value = payload.prompt_variant_id || "";
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

async function saveSingleRunToHistory() {
  if (!singleRunResult.value || !lastSingleRunRequest.value) return;
  singleRunHistorySaving.value = true;
  error.value = "";
  try {
    const data = await workflowApi.saveSingleRunToHistory({
      request: lastSingleRunRequest.value,
      result: singleRunResult.value,
      run_id: singleRunResult.value?.saved_history_run_id || null,
    });
    const runId = data?.record?.run_id || data?.summary?.run_id || data?.run_id || "";
    if (runId) {
      singleRunResult.value = {
        ...singleRunResult.value,
        saved_history_run_id: runId,
      };
    }
    message.value = runId ? `Single Run 已保存到 Run History：${runId}` : "Single Run 已保存到 Run History。";
    await loadRuns({ keepSelection: true });
  } catch (err) {
    setError(err, "Failed to save workflow single run to Run History.");
  } finally {
    singleRunHistorySaving.value = false;
  }
}

function openWorkflowRunHistory(runId) {
  if (!runId) return;
  emit("open-run-history", { source: "workflow", runId });
}

async function createDataset(payload) {
  datasetCreating.value = true;
  error.value = "";
  message.value = "";
  try {
    const result = await workflowApi.createDataset(payload);
    const datasetId = result?.dataset?.id || payload.dataset_id;
    const caseId = result?.case?.case_id || result?.initial_case?.case_id || "";
    message.value = caseId
      ? `已创建 evals/datasets/${datasetId}/dataset.yaml，并写入 cases/${caseId}.json`
      : `已创建 evals/datasets/${datasetId}/dataset.yaml`;
    await loadDatasets();
    activeWorkspace.value = "datasets";
  } catch (err) {
    setError(err, "Failed to create workflow dataset.");
  } finally {
    datasetCreating.value = false;
  }
}

async function addSingleRunCaseToDataset(payload) {
  datasetCaseSaving.value = true;
  error.value = "";
  message.value = "";
  try {
    const result = await workflowApi.addDatasetCase(payload.dataset_id, payload);
    const caseId = result?.case?.case_id || "";
    message.value = caseId
      ? `已写入 evals/datasets/${payload.dataset_id}/cases/${caseId}.json`
      : `已写入 evals/datasets/${payload.dataset_id}/cases/`;
    await loadDatasets();
    activeWorkspace.value = "datasets";
  } catch (err) {
    setError(err, "Failed to write single run case into dataset.");
  } finally {
    datasetCaseSaving.value = false;
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
    scope: "workflow_lab",
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
        { label: "运行中", value: String(requests.value.filter((r) => r.status === "queued" || r.status === "running").length) },
        { label: "可读 run", value: String(datasetRuns.value.length) },
        { label: "当前 Run", value: dash(selectedRunId.value, "未选择") },
      ];
    }
    if (activeWorkspace.value === "datasets") {
      return [
        { label: "Datasets", value: String(availableDatasets.value.length) },
        { label: "Cases", value: String(availableDatasets.value.reduce((sum, item) => sum + Number(item.case_count || 0), 0)) },
        { label: "当前 Single Run", value: singleRunResult.value ? "可入库" : "未准备" },
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
      <div class="context-bar-main">
        <p class="context-bar-kicker">Workflow Lab</p>
        <h1>{{ activeWorkspaceMeta.label }}</h1>
        <div v-if="activeWorkspaceMeta.nextHint" class="next-hint">
          <span>{{ activeWorkspaceMeta.nextHint }}</span>
          <button v-if="nextWorkspaceMeta" type="button" @click="goToNextWorkspace">去{{ nextWorkspaceMeta.label }}</button>
        </div>
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
      @go-to-single-run="onGoToSingleRun"
    />

    <div v-else-if="activeWorkspace === 'single_run'" class="single-run-flow">
      <template v-if="singleRunResult || singleRunSubmitting">
        <WorkflowSingleRunResult
          :result="singleRunResult"
          :loading="singleRunSubmitting"
          :saving-history="singleRunHistorySaving"
          @go-to-dataset-runs="onGoToDatasetRuns"
          @save-run-history="saveSingleRunToHistory"
          @open-run-history="openWorkflowRunHistory"
        />
        <WorkflowSingleRunLauncher
          :candidates="readyCandidates"
          :model-profiles="modelProfiles"
          :submitting="singleRunSubmitting"
          :initial-candidate-id="pendingSingleRunCandidateId"
          @submit="submitSingleRun"
          @go-to-candidate="onGoToCandidate"
        />
      </template>
      <template v-else>
        <WorkflowSingleRunLauncher
          :candidates="readyCandidates"
          :model-profiles="modelProfiles"
          :submitting="singleRunSubmitting"
          :initial-candidate-id="pendingSingleRunCandidateId"
          @submit="submitSingleRun"
          @go-to-candidate="onGoToCandidate"
        />
        <WorkflowSingleRunResult
          :result="singleRunResult"
          :loading="singleRunSubmitting"
          :saving-history="singleRunHistorySaving"
          @go-to-dataset-runs="onGoToDatasetRuns"
          @save-run-history="saveSingleRunToHistory"
          @open-run-history="openWorkflowRunHistory"
        />
      </template>
    </div>

    <div v-else-if="activeWorkspace === 'dataset_runs'" class="workspace-layout dataset-layout">
      <aside class="side-stack">
        <WorkflowRunQueue
          :requests="unifiedRunsList"
          :loading="requestsLoading || runsLoading"
          :selected-run-id="selectedRunId"
          @refresh="() => { loadRequests(); loadRuns({ keepSelection: true }); }"
          @select-run="selectRun"
          @cancel="cancelRequest"
          @retry="retryRequest"
        />
      </aside>

      <main class="main-stack">
        <section class="launcher-shell">
          <WorkflowRunLauncher
            :candidates="readyCandidates"
            :model-profiles="modelProfiles"
            :submitting="runSubmitting"
            :datasets="availableDatasets"
            :initial-candidate-id="pendingDatasetCandidateId"
            @submit="submitRun"
            @open-dataset-workspace="onGoToDatasets"
          />
        </section>
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
          @open-history="emit('open-run-history', $event)"
        />
        <CaseEvidenceInspector
          :artifact="selectedCaseArtifact"
          :loading="caseLoading"
        />
      </main>
    </div>

    <WorkflowDatasetWorkspace
      v-else-if="activeWorkspace === 'datasets'"
      :datasets="availableDatasets"
      :loading="datasetsLoading"
      :creating="datasetCreating"
      :adding-case="datasetCaseSaving"
      :single-run-request="lastSingleRunRequest"
      :single-run-result="singleRunResult"
      @refresh="loadDatasets"
      @create-dataset="createDataset"
      @add-single-run-case="addSingleRunCaseToDataset"
      @go-to-single-run="activeWorkspace = 'single_run'"
      @go-to-dataset-runs="activeWorkspace = 'dataset_runs'"
    />

    <div v-else class="workspace-layout compare-layout">
      <main class="main-stack">
        <WorkflowCompareBuilder
          v-model:baseline-run-id="baselineRunId"
          v-model:candidate-run-id="candidateRunId"
          :runs="compareRuns"
          :loading="compareLoading"
          :case-coverage="compareCaseCoverage"
          :dataset-id="compareDatasetId"
          :compare-result="compareResult"
          @compare="createCompare"
          @select-run="selectRun"
        />

        <section class="compare-tabs">
          <button type="button" :class="{ active: activeCompareTab === 'compare' }" @click="activeCompareTab = 'compare'">对比报告</button>
          <button type="button" :class="{ active: activeCompareTab === 'judge' }" @click="activeCompareTab = 'judge'; if (currentCompareJudgeRunId) { loadJudgeRequests(currentCompareJudgeRunId); } else { judgeRequests = []; }">Judge 评审</button>
        </section>

        <WorkflowCompareReport
          v-if="activeCompareTab === 'compare'"
          :result="compareResult"
          :selected-case-id="selectedCompareCase?.case_id || ''"
          :baseline-artifact="compareArtifacts.baseline"
          :candidate-artifact="compareArtifacts.candidate"
          @select-case="selectCompareCase"
        />

        <section v-else class="judge-workspace">
          <header class="judge-workspace-header">
            <div>
              <p>Judge 评审</p>
              <h2>{{ currentCompareJudgeRunId || "请先生成一条差异报告" }}</h2>
            </div>
            <span>本面板展示的是 candidate run 的 run-level judge 请求与结果摘要，不是 baseline vs candidate 的 pairwise judge。</span>
          </header>

          <p class="judge-semantic-note" role="note">
            <strong>Judge 语义：</strong>
            这里展示的是当前 candidate run 自身的 judge 请求和结果；
            <strong>不是</strong> baseline vs candidate 的 pairwise 比较。
            Compare-level pairwise judge 是未来工作，本轮不在主路径内，
            <strong>本面板不会生成任何裁决文案</strong>。
          </p>

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

.context-bar-kicker {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

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
  margin: 4px 0 0;
  font-size: 20px;
  font-weight: 600;
}

.workspace-desc {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.55;
}

.next-hint {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
}

.next-hint button {
  min-height: 32px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  padding: 4px 12px;
}

.context-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
  align-self: start;
}

.context-grid div {
  display: grid;
  align-content: start;
  gap: 2px;
  min-width: 0;
  background: var(--theme--background-subdued);
  padding: 10px 12px;
}

dt {
  color: var(--theme--foreground-subdued);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

dd {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
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
  transition: border-color 0.15s ease, background 0.15s ease;
}

.workspace-nav button:hover,
.run-list button:hover,
.compare-tabs button:hover {
  border-color: color-mix(in srgb, var(--theme--primary) 35%, var(--theme--border-color));
}

.workspace-nav button.active,
.run-list button.active,
.compare-tabs button.active {
  border-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
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

.single-run-flow {
  display: grid;
  gap: 16px;
  align-items: start;
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

.judge-semantic-note {
  margin: 0 0 10px;
  border: 1px solid var(--theme--primary);
  border-radius: 6px;
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
  padding: 10px 12px;
  color: var(--theme--foreground);
  font-size: 12px;
  line-height: 1.6;
  position: relative;
}
.judge-semantic-note::before {
  content: "";
  position: absolute;
  top: 12px;
  left: 12px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--theme--primary);
}
.judge-semantic-note {
  padding-left: 26px;
}

.judge-semantic-note strong {
  color: var(--theme--foreground);
}

@media (max-width: 1240px) {
  .context-bar,
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
