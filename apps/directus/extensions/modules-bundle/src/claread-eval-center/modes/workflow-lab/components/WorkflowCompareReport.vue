<script setup>
import { computed, ref } from "vue";
import ReviewNotesPanel from "../../../components/ReviewNotesPanel.vue";
import WorkflowSentenceCompareNotebook from "./WorkflowSentenceCompareNotebook.vue";

const props = defineProps({
  result: { type: Object, default: null },
  compareId: { type: String, default: "" },
  selectedCaseId: { type: String, default: "" },
  baselineArtifact: { type: Object, default: null },
  candidateArtifact: { type: Object, default: null },
});
const emit = defineEmits(["select-case"]);

const report = computed(() => props.result?.report || props.result);

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

const filterMode = ref("all");
</script>

<template>
  <section class="compare-report">
    <div v-if="!result" class="empty">先选择两条 run 并生成对比报告，这里才会显示逐句的双边对照。</div>
    <template v-else>
      <header>
        <div>
          <p>{{ result.created ? "新生成的对比报告" : "已有对比报告" }}</p>
          <h2>{{ report.baseline_run_id }} vs {{ report.candidate_run_id }}</h2>
        </div>
        <span>{{ result.report_id || `vs-${report.baseline_run_id}` }}</span>
      </header>

      <p class="disclaimer">
        以下逐句对照基于 deterministic 信号。
        <strong>结构失败</strong>指 error / timeout / schema 缺失等硬性异常；
        <strong>轻微信号</strong>指 degraded_light、warning、drop 等弱异常。
        结论性判断以 judge 评审和人工 review 为准。
      </p>

      <div v-if="report.identity_warnings?.length" class="warnings">
        <strong>运行身份提醒</strong>
        <p v-for="warning in report.identity_warnings" :key="warning">{{ warning }}</p>
      </div>

      <section class="notebook-section">
        <header class="section-head">
          <div>
            <p>逐句双边对照</p>
            <h3>直接阅读每句的 Baseline 与候选输出差异</h3>
          </div>
          <div class="filter-bar">
            <button type="button" :class="{ active: filterMode === 'all' }" @click="filterMode = 'all'">全部</button>
            <button type="button" :class="{ active: filterMode === 'changed' }" @click="filterMode = 'changed'">仅变化</button>
          </div>
        </header>

        <WorkflowSentenceCompareNotebook
          :baseline-artifact="baselineArtifact"
          :candidate-artifact="candidateArtifact"
          :prepared-sentences="preparedSentences"
          :comparisons="report.comparisons || []"
          :filter-mode="filterMode"
          empty-text="本次对比没有可用的句子数据。"
        />
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
          :target-id="compareId || result?.compare_id || report.compare_id || ''"
          :run-id="report.candidate_run_id"
          title="Compare Review"
          scope-note="这类 note 挂在 workflow_compare 记录上，表达 compare-scope 判断；不是逐句 review。"
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
.empty,
.disclaimer {
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

.notebook-section,
.review-block {
  display: grid;
  gap: 10px;
}

.filter-bar {
  display: flex;
  gap: 4px;
}

.filter-bar button {
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.filter-bar button.active {
  background: var(--theme--primary);
  color: var(--theme--background);
  border-color: var(--theme--primary);
}
</style>
