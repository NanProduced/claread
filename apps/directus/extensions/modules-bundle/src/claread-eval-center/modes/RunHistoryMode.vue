<script setup>
import { computed, onMounted, ref, watch } from "vue";
import ResultBlock from "../components/ResultBlock.vue";
import ReviewNotesPanel from "../components/ReviewNotesPanel.vue";

const runsEndpoint = "/eval-center/runs";
const nodeProbeRunsEndpoint =
  "/items/eval_node_probe_runs?sort=-date_created&limit=50";
const props = defineProps({
  initialRunId: { type: String, default: "" },
});
const emit = defineEmits(["compare-run"]);

const loading = ref(false);
const detailLoading = ref(false);
const error = ref("");
const activeSource = ref("workflow");
const runs = ref([]);
const selectedRunId = ref("");
const selectedRun = ref(null);
const selectedCaseArtifact = ref(null);
const selectedAbReport = ref(null);
const nodeProbeRuns = ref([]);
const selectedNodeProbeRun = ref(null);
const caseFilter = ref("all");

const filteredCaseArtifacts = computed(() => {
  const artifacts = selectedRun.value?.case_artifacts || [];
  if (caseFilter.value === "hard_failures") {
    return artifacts.filter((artifact) => Number(artifact.hard_failures || 0) > 0);
  }
  if (caseFilter.value === "soft_failures") {
    return artifacts.filter((artifact) => Number(artifact.soft_failures || 0) > 0);
  }
  if (caseFilter.value === "warnings") {
    return artifacts.filter((artifact) => Number(artifact.warning_count || 0) > 0);
  }
  if (caseFilter.value === "adapter_failed") {
    return artifacts.filter((artifact) => artifact.adapter_status && artifact.adapter_status !== "succeeded");
  }
  if (caseFilter.value === "prompt_variant") {
    return artifacts.filter((artifact) => artifact.prompt_identity?.prompt_variant_id);
  }
  return artifacts;
});

onMounted(() => {
  if (props.initialRunId) {
    void openWorkflowRun(props.initialRunId);
    return;
  }
  void refreshCurrent();
});

watch(
  () => props.initialRunId,
  (runId) => {
    if (runId) void openWorkflowRun(runId);
  },
);

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

async function refreshCurrent() {
  if (activeSource.value === "node_probe") {
    await refreshNodeProbeRuns();
    return;
  }
  await refreshWorkflowRuns();
}

async function refreshWorkflowRuns(options = {}) {
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchJson(runsEndpoint);
    runs.value = Array.isArray(data?.runs) ? data.runs : [];
    if (options.selectFirst !== false && !selectedRunId.value && runs.value.length) {
      await selectRun(runs.value[0].run_id);
    }
  } catch (err) {
    error.value = err?.message || "读取 eval runs 失败。";
  } finally {
    loading.value = false;
  }
}

async function openWorkflowRun(runId) {
  if (!runId) return;
  activeSource.value = "workflow";
  if (!runs.value.length) {
    await refreshWorkflowRuns({ selectFirst: false });
  }
  await selectRun(runId);
}

async function refreshNodeProbeRuns() {
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchJson(nodeProbeRunsEndpoint);
    nodeProbeRuns.value = Array.isArray(data) ? data : [];
    if (!selectedNodeProbeRun.value && nodeProbeRuns.value.length) {
      selectedNodeProbeRun.value = nodeProbeRuns.value[0];
    }
  } catch (err) {
    error.value = err?.message || "读取 Node Probe runs 失败。请先同步 eval_node_probe_runs metadata。";
  } finally {
    loading.value = false;
  }
}

async function selectRun(runId) {
  if (!runId) return;
  selectedRunId.value = runId;
  selectedCaseArtifact.value = null;
  selectedAbReport.value = null;
  caseFilter.value = "all";
  detailLoading.value = true;
  error.value = "";
  try {
    selectedRun.value = await fetchJson(`${runsEndpoint}/${encodeURIComponent(runId)}`);
  } catch (err) {
    selectedRun.value = null;
    error.value = err?.message || "读取 run detail 失败。";
  } finally {
    detailLoading.value = false;
  }
}

async function selectCaseArtifact(caseId) {
  if (!selectedRunId.value || !caseId) return;
  detailLoading.value = true;
  error.value = "";
  selectedAbReport.value = null;
  try {
    selectedCaseArtifact.value = await fetchJson(
      `${runsEndpoint}/${encodeURIComponent(selectedRunId.value)}/cases/${encodeURIComponent(caseId)}`,
    );
  } catch (err) {
    selectedCaseArtifact.value = null;
    error.value = err?.message || "读取 case artifact 失败。";
  } finally {
    detailLoading.value = false;
  }
}

async function selectAbReport(reportId) {
  if (!selectedRunId.value || !reportId) return;
  detailLoading.value = true;
  error.value = "";
  selectedCaseArtifact.value = null;
  try {
    selectedAbReport.value = await fetchJson(
      `${runsEndpoint}/${encodeURIComponent(selectedRunId.value)}/ab/${encodeURIComponent(reportId)}`,
    );
  } catch (err) {
    selectedAbReport.value = null;
    error.value = err?.message || "读取 A/B report 失败。";
  } finally {
    detailLoading.value = false;
  }
}

function formatJson(value) {
  if (value == null) return "";
  return JSON.stringify(value, null, 2);
}

function dash(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function formatSeconds(value) {
  return typeof value === "number" ? `${value.toFixed(2)}s` : "—";
}

function identityDeltaSummary(delta) {
  if (!delta || typeof delta !== "object") return "—";
  return Object.keys(delta).join(", ") || "—";
}

function failedGraderSummary(artifact) {
  const failed = (artifact.grader_summaries || []).filter((grader) => grader.verdict === "fail");
  if (!failed.length) return `${artifact.grader_count || 0} graders`;
  return failed.map((grader) => grader.grader_name || grader.metric || "grader").join(", ");
}

function verdictClass(verdict) {
  return {
    "is-win": verdict === "win",
    "is-loss": verdict === "loss",
    "is-review": verdict === "manual_review",
  };
}

function compareAsBaseline() {
  if (!selectedRunId.value) return;
  emit("compare-run", { baseline_run_id: selectedRunId.value });
}

function compareAsCandidate() {
  if (!selectedRunId.value) return;
  emit("compare-run", { candidate_run_id: selectedRunId.value });
}
</script>

<template>
  <section class="run-history">
    <section class="history-pane run-list">
      <div class="section-heading">
        <div>
          <h2>运行历史</h2>
          <span>Workflow 读取文件型 artifacts；Node Probe 读取用户主动保存的 eval 控制面表。</span>
        </div>
        <v-button small secondary :loading="loading" @click="refreshCurrent">刷新</v-button>
      </div>

      <p v-if="error" class="error-message">{{ error }}</p>

      <div class="source-tabs">
        <button
          type="button"
          :class="{ 'is-active': activeSource === 'workflow' }"
          @click="activeSource = 'workflow'; refreshCurrent()"
        >
          Workflow Runs
        </button>
        <button
          type="button"
          :class="{ 'is-active': activeSource === 'node_probe' }"
          @click="activeSource = 'node_probe'; refreshCurrent()"
        >
          Node Probe Runs
        </button>
      </div>

      <div v-if="activeSource === 'workflow' && !runs.length && !loading" class="empty-state">
        <h3>暂无可读 run</h3>
        <p>如果本地已有 `evals/runs`，请确认 Directus 容器已按最新 compose 重启，并挂载 `/directus/evals`。</p>
      </div>

      <template v-if="activeSource === 'workflow'">
        <button
          v-for="run in runs"
          :key="run.run_id"
          class="run-row"
          :class="{ 'is-active': selectedRunId === run.run_id }"
          type="button"
          @click="selectRun(run.run_id)"
        >
          <span>
            <strong>{{ run.run_id }}</strong>
            <small>{{ dash(run.dataset_id) }} · {{ dash(run.eval_purpose) }}</small>
          </span>
          <em>{{ run.total_cases }} cases</em>
        </button>
      </template>

      <div v-if="activeSource === 'node_probe' && !nodeProbeRuns.length && !loading" class="empty-state">
        <h3>暂无 Node Probe 保存记录</h3>
        <p>在 Node Probe 页面运行后点击“保存本次结果”，这里会显示保存的 prompt 实验记录。</p>
      </div>

      <template v-if="activeSource === 'node_probe'">
        <button
          v-for="run in nodeProbeRuns"
          :key="run.id"
          class="run-row"
          :class="{ 'is-active': selectedNodeProbeRun?.id === run.id }"
          type="button"
          @click="selectedNodeProbeRun = run"
        >
          <span>
            <strong>{{ run.node_name }} · {{ run.reading_variant }}</strong>
            <small>{{ dash(run.prompt_mode) }} · {{ dash(run.date_created) }}</small>
          </span>
          <em>{{ run.status }}</em>
        </button>
      </template>
    </section>

    <section class="history-pane run-detail">
      <div class="section-heading">
        <div>
          <h2>Run Detail</h2>
          <span>{{ activeSource === 'workflow' ? selectedRunId || "未选择 run" : selectedNodeProbeRun?.id || "未选择记录" }}</span>
        </div>
      </div>

      <p v-if="detailLoading" class="muted-line">正在读取 run artifacts...</p>

      <template v-if="activeSource === 'workflow' && selectedRun">
        <div class="summary-grid">
          <div>
            <span>Dataset</span>
            <strong>{{ dash(selectedRun.summary.dataset_id) }}</strong>
          </div>
          <div>
            <span>Purpose</span>
            <strong>{{ dash(selectedRun.summary.eval_purpose) }}</strong>
          </div>
          <div>
            <span>Cases</span>
            <strong>{{ selectedRun.summary.total_cases }}</strong>
            <small>{{ selectedRun.summary.case_artifact_count }} artifacts</small>
          </div>
          <div>
            <span>Failed</span>
            <strong>{{ dash(selectedRun.summary.failed) }}</strong>
          </div>
          <div>
            <span>Regressions</span>
            <strong>{{ dash(selectedRun.summary.regression_count) }}</strong>
            <small>{{ dash(selectedRun.summary.hard_failure_count) }} hard cases</small>
          </div>
          <div>
            <span>Trace</span>
            <strong>{{ dash(selectedRun.summary.trace_scope) }}</strong>
            <small>{{ dash(selectedRun.summary.rag_mode) }}</small>
          </div>
          <div>
            <span>Prompt Variant</span>
            <strong>{{ dash(selectedRun.summary.prompt_variant_id) }}</strong>
          </div>
          <div>
            <span>A/B Reports</span>
            <strong>{{ selectedRun.summary.ab_report_count }}</strong>
          </div>
        </div>

        <div class="run-action-bar">
          <button type="button" @click="compareAsBaseline">Compare as baseline</button>
          <button type="button" @click="compareAsCandidate">Compare as candidate</button>
        </div>

        <ReviewNotesPanel
          title="Run Review Notes"
          target-type="workflow_run"
          :target-id="selectedRunId"
          :run-id="selectedRunId"
          :prompt-variant-id="selectedRun.summary.prompt_variant_id || ''"
        />

        <ResultBlock title="Case Artifacts" :open="true">
          <div class="case-filter-bar">
            <label>
              <span>Case filter</span>
              <select v-model="caseFilter">
                <option value="all">All cases</option>
                <option value="hard_failures">Hard failures</option>
                <option value="soft_failures">Soft failures</option>
                <option value="warnings">Warnings</option>
                <option value="adapter_failed">Adapter failed</option>
                <option value="prompt_variant">Prompt variant</option>
              </select>
            </label>
            <small>{{ filteredCaseArtifacts.length }} / {{ selectedRun.case_artifacts.length }} cases</small>
          </div>
          <div class="case-table">
            <div class="case-row case-head">
              <span>Case</span>
              <span>Status</span>
              <span>Failures</span>
              <span>Tokens</span>
              <span>Prompt</span>
            </div>
            <button
              v-for="artifact in filteredCaseArtifacts"
              :key="artifact.case_id"
              class="case-row"
              :class="{ 'is-active': selectedCaseArtifact?.case_id === artifact.case_id }"
              type="button"
              @click="selectCaseArtifact(artifact.case_id)"
            >
              <span>{{ artifact.case_id }}</span>
              <span>
                {{ dash(artifact.adapter_status) }}
                <small>{{ artifact.warning_count }} warnings · {{ artifact.drop_count }} drops</small>
              </span>
              <span>
                {{ artifact.hard_failures }} hard / {{ artifact.soft_failures }} soft
                <small>{{ failedGraderSummary(artifact) }}</small>
              </span>
              <span>
                {{ dash(artifact.total_tokens) }}
                <small>{{ formatSeconds(artifact.latency_seconds) }}</small>
              </span>
              <span>{{ dash(artifact.prompt_identity?.prompt_variant_id || artifact.prompt_identity?.prompt_version) }}</span>
            </button>
          </div>
        </ResultBlock>

        <ResultBlock title="Selected Case Artifact" :open="Boolean(selectedCaseArtifact)">
          <template v-if="selectedCaseArtifact">
            <ReviewNotesPanel
              title="Case Review Notes"
              target-type="case_artifact"
              :target-id="`${selectedRunId}/${selectedCaseArtifact.case_id}`"
              :run-id="selectedRunId"
              :case-id="selectedCaseArtifact.case_id"
              :prompt-variant-id="selectedCaseArtifact.prompt_identity?.prompt_variant_id || ''"
            />

            <div class="summary-grid compact">
              <div>
                <span>Case</span>
                <strong>{{ selectedCaseArtifact.case_id }}</strong>
              </div>
              <div>
                <span>Status</span>
                <strong>{{ dash(selectedCaseArtifact.adapter_status) }}</strong>
              </div>
              <div>
                <span>User State</span>
                <strong>{{ dash(selectedCaseArtifact.user_facing_state) }}</strong>
              </div>
              <div>
                <span>Translations</span>
                <strong>{{ selectedCaseArtifact.translations?.length || 0 }}</strong>
              </div>
              <div>
                <span>Inline Marks</span>
                <strong>{{ selectedCaseArtifact.inline_marks?.length || 0 }}</strong>
              </div>
              <div>
                <span>Sentence Entries</span>
                <strong>{{ selectedCaseArtifact.sentence_entries?.length || 0 }}</strong>
              </div>
            </div>

            <ResultBlock title="Output / Render Scene" :open="false">
              <pre>{{ formatJson(selectedCaseArtifact.output) || "暂无 output。" }}</pre>
            </ResultBlock>

            <ResultBlock title="Graders / Warnings" :open="true">
              <div class="grader-table">
                <div class="grader-row grader-head">
                  <span>Grader</span>
                  <span>Verdict</span>
                  <span>Severity</span>
                  <span>Metric</span>
                  <span>Evidence</span>
                </div>
                <div
                  v-for="grader in selectedCaseArtifact.grader_results || []"
                  :key="`${grader.grader_name}-${grader.metric}`"
                  class="grader-row"
                >
                  <span>{{ dash(grader.grader_name) }}</span>
                  <span class="verdict-pill" :class="{ 'is-loss': grader.verdict === 'fail', 'is-win': grader.verdict === 'pass' }">
                    {{ dash(grader.verdict) }}
                  </span>
                  <span>{{ dash(grader.severity) }}</span>
                  <span>{{ dash(grader.metric) }}</span>
                  <span>{{ dash(grader.evidence || grader.reason || grader.message) }}</span>
                </div>
              </div>

              <div class="evidence-list">
                <section>
                  <strong>Warnings</strong>
                  <p v-if="!(selectedCaseArtifact.warnings || []).length" class="muted-line">无 warnings。</p>
                  <code v-for="warning in selectedCaseArtifact.warnings || []" :key="warning.code || warning.message">
                    {{ warning.code || "warning" }} · {{ warning.message || formatJson(warning) }}
                  </code>
                </section>
                <section>
                  <strong>Drop Log</strong>
                  <p v-if="!(selectedCaseArtifact.drop_log || []).length" class="muted-line">无 drop log。</p>
                  <code v-for="drop in selectedCaseArtifact.drop_log || []" :key="drop.code || drop.reason || formatJson(drop)">
                    {{ drop.code || "drop" }} · {{ drop.reason || drop.message || formatJson(drop) }}
                  </code>
                </section>
                <section v-if="selectedCaseArtifact.error">
                  <strong>Error</strong>
                  <code>{{ selectedCaseArtifact.error.code || "error" }} · {{ selectedCaseArtifact.error.message || formatJson(selectedCaseArtifact.error) }}</code>
                </section>
              </div>
            </ResultBlock>

            <ResultBlock title="Runtime / Identity" :open="true">
              <div class="identity-panels">
                <section>
                  <h3>Prompt</h3>
                  <dl>
                    <dt>Version</dt>
                    <dd>{{ dash(selectedCaseArtifact.prompt_identity?.prompt_version) }}</dd>
                    <dt>Variant</dt>
                    <dd>{{ dash(selectedCaseArtifact.prompt_identity?.prompt_variant_id) }}</dd>
                    <dt>Snapshot</dt>
                    <dd>{{ dash(selectedCaseArtifact.prompt_identity?.prompt_snapshot_hash) }}</dd>
                  </dl>
                </section>
                <section>
                  <h3>Model</h3>
                  <dl>
                    <dt>Provider</dt>
                    <dd>{{ dash(selectedCaseArtifact.model_identity?.provider) }}</dd>
                    <dt>Model</dt>
                    <dd>{{ dash(selectedCaseArtifact.model_identity?.model_name) }}</dd>
                    <dt>Profile</dt>
                    <dd>{{ dash(selectedCaseArtifact.model_identity?.profile_name) }}</dd>
                  </dl>
                </section>
                <section>
                  <h3>Workflow</h3>
                  <dl>
                    <dt>Name</dt>
                    <dd>{{ dash(selectedCaseArtifact.workflow_identity?.workflow_name) }}</dd>
                    <dt>Version</dt>
                    <dd>{{ dash(selectedCaseArtifact.workflow_identity?.workflow_version) }}</dd>
                    <dt>Topology</dt>
                    <dd>{{ dash(selectedCaseArtifact.workflow_identity?.topology_mode) }}</dd>
                  </dl>
                </section>
                <section>
                  <h3>Runtime</h3>
                  <dl>
                    <dt>Tokens</dt>
                    <dd>{{ dash(selectedCaseArtifact.usage_summary?.total_tokens) }}</dd>
                    <dt>Latency</dt>
                    <dd>{{ formatSeconds(selectedCaseArtifact.latency_seconds) }}</dd>
                    <dt>Trace</dt>
                    <dd>{{ dash(selectedCaseArtifact.trace_refs?.request_id) }}</dd>
                  </dl>
                </section>
              </div>
            </ResultBlock>

            <ResultBlock title="Full Case Artifact JSON" :open="false">
              <pre>{{ formatJson(selectedCaseArtifact) }}</pre>
            </ResultBlock>
          </template>
          <p v-else class="muted-line">点击上方 case 行读取完整 artifact。</p>
        </ResultBlock>

        <ResultBlock title="A/B Reports" :open="Boolean(selectedRun.ab_reports.length)">
          <div v-if="selectedRun.ab_reports.length" class="ab-list">
            <button
              v-for="report in selectedRun.ab_reports"
              :key="report.id"
              type="button"
              :class="{ 'is-active': selectedAbReport?.baseline_run_id && report.id === `vs-${selectedAbReport.baseline_run_id}` }"
              @click="selectAbReport(report.id)"
            >
              {{ report.id }}
            </button>
          </div>
          <p v-else class="muted-line">该 run 下没有 `ab/*.json`。</p>
        </ResultBlock>

        <ResultBlock title="Selected A/B Report" :open="Boolean(selectedAbReport)">
          <template v-if="selectedAbReport">
            <ReviewNotesPanel
              title="A/B Review Notes"
              target-type="ab_report"
              :target-id="`${selectedRunId}/${selectedAbReport.baseline_run_id}`"
              :run-id="selectedRunId"
              :ab-report-id="`vs-${selectedAbReport.baseline_run_id}`"
            />

            <div class="summary-grid compact">
              <div>
                <span>Baseline</span>
                <strong>{{ selectedAbReport.baseline_run_id }}</strong>
                <small>{{ dash(selectedAbReport.baseline_dataset_id) }}</small>
              </div>
              <div>
                <span>Candidate</span>
                <strong>{{ selectedAbReport.candidate_run_id }}</strong>
                <small>{{ dash(selectedAbReport.candidate_dataset_id) }}</small>
              </div>
              <div>
                <span>Cases</span>
                <strong>{{ selectedAbReport.total_cases }}</strong>
              </div>
              <div>
                <span>Wins</span>
                <strong>{{ selectedAbReport.wins }}</strong>
              </div>
              <div>
                <span>Losses</span>
                <strong>{{ selectedAbReport.losses }}</strong>
              </div>
              <div>
                <span>Manual Review</span>
                <strong>{{ selectedAbReport.manual_review }}</strong>
              </div>
            </div>

            <div v-if="selectedAbReport.identity_warnings?.length" class="warning-list">
              <strong>Identity Warnings</strong>
              <span v-for="warning in selectedAbReport.identity_warnings" :key="warning">{{ warning }}</span>
            </div>

            <div class="comparison-table">
              <div class="comparison-row comparison-head">
                <span>Case</span>
                <span>Verdict</span>
                <span>Baseline</span>
                <span>Candidate</span>
                <span>Identity</span>
                <span>Reason</span>
              </div>
              <div
                v-for="item in selectedAbReport.comparisons"
                :key="item.case_id"
                class="comparison-row"
              >
                <span>{{ item.case_id }}</span>
                <span class="verdict-pill" :class="verdictClass(item.verdict)">{{ item.verdict }}</span>
                <span>{{ item.baseline_hard_failures }}H / {{ item.baseline_soft_failures }}S · {{ dash(item.baseline_status) }}</span>
                <span>{{ item.candidate_hard_failures }}H / {{ item.candidate_soft_failures }}S · {{ dash(item.candidate_status) }}</span>
                <span>{{ identityDeltaSummary(item.identity_delta) }}</span>
                <span>{{ item.reasons?.join("; ") || "—" }}</span>
              </div>
            </div>

            <ResultBlock title="Identity Delta JSON" :open="false">
              <pre>{{ formatJson(selectedAbReport.comparisons?.filter((item) => item.identity_delta).map((item) => ({
                case_id: item.case_id,
                identity_delta: item.identity_delta,
              }))) || "无 identity delta。" }}</pre>
            </ResultBlock>

            <ResultBlock title="A/B Report JSON" :open="false">
              <pre>{{ formatJson(selectedAbReport) }}</pre>
            </ResultBlock>
          </template>
          <p v-else class="muted-line">点击 A/B report 读取对比结果。</p>
        </ResultBlock>

        <ResultBlock title="Report JSON" :open="false">
          <pre>{{ formatJson(selectedRun.report) || "暂无 report.json。" }}</pre>
        </ResultBlock>

        <ResultBlock title="Run JSON" :open="false">
          <pre>{{ formatJson(selectedRun.run) }}</pre>
        </ResultBlock>
      </template>

      <template v-if="activeSource === 'node_probe' && selectedNodeProbeRun">
        <div class="summary-grid">
          <div>
            <span>Node</span>
            <strong>{{ dash(selectedNodeProbeRun.node_name) }}</strong>
          </div>
          <div>
            <span>Status</span>
            <strong>{{ dash(selectedNodeProbeRun.status) }}</strong>
          </div>
          <div>
            <span>Variant</span>
            <strong>{{ dash(selectedNodeProbeRun.reading_variant) }}</strong>
          </div>
          <div>
            <span>Prompt Mode</span>
            <strong>{{ dash(selectedNodeProbeRun.prompt_mode) }}</strong>
          </div>
          <div>
            <span>Prompt Variant</span>
            <strong>{{ dash(selectedNodeProbeRun.prompt_variant_id) }}</strong>
          </div>
          <div>
            <span>Dry Run</span>
            <strong>{{ selectedNodeProbeRun.dry_run ? "yes" : "no" }}</strong>
          </div>
        </div>

        <ResultBlock title="Human Review" :open="true">
          <div class="review-box">
            <strong>{{ dash(selectedNodeProbeRun.human_verdict) }}</strong>
            <p>{{ selectedNodeProbeRun.human_notes || "暂无人工备注。" }}</p>
          </div>
        </ResultBlock>

        <ResultBlock title="Identity / Observations">
          <pre>{{ formatJson({
            prompt_identity: selectedNodeProbeRun.prompt_identity_json,
            model_identity: selectedNodeProbeRun.model_identity_json,
            workflow_identity: selectedNodeProbeRun.workflow_identity_json,
            schema_identity: selectedNodeProbeRun.schema_identity_json,
            preprocess_summary: selectedNodeProbeRun.preprocess_summary_json,
            example_summary: selectedNodeProbeRun.example_summary_json,
            warnings: selectedNodeProbeRun.warnings_json,
            runtime_summary: selectedNodeProbeRun.runtime_summary_json,
            trace_refs: selectedNodeProbeRun.trace_refs_json,
            error: selectedNodeProbeRun.error_json,
          }) }}</pre>
        </ResultBlock>

        <ResultBlock title="Prompt Preview" :open="true">
          <pre>{{ selectedNodeProbeRun.prompt_preview || "暂无 prompt preview。" }}</pre>
        </ResultBlock>

        <ResultBlock title="Node Output" :open="false">
          <pre>{{ formatJson(selectedNodeProbeRun.node_output_json) || "暂无节点输出。" }}</pre>
        </ResultBlock>

        <ResultBlock title="Saved Record JSON" :open="false">
          <pre>{{ formatJson(selectedNodeProbeRun) }}</pre>
        </ResultBlock>
      </template>
    </section>
  </section>
</template>

<style scoped>
.run-history {
  display: grid;
  grid-template-columns: minmax(280px, 0.45fr) minmax(0, 1fr);
  gap: 24px;
}

.history-pane {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 20px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.section-heading h2,
.empty-state h3 {
  margin: 0;
}

.section-heading span,
.muted-line,
.empty-state p {
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

.error-message {
  color: var(--theme--danger);
  font-size: 13px;
}

.empty-state {
  border: 1px dashed var(--theme--border-color);
  border-radius: 8px;
  padding: 16px;
}

.source-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.run-action-bar,
.case-filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}

.run-action-bar button {
  border: 1px solid var(--theme--primary);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--primary);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  padding: 6px 10px;
}

.case-filter-bar label {
  display: flex;
  align-items: center;
  gap: 8px;
}

.case-filter-bar span,
.case-filter-bar small {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.case-filter-bar select {
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 12px;
  padding: 6px 8px;
}

.source-tabs button {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background);
  padding: 6px 10px;
  color: var(--theme--foreground-subdued);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
}

.source-tabs button.is-active {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.run-row {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 10px;
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.run-row + .run-row {
  margin-top: 8px;
}

.run-row:hover,
.run-row.is-active {
  border-color: var(--theme--primary);
}

.run-row strong,
.run-row small {
  display: block;
}

.run-row small,
.run-row em {
  overflow: hidden;
  color: var(--theme--foreground-subdued);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-style: normal;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
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

.case-table,
.grader-table {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}

.case-row {
  display: grid;
  grid-template-columns: minmax(160px, 1.4fr) repeat(4, minmax(86px, 1fr));
  gap: 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 8px;
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  text-align: left;
}

.case-row:hover,
.case-row.is-active {
  border-color: var(--theme--primary);
}

.case-row span,
.grader-row span,
.comparison-row span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-row small {
  display: block;
  margin-top: 2px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.case-head {
  color: var(--theme--foreground-subdued);
  cursor: default;
  font-weight: 700;
}

.grader-row {
  display: grid;
  grid-template-columns:
    minmax(120px, 1fr) minmax(80px, 0.6fr) minmax(80px, 0.6fr) minmax(120px, 1fr)
    minmax(220px, 1.8fr);
  gap: 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px;
  font-size: 12px;
}

.grader-head {
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}

.evidence-list {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.evidence-list section {
  display: grid;
  gap: 6px;
}

.evidence-list strong {
  font-size: 13px;
}

.evidence-list code {
  display: block;
  overflow: hidden;
  border-radius: 6px;
  background: var(--theme--background-subdued);
  padding: 6px 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.identity-panels {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.identity-panels section {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px;
}

.identity-panels h3 {
  margin: 0 0 8px;
  font-size: 13px;
}

.identity-panels dl {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 6px 8px;
  margin: 0;
}

.identity-panels dt {
  color: var(--theme--foreground-subdued);
}

.identity-panels dd {
  overflow: hidden;
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ab-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.ab-list button {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background);
  padding: 4px 8px;
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}

.ab-list button:hover,
.ab-list button.is-active {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.warning-list {
  display: grid;
  gap: 6px;
  margin-bottom: 14px;
}

.warning-list strong {
  font-size: 13px;
}

.warning-list span {
  border-left: 3px solid var(--theme--warning);
  padding-left: 8px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.comparison-table {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}

.comparison-row {
  display: grid;
  grid-template-columns:
    minmax(120px, 1fr) minmax(76px, 0.6fr) repeat(2, minmax(120px, 1fr))
    minmax(100px, 0.8fr) minmax(180px, 1.5fr);
  gap: 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px;
  font-size: 12px;
}

.comparison-head {
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}

.verdict-pill {
  display: inline-flex;
  width: fit-content;
  max-width: 100%;
  border-radius: 999px;
  background: var(--theme--background-subdued);
  padding: 2px 8px;
  font-weight: 700;
}

.verdict-pill.is-win {
  background: var(--theme--success-background);
}

.verdict-pill.is-loss {
  background: var(--theme--danger-background);
}

.verdict-pill.is-review {
  background: var(--theme--warning-background);
}

.review-box {
  margin-top: 12px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px;
}

.review-box p {
  margin: 6px 0 0;
  color: var(--theme--foreground-subdued);
  line-height: 1.6;
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
  .run-history {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .summary-grid,
  .summary-grid.compact,
  .grader-row,
  .identity-panels,
  .comparison-row,
  .case-row {
    grid-template-columns: 1fr;
  }
}
</style>
