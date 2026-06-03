<script setup>
import { computed } from "vue";
import StatusPill from "./StatusPill.vue";

const props = defineProps({
  nodeName: { type: String, required: true },
  output: { type: Object, default: null },
  preparedSentences: { type: Array, default: () => [] },
  quickValidation: { type: Object, default: null },
  emptyText: { type: String, default: "暂无节点输出。" },
});

const grammarNotes = computed(() => Array.isArray(props.output?.grammar_notes) ? props.output.grammar_notes : []);
const sentenceAnalyses = computed(() => Array.isArray(props.output?.sentence_analyses) ? props.output.sentence_analyses : []);
const vocabHighlights = computed(() => Array.isArray(props.output?.vocab_highlights) ? props.output.vocab_highlights : []);
const phraseGlosses = computed(() => Array.isArray(props.output?.phrase_glosses) ? props.output.phrase_glosses : []);
const contextGlosses = computed(() => Array.isArray(props.output?.context_glosses) ? props.output.context_glosses : []);
const sentenceTranslations = computed(() => Array.isArray(props.output?.sentence_translations) ? props.output.sentence_translations : []);
const preparedSentenceMap = computed(() => {
  const entries = Array.isArray(props.preparedSentences) ? props.preparedSentences : [];
  return Object.fromEntries(
    entries
      .filter((item) => item && item.sentence_id)
      .map((item) => [String(item.sentence_id), String(item.text || "")]),
  );
});

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

function sentenceText(sentenceId) {
  const text = preparedSentenceMap.value[String(sentenceId || "")];
  return text ? text : "";
}

function anchorSummary(spans) {
  if (!Array.isArray(spans) || spans.length === 0) return "未提供锚点";
  return spans
    .map((span) => String(span?.text || "").trim())
    .filter(Boolean)
    .join(" · ");
}

function chunkSummary(chunks) {
  if (!Array.isArray(chunks) || chunks.length === 0) return "未提供拆解块";
  return chunks
    .map((chunk) => String(chunk?.text || "").trim())
    .filter(Boolean)
    .join(" · ");
}

function chunksText(chunks) {
  if (!Array.isArray(chunks) || chunks.length === 0) return "未提供拆解块。";
  return chunks
    .map((chunk) => `${chunk?.order || "?"}. ${chunk?.label || "未命名"}: ${chunk?.text || "—"}`)
    .join("\n");
}

function roleLabel(role) {
  const normalized = String(role || "").trim();
  if (!normalized) return "片段";
  const map = {
    cue: "线索",
    focus: "焦点",
    head: "核心",
    tail: "尾部",
    modifier: "修饰",
    clause: "从句",
    marker: "标记",
  };
  return map[normalized] || normalized;
}

function highlightSegments(text, fragments) {
  const source = String(text || "");
  const anchors = Array.isArray(fragments)
    ? fragments
        .map((item) => String(item || "").trim())
        .filter(Boolean)
    : [];
  if (!source || !anchors.length) {
    return [{ text: source, highlighted: false }];
  }

  const matches = [];
  for (const anchor of anchors) {
    const start = source.indexOf(anchor);
    if (start >= 0) {
      matches.push({ start, end: start + anchor.length, text: anchor });
    }
  }

  if (!matches.length) {
    return [{ text: source, highlighted: false }];
  }

  matches.sort((left, right) => left.start - right.start || right.end - left.end);
  const filtered = [];
  let cursor = -1;
  for (const match of matches) {
    if (match.start >= cursor) {
      filtered.push(match);
      cursor = match.end;
    }
  }

  const segments = [];
  let index = 0;
  for (const match of filtered) {
    if (match.start > index) {
      segments.push({ text: source.slice(index, match.start), highlighted: false });
    }
    segments.push({ text: source.slice(match.start, match.end), highlighted: true });
    index = match.end;
  }
  if (index < source.length) {
    segments.push({ text: source.slice(index), highlighted: false });
  }
  return segments;
}

function noteWarnings(item) {
  const warnings = Array.isArray(props.quickValidation?.warnings) ? props.quickValidation.warnings : [];
  const spanTexts = Array.isArray(item?.spans)
    ? item.spans.map((span) => String(span?.text || "").trim()).filter(Boolean)
    : [];
  return warnings.filter((warning) => {
    if (warning?.sentence_id && String(warning.sentence_id) !== String(item?.sentence_id || "")) return false;
    if (warning?.anchor_text && spanTexts.length > 0 && !spanTexts.includes(String(warning.anchor_text).trim())) return false;
    return String(warning?.code || "").startsWith("grammar_");
  });
}

function analysisWarnings(item) {
  const warnings = Array.isArray(props.quickValidation?.warnings) ? props.quickValidation.warnings : [];
  const chunkTexts = Array.isArray(item?.chunks)
    ? item.chunks.map((chunk) => String(chunk?.text || "").trim()).filter(Boolean)
    : [];
  return warnings.filter((warning) => {
    if (warning?.sentence_id && String(warning.sentence_id) !== String(item?.sentence_id || "")) return false;
    if (warning?.anchor_text && chunkTexts.length > 0 && !chunkTexts.includes(String(warning.anchor_text).trim())) return false;
    return String(warning?.code || "").startsWith("sentence_analysis_");
  });
}

function evidenceStatus(warnings) {
  return Array.isArray(warnings) && warnings.length > 0
    ? { label: `${warnings.length} 处待检查`, tone: "warning" }
    : { label: "命中正常", tone: "success" };
}

function simpleAnchorStatus(sentenceId, fragments) {
  const source = sentenceText(sentenceId);
  const anchors = Array.isArray(fragments)
    ? fragments.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (!source || anchors.length === 0) return { label: "待人工复看", tone: "neutral" };
  const missing = anchors.filter((anchor) => !source.includes(anchor));
  if (missing.length) return { label: `${missing.length} 处待检查`, tone: "warning", missing };
  return { label: "命中正常", tone: "success", missing: [] };
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
          <div class="card-heading">
            <strong>{{ dash(item.label) }}</strong>
            <span class="sentence-id">{{ dash(item.sentence_id) }}</span>
          </div>
          <StatusPill :label="evidenceStatus(noteWarnings(item)).label" :tone="evidenceStatus(noteWarnings(item)).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), item.spans?.map((span) => span?.text))" :key="`note-segment-${index}-${segmentIndex}`">
              <mark v-if="segment.highlighted" class="anchor-mark">{{ segment.text }}</mark>
              <span v-else>{{ segment.text }}</span>
            </template>
          </p>
        </div>
        <div class="evidence-row">
          <div class="evidence-summary">
            <span class="evidence-label">锚点</span>
            <span class="evidence-value">{{ Array.isArray(item.spans) ? `${item.spans.length} 段` : "未提供" }}</span>
          </div>
          <div v-if="Array.isArray(item.spans) && item.spans.length" class="anchor-chip-row">
            <span v-for="(span, spanIndex) in item.spans" :key="`span-${index}-${spanIndex}`" class="anchor-chip">
              <span class="anchor-chip__text">{{ dash(span?.text) }}</span>
              <span class="anchor-chip__role">{{ roleLabel(span?.role) }}</span>
            </span>
          </div>
        </div>
        <ul v-if="noteWarnings(item).length" class="warning-list">
          <li v-for="(warning, warningIndex) in noteWarnings(item)" :key="`note-warning-${index}-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
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
          <div class="card-heading">
            <strong>{{ dash(item.label) }}</strong>
            <span class="sentence-id">{{ dash(item.sentence_id) }}</span>
          </div>
          <StatusPill :label="evidenceStatus(analysisWarnings(item)).label" :tone="evidenceStatus(analysisWarnings(item)).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), item.chunks?.map((chunk) => chunk?.text))" :key="`analysis-segment-${index}-${segmentIndex}`">
              <mark v-if="segment.highlighted" class="anchor-mark">{{ segment.text }}</mark>
              <span v-else>{{ segment.text }}</span>
            </template>
          </p>
        </div>
        <div class="evidence-row">
          <div class="evidence-summary">
            <span class="evidence-label">拆解块</span>
            <span class="evidence-value">{{ Array.isArray(item.chunks) ? `${item.chunks.length} 段` : "未提供" }}</span>
          </div>
        </div>
        <ul v-if="analysisWarnings(item).length" class="warning-list">
          <li v-for="(warning, warningIndex) in analysisWarnings(item)" :key="`analysis-warning-${index}-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
        <p>{{ dash(item.analysis_zh) }}</p>
        <div v-if="Array.isArray(item.chunks) && item.chunks.length" class="chunk-list">
          <div v-for="(chunk, chunkIndex) in item.chunks" :key="`chunk-${index}-${chunkIndex}`" class="chunk-row">
            <span class="chunk-order">{{ chunk?.order || chunkIndex + 1 }}</span>
            <div class="chunk-main">
              <strong>{{ dash(chunk?.label) }}</strong>
              <span>{{ dash(chunk?.text) }}</span>
            </div>
          </div>
        </div>
        <p v-else class="anchor-line">{{ chunksText(item.chunks) }}</p>
      </article>
    </section>
  </div>

  <div v-else-if="nodeName === 'vocabulary' && hasStructuredContent" class="output-layout">
    <section v-if="vocabHighlights.length" class="output-section">
      <header>
        <h4>Vocab Highlights</h4>
        <small>{{ vocabHighlights.length }} 条</small>
      </header>
      <article v-for="(item, index) in vocabHighlights" :key="`vocab-${index}`" class="output-card">
        <div class="card-head">
          <div class="card-heading">
            <strong>{{ dash(item.text) }}</strong>
            <span class="sentence-id">{{ dash(item.sentence_id) }}</span>
          </div>
          <StatusPill :label="simpleAnchorStatus(item.sentence_id, [item.text]).label" :tone="simpleAnchorStatus(item.sentence_id, [item.text]).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), [item.text])" :key="`vocab-segment-${index}-${segmentIndex}`">
              <mark v-if="segment.highlighted" class="anchor-mark">{{ segment.text }}</mark>
              <span v-else>{{ segment.text }}</span>
            </template>
          </p>
        </div>
        <div class="evidence-row">
          <div class="evidence-summary">
            <span class="evidence-label">锚点</span>
            <span class="evidence-value">{{ dash(item.text) }}</span>
          </div>
        </div>
        <ul v-if="simpleAnchorStatus(item.sentence_id, [item.text]).missing?.length" class="warning-list">
          <li>原文中未找到锚点：{{ simpleAnchorStatus(item.sentence_id, [item.text]).missing.join("，") }}</li>
        </ul>
      </article>
    </section>

    <section v-if="phraseGlosses.length" class="output-section">
      <header>
        <h4>Phrase Glosses</h4>
        <small>{{ phraseGlosses.length }} 条</small>
      </header>
      <article v-for="(item, index) in phraseGlosses" :key="`phrase-${index}`" class="output-card">
        <div class="card-head">
          <div class="card-heading">
            <strong>{{ dash(item.text) }}</strong>
            <span class="sentence-id">{{ dash(item.sentence_id) }}</span>
          </div>
          <StatusPill :label="simpleAnchorStatus(item.sentence_id, [item.text]).label" :tone="simpleAnchorStatus(item.sentence_id, [item.text]).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), [item.text])" :key="`phrase-segment-${index}-${segmentIndex}`">
              <mark v-if="segment.highlighted" class="anchor-mark">{{ segment.text }}</mark>
              <span v-else>{{ segment.text }}</span>
            </template>
          </p>
        </div>
        <div class="evidence-row">
          <div class="evidence-summary">
            <span class="evidence-label">锚点</span>
            <span class="evidence-value">{{ dash(item.text) }}</span>
          </div>
          <div class="evidence-summary">
            <span class="evidence-label">类型</span>
            <span class="evidence-value">{{ dash(item.phrase_type) }}</span>
          </div>
        </div>
        <ul v-if="simpleAnchorStatus(item.sentence_id, [item.text]).missing?.length" class="warning-list">
          <li>原文中未找到锚点：{{ simpleAnchorStatus(item.sentence_id, [item.text]).missing.join("，") }}</li>
        </ul>
        <p>{{ dash(item.zh) }}</p>
      </article>
    </section>

    <section v-if="contextGlosses.length" class="output-section">
      <header>
        <h4>Context Glosses</h4>
        <small>{{ contextGlosses.length }} 条</small>
      </header>
      <article v-for="(item, index) in contextGlosses" :key="`context-${index}`" class="output-card">
        <div class="card-head">
          <div class="card-heading">
            <strong>{{ dash(item.text) }}</strong>
            <span class="sentence-id">{{ dash(item.sentence_id) }}</span>
          </div>
          <StatusPill :label="simpleAnchorStatus(item.sentence_id, [item.text]).label" :tone="simpleAnchorStatus(item.sentence_id, [item.text]).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), [item.text])" :key="`context-segment-${index}-${segmentIndex}`">
              <mark v-if="segment.highlighted" class="anchor-mark">{{ segment.text }}</mark>
              <span v-else>{{ segment.text }}</span>
            </template>
          </p>
        </div>
        <div class="evidence-row">
          <div class="evidence-summary">
            <span class="evidence-label">锚点</span>
            <span class="evidence-value">{{ dash(item.text) }}</span>
          </div>
        </div>
        <ul v-if="simpleAnchorStatus(item.sentence_id, [item.text]).missing?.length" class="warning-list">
          <li>原文中未找到锚点：{{ simpleAnchorStatus(item.sentence_id, [item.text]).missing.join("，") }}</li>
        </ul>
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
          <div class="card-heading">
            <strong>{{ dash(item.sentence_id) }}</strong>
            <span class="sentence-id">逐句翻译</span>
          </div>
          <span>逐句翻译</span>
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">{{ sentenceText(item.sentence_id) }}</p>
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
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.card-heading {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sentence-id {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
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

.sentence-context {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--theme--background);
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
}

.context-label {
  display: block;
  margin-bottom: 6px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.context-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
}

.anchor-mark {
  background: color-mix(in srgb, var(--theme--warning) 24%, transparent);
  color: inherit;
  border-radius: 4px;
  padding: 0 1px;
}

.evidence-row {
  margin-top: 10px;
  display: grid;
  gap: 8px;
}

.evidence-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.evidence-label {
  color: var(--theme--foreground-subdued);
  font-weight: 600;
}

.evidence-value {
  color: var(--theme--foreground);
  font-weight: 600;
}

.anchor-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.anchor-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
}

.anchor-chip__text {
  font-size: 12px;
  font-weight: 600;
  color: var(--theme--foreground);
}

.anchor-chip__role {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.warning-list {
  margin: 10px 0 0;
  padding-left: 18px;
  color: var(--theme--warning);
  font-size: 12px;
  line-height: 1.6;
}

.chunk-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.chunk-row {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 10px;
  align-items: start;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--theme--background);
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
}

.chunk-order {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.6;
}

.chunk-main {
  display: grid;
  gap: 4px;
}

.chunk-main strong {
  font-size: 12px;
}

.chunk-main span {
  font-size: 13px;
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
