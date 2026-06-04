<script setup>
import { computed } from "vue";
import ReviewNotesPanel from "../../../components/ReviewNotesPanel.vue";
import CaseArtifactTable from "./CaseArtifactTable.vue";
import WorkflowJudgePanel from "./WorkflowJudgePanel.vue";
import { dash, isLearningArtifact } from "../composables/workflowLabFormatting.js";

const props = defineProps({
  detail: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  selectedCaseId: { type: String, default: "" },
  rubrics: { type: Array, default: () => [] },
  judgeRequests: { type: Array, default: () => [] },
  judgeSubmitting: { type: Boolean, default: false },
});
const emit = defineEmits(["select-case", "queue-judge", "refresh-judge", "open-history"]);

const learningCases = computed(() => (props.detail?.case_artifacts || []).filter((item) => isLearningArtifact(item)));
const unsupportedCaseCount = computed(() => Math.max((props.detail?.case_artifacts || []).length - learningCases.value.length, 0));
const judgeDisabled = computed(() => learningCases.value.length === 0);
const summaryCards = computed(() => {
  const warnings = learningCases.value.reduce((sum, item) => sum + (item.warning_count ?? 0), 0);
  const hardFailures = learningCases.value.reduce((sum, item) => sum + (item.hard_failures ?? 0), 0);
  const softFailures = learningCases.value.reduce((sum, item) => sum + (item.soft_failures ?? 0), 0);
  return {
    totalCases: props.detail?.summary?.total_cases ?? 0,
    learningCases: props.detail?.summary?.learning_case_count ?? learningCases.value.length,
    warnings,
    hardFailures,
    softFailures,
  };
});
const topologyTone = computed(() => {
  if (judgeDisabled.value) return "blocked";
  if (props.detail?.summary?.topology_mode === "mixed") return "mixed";
  return "";
});
const topologyLabel = computed(() => {
  if (props.detail?.summary?.topology_mode === "mixed") return "mixed，已过滤为 learning";
  return props.detail?.summary?.topology_mode || "unknown topology";
});
</script>

<template>
  <section class="run-detail">
    <div v-if="loading" class="empty">正在读取这条运行的详情...</div>
    <div v-else-if="!detail" class="empty">从左侧选择一条 run 查看详情。</div>
    <template v-else>
      <header class="detail-header">
        <div>
          <p>运行详情</p>
          <h2>{{ detail.summary?.run_id }}</h2>
        </div>
        <div class="header-actions">
          <button
            v-if="detail.summary?.run_id"
            type="button"
            class="history-link"
            @click="emit('open-history', detail.summary.run_id)"
          >
            在 Run History 中打开
          </button>
          <span :class="topologyTone">
            {{ topologyLabel }}
          </span>
        </div>
      </header>

      <p v-if="detail.pending_message" class="pending-note">
        {{ detail.pending_message }}
      </p>

      <section class="summary-block">
        <dl class="metrics">
          <div><dt>数据集</dt><dd>{{ dash(detail.summary?.dataset_id) }}</dd></div>
          <div><dt>Candidate</dt><dd>{{ dash(detail.summary?.prompt_variant_id, "baseline") }}</dd></div>
          <div><dt>RAG</dt><dd>{{ dash(detail.summary?.rag_mode) }}</dd></div>
          <div><dt>Topology</dt><dd>{{ topologyLabel }}</dd></div>
          <div><dt>Status</dt><dd>{{ dash(detail.summary?.status || detail.summary?.adapter_status, "已落盘") }}</dd></div>
        </dl>
      </section>

      <section class="summary-block">
        <dl class="metrics metrics-health">
          <div><dt>总 Cases</dt><dd>{{ summaryCards.totalCases }}</dd></div>
          <div><dt>Learning</dt><dd>{{ summaryCards.learningCases }}</dd></div>
          <div><dt>Warnings</dt><dd>{{ summaryCards.warnings }}</dd></div>
          <div><dt>硬失败</dt><dd>{{ summaryCards.hardFailures }}</dd></div>
          <div><dt>软失败</dt><dd>{{ summaryCards.softFailures }}</dd></div>
        </dl>
      </section>

      <p v-if="unsupportedCaseCount > 0" class="unsupported-note">
        {{ unsupportedCaseCount }} 条非 learning case 未展示。
      </p>

      <section class="summary-block">
        <header class="block-head">
          <h3>Case 列表</h3>
        </header>
        <CaseArtifactTable
          :cases="learningCases"
          :selected-case-id="selectedCaseId"
          @select="emit('select-case', $event)"
        />
      </section>

      <section class="secondary-block">
        <header class="block-head">
          <h3>Judge 结果</h3>
        </header>
        <WorkflowJudgePanel
          :run-id="detail.summary?.run_id || ''"
          :rubrics="rubrics"
          :requests="judgeRequests"
          :submitting="judgeSubmitting"
          :disabled="judgeDisabled"
          @queue="emit('queue-judge', $event)"
          @refresh="emit('refresh-judge')"
        />
      </section>

      <section v-if="detail.summary?.run_id" class="secondary-block">
        <header class="block-head">
          <h3>人工 Review</h3>
        </header>
        <ReviewNotesPanel
          title="Run Review"
          target-type="workflow_run"
          :target-id="detail.summary.run_id"
          :run-id="detail.summary.run_id"
          scope-note="这类 note 挂在整条 workflow_run 上，用于记录 run-level 人工结论；不会自动拆分到 case 或 compare。"
        />
      </section>
    </template>
  </section>
</template>

<style scoped>
.run-detail {
  display: grid;
  gap: 14px;
}
.summary-block,
.secondary-block {
  display: grid;
  gap: 10px;
}
.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
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
.history-link {
  min-height: 30px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  padding: 4px 12px;
}
.detail-header span.blocked {
  background: var(--theme--danger-background);
}
.detail-header span.mixed {
  background: var(--theme--warning-background);
}
.block-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.block-head p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
.block-head h3 {
  margin: 2px 0 0;
  font-size: 15px;
  line-height: 1.45;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
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
.metrics-health div {
  background: var(--theme--background-subdued);
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
.unsupported-note {
  margin: -4px 0 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
}

.pending-note {
  margin: -2px 0 0;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  padding: 10px 12px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
}
@media (max-width: 980px) {
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
