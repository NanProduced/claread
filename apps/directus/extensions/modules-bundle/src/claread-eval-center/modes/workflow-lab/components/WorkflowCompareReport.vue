<script setup>
import ReviewNotesPanel from "../../../components/ReviewNotesPanel.vue";

defineProps({
  result: { type: Object, default: null },
  selectedCaseId: { type: String, default: "" },
});
const emit = defineEmits(["select-case"]);

function reportOf(result) {
  return result?.report || result;
}

function tone(verdict) {
  if (verdict === "win") return "success";
  if (verdict === "loss") return "danger";
  if (verdict === "manual_review" || verdict === "needs_review") return "warning";
  return "neutral";
}
</script>

<template>
  <section class="compare-report">
    <div v-if="!result" class="empty">先选择两条已完成 run 并生成差异报告，这里才会显示逐 case 的比较结果。</div>
    <template v-else>
      <header>
        <div>
          <p>{{ result.created ? "新生成的差异报告" : "已有差异报告" }}</p>
          <h2>{{ reportOf(result).baseline_run_id }} vs {{ reportOf(result).candidate_run_id }}</h2>
        </div>
        <span>{{ result.report_id || `vs-${reportOf(result).baseline_run_id}` }}</span>
      </header>

      <dl class="summary">
        <div><dt title="两侧共有 case 数。">总 case</dt><dd>{{ reportOf(result).total_cases }}</dd></div>
        <div><dt title="候选版本 deterministic 表现优于 baseline 的 case 数。">更好</dt><dd>{{ reportOf(result).wins }}</dd></div>
        <div><dt title="候选版本 deterministic 表现差于 baseline 的 case 数。">变差</dt><dd>{{ reportOf(result).losses }}</dd></div>
        <div><dt title="deterministic 信号无明显差异的 case 数。">持平</dt><dd>{{ reportOf(result).ties }}</dd></div>
      </dl>

      <div v-if="reportOf(result).identity_warnings?.length" class="warnings">
        <strong>运行身份提醒</strong>
        <p v-for="warning in reportOf(result).identity_warnings" :key="warning">{{ warning }}</p>
      </div>

      <div class="comparison-table">
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th title="deterministic compare 结论，不代表最终人工结论。">结论</th>
              <th title="Baseline 的硬失败/软失败数量。">Baseline 硬/软</th>
              <th title="Candidate 的硬失败/软失败数量。">候选版本 硬/软</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="comparison in reportOf(result).comparisons || []"
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
              <td data-label="结论"><span :class="tone(comparison.verdict)">{{ comparison.verdict }}</span></td>
              <td data-label="Baseline 硬/软">{{ comparison.baseline_hard_failures }}/{{ comparison.baseline_soft_failures }}</td>
              <td data-label="候选版本 硬/软">{{ comparison.candidate_hard_failures }}/{{ comparison.candidate_soft_failures }}</td>
              <td data-label="原因">{{ (comparison.reasons || []).join("; ") || "暂无" }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <ReviewNotesPanel
        target-type="ab_report"
        :target-id="`${reportOf(result).candidate_run_id}/vs-${reportOf(result).baseline_run_id}`"
        :run-id="reportOf(result).candidate_run_id"
        :ab-report-id="`vs-${reportOf(result).baseline_run_id}`"
        title="Compare Review"
      />
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
.empty {
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
}

.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.summary div {
  background: var(--theme--background);
  padding: 10px;
}

dd {
  margin: 4px 0 0;
  font-size: 18px;
  font-weight: 700;
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

.comparison-table {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
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

td span {
  border-radius: 999px;
  padding: 3px 7px;
  font-size: 11px;
  font-weight: 700;
}

.success { background: var(--theme--success-background); }
.warning { background: var(--theme--warning-background); }
.danger { background: var(--theme--danger-background); }
.neutral { background: var(--theme--background-subdued); }

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
