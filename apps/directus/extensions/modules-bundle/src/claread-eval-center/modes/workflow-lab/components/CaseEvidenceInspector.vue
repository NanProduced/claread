<script setup>
import JsonTreeView from "../../../components/JsonTreeView.vue";
import ResultBlock from "../../../components/ResultBlock.vue";

defineProps({
  artifact: { type: Object, default: null },
  compareCase: { type: Object, default: null },
  loading: { type: Boolean, default: false },
});

function list(value) {
  return Array.isArray(value) ? value : [];
}
</script>

<template>
  <aside class="inspector">
    <header>
      <p>Evidence</p>
      <h2>{{ artifact?.case_id || compareCase?.case_id || "No case selected" }}</h2>
    </header>

    <div v-if="loading" class="empty">Loading evidence...</div>
    <div v-else-if="!artifact && !compareCase" class="empty">Select a case from a run or compare report.</div>
    <template v-else>
      <section v-if="compareCase" class="compare-evidence">
        <strong>{{ compareCase.verdict }}</strong>
        <p v-for="reason in compareCase.reasons || []" :key="reason">{{ reason }}</p>
      </section>

      <template v-if="artifact">
        <dl class="meta">
          <div><dt>Status</dt><dd>{{ artifact.adapter_status || "-" }}</dd></div>
          <div><dt>State</dt><dd>{{ artifact.user_facing_state || "-" }}</dd></div>
          <div><dt>Warnings</dt><dd>{{ list(artifact.warnings).length }}</dd></div>
          <div><dt>Drops</dt><dd>{{ list(artifact.drop_log).length }}</dd></div>
        </dl>

        <ResultBlock title="Translations" :open="true">
          <JsonTreeView :value="list(artifact.translations).slice(0, 8)" label="translations" />
        </ResultBlock>
        <ResultBlock title="Inline marks">
          <JsonTreeView :value="list(artifact.inline_marks).slice(0, 12)" label="inline_marks" />
        </ResultBlock>
        <ResultBlock title="Sentence entries">
          <JsonTreeView :value="list(artifact.sentence_entries).slice(0, 12)" label="sentence_entries" />
        </ResultBlock>
        <ResultBlock title="Warnings / drop log">
          <JsonTreeView :value="{ warnings: artifact.warnings || [], drop_log: artifact.drop_log || [] }" label="quality_signals" />
        </ResultBlock>
        <ResultBlock title="Raw artifact">
          <JsonTreeView :value="artifact" label="case_artifact" />
        </ResultBlock>
      </template>
    </template>
  </aside>
</template>

<style scoped>
.inspector {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  min-height: 520px;
  max-height: calc(100vh - 190px);
  overflow: auto;
  padding: 14px;
}
header p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
header h2 {
  margin: 2px 0 12px;
  font-size: 16px;
  overflow-wrap: anywhere;
}
.empty {
  color: var(--theme--foreground-subdued);
}
.compare-evidence {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  margin-bottom: 12px;
  padding: 10px;
}
.compare-evidence strong {
  display: inline-block;
  border-radius: 999px;
  background: var(--theme--background-subdued);
  padding: 3px 7px;
}
.compare-evidence p {
  margin: 8px 0 0;
}
.meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}
.meta div {
  background: var(--theme--background-subdued);
  padding: 8px;
}
dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}
dd {
  margin: 3px 0 0;
}
</style>
