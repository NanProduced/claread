<script setup>
import { computed, onMounted, ref, watch } from "vue";
import CandidatePanel from "./components/CandidatePanel.vue";
import CaseEvidenceInspector from "./components/CaseEvidenceInspector.vue";
import WorkflowCompareBuilder from "./components/WorkflowCompareBuilder.vue";
import WorkflowCompareReport from "./components/WorkflowCompareReport.vue";
import WorkflowRunDetail from "./components/WorkflowRunDetail.vue";
import WorkflowRunLauncher from "./components/WorkflowRunLauncher.vue";
import WorkflowRunQueue from "./components/WorkflowRunQueue.vue";
import WorkflowSingleRunLauncher from "./components/WorkflowSingleRunLauncher.vue";
import WorkflowSingleRunResult from "./components/WorkflowSingleRunResult.vue";
import { useWorkflowLabApi } from "./composables/useWorkflowLabApi.js";

const props = defineProps({
  initialBaselineRunId: { type: String, default: "" },
  initialCandidateRunId: { type: String, default: "" },
});
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

const activeView = ref("runs");
const activeRunTool = ref("single");
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
const selectedCompareCase = ref(null);

const baselineRunId = ref("");
const candidateRunId = ref("");
const compareLoading = ref(false);
const compareResult = ref(null);

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

const rubrics = ref([]);
const judgeRequests = ref([]);
const judgeSubmitting = ref(false);

const learningRuns = computed(() => runs.value.filter((run) => run.topology_mode === "learning"));
const selectedRunSummary = computed(() => selectedRunDetail.value?.summary || null);
const contextSummary = computed(() => ({
  dataset: selectedRunSummary.value?.dataset_id || "article-analysis-v1",
  baseline: baselineRunId.value || "-",
  candidate: candidateRunId.value || selectedRunSummary.value?.prompt_variant_id || "-",
  topology: selectedRunSummary.value?.topology_mode || "learning only",
}));

watch(
  () => [props.initialBaselineRunId, props.initialCandidateRunId],
  ([baseline, candidate]) => {
    if (baseline) baselineRunId.value = baseline;
    if (candidate) candidateRunId.value = candidate;
    if (baseline || candidate) activeView.value = "compare";
  },
  { immediate: true },
);

onMounted(async () => {
  await Promise.all([
    loadRuns(),
    loadRequests(),
    loadCandidates(),
    loadRubrics(),
    loadJudgeRequests(),
    loadModelProfiles(),
  ]);
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
      const firstLearning = learningRuns.value[0];
      if (firstLearning) await selectRun(firstLearning.run_id, { silentView: true });
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
  if (!options.silentView) activeView.value = "runs";
  runDetailLoading.value = true;
  selectedCaseId.value = "";
  selectedCaseArtifact.value = null;
  selectedCompareCase.value = null;
  try {
    selectedRunDetail.value = await workflowApi.getRunDetail(runId);
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
  error.value = "";
  message.value = "";
  try {
    compareResult.value = await workflowApi.createCompare({
      baseline_run_id: baselineRunId.value,
      candidate_run_id: candidateRunId.value,
    });
    message.value = compareResult.value.created ? "对比报告已生成。" : "已读取已有对比报告。";
    activeView.value = "compare";
    const firstComparison = compareResult.value.report?.comparisons?.[0];
    if (firstComparison) await selectCompareCase(firstComparison);
    await loadRuns({ keepSelection: true });
  } catch (err) {
    setError(err, "Failed to generate compare report.");
  } finally {
    compareLoading.value = false;
  }
}

async function selectCompareCase(comparison) {
  selectedCompareCase.value = comparison;
  const report = compareResult.value?.report;
  if (comparison?.case_id && report?.candidate_run_id) {
    await selectCase(comparison.case_id, report.candidate_run_id);
  }
}

async function loadCandidates() {
  candidateLoading.value = true;
  candidateError.value = "";
  try {
    const [ready, drafts] = await Promise.all([
      workflowApi.listReadyCandidates(),
      workflowApi.listCandidateDrafts(),
    ]);
    readyCandidates.value = ready;
    candidateDrafts.value = drafts;
    if (!selectedDraftId.value && drafts.length) selectDraft(drafts[0]);
  } catch (err) {
    candidateError.value = workflowApi.directusError(err, "Failed to load candidates.");
  } finally {
    candidateLoading.value = false;
  }
}

function newCandidate() {
  selectedDraftId.value = "";
  candidatePreview.value = null;
  candidateMessage.value = "";
  candidateError.value = "";
  candidateForm.value = emptyCandidateForm();
}

function selectDraft(draft) {
  selectedDraftId.value = draft.id || "";
  candidatePreview.value = null;
  candidateMessage.value = "";
  candidateError.value = "";
  candidateForm.value = candidateFormFromDraft(draft);
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

async function saveCandidate() {
  candidateSaving.value = true;
  candidateError.value = "";
  candidateMessage.value = "";
  try {
    const preview = await previewCandidate();
    if (!preview) return;
    const payload = candidatePayload({
      manifest_json: preview.manifest_json,
      snapshot_hash: preview.snapshot_hash,
    });
    const saved = await workflowApi.saveCandidateDraft(payload, selectedDraftId.value);
    if (!selectedDraftId.value && saved?.id) selectedDraftId.value = saved.id;
    candidateMessage.value = "Candidate 已保存。";
    await loadCandidates();
  } catch (err) {
    candidateError.value = workflowApi.directusError(err, "Failed to save candidate.");
  } finally {
    candidateSaving.value = false;
  }
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

async function loadJudgeRequests(runId = selectedRunId.value) {
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
</script>

<template>
  <section class="workflow-lab">
    <header class="context-bar">
      <div>
        <p>Workflow Lab</p>
        <h1>Learning Workflow 实验</h1>
      </div>
      <dl>
        <div><dt title="当前工作台只支持 learning workflow。">数据集</dt><dd>{{ contextSummary.dataset }}</dd></div>
        <div><dt title="Workflow Lab 暂不支持 academic topology。">拓扑</dt><dd>{{ contextSummary.topology }}</dd></div>
        <div><dt>Baseline</dt><dd>{{ contextSummary.baseline }}</dd></div>
        <div><dt>Candidate</dt><dd>{{ contextSummary.candidate }}</dd></div>
      </dl>
    </header>

    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-if="message" class="notice success">{{ message }}</p>

    <div class="workbench">
      <aside class="sidebar">
        <nav>
          <button type="button" :class="{ active: activeView === 'runs' }" title="创建 run、查看队列和 case 结果。" @click="activeView = 'runs'">运行</button>
          <button type="button" :class="{ active: activeView === 'compare' }" title="选择 baseline/candidate run 并同步生成对比报告。" @click="activeView = 'compare'">对比</button>
          <button type="button" :class="{ active: activeView === 'candidates' }" title="编辑并保存可用于 workflow run 的 Candidate。" @click="activeView = 'candidates'">Candidate</button>
        </nav>

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
            <strong>已完成的 learning runs</strong>
            <button type="button" :disabled="runsLoading" title="刷新本地 evals/runs artifact 列表。" @click="loadRuns({ keepSelection: true })">刷新</button>
          </header>
          <button
            v-for="run in learningRuns"
            :key="run.run_id"
            type="button"
            :class="{ active: run.run_id === selectedRunId }"
            @click="selectRun(run.run_id)"
          >
            <span>{{ run.run_id }}</span>
            <small>{{ run.prompt_variant_id || "baseline" }} / {{ run.total_cases || 0 }} cases</small>
          </button>
          <p v-if="!runsLoading && learningRuns.length === 0">暂无已完成的 learning run。</p>
        </section>
      </aside>

      <main class="main-pane">
        <template v-if="activeView === 'runs'">
          <section class="run-tools">
            <header>
              <div>
                <p>运行工作流</p>
                <h2>先用单条调试验证 Candidate</h2>
              </div>
              <nav>
                <button type="button" :class="{ active: activeRunTool === 'single' }" title="手动输入一篇文章，同步运行完整 workflow。" @click="activeRunTool = 'single'">单条调试</button>
                <button type="button" :class="{ active: activeRunTool === 'dataset' }" title="对已有 eval dataset 批量运行，适合稳定后回归。" @click="activeRunTool = 'dataset'">数据集批跑</button>
              </nav>
            </header>
            <p>先用单条调试确认 candidate 对完整 workflow 输出有改善；确认有效后再用数据集批跑做 regression。</p>
          </section>

          <template v-if="activeRunTool === 'single'">
            <WorkflowSingleRunLauncher
              :candidates="readyCandidates"
              :model-profiles="modelProfiles"
              :submitting="singleRunSubmitting"
              @submit="submitSingleRun"
            />
            <WorkflowSingleRunResult
              :result="singleRunResult"
              :loading="singleRunSubmitting"
            />
          </template>

          <WorkflowRunLauncher
            v-else
            :candidates="readyCandidates"
            :submitting="runSubmitting"
            @submit="submitRun"
          />
          <WorkflowRunDetail
            :detail="selectedRunDetail"
            :loading="runDetailLoading"
            :selected-case-id="selectedCaseId"
            :rubrics="rubrics"
            :judge-requests="judgeRequests"
            :judge-submitting="judgeSubmitting"
            @select-case="selectCase"
            @queue-judge="queueJudge"
            @refresh-judge="loadJudgeRequests"
          />
        </template>

        <template v-else-if="activeView === 'compare'">
          <WorkflowCompareBuilder
            v-model:baseline-run-id="baselineRunId"
            v-model:candidate-run-id="candidateRunId"
            :runs="runs"
            :loading="compareLoading"
            @compare="createCompare"
            @select-run="selectRun"
          />
          <WorkflowCompareReport
            :result="compareResult"
            :selected-case-id="selectedCompareCase?.case_id || ''"
            @select-case="selectCompareCase"
          />
        </template>

        <CandidatePanel
          v-else
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
          @refresh="loadCandidates"
          @new="newCandidate"
          @create-from-baseline="createCandidateFromBaseline"
          @select="selectDraft"
          @preview="previewCandidate"
          @save="saveCandidate"
        />
      </main>

      <CaseEvidenceInspector
        :artifact="selectedCaseArtifact"
        :compare-case="selectedCompareCase"
        :loading="caseLoading"
      />
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
  grid-template-columns: minmax(260px, 0.38fr) minmax(0, 1fr);
  gap: 16px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 16px;
}
.context-bar p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
.context-bar h1 {
  margin: 2px 0 0;
  font-size: 22px;
}
.context-bar dl {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}
.context-bar dl div {
  min-width: 0;
  background: var(--theme--background-subdued);
  padding: 9px;
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
  border-radius: 6px;
  margin: 0;
  padding: 10px 12px;
}
.notice.error {
  background: var(--theme--danger-background);
}
.notice.success {
  background: var(--theme--success-background);
}
.workbench {
  display: grid;
  grid-template-columns: minmax(260px, 0.24fr) minmax(0, 1fr) minmax(300px, 0.3fr);
  gap: 16px;
  align-items: start;
}
.sidebar,
.main-pane {
  display: grid;
  gap: 14px;
  min-width: 0;
}
nav {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
button {
  min-height: 34px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  padding: 7px 9px;
}
button.active {
  border-color: var(--theme--primary);
  background: var(--theme--background-subdued);
}
.run-tools {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 14px;
}
.run-tools header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.run-tools p,
.run-tools header p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
.run-tools > p {
  margin-top: 10px;
}
.run-tools h2 {
  margin: 2px 0 0;
  font-size: 17px;
}
.run-tools nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.run-tools nav button.active {
  color: var(--theme--primary);
}
.run-list {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 14px;
}
.run-list header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.run-list > button {
  display: block;
  width: 100%;
  margin-top: 8px;
  text-align: left;
}
.run-list span,
.run-list small {
  display: block;
  overflow-wrap: anywhere;
}
.run-list small,
.run-list p {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
@media (max-width: 1240px) {
  .workbench,
  .context-bar {
    grid-template-columns: 1fr;
  }
  .context-bar dl {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
