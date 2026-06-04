<script setup>
import { computed } from "vue";
import ReviewNotesPanel from "../../../components/ReviewNotesPanel.vue";
import SentenceCompareDiffView from "./SentenceCompareDiffView.vue";

const props = defineProps({
  result: { type: Object, default: null },
  selectedCaseId: { type: String, default: "" },
  baselineArtifact: { type: Object, default: null },
  candidateArtifact: { type: Object, default: null },
});
const emit = defineEmits(["select-case"]);

const report = computed(() => props.result?.report || props.result);

const selectedCase = computed(() => {
  if (!props.selectedCaseId) return null;
  return (report.value?.comparisons || []).find((c) => c.case_id === props.selectedCaseId) || null;
});

function comparisonPriority(comparison) {
  const verdict = comparison?.verdict;
  if (verdict === "loss") return 5000;
  if (verdict === "manual_review" || verdict === "needs_review") return 4000;
  if (verdict === "win") return 3000;
  const hardDelta = Math.abs((comparison?.candidate_hard_failures ?? 0) - (comparison?.baseline_hard_failures ?? 0));
  const softDelta = Math.abs((comparison?.candidate_soft_failures ?? 0) - (comparison?.baseline_soft_failures ?? 0));
  return hardDelta * 100 + softDelta * 10;
}

const sortedComparisons = computed(() => [...(report.value?.comparisons || [])].sort((a, b) => {
  const delta = comparisonPriority(b) - comparisonPriority(a);
  if (delta !== 0) return delta;
  return String(a.case_id || "").localeCompare(String(b.case_id || ""));
}));

const preparedSentences = computed(() => {
  const candidates = [
    props.baselineArtifact?.prepared_sentences,
    props.baselineArtifact?.input_snapshot?.prepared_sentences,
    props.candidateArtifact?.prepared_sentences,
    props.candidateArtifact?.input_snapshot?.prepared_sentences,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.length) return candidate;
  }
  return [];
});

function tone(verdict) {
  if (verdict === "win") return "success";
  if (verdict === "loss") return "danger";
  if (verdict === "manual_review" || verdict === "needs_review") return "warning";
  return "neutral";
}

function deltaSummary(comparison) {
  const bHard = comparison?.baseline_hard_failures ?? 0;
  const cHard = comparison?.candidate_hard_failures ?? 0;
  const bSoft = comparison?.baseline_soft_failures ?? 0;
  const cSoft = comparison?.candidate_soft_failures ?? 0;
  const hardDelta = cHard - bHard;
  const softDelta = cSoft - bSoft;
  const parts = [];
  if (hardDelta !== 0) parts.push(`硬失败 ${hardDelta > 0 ? "+" : ""}${hardDelta}`);
  if (softDelta !== 0) parts.push(`软失败 ${softDelta > 0 ? "+" : ""}${softDelta}`);
  return parts.length ? parts.join(" / ") : "无明显 delta";
}
</script>

<template>
  <section class="compare-report">
    <div v-if="!result" class="empty">先选择两条 run 并生成对比报告，这里才会显示逐 case 的 deterministic 差异。</div>
    <template v-else>
      <header>
        <div>
          <p>{{ result.created ? "新生成的对比报告" : "已有对比报告" }}</p>
          <h2>{{ report.baseline_run_id }} vs {{ report.candidate_run_id }}</h2>
        </div>
        <span>{{ result.report_id || `vs-${report.baseline_run_id}` }}</span>
      </header>

      <p class="disclaimer">
        当前展示的对比基于 deterministic 信号（硬/软失败 + adapter 状态 + warnings）。
        <strong>这只是信号，不等同于质量判断</strong>。结论性判断以 judge 评审和人工 review 为准。
      </p>

      <div v-if="report.identity_warnings?.length" class="warnings">
        <strong>运行身份提醒</strong>
        <p v-for="warning in report.identity_warnings" :key="warning">{{ warning }}</p>
      </div>

      <section v-if="selectedCase" class="case-focus">
        <header>
          <strong>当前 case：{{ selectedCase.case_id }}</strong>
          <button type="button" class="link-button" @click="emit('select-case', null)">清空当前 case</button>
        </header>
        <SentenceCompareDiffView
          :baseline-artifact="baselineArtifact"
          :candidate-artifact="candidateArtifact"
          :prepared-sentences="preparedSentences"
          :compare-case="selectedCase"
          empty-text="请先在左侧点击一个 case 加载证据。"
        />
        <div class="case-delta-note">
          <span :class="`verdict-pill is-${tone(selectedCase.verdict)}`">{{ selectedCase.verdict || "—" }}</span>
          <span>{{ deltaSummary(selectedCase) }}</span>
        </div>
      </section>

      <section class="comparison-section">
        <header class="section-head">
          <div>
            <p>Case 列表</p>
            <h3>{{ selectedCase ? "切换当前 case" : "先选择一个 case 再看句子差异" }}</h3>
          </div>
        </header>
        <div class="comparison-table">
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th title="deterministic 结论，仅供参考。">状态/结论</th>
              <th>Baseline 硬/软</th>
              <th>候选 硬/软</th>
              <th>Delta / 原因</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="comparison in sortedComparisons"
              :key="comparison.case_id"
              :class="{ active: selectedCaseId === comparison.case_id }"
            >
              <td data-label="Case">
                <button
                  type="button"
                  class="case-link"
                  :aria-current="selectedCaseId === comparison.case_id ? 'true' : undefined"
                  @click="emit('select-case', comparison)"
                >
                  {{ comparison.case_id }}
                </button>
              </td>
              <td data-label="状态/结论"><span :class="`verdict-pill is-${tone(comparison.verdict)}`">{{ comparison.verdict || "—" }}</span></td>
              <td data-label="Baseline 硬/软">{{ comparison.baseline_hard_failures ?? 0 }}/{{ comparison.baseline_soft_failures ?? 0 }}</td>
              <td data-label="候选 硬/软">{{ comparison.candidate_hard_failures ?? 0 }}/{{ comparison.candidate_soft_failures ?? 0 }}</td>
              <td data-label="Delta / 原因">
                <div class="delta-cell">
                  <strong>{{ deltaSummary(comparison) }}</strong>
                  <ul v-if="comparison.reasons?.length" class="reason-tags">
                    <li v-for="reason in comparison.reasons" :key="`${comparison.case_id}-${reason}`">{{ reason }}</li>
                  </ul>
                  <span v-else class="empty-reason">暂无</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!sortedComparisons.length" class="empty">本次对比没有可用的 case 差异。</p>
        </div>
      </section>

      <section class="auxiliary">
        <header>
          <strong>Deterministic 概览</strong>
          <small>辅助参考，不作为结论。</small>
        </header>
        <dl class="summary">
          <div><dt>总 case</dt><dd>{{ report.total_cases ?? "—" }}</dd></div>
          <div><dt>更好</dt><dd>{{ report.wins ?? 0 }}</dd></div>
          <div><dt>变差</dt><dd>{{ report.losses ?? 0 }}</dd></div>
          <div><dt>持平</dt><dd>{{ report.ties ?? 0 }}</dd></div>
        </dl>
      </section>

      <section class="review-block">
        <header class="section-head">
          <div>
            <p>人工 Review</p>
            <h3>记录人工判断，放在证据阅读之后</h3>
          </div>
        </header>
        <ReviewNotesPanel
          target-type="workflow_compare"
          :target-id="`${report.baseline_run_id}::${report.candidate_run_id}`"
          :run-id="report.candidate_run_id"
          title="Compare Review"
          scope-note="这类 note 挂在 baseline_run_id::candidate_run_id 这组 run pair 上，表达 compare-scope 判断；不是 case review，也不绑定某个 compare artifact 版本。"
        />
      </section>
    </template>
  </section>
</template>

<style scoped>
.compare-report {
  display: grid;
  gap: 14px;
}

header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

header p,
dt,
.empty,
.auxiliary small,
.disclaimer,
.slot-placeholder {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

header h2 {
  margin: 2px 0 0;
  font-size: 20px;
  overflow-wrap: anywhere;
}

header span {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 12px;
  background: var(--theme--background);
}

.disclaimer {
  margin-top: 4px;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  padding: 10px 12px;
  line-height: 1.6;
  font-weight: 400;
}

.disclaimer strong {
  color: var(--theme--foreground);
}

.warnings {
  border: 1px solid var(--theme--warning);
  border-radius: 8px;
  background: var(--theme--warning-background);
  padding: 10px;
}

.warnings p {
  margin: 6px 0 0;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.section-head p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.section-head h3 {
  margin: 2px 0 0;
  font-size: 15px;
  line-height: 1.45;
}

.case-focus {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 12px 14px;
  display: grid;
  gap: 12px;
}

.case-delta-note {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.case-focus header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.link-button {
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--primary);
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.auxiliary {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px 14px;
  display: grid;
  gap: 10px;
}

.comparison-section,
.review-block {
  display: grid;
  gap: 10px;
}

.auxiliary header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}

.summary div {
  background: var(--theme--background);
  padding: 10px;
}

.summary dd {
  margin: 4px 0 0;
  font-size: 16px;
  font-weight: 700;
  overflow-wrap: anywhere;
}

.comparison-table {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.delta-cell {
  display: grid;
  gap: 6px;
}

.delta-cell strong {
  color: var(--theme--foreground);
  font-size: 12px;
}

.reason-tags {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
}

.reason-tags li {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 999px;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  line-height: 1.4;
  padding: 2px 8px;
}

.empty-reason {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  border-bottom: 1px solid var(--theme--border-color);
  padding: 9px 10px;
  text-align: left;
  vertical-align: top;
}

th {
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

tbody tr.active {
  background: var(--theme--background-subdued);
}

.case-link {
  border: 0;
  background: transparent;
  color: var(--theme--primary);
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  padding: 0;
}

.case-link[aria-current="true"] {
  text-decoration: underline;
}

.verdict-pill {
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

.verdict-pill.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}

.verdict-pill.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}

.verdict-pill.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}

.verdict-pill.is-neutral {
  color: var(--theme--foreground-subdued);
}

@media (max-width: 980px) {
  .summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .comparison-table {
    border: 0;
    overflow: visible;
  }

  table,
  thead,
  tbody,
  tr,
  td {
    display: block;
    width: 100%;
  }

  thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
  }

  tbody {
    display: grid;
    gap: 10px;
  }

  tr {
    border: 1px solid var(--theme--border-color);
    border-radius: 8px;
    overflow: hidden;
    background: var(--theme--background);
  }

  td {
    display: grid;
    grid-template-columns: minmax(112px, 0.9fr) minmax(0, 1fr);
    gap: 10px;
  }

  td:last-child {
    border-bottom: 0;
  }

  td::before {
    content: attr(data-label);
    color: var(--theme--foreground-subdued);
    font-size: 12px;
    font-weight: 700;
  }
}
</style>
