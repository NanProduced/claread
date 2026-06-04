<script setup>
import { computed } from "vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import { dash, normalizeWorkflowScene, sceneInlineMarks, sceneSentenceEntries, sceneTranslations, sceneWarnings } from "../composables/workflowLabFormatting.js";

const props = defineProps({
  payload: { type: Object, default: null },
  title: { type: String, default: "Artifact 摘要" },
  emptyText: { type: String, default: "当前没有可展示的 artifact 摘要。" },
});

const scene = computed(() => normalizeWorkflowScene(props.payload));
const translations = computed(() => sceneTranslations(scene.value));
const inlineMarks = computed(() => sceneInlineMarks(scene.value));
const sentenceEntries = computed(() => sceneSentenceEntries(scene.value));
const warnings = computed(() => sceneWarnings(scene.value));
const dropLog = computed(() => Array.isArray(props.payload?.drop_log) ? props.payload.drop_log : []);
const runtimeSummary = computed(() => (
  props.payload?.runtime_summary && typeof props.payload.runtime_summary === "object"
    ? props.payload.runtime_summary
    : {}
));

const sentenceIds = computed(() => {
  const ids = new Set();
  for (const item of scene.value?.article?.sentences || []) {
    if (item?.sentence_id) ids.add(String(item.sentence_id));
  }
  for (const item of translations.value) {
    if (item?.sentence_id) ids.add(String(item.sentence_id));
  }
  for (const item of inlineMarks.value) {
    if (item?.anchor?.sentence_id) ids.add(String(item.anchor.sentence_id));
  }
  for (const item of sentenceEntries.value) {
    if (item?.sentence_id) ids.add(String(item.sentence_id));
  }
  return Array.from(ids);
});

function countSentences(predicate) {
  const sidSet = new Set();
  for (const item of predicate()) {
    if (item?.sentence_id) sidSet.add(String(item.sentence_id));
    if (item?.anchor?.sentence_id) sidSet.add(String(item.anchor.sentence_id));
  }
  return sidSet.size;
}

const coverageFacts = computed(() => {
  const total = sentenceIds.value.length;
  const translated = countSentences(() => translations.value);
  const lexical = countSentences(() => inlineMarks.value.filter((item) => item?.annotation_type !== "grammar_note"));
  const grammar = countSentences(() => [
    ...inlineMarks.value.filter((item) => item?.annotation_type === "grammar_note"),
    ...sentenceEntries.value.filter((item) => item?.entry_type === "grammar_note" || item?.entry_type === "sentence_analysis"),
  ]);
  return [
    { label: "总句数", value: total },
    { label: "有译文", value: translated },
    { label: "有词汇标注", value: lexical },
    { label: "有语法标注", value: grammar },
  ];
});

const lexicalGroups = computed(() => {
  const map = new Map();
  for (const item of inlineMarks.value.filter((entry) => entry?.annotation_type !== "grammar_note")) {
    const key = String(item.annotation_type || "other");
    const current = map.get(key) || { type: key, count: 0, anchors: [] };
    current.count += 1;
    current.anchors.push(item?.anchor?.anchor_text || item?.lookup_text || "—");
    map.set(key, current);
  }
  return Array.from(map.values());
});

const grammarItems = computed(() => {
  const rows = [];
  for (const item of inlineMarks.value.filter((entry) => entry?.annotation_type === "grammar_note")) {
    rows.push({
      sentenceId: item?.anchor?.sentence_id || "—",
      title: item?.anchor?.anchor_text || "grammar note",
      type: "grammar_note",
    });
  }
  for (const item of sentenceEntries.value.filter((entry) => entry?.entry_type === "grammar_note" || entry?.entry_type === "sentence_analysis")) {
    rows.push({
      sentenceId: item?.sentence_id || "—",
      title: item?.label || item?.title || item?.entry_type || "entry",
      type: item?.entry_type || "entry",
    });
  }
  return rows;
});

const usageFacts = computed(() => {
  const aggregate = runtimeSummary.value?.aggregate && typeof runtimeSummary.value.aggregate === "object"
    ? runtimeSummary.value.aggregate
    : runtimeSummary.value;
  return [
    { label: "Input", value: dash(aggregate?.input_tokens, "—") },
    { label: "Output", value: dash(aggregate?.output_tokens, "—") },
    { label: "Total", value: dash(aggregate?.total_tokens, "—") },
  ];
});

const perAgentUsage = computed(() => {
  const rows = runtimeSummary.value?.per_agent && typeof runtimeSummary.value.per_agent === "object"
    ? Object.entries(runtimeSummary.value.per_agent)
    : [];
  return rows.map(([agentName, usage]) => ({
    agentName,
    input: usage?.input_tokens ?? null,
    output: usage?.output_tokens ?? null,
    total: usage?.total_tokens ?? null,
  }));
});
</script>

<template>
  <section class="scene-summary">
    <div v-if="!scene" class="empty-state">{{ emptyText }}</div>
    <template v-else>
      <header class="summary-head">
        <div>
          <p>{{ title }}</p>
          <h3>{{ dash(scene.user_facing_state, "normal") }}</h3>
        </div>
        <div class="summary-badges">
          <span>{{ warnings.length }} 条提醒</span>
          <span>{{ dropLog.length }} 条 drop</span>
        </div>
      </header>

      <dl class="coverage-grid">
        <div v-for="fact in coverageFacts" :key="fact.label">
          <dt>{{ fact.label }}</dt>
          <dd>{{ fact.value }}</dd>
        </div>
      </dl>

      <div class="summary-columns">
        <section class="summary-panel">
          <div class="panel-head">
            <strong>词汇摘要</strong>
            <small>{{ inlineMarks.length }} 条标注</small>
          </div>
          <div v-if="lexicalGroups.length" class="group-list">
            <article v-for="group in lexicalGroups" :key="group.type" class="group-card">
              <div class="group-meta">
                <strong>{{ group.type }}</strong>
                <span>{{ group.count }} 条</span>
              </div>
              <p>{{ group.anchors.join(" / ") }}</p>
            </article>
          </div>
          <p v-else class="empty-line">没有词汇类标注。</p>
        </section>

        <section class="summary-panel">
          <div class="panel-head">
            <strong>语法摘要</strong>
            <small>{{ grammarItems.length }} 条条目</small>
          </div>
          <div v-if="grammarItems.length" class="group-list">
            <article v-for="(item, index) in grammarItems" :key="`grammar-${index}`" class="group-card">
              <div class="group-meta">
                <strong>{{ item.title }}</strong>
                <span>{{ item.type }}</span>
              </div>
              <p>句子：{{ item.sentenceId }}</p>
            </article>
          </div>
          <p v-else class="empty-line">没有语法类标注。</p>
        </section>
      </div>

      <section class="usage-panel">
        <div class="panel-head">
          <strong>运行消耗</strong>
          <small>runtime summary</small>
        </div>
        <dl class="usage-grid">
          <div v-for="fact in usageFacts" :key="fact.label">
            <dt>{{ fact.label }}</dt>
            <dd>{{ fact.value }}</dd>
          </div>
        </dl>
        <div v-if="perAgentUsage.length" class="agent-grid">
          <article v-for="agent in perAgentUsage" :key="agent.agentName" class="agent-card">
            <strong>{{ agent.agentName }}</strong>
            <p>Input {{ dash(agent.input, "—") }} / Output {{ dash(agent.output, "—") }} / Total {{ dash(agent.total, "—") }}</p>
          </article>
        </div>
      </section>

      <ResultBlock v-if="warnings.length || dropLog.length" title="质量信号" :open="false">
        <div class="quality-grid">
          <section class="quality-panel">
            <strong>Warnings</strong>
            <ul v-if="warnings.length">
              <li v-for="(warning, index) in warnings" :key="`warning-${index}`">
                {{ warning.code || warning.level || "warning" }}：{{ warning.message || JSON.stringify(warning) }}
              </li>
            </ul>
            <p v-else>无 warnings。</p>
          </section>
          <section class="quality-panel">
            <strong>Drop Log</strong>
            <ul v-if="dropLog.length">
              <li v-for="(item, index) in dropLog" :key="`drop-${index}`">
                {{ item.code || item.reason || "drop" }}：{{ item.message || item.anchor_text || JSON.stringify(item) }}
              </li>
            </ul>
            <p v-else>无 drop log。</p>
          </section>
        </div>
      </ResultBlock>
    </template>
  </section>
</template>

<style scoped>
.scene-summary {
  display: grid;
  gap: 14px;
}

.summary-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.summary-head p,
.coverage-grid dt,
.group-card span,
.panel-head small,
.quality-panel p,
.quality-panel li,
.empty-state,
.empty-line {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.summary-head h3 {
  margin: 2px 0 0;
  font-size: 18px;
}

.summary-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-badges span {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}

.coverage-grid,
.usage-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  overflow: hidden;
}

.coverage-grid div,
.usage-grid div {
  background: var(--theme--background-subdued);
  padding: 10px 12px;
}

.coverage-grid dd,
.usage-grid dd {
  margin: 4px 0 0;
  font-size: 15px;
  font-weight: 700;
}

.summary-columns,
.quality-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.summary-panel,
.usage-panel,
.quality-panel {
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 12px 14px;
  display: grid;
  gap: 10px;
}

.panel-head,
.group-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.group-list,
.agent-grid {
  display: grid;
  gap: 8px;
}

.group-card,
.agent-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 10px 12px;
}

.group-card p,
.agent-card p {
  margin: 6px 0 0;
  color: var(--theme--foreground);
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.agent-card strong {
  font-size: 12px;
}

.quality-panel ul {
  margin: 8px 0 0;
  padding-left: 18px;
}

.quality-panel li + li {
  margin-top: 6px;
}

.empty-state,
.empty-line {
  border: 1px dashed var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background-subdued);
  padding: 14px;
  line-height: 1.6;
}

@media (max-width: 900px) {
  .summary-columns,
  .quality-grid {
    grid-template-columns: 1fr;
  }
}
</style>
