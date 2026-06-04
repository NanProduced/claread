<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  runs: { type: Array, default: () => [] },
  baselineRunId: { type: String, default: "" },
  candidateRunId: { type: String, default: "" },
  loading: { type: Boolean, default: false },
  compareResult: { type: Object, default: null },
  caseCoverage: { type: Number, default: 0 },
  datasetId: { type: String, default: "" },
});
const emit = defineEmits(["update:baseline-run-id", "update:candidate-run-id", "compare", "select-run"]);

const localBaseline = ref(props.baselineRunId);
const localCandidate = ref(props.candidateRunId);

watch(() => props.baselineRunId, (value) => { localBaseline.value = value; });
watch(() => props.candidateRunId, (value) => { localCandidate.value = value; });

const learningRuns = computed(() => props.runs.filter((run) => (run.learning_case_count || 0) > 0 && run.has_report));
const baselineRun = computed(() => learningRuns.value.find((run) => run.run_id === localBaseline.value) || null);
const candidateRun = computed(() => learningRuns.value.find((run) => run.run_id === localCandidate.value) || null);
const canCompare = computed(() => (
  localBaseline.value
  && localCandidate.value
  && localBaseline.value !== localCandidate.value
  && !props.loading
));

function setBaseline(value) {
  localBaseline.value = value;
  emit("update:baseline-run-id", value);
}

function setCandidate(value) {
  localCandidate.value = value;
  emit("update:candidate-run-id", value);
}
</script>

<template>
  <section class="compare-builder">
    <header>
      <div>
        <p>对比构建</p>
        <h2>选择 baseline run 与候选 run，生成句子级差异</h2>
      </div>
      <button type="button" :disabled="!canCompare" title="同步读取两侧 artifact，生成当前对比报告。" @click="emit('compare')">
        {{ loading ? "生成中..." : "生成对比" }}
      </button>
    </header>

    <p class="builder-hint">
      这里的 baseline run / 候选 run 都来自<strong>已经完成的 dataset run</strong>，不是 single run。
      先在「数据集验证」里跑出至少两条已完成 run，再回来对比。
    </p>

    <dl class="context-facts" v-if="baselineRun || candidateRun || datasetId">
      <div>
        <dt>Baseline run</dt>
        <dd>{{ baselineRun?.run_id || "未选择" }}</dd>
      </div>
      <div>
        <dt>候选 run</dt>
        <dd>{{ candidateRun?.run_id || "未选择" }}</dd>
      </div>
      <div>
        <dt>Dataset</dt>
        <dd>{{ datasetId || baselineRun?.dataset_id || candidateRun?.dataset_id || "—" }}</dd>
      </div>
      <div>
        <dt>Case 覆盖</dt>
        <dd>{{ caseCoverage > 0 ? `${caseCoverage} 条` : "生成对比后计算" }}</dd>
      </div>
    </dl>

    <div class="compare-grid">
      <label>
        <span title="作为参照的 baseline run，必须是已完成的 learning run。">Baseline run</span>
        <select :value="localBaseline" @change="setBaseline($event.target.value)">
          <option value="">选择 baseline</option>
          <option v-for="run in learningRuns" :key="`b-${run.run_id}`" :value="run.run_id">
            {{ run.run_id }} / {{ run.prompt_variant_id || "baseline" }} / {{ run.learning_case_count || 0 }} learning
          </option>
        </select>
      </label>
      <label>
        <span title="待验证的候选 run，必须是已完成的 learning run。">候选 run</span>
        <select :value="localCandidate" @change="setCandidate($event.target.value)">
          <option value="">选择候选 run</option>
          <option v-for="run in learningRuns" :key="`c-${run.run_id}`" :value="run.run_id">
            {{ run.run_id }} / {{ run.prompt_variant_id || "baseline" }} / {{ run.learning_case_count || 0 }} learning
          </option>
        </select>
      </label>
    </div>

    <div class="selected-runs">
      <button v-if="localBaseline" type="button" @click="emit('select-run', localBaseline)">查看 baseline 详情</button>
      <button v-if="localCandidate" type="button" @click="emit('select-run', localCandidate)">查看候选 run 详情</button>
      <p v-if="learningRuns.length === 0">暂无可用于 learning compare 的 run。先去「数据集验证」完成 baseline 与 candidate 两条 runs。</p>
    </div>
  </section>
</template>

<style scoped>
.compare-builder {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 16px;
}
header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
header p,
label span,
.selected-runs p,
.builder-hint {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
header h2 {
  margin: 2px 0 0;
  font-size: 18px;
  line-height: 1.45;
}
.builder-hint {
  margin-top: 12px;
  line-height: 1.6;
}
.context-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 14px 0 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}
.context-facts div {
  background: var(--theme--background-subdued);
  padding: 10px;
  min-width: 0;
}
.context-facts dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}
.context-facts dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}
.compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}
label {
  display: grid;
  gap: 6px;
}
button,
select {
  min-height: 36px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  padding: 7px 9px;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.selected-runs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
@media (max-width: 900px) {
  .context-facts,
  .compare-grid {
    grid-template-columns: 1fr;
  }
}
</style>
