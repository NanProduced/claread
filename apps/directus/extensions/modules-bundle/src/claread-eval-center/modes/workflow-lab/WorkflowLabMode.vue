<script setup>
import { computed, onMounted, ref, watch } from "vue";
import CandidatePanel from "./components/CandidatePanel.vue";
import WorkflowCompareReport from "./components/WorkflowCompareReport.vue";
import WorkflowJudgePanel from "./components/WorkflowJudgePanel.vue";
import WorkflowSingleRunLauncher from "./components/WorkflowSingleRunLauncher.vue";
import WorkflowSingleRunResult from "./components/WorkflowSingleRunResult.vue";
import { useWorkflowLabApi } from "./composables/useWorkflowLabApi.js";

const emit = defineEmits(["open-run-history"]);
const workflowApi = useWorkflowLabApi();

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
    desc: "从 baseline 派生候选版本，编辑后发布到双跑入口。",
    nextHint: "完成后到「单篇验证」跑 baseline / candidate 双跑 compare。",
  },
  {
    id: "single_run",
    label: "单篇验证",
    desc: "同一篇文章并发跑 baseline 与 candidate，直接物化 workflow compare。",
    nextHint: "完成后到「对比与证据」看差异、发起 compare-level judge。",
  },
  {
    id: "compare_judge",
    label: "对比与证据",
    desc: "只消费 persisted workflow compare；Judge 与 Review 都锚定 compare_id。",
    nextHint: "形成结论后回「候选版本」继续迭代。",
  },
];
const NEXT_WORKSPACE_BY_ID = {
  candidate: "single_run",
  single_run: "compare_judge",
  compare_judge: "candidate",
};

const activeWorkspace = ref("candidate");
const activeCompareTab = ref("compare");
const error = ref("");
const message = ref("");

const singleRunCompareSubmitting = ref(false);
const compareResult = ref(null);
const selectedCompareCase = ref(null);
const compareArtifacts = ref({ baseline: null, candidate: null });
const compareCaseLoading = ref(false);
const pendingSingleRunCandidateId = ref("");

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
const modelProfiles = ref([]);

const activeWorkspaceMeta = computed(() => WORKSPACES.find((item) => item.id === activeWorkspace.value) || WORKSPACES[0]);
const nextWorkspaceMeta = computed(() => WORKSPACES.find((item) => item.id === NEXT_WORKSPACE_BY_ID[activeWorkspace.value]) || null);
const publishedCandidateCount = computed(() => readyCandidates.value.length);
const draftCandidateCount = computed(() => candidateDrafts.value.filter((item) => item.status !== "ready_for_eval").length);
const candidateDirty = computed(() => JSON.stringify(candidateForm.value) !== candidateBaselineJson.value);
const currentCompareId = computed(() => compareResult.value?.compare?.compare_id || compareResult.value?.compare_id || "");
const currentCompareRecord = computed(() => compareResult.value?.compare || null);
const baselineArtifact = computed(() => compareResult.value?.compare?.baseline_artifact || compareArtifacts.value.baseline || null);
const candidateArtifact = computed(() => compareResult.value?.compare?.candidate_artifact || compareArtifacts.value.candidate || null);
const compareCaseCoverage = computed(() => {
  const comparisons = compareResult.value?.compare?.report?.comparisons;
  return Array.isArray(comparisons) ? comparisons.length : 0;
});

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
  if (variantId === null) return;
  if (variantId && activeWorkspace.value === "candidate") {
    pendingSingleRunCandidateId.value = variantId;
  }
  activeWorkspace.value = "single_run";
}

function onGoToCandidate() {
  activeWorkspace.value = "candidate";
}

async function onGoToCompare() {
  if (!currentCompareRecord.value) {
    error.value = "请先完成一次单篇双跑 compare。";
    return;
  }
  activeWorkspace.value = "compare_judge";
  activeCompareTab.value = "compare";
  const firstComparison = compareResult.value?.compare?.report?.comparisons?.[0];
  if (firstComparison) await selectCompareCase(firstComparison);
  if (currentCompareId.value) {
    await loadJudgeRequests(currentCompareId.value);
  }
}

async function requestWorkspaceChange(workspaceId) {
  if (!workspaceId || workspaceId === activeWorkspace.value) return;
  if (workspaceId === "compare_judge" && !currentCompareRecord.value) {
    error.value = "当前还没有 workflow compare 记录，先完成一次单篇双跑。";
    return;
  }
  activeWorkspace.value = workspaceId;
}

async function goToNextWorkspace() {
  if (nextWorkspaceMeta.value?.id) {
    await requestWorkspaceChange(nextWorkspaceMeta.value.id);
  }
}

async function submitSingleRunCompare(payload) {
  singleRunCompareSubmitting.value = true;
  compareResult.value = null;
  selectedCompareCase.value = null;
  compareArtifacts.value = { baseline: null, candidate: null };
  error.value = "";
  message.value = "";
  try {
    const result = await workflowApi.runSingleRunCompare(payload);
    compareResult.value = result;
    activeWorkspace.value = "single_run";
    message.value = "双跑 compare 完成：已生成 persisted workflow compare，可直接进入「对比与证据」。";
  } catch (err) {
    setError(err, "Failed to run workflow single-run compare.");
  } finally {
    singleRunCompareSubmitting.value = false;
  }
}

async function selectCompareCase(comparison) {
  selectedCompareCase.value = comparison || null;
  compareCaseLoading.value = true;
  compareArtifacts.value = { baseline: null, candidate: null };
  try {
    if (!comparison?.case_id || !currentCompareId.value) {
      compareArtifacts.value = { baseline: null, candidate: null };
      return;
    }
    const evidence = await workflowApi.getCompareCaseEvidence(currentCompareId.value, comparison.case_id);
    compareArtifacts.value = {
      baseline: evidence?.baseline_artifact || null,
      candidate: evidence?.candidate_artifact || null,
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
  await persistCandidate("ready_for_eval", "Candidate 已发布，可在「单篇验证」双跑 compare 中选择。");
}

async function unpublishCandidate() {
  await persistCandidate("draft", "Candidate 已撤回发布。");
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

async function loadJudgeRequests(compareId = "") {
  try {
    judgeRequests.value = compareId ? await workflowApi.listCompareJudgeRequests(compareId) : [];
  } catch (err) {
    setError(err, "Failed to load compare judge requests.");
  }
}

async function queueJudge(payload) {
  judgeSubmitting.value = true;
  error.value = "";
  try {
    if (!currentCompareId.value) {
      error.value = "当前 compare 尚未就绪，无法发起 Judge。";
      return;
    }
    await workflowApi.createCompareJudgeRequest(currentCompareId.value, payload);
    await loadJudgeRequests(currentCompareId.value);
  } catch (err) {
    setError(err, "Failed to queue compare judge request.");
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
  if (activeWorkspace.value === "single_run") {
    return [
      { label: "运行模式", value: "baseline / candidate 双跑" },
      { label: "Compare", value: currentCompareId.value ? "已物化" : "未生成" },
      { label: "Compare id", value: currentCompareId.value || "未生成" },
    ];
  }
  return [
    { label: "来源", value: currentCompareRecord.value ? "persisted compare" : "未选择" },
    { label: "Judge 语义", value: "compare-level pairwise" },
    { label: "Compare id", value: currentCompareId.value || "未生成" },
  ];
});

onMounted(async () => {
  await Promise.all([
    loadCandidates({ syncSelection: true }),
    loadRubrics(),
    loadModelProfiles(),
  ]);
});

watch(currentCompareId, async (compareId) => {
  if (activeWorkspace.value === "compare_judge" && activeCompareTab.value === "judge" && compareId) {
    await loadJudgeRequests(compareId);
  }
});
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
        @click="requestWorkspaceChange(workspace.id)"
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
      <template v-if="compareResult || singleRunCompareSubmitting">
        <WorkflowSingleRunResult
          :compare-result="compareResult"
          :loading="singleRunCompareSubmitting"
          @open-compare="onGoToCompare"
        />
        <WorkflowSingleRunLauncher
          :candidates="readyCandidates"
          :model-profiles="modelProfiles"
          :submitting="singleRunCompareSubmitting"
          :initial-candidate-id="pendingSingleRunCandidateId"
          @submit="submitSingleRunCompare"
          @go-to-candidate="onGoToCandidate"
        />
      </template>
      <template v-else>
        <WorkflowSingleRunLauncher
          :candidates="readyCandidates"
          :model-profiles="modelProfiles"
          :submitting="singleRunCompareSubmitting"
          :initial-candidate-id="pendingSingleRunCandidateId"
          @submit="submitSingleRunCompare"
          @go-to-candidate="onGoToCandidate"
        />
        <WorkflowSingleRunResult
          :compare-result="compareResult"
          :loading="singleRunCompareSubmitting"
          @open-compare="onGoToCompare"
        />
      </template>
    </div>

    <div v-else class="workspace-layout compare-layout">
      <main class="main-stack compare-main-stack">
        <header class="cw-source-banner" role="status">
          <strong>当前消费的是 persisted workflow compare</strong>
          <small>compare_id {{ currentCompareId || "—" }}</small>
        </header>

        <section class="compare-tabs">
          <button type="button" :class="{ active: activeCompareTab === 'compare' }" @click="activeCompareTab = 'compare'">对比报告</button>
          <button
            type="button"
            :class="{ active: activeCompareTab === 'judge' }"
            @click="activeCompareTab = 'judge'; if (currentCompareId) { loadJudgeRequests(currentCompareId); } else { judgeRequests = []; }"
          >
            Judge 评审
          </button>
        </section>

        <WorkflowCompareReport
          v-if="activeCompareTab === 'compare'"
          :compare-id="currentCompareId"
          :result="currentCompareRecord ? { compare_id: currentCompareId, report: currentCompareRecord.report, report_id: currentCompareRecord.report_id, created: true } : null"
          :selected-case-id="selectedCompareCase?.case_id || ''"
          :baseline-artifact="baselineArtifact"
          :candidate-artifact="candidateArtifact"
          @select-case="selectCompareCase"
        />

        <section v-else class="judge-workspace">
          <header class="judge-workspace-header">
            <div>
              <p>Judge 评审</p>
              <h2>{{ currentCompareId || "请先生成一条 compare" }}</h2>
            </div>
            <span>这里展示的是 compare-level pairwise judge，请求与结果都锚定 compare_id。</span>
          </header>

          <WorkflowJudgePanel
            :compare-id="currentCompareId"
            :rubrics="rubrics"
            :requests="judgeRequests"
            :model-profiles="modelProfiles"
            :submitting="judgeSubmitting"
            :disabled="!currentCompareId"
            @queue="queueJudge"
            @refresh="loadJudgeRequests(currentCompareId)"
          />
        </section>
      </main>
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

.context-bar-kicker,
.cw-source-banner small,
.workspace-nav small,
.notice,
.next-hint span {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.context-bar-main h1 {
  margin: 4px 0 0;
  font-size: 22px;
}

.next-hint {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.next-hint button,
.compare-tabs button,
.workspace-nav button {
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground);
  border-radius: 8px;
  font: inherit;
  cursor: pointer;
}

.next-hint button {
  min-height: 32px;
  padding: 6px 12px;
  font-weight: 700;
}

.context-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.context-grid div {
  background: var(--theme--background-subdued);
  padding: 12px;
}

.context-grid dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}

.context-grid dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
}

.notice {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 10px 12px;
}

.notice.error {
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  color: var(--theme--danger);
}

.notice.success {
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
  color: var(--theme--success);
}

.workspace-nav {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.workspace-nav button {
  padding: 14px;
  text-align: left;
  display: grid;
  gap: 4px;
}

.workspace-nav button.active {
  border-color: var(--theme--primary);
  box-shadow: inset 0 0 0 1px var(--theme--primary);
}

.workspace-nav strong {
  font-size: 15px;
}

.single-run-flow,
.workspace-layout {
  display: grid;
  gap: 16px;
}

.compare-layout {
  grid-template-columns: minmax(0, 1fr);
}

.main-stack,
.side-stack {
  display: grid;
  gap: 14px;
}

.compare-main-stack {
  max-width: 1480px;
}

.cw-source-banner,
.compare-tabs,
.judge-workspace {
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 14px;
}

.cw-source-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.compare-tabs {
  display: flex;
  gap: 10px;
  padding: 8px;
}

.compare-tabs button {
  min-height: 36px;
  padding: 0 14px;
  font-weight: 700;
}

.compare-tabs button.active {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.judge-workspace {
  display: grid;
  gap: 12px;
}

.judge-workspace-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.judge-workspace-header p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.judge-workspace-header h2 {
  margin: 4px 0 0;
  font-size: 20px;
}

.judge-workspace-header span {
  max-width: 420px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.5;
}

@media (max-width: 1200px) {
  .context-bar,
  .compare-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 860px) {
  .workspace-nav,
  .context-grid {
    grid-template-columns: 1fr;
  }
}
</style>
