<script setup>
import { computed } from "vue";

const props = defineProps({
  nodeName: { type: String, required: true },
  output: { type: Object, default: null },
  emptyText: { type: String, default: "暂无节点输出。" },
});

const grammarNotes = computed(() => Array.isArray(props.output?.grammar_notes) ? props.output.grammar_notes : []);
const sentenceAnalyses = computed(() => Array.isArray(props.output?.sentence_analyses) ? props.output.sentence_analyses : []);
const vocabHighlights = computed(() => Array.isArray(props.output?.vocab_highlights) ? props.output.vocab_highlights : []);
const phraseGlosses = computed(() => Array.isArray(props.output?.phrase_glosses) ? props.output.phrase_glosses : []);
const contextGlosses = computed(() => Array.isArray(props.output?.context_glosses) ? props.output.context_glosses : []);
const sentenceTranslations = computed(() => Array.isArray(props.output?.sentence_translations) ? props.output.sentence_translations : []);

const hasStructuredContent = computed(() => {
  if (!props.output || typeof props.output !== "object") return false;
  if (props.nodeName === "grammar") return grammarNotes.value.length > 0 || sentenceAnalyses.value.length > 0;
  if (props.nodeName === "vocabulary") {
    return vocabHighlights.value.length > 0 || phraseGlosses.value.length > 0 || contextGlosses.value.length > 0;
  }
  if (props.nodeName === "translation") return sentenceTranslations.value.length > 0 || Boolean(props.output?.title);
  return false;
});

function dash(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function spansText(spans) {
  if (!Array.isArray(spans) || spans.length === 0) return "—";
  return spans
    .map((span) => {
      const label = span?.text || "—";
      return span?.role ? `${label} (${span.role})` : label;
    })
    .join(" / ");
}

function chunksText(chunks) {
  if (!Array.isArray(chunks) || chunks.length === 0) return "未提供拆解块。";
  return chunks
    .map((chunk) => `${chunk?.order || "?"}. ${chunk?.label || "未命名"}: ${chunk?.text || "—"}`)
    .join("\n");
}
</script>

<template>
  <div v-if="!output" class="empty-output">{{ emptyText }}</div>

  <div v-else-if="nodeName === 'grammar' && hasStructuredContent" class="output-layout">
    <section v-if="grammarNotes.length" class="output-section">
      <header>
        <h4>Grammar Notes</h4>
        <small>{{ grammarNotes.length }} 条</small>
      </header>
      <article v-for="(item, index) in grammarNotes" :key="`grammar-note-${index}`" class="output-card">
        <div class="card-head">
          <strong>{{ dash(item.label) }}</strong>
          <span>{{ dash(item.sentence_id) }}</span>
        </div>
        <p class="anchor-line">{{ spansText(item.spans) }}</p>
        <p>{{ dash(item.note_zh) }}</p>
      </article>
    </section>

    <section v-if="sentenceAnalyses.length" class="output-section">
      <header>
        <h4>Sentence Analyses</h4>
        <small>{{ sentenceAnalyses.length }} 条</small>
      </header>
      <article v-for="(item, index) in sentenceAnalyses" :key="`sentence-analysis-${index}`" class="output-card">
        <div class="card-head">
          <strong>{{ dash(item.label) }}</strong>
          <span>{{ dash(item.sentence_id) }}</span>
        </div>
        <p>{{ dash(item.analysis_zh) }}</p>
        <pre>{{ chunksText(item.chunks) }}</pre>
      </article>
    </section>
  </div>

  <div v-else-if="nodeName === 'vocabulary' && hasStructuredContent" class="output-layout">
    <section v-if="vocabHighlights.length" class="output-section">
      <header>
        <h4>Vocab Highlights</h4>
        <small>{{ vocabHighlights.length }} 条</small>
      </header>
      <article v-for="(item, index) in vocabHighlights" :key="`vocab-${index}`" class="output-card compact">
        <strong>{{ dash(item.text) }}</strong>
        <span>{{ dash(item.sentence_id) }}</span>
      </article>
    </section>

    <section v-if="phraseGlosses.length" class="output-section">
      <header>
        <h4>Phrase Glosses</h4>
        <small>{{ phraseGlosses.length }} 条</small>
      </header>
      <article v-for="(item, index) in phraseGlosses" :key="`phrase-${index}`" class="output-card">
        <div class="card-head">
          <strong>{{ dash(item.text) }}</strong>
          <span>{{ dash(item.phrase_type) }}</span>
        </div>
        <p>{{ dash(item.zh) }}</p>
        <small>{{ dash(item.sentence_id) }}</small>
      </article>
    </section>

    <section v-if="contextGlosses.length" class="output-section">
      <header>
        <h4>Context Glosses</h4>
        <small>{{ contextGlosses.length }} 条</small>
      </header>
      <article v-for="(item, index) in contextGlosses" :key="`context-${index}`" class="output-card">
        <div class="card-head">
          <strong>{{ dash(item.text) }}</strong>
          <span>{{ dash(item.sentence_id) }}</span>
        </div>
        <p><strong>语境义：</strong>{{ dash(item.gloss) }}</p>
        <p><strong>原因：</strong>{{ dash(item.reason) }}</p>
      </article>
    </section>
  </div>

  <div v-else-if="nodeName === 'translation' && hasStructuredContent" class="output-layout">
    <section class="output-section">
      <header>
        <h4>Translation Draft</h4>
        <small>{{ sentenceTranslations.length }} 句</small>
      </header>
      <article class="output-card">
        <div class="card-head">
          <strong>{{ dash(output.title) }}</strong>
          <span>标题</span>
        </div>
      </article>
      <article v-for="(item, index) in sentenceTranslations" :key="`translation-${index}`" class="output-card">
        <div class="card-head">
          <strong>{{ dash(item.sentence_id) }}</strong>
          <span>逐句翻译</span>
        </div>
        <p>{{ dash(item.translation_zh) }}</p>
      </article>
    </section>
  </div>

  <pre v-else>{{ JSON.stringify(output, null, 2) }}</pre>
</template>

<style scoped>
.empty-output {
  margin-top: 12px;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

.output-layout {
  display: grid;
  gap: 12px;
  margin-top: 12px;
}

.output-section {
  display: grid;
  gap: 10px;
}

.output-section header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.output-section h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
}

.output-section small {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.output-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px;
}

.output-card.compact {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.card-head span,
.output-card small,
.anchor-line {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.output-card p {
  margin: 8px 0 0;
  line-height: 1.6;
}

.output-card pre,
pre {
  max-height: 320px;
  overflow: auto;
  margin: 10px 0 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 10px;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
