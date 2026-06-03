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
const emit = defineEmits(["select-case", "queue-judge", "refresh-judge"]);

const learningCases = computed(() => (props.detail?.case_artifacts || []).filter((item) => isLearningArtifact(item)));
const unsupportedCaseCount = computed(() => Math.max((props.detail?.case_artifacts || []).length - learningCases.value.length, 0));
const judgeDisabled = computed(() => learningCases.value.length === 0);
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
    <div v-if="loading" class="empty">正在读取这条回归任务的详情...</div>
    <div v-else-if="!detail" class="empty">先从左侧选择一条已完成 run，这里会显示 case 列表、证据和 judge 请求。</div>
    <template v-else>
      <header class="detail-header">
        <div>
          <p>运行详情</p>
          <h2>{{ detail.summary?.run_id }}</h2>
        </div>
        <span :class="topologyTone">
          {{ topologyLabel }}
        </span>
      </header>

      <dl class="metrics">
        <div><dt title="本次运行使用的数据集。">数据集</dt><dd>{{ dash(detail.summary?.dataset_id) }}</dd></div>
        <div><dt>Cases</dt><dd>{{ detail.summary?.total_cases ?? 0 }}</dd></div>
        <div><dt title="当前 Workflow Lab 可展示的 learning case 数。">Learning Cases</dt><dd>{{ detail.summary?.learning_case_count ?? learningCases.length }}</dd></div>
        <div><dt title="硬失败 case 数。">硬失败</dt><dd>{{ detail.summary?.hard_failure_count ?? "-" }}</dd></div>
        <div><dt title="软失败 case 数。">软失败</dt><dd>{{ detail.summary?.soft_failure_count ?? "-" }}</dd></div>
        <div><dt title="本次运行注入的 Candidate。">Candidate</dt><dd>{{ dash(detail.summary?.prompt_variant_id) }}</dd></div>
        <div><dt title="本次运行的 RAG 模式。">RAG</dt><dd>{{ dash(detail.summary?.rag_mode) }}</dd></div>
      </dl>

      <p v-if="unsupportedCaseCount > 0" class="unsupported-note">
        本次 run 中有 {{ unsupportedCaseCount }} 条非 learning case。当前模块只展开 learning case；其余 case 暂不在这里渲染。
      </p>

      <CaseArtifactTable
        :cases="learningCases"
        :selected-case-id="selectedCaseId"
        @select="emit('select-case', $event)"
      />

      <WorkflowJudgePanel
        :run-id="detail.summary?.run_id || ''"
        :rubrics="rubrics"
        :requests="judgeRequests"
        :submitting="judgeSubmitting"
        :disabled="judgeDisabled"
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
.detail-header span.mixed {
  background: var(--theme--warning-background);
}
.metrics {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
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
.unsupported-note {
  margin: -4px 0 0;
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
