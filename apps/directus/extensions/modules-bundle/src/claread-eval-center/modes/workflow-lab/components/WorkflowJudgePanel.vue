<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  runId: { type: String, default: "" },
  rubrics: { type: Array, default: () => [] },
  requests: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["queue", "refresh"]);

const rubricId = ref("");
const adapterKind = ref("fake");
const maxCases = ref(30);

const runRequests = computed(() => props.requests.filter((item) => item.run_id === props.runId));

watch(
  () => props.rubrics,
  (rubrics) => {
    if (!rubricId.value && rubrics.length) rubricId.value = rubrics[0].id;
  },
  { immediate: true },
);

function queue() {
  if (!props.runId || !rubricId.value || props.disabled) return;
  emit("queue", {
    run_id: props.runId,
    rubric_id: rubricId.value,
    judge_adapter_kind: adapterKind.value,
    config_json: {
      source: "workflow_lab",
      max_concurrency: 1,
      max_cases: Number(maxCases.value) || 30,
    },
  });
}
</script>

<template>
  <section class="judge-panel">
    <header>
      <div>
        <p>Judge evidence</p>
        <h3>Run-level judge</h3>
      </div>
      <button type="button" @click="emit('refresh')">Refresh</button>
    </header>

    <div class="judge-form">
      <label>
        <span>Rubric</span>
        <select v-model="rubricId" :disabled="disabled">
          <option v-for="rubric in rubrics" :key="rubric.id" :value="rubric.id">
            {{ rubric.id }}
          </option>
        </select>
      </label>
      <label>
        <span>Adapter</span>
        <select v-model="adapterKind" :disabled="disabled">
          <option value="fake">fake</option>
          <option value="llm">llm</option>
        </select>
      </label>
      <label>
        <span>Max cases</span>
        <input v-model.number="maxCases" type="number" min="1" :disabled="disabled" />
      </label>
      <button type="button" :disabled="disabled || submitting || !runId || !rubricId" @click="queue">
        {{ submitting ? "Queueing" : "Queue judge" }}
      </button>
    </div>

    <div class="judge-requests">
      <article v-for="request in runRequests" :key="request.id">
        <strong>{{ request.judge_run_id }}</strong>
        <span>{{ request.status }}</span>
        <small>{{ request.rubric_id }} / {{ request.judge_adapter_kind }}</small>
      </article>
      <p v-if="runRequests.length === 0">No judge requests for this run.</p>
    </div>
  </section>
</template>

<style scoped>
.judge-panel {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 12px;
}
header,
.judge-form,
article {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
header p,
label span,
small,
.judge-requests p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
header h3 {
  margin: 2px 0 0;
  font-size: 14px;
}
.judge-form {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(100px, 0.8fr) minmax(90px, 0.6fr) auto;
  margin-top: 12px;
}
label {
  display: grid;
  gap: 5px;
}
button,
select,
input {
  min-height: 34px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  padding: 6px 8px;
}
button {
  cursor: pointer;
  font-weight: 700;
}
button:disabled,
select:disabled,
input:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.judge-requests {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}
article {
  border-top: 1px solid var(--theme--border-color);
  padding-top: 8px;
}
article span {
  border-radius: 999px;
  background: var(--theme--background-subdued);
  padding: 3px 7px;
  font-size: 11px;
  font-weight: 700;
}
article small {
  display: block;
}
@media (max-width: 860px) {
  .judge-form {
    grid-template-columns: 1fr;
  }
}
</style>
