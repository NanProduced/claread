<script setup>
import { computed, onMounted, ref, watch } from "vue";
import ResultBlock from "../components/ResultBlock.vue";

const runsEndpoint = "/eval-center/runs";
const props = defineProps({
  initialBaselineRunId: { type: String, default: "" },
  initialCandidateRunId: { type: String, default: "" },
});

const loading = ref(false);
const comparing = ref(false);
const error = ref("");
const runs = ref([]);
const baselineRunId = ref("");
const candidateRunId = ref("");
const report = ref(null);

const canCompare = computed(
  () => baselineRunId.value && candidateRunId.value && baselineRunId.value !== candidateRunId.value && !comparing.value,
);
const runOptions = computed(() =>
  runs.value.map((run) => ({
    text: `${run.run_id} · ${run.dataset_id || "no dataset"} · ${run.eval_purpose || "unknown"} · ${run.prompt_variant_id || run.prompt_version || "baseline"}`,
    value: run.run_id,
  })),
);
const comparisonSides = computed(() => [
  { label: "Baseline", run_id: baselineRunId.value },
  { label: "Candidate", run_id: candidateRunId.value },
]);

onMounted(() => {
  void refreshRuns();
});

watch(
  () => [props.initialBaselineRunId, props.initialCandidateRunId],
  () => {
    applyInitialSelection();
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

async function refreshRuns() {
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchJson(`${runsEndpoint}?limit=100`);
    runs.value = Array.isArray(data?.runs) ? data.runs : [];
    applyInitialSelection();
    if (!baselineRunId.value && runs.value.length > 1) baselineRunId.value = runs.value[1].run_id;
    if (!candidateRunId.value && runs.value.length) candidateRunId.value = runs.value[0].run_id;
  } catch (err) {
    error.value = err?.message || "读取 workflow runs 失败。";
  } finally {
    loading.value = false;
  }
}

function applyInitialSelection() {
  let changed = false;
  if (props.initialBaselineRunId && baselineRunId.value !== props.initialBaselineRunId) {
    baselineRunId.value = props.initialBaselineRunId;
    changed = true;
  }
  if (props.initialCandidateRunId && candidateRunId.value !== props.initialCandidateRunId) {
    candidateRunId.value = props.initialCandidateRunId;
    changed = true;
  }
  if (changed) report.value = null;
}

async function loadComparison() {
  if (!canCompare.value) return;
  comparing.value = true;
  error.value = "";
  report.value = null;
  try {
    const params = new URLSearchParams({
      baseline_run_id: baselineRunId.value,
      candidate_run_id: candidateRunId.value,
    });
    report.value = await fetchJson(`/eval-center/ab/compare?${params.toString()}`);
  } catch (err) {
    error.value = err?.message || "读取 A/B report 失败。请先为 candidate run 生成对应的 ab/vs-baseline report。";
  } finally {
    comparing.value = false;
  }
}

function dash(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function formatJson(value) {
  if (value == null) return "";
  return JSON.stringify(value, null, 2);
}

function identityDeltaSummary(delta) {
  if (!delta || typeof delta !== "object") return "—";
  return Object.keys(delta).join(", ") || "—";
}

function identityDeltaDetail(delta) {
  if (!delta || typeof delta !== "object") return [];
  const lines = [];
  for (const [identityKey, change] of Object.entries(delta)) {
    if (!change || typeof change !== "object") continue;
    const base = change.baseline;
    const cand = change.candidate;
    if (base && cand && typeof base === "object" && typeof cand === "object") {
      const allKeys = new Set([...Object.keys(base), ...Object.keys(cand)]);
      for (const field of allKeys) {
        const bv = base[field];
        const cv = cand[field];
        if (JSON.stringify(bv) !== JSON.stringify(cv)) {
          lines.push({
            identity: identityKey,
            field,
            baseline: bv == null ? "—" : (typeof bv === "object" ? JSON.stringify(bv) : String(bv)),
            candidate: cv == null ? "—" : (typeof cv === "object" ? JSON.stringify(cv) : String(cv)),
          });
        }
      }
    }
  }
  return lines;
}

function verdictClass(verdict) {
  return {
    "is-win": verdict === "win",
    "is-loss": verdict === "loss",
    "is-review": verdict === "manual_review",
  };
}
</script>

<template>
  <section class="ab-compare">
    <section class="compare-pane">
      <div class="section-heading">
        <div>
          <h2>A/B 对比</h2>
          <span>读取 candidate run 下的 `ab/vs-&lt;baseline&gt;.json`，不重新执行评测。</span>
        </div>
        <v-button small secondary :loading="loading" @click="refreshRuns">刷新 runs</v-button>
      </div>

      <p v-if="error" class="error-message">{{ error }}</p>

      <div class="selector-grid">
        <label class="field-block">
          <span>Baseline Run</span>
          <v-select
            v-model="baselineRunId"
            :items="runOptions"
            placeholder="选择 baseline"
          />
        </label>
        <label class="field-block">
          <span>Candidate Run</span>
          <v-select
            v-model="candidateRunId"
            :items="runOptions"
            placeholder="选择 candidate"
          />
        </label>
      </div>

      <div class="run-context">
        <div v-for="side in comparisonSides" :key="side.label">
          <span>{{ side.label }}</span>
          <strong>{{ dash(side.run_id) }}</strong>
          <small>
            {{
              dash(runs.find((item) => item.run_id === side.run_id)?.dataset_id)
            }}
            ·
            {{
              dash(runs.find((item) => item.run_id === side.run_id)?.prompt_variant_id || runs.find((item) => item.run_id === side.run_id)?.prompt_version)
            }}
          </small>
        </div>
      </div>

      <div class="action-row">
        <v-button :disabled="!canCompare" :loading="comparing" @click="loadComparison">
          读取 A/B Report
        </v-button>
      </div>
    </section>

    <section class="compare-pane">
      <div class="section-heading">
        <div>
          <h2>对比结果</h2>
          <span>{{ report ? `${report.baseline_run_id} vs ${report.candidate_run_id}` : "尚未读取 report" }}</span>
        </div>
      </div>

      <div v-if="report" class="summary-grid">
        <div>
          <span>Total</span>
          <strong>{{ report.total_cases }}</strong>
        </div>
        <div>
          <span>Wins</span>
          <strong>{{ report.wins }}</strong>
        </div>
        <div>
          <span>Losses</span>
          <strong>{{ report.losses }}</strong>
        </div>
        <div>
          <span>Ties</span>
          <strong>{{ report.ties }}</strong>
        </div>
        <div>
          <span>Manual Review</span>
          <strong>{{ report.manual_review }}</strong>
        </div>
        <div>
          <span>Regressions</span>
          <strong>{{ report.regression_case_ids?.length || 0 }}</strong>
        </div>
      </div>

      <template v-if="report">
        <div v-if="report.identity_warnings?.length" class="warning-list">
          <strong>Identity Warnings</strong>
          <span v-for="warning in report.identity_warnings" :key="warning">{{ warning }}</span>
        </div>

        <ResultBlock title="Case-Level Delta" :open="true">
          <div class="comparison-table">
            <div class="comparison-row comparison-head">
              <span>Case</span>
              <span>Verdict</span>
              <span>Baseline</span>
              <span>Candidate</span>
              <span>Identity</span>
              <span>Reason</span>
            </div>
            <template v-for="item in report.comparisons" :key="item.case_id">
              <div class="comparison-row" :class="{ 'is-loss': item.verdict === 'loss' }">
                <span>{{ item.case_id }}</span>
                <span class="verdict-pill" :class="verdictClass(item.verdict)">{{ item.verdict }}</span>
                <span>{{ item.baseline_hard_failures }}H / {{ item.baseline_soft_failures }}S · {{ dash(item.baseline_status) }}</span>
                <span>{{ item.candidate_hard_failures }}H / {{ item.candidate_soft_failures }}S · {{ dash(item.candidate_status) }}</span>
                <span>{{ identityDeltaSummary(item.identity_delta) }}</span>
                <span>{{ item.reasons?.join("; ") || "—" }}</span>
              </div>
              <details v-if="item.identity_delta" class="delta-detail">
                <summary>{{ identityDeltaSummary(item.identity_delta) }} delta</summary>
                <div class="delta-detail-row delta-detail-head">
                  <span>Identity</span>
                  <span>Field</span>
                  <span>Baseline</span>
                  <span>Candidate</span>
                </div>
                <div v-for="line in identityDeltaDetail(item.identity_delta)" :key="`${line.identity}-${line.field}`" class="delta-detail-row">
                  <span>{{ line.identity }}</span>
                  <span>{{ line.field }}</span>
                  <span>{{ line.baseline }}</span>
                  <span>{{ line.candidate }}</span>
                </div>
              </details>
            </template>
          </div>
        </ResultBlock>

        <ResultBlock title="Identity Delta JSON" :open="false">
          <pre>{{ formatJson(report.comparisons?.filter((item) => item.identity_delta).map((item) => ({
            case_id: item.case_id,
            identity_delta: item.identity_delta,
          }))) || "无 identity delta。" }}</pre>
        </ResultBlock>

        <ResultBlock title="A/B Report JSON" :open="false">
          <pre>{{ formatJson(report) }}</pre>
        </ResultBlock>
      </template>

      <div v-else class="empty-state">
        <h3>等待选择 run</h3>
        <p>先选择 baseline 和 candidate，再读取已经生成的 A/B report。生成 report 的执行入口后续接入。</p>
      </div>
    </section>
  </section>
</template>

<style scoped>
.ab-compare {
  display: grid;
  grid-template-columns: minmax(320px, 0.5fr) minmax(0, 1fr);
  gap: 24px;
}

.compare-pane {
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
.empty-state p {
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

.error-message {
  color: var(--theme--danger);
  font-size: 13px;
}

.selector-grid {
  display: grid;
  gap: 14px;
}

.field-block span {
  display: block;
  margin-bottom: 6px;
  color: var(--theme--foreground);
  font-size: 13px;
  font-weight: 700;
}

.run-context,
.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 16px;
}

.summary-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 0;
  margin-bottom: 16px;
}

.run-context div,
.summary-grid div {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px;
}

.run-context span,
.run-context small,
.summary-grid span {
  display: block;
  overflow: hidden;
  color: var(--theme--foreground-subdued);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.run-context strong,
.summary-grid strong {
  display: block;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.action-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
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

.comparison-row span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.comparison-head {
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}

.comparison-row.is-loss {
  border-color: var(--theme--danger);
}

.delta-detail {
  margin: 0 0 6px 20px;
  border-left: 3px solid var(--theme--primary);
  padding: 6px 0;
}

.delta-detail summary {
  margin: 0 8px 6px;
  color: var(--theme--primary);
  cursor: pointer;
  font-size: 11px;
  font-weight: 700;
}

.delta-detail-row {
  display: grid;
  grid-template-columns: minmax(100px, 1fr) minmax(100px, 1fr) minmax(120px, 1.2fr) minmax(120px, 1.2fr);
  gap: 8px;
  padding: 4px 8px;
  font-size: 11px;
}

.delta-detail-row span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.delta-detail-head {
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

.empty-state {
  border: 1px dashed var(--theme--border-color);
  border-radius: 8px;
  padding: 16px;
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
  .ab-compare {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .run-context,
  .summary-grid,
  .comparison-row {
    grid-template-columns: 1fr;
  }
}
</style>
