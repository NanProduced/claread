<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  runs: { type: Array, default: () => [] },
  baselineRunId: { type: String, default: "" },
  candidateRunId: { type: String, default: "" },
  loading: { type: Boolean, default: false },
});
const emit = defineEmits(["update:baseline-run-id", "update:candidate-run-id", "compare", "select-run"]);

const localBaseline = ref(props.baselineRunId);
const localCandidate = ref(props.candidateRunId);

watch(() => props.baselineRunId, (value) => { localBaseline.value = value; });
watch(() => props.candidateRunId, (value) => { localCandidate.value = value; });

const learningRuns = computed(() => props.runs.filter((run) => run.topology_mode === "learning" && run.has_report));
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
        <p>对比生成</p>
        <h2>Baseline vs Candidate</h2>
      </div>
      <button type="button" :disabled="!canCompare" title="同步读取两个 run 的 artifact，生成 deterministic compare report。" @click="emit('compare')">
        {{ loading ? "生成中..." : "生成对比" }}
      </button>
    </header>

    <div class="compare-grid">
      <label>
        <span title="作为参照的 baseline run，必须是已完成的 learning run。">Baseline run</span>
        <select :value="localBaseline" @change="setBaseline($event.target.value)">
          <option value="">选择 baseline</option>
          <option v-for="run in learningRuns" :key="`b-${run.run_id}`" :value="run.run_id">
            {{ run.run_id }} / {{ run.prompt_variant_id || "baseline" }}
          </option>
        </select>
      </label>
      <label>
        <span title="待验证的 candidate run，必须是已完成的 learning run。">Candidate run</span>
        <select :value="localCandidate" @change="setCandidate($event.target.value)">
          <option value="">选择 candidate</option>
          <option v-for="run in learningRuns" :key="`c-${run.run_id}`" :value="run.run_id">
            {{ run.run_id }} / {{ run.prompt_variant_id || "baseline" }}
          </option>
        </select>
      </label>
    </div>

    <div class="selected-runs">
      <button v-if="localBaseline" type="button" @click="emit('select-run', localBaseline)">查看 baseline</button>
      <button v-if="localCandidate" type="button" @click="emit('select-run', localCandidate)">查看 candidate</button>
      <p v-if="learningRuns.length === 0">暂无可对比的 completed learning run。</p>
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
.selected-runs p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
header h2 {
  margin: 2px 0 0;
  font-size: 18px;
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
@media (max-width: 760px) {
  .compare-grid {
    grid-template-columns: 1fr;
  }
}
</style>
