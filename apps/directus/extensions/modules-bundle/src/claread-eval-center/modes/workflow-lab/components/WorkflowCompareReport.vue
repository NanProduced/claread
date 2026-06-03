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
    <div v-if="!result" class="empty">生成或选择一条 compare report 后，可查看 deterministic delta。</div>
    <template v-else>
      <header>
        <div>
          <p>{{ result.created ? "新生成对比" : "已有对比" }}</p>
          <h2>{{ reportOf(result).baseline_run_id }} vs {{ reportOf(result).candidate_run_id }}</h2>
        </div>
        <span>{{ result.report_id || `vs-${reportOf(result).baseline_run_id}` }}</span>
      </header>

      <dl class="summary">
        <div><dt title="两侧共有 case 数。">总数</dt><dd>{{ reportOf(result).total_cases }}</dd></div>
        <div><dt title="candidate deterministic 表现优于 baseline 的 case 数。">Win</dt><dd>{{ reportOf(result).wins }}</dd></div>
        <div><dt title="candidate deterministic 表现差于 baseline 的 case 数。">Loss</dt><dd>{{ reportOf(result).losses }}</dd></div>
        <div><dt title="deterministic 信号无明显差异的 case 数。">Tie</dt><dd>{{ reportOf(result).ties }}</dd></div>
      </dl>

      <div v-if="reportOf(result).identity_warnings?.length" class="warnings">
        <strong>身份差异提醒</strong>
        <p v-for="warning in reportOf(result).identity_warnings" :key="warning">{{ warning }}</p>
      </div>

      <div class="comparison-table">
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th title="deterministic compare 结论，不代表最终人工结论。">结论</th>
              <th title="Baseline 的硬失败/软失败数量。">Baseline 硬/软</th>
              <th title="Candidate 的硬失败/软失败数量。">Candidate 硬/软</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="comparison in reportOf(result).comparisons || []"
              :key="comparison.case_id"
              :class="{ active: selectedCaseId === comparison.case_id }"
              @click="emit('select-case', comparison)"
            >
              <td><button type="button">{{ comparison.case_id }}</button></td>
              <td><span :class="tone(comparison.verdict)">{{ comparison.verdict }}</span></td>
              <td>{{ comparison.baseline_hard_failures }}/{{ comparison.baseline_soft_failures }}</td>
              <td>{{ comparison.candidate_hard_failures }}/{{ comparison.candidate_soft_failures }}</td>
              <td>{{ (comparison.reasons || []).join("; ") }}</td>
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
  border-radius: 6px;
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
  border-radius: 6px;
  background: var(--theme--warning-background);
  padding: 10px;
}
.warnings p {
  margin: 6px 0 0;
}
.comparison-table {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: auto;
}
table {
  width: 100%;
  min-width: 780px;
  border-collapse: collapse;
}
th,
td {
  border-bottom: 1px solid var(--theme--border-color);
  padding: 9px 10px;
  text-align: left;
}
th {
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
tbody tr {
  cursor: pointer;
}
tbody tr:hover,
tbody tr.active {
  background: var(--theme--background-subdued);
}
button {
  border: 0;
  background: transparent;
  color: var(--theme--primary);
  cursor: pointer;
  font: inherit;
  padding: 0;
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
</style>
