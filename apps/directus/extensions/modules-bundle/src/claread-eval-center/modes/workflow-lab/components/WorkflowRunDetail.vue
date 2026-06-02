<script setup>
import ReviewNotesPanel from "../../../components/ReviewNotesPanel.vue";
import CaseArtifactTable from "./CaseArtifactTable.vue";
import WorkflowJudgePanel from "./WorkflowJudgePanel.vue";

defineProps({
  detail: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  selectedCaseId: { type: String, default: "" },
  rubrics: { type: Array, default: () => [] },
  judgeRequests: { type: Array, default: () => [] },
  judgeSubmitting: { type: Boolean, default: false },
});
const emit = defineEmits(["select-case", "queue-judge", "refresh-judge"]);

function dash(value) {
  return value === null || value === undefined || value === "" ? "-" : value;
}
</script>

<template>
  <section class="run-detail">
    <div v-if="loading" class="empty">Loading run detail...</div>
    <div v-else-if="!detail" class="empty">Select a learning run to inspect cases and judge evidence.</div>
    <template v-else>
      <header class="detail-header">
        <div>
          <p>Run detail</p>
          <h2>{{ detail.summary?.run_id }}</h2>
        </div>
        <span :class="{ blocked: detail.summary?.topology_mode !== 'learning' }">
          {{ detail.summary?.topology_mode || "unknown topology" }}
        </span>
      </header>

      <dl class="metrics">
        <div><dt>Dataset</dt><dd>{{ dash(detail.summary?.dataset_id) }}</dd></div>
        <div><dt>Cases</dt><dd>{{ detail.summary?.total_cases ?? 0 }}</dd></div>
        <div><dt>Hard</dt><dd>{{ detail.summary?.hard_failure_count ?? "-" }}</dd></div>
        <div><dt>Soft</dt><dd>{{ detail.summary?.soft_failure_count ?? "-" }}</dd></div>
        <div><dt>Candidate</dt><dd>{{ dash(detail.summary?.prompt_variant_id) }}</dd></div>
        <div><dt>RAG</dt><dd>{{ dash(detail.summary?.rag_mode) }}</dd></div>
      </dl>

      <CaseArtifactTable
        :cases="detail.case_artifacts || []"
        :selected-case-id="selectedCaseId"
        @select="emit('select-case', $event)"
      />

      <WorkflowJudgePanel
        :run-id="detail.summary?.run_id || ''"
        :rubrics="rubrics"
        :requests="judgeRequests"
        :submitting="judgeSubmitting"
        :disabled="detail.summary?.topology_mode !== 'learning'"
        @queue="emit('queue-judge', $event)"
        @refresh="emit('refresh-judge')"
      />

      <ReviewNotesPanel
        v-if="detail.summary?.run_id"
        title="Run Review"
        target-type="workflow_run"
        :target-id="detail.summary.run_id"
        :run-id="detail.summary.run_id"
      />
    </template>
  </section>
</template>

<style scoped>
.run-detail {
  display: grid;
  gap: 14px;
}
.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.detail-header p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
.detail-header h2 {
  margin: 2px 0 0;
  font-size: 20px;
  overflow-wrap: anywhere;
}
.detail-header span {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 4px 8px;
  background: var(--theme--success-background);
  font-size: 12px;
  font-weight: 700;
}
.detail-header span.blocked {
  background: var(--theme--danger-background);
}
.metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}
.metrics div {
  min-width: 0;
  background: var(--theme--background);
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
.empty {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 18px;
  color: var(--theme--foreground-subdued);
}
@media (max-width: 980px) {
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
