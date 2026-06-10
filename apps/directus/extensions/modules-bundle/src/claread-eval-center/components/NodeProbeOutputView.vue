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
const quickWarnings = computed(() => Array.isArray(props.quickValidation?.warnings) ? props.quickValidation.warnings : []);
const quickSoftWarnings = computed(() => Array.isArray(props.quickValidation?.soft_warnings) ? props.quickValidation.soft_warnings : []);
const hasQuickValidation = computed(() => Boolean(props.quickValidation && typeof props.quickValidation === "object"));
const generalValidationWarnings = computed(() => quickWarnings.value.filter((warning) => {
  if (warning?.sentence_id) return false;
  return warningTextCandidates(warning).length === 0;
}));
const generalSoftObservations = computed(() => quickSoftWarnings.value.filter((warning) => {
  if (warning?.sentence_id) return false;
  return warningTextCandidates(warning).length === 0;
}));
const generalValidationStatus = computed(() => evidenceStatus(generalValidationWarnings.value, generalSoftObservations.value));
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
  if (props.nodeName === "translation") return sentenceTranslations.value.length > 0 || hasText(props.output?.title);
  return false;
});

function dash(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function hasText(value) {
  return String(value ?? "").trim().length > 0;
}

function uniqueTexts(values) {
  return [...new Set(
    (Array.isArray(values) ? values : [values])
      .map((value) => String(value ?? "").trim())
      .filter(Boolean),
  )];
}

function sentenceText(sentenceId) {
  const text = preparedSentenceMap.value[String(sentenceId || "")];
  return text ? text : "";
}

function anchorFragmentsFromText(text) {
  const raw = String(text || "").trim();
  if (!raw) return [];
  const normalized = raw
    .replace(/\.\.\./g, " | ")
    .replace(/\bone's\b/gi, " | ")
    .replace(/\boneself\b/gi, " | ")
    .replace(/\bsomebody\b/gi, " | ")
    .replace(/\bsomething\b/gi, " | ")
    .replace(/\bsb\b\.?/gi, " | ")
    .replace(/\bsth\b\.?/gi, " | ");
  const fragments = uniqueTexts(normalized.split(/\s*\|\s*/));
  return fragments.length ? fragments : [raw];
}

function noteAnchorFragments(item) {
  return uniqueTexts(Array.isArray(item?.spans) ? item.spans.map((span) => span?.text) : []);
}

function phraseAnchorFragments(item) {
  const spanTexts = uniqueTexts(Array.isArray(item?.spans) ? item.spans.map((span) => span?.text) : []);
  if (spanTexts.length) return spanTexts;
  return anchorFragmentsFromText(item?.text);
}

function contextAnchorFragments(item) {
  return anchorFragmentsFromText(item?.text);
}

function analysisAnchorFragments(item) {
  return uniqueTexts(Array.isArray(item?.chunks) ? item.chunks.map((chunk) => chunk?.text) : []);
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

function phraseTypeLabel(phraseType) {
  const normalized = String(phraseType || "").trim().toLowerCase();
  if (!normalized) return "—";
  const map = {
    collocation: "固定搭配",
    idiom: "习语",
    phrasal_verb: "短语动词",
    fixed_expression: "固定表达",
    discourse_marker: "话语标记",
    compound: "复合词",
    set_phrase: "固定短语",
  };
  return map[normalized] || phraseType;
}

function fieldSourceLabel(kind) {
  switch (kind) {
    case "vocab_title":
      return "text（标题兼锚点）";
    case "phrase_title":
      return "text（短语标题 / lookup）";
    case "context_title":
      return "text（语境表达）";
    case "vocab_anchor":
      return "原文锚点 / text";
    case "context_anchor":
      return "原文锚点 / text";
    default:
      return "字段";
  }
}

function phraseAnchorLabel(item) {
  if (Array.isArray(item?.spans) && item.spans.length > 0) {
    return "原文锚点 / spans";
  }
  return "原文锚点 / text（fallback）";
}

function canonicalInlineText(value) {
  return String(value || "")
    .replace(/[“”]/g, "\"")
    .replace(/[‘’]/g, "'")
    .replace(/[—–]/g, "-")
    .toLocaleLowerCase();
}

function locateAnchorStart(source, anchor) {
  const directIndex = source.indexOf(anchor);
  if (directIndex >= 0) return directIndex;
  return canonicalInlineText(source).indexOf(canonicalInlineText(anchor));
}

function highlightSegments(text, fragments) {
  const source = String(text || "");
  const anchors = uniqueTexts(fragments);
  if (!source || !anchors.length) {
    return [{ text: source, highlighted: false }];
  }

  const matches = [];
  for (const anchor of anchors) {
    const start = locateAnchorStart(source, anchor);
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

function warningTextCandidates(warning) {
  return uniqueTexts([
    warning?.anchor_text,
    warning?.other_anchor_text,
    warning?.container_text,
    warning?.resolved_anchor_text,
  ]);
}

function warningTargetsItem(warning, sentenceId, texts) {
  if (warning?.sentence_id && String(warning.sentence_id) !== String(sentenceId || "")) return false;
  const candidates = warningTextCandidates(warning);
  if (!candidates.length) return Boolean(warning?.sentence_id);
  return candidates.some((candidate) => texts.includes(candidate));
}

function vocabularyAnnotationHint(warning) {
  const explicit = String(warning?.annotation_type || "").trim();
  if (explicit) return explicit;
  const message = String(warning?.message || "").trim();
  const match = message.match(/^(vocab_highlight|phrase_gloss|context_gloss):/);
  return match ? match[1] : "";
}

function grammarWarningsByPrefix(item, prefixes, warningsSource, extraTexts = []) {
  const texts = uniqueTexts([
    ...noteAnchorFragments(item),
    sentenceText(item?.sentence_id),
    ...extraTexts,
  ]);
  return warningsSource.value.filter((warning) => {
    const code = String(warning?.code || "");
    if (!prefixes.some((prefix) => code.startsWith(prefix) || code.includes(prefix))) return false;
    return warningTargetsItem(warning, item?.sentence_id, texts);
  });
}

function vocabularyWarnings(item, annotationType, fragments) {
  const texts = uniqueTexts([item?.text, ...fragments]);
  return quickWarnings.value.filter((warning) => {
    const hint = vocabularyAnnotationHint(warning);
    if (hint && hint !== annotationType) return false;
    return warningTargetsItem(warning, item?.sentence_id, texts);
  });
}

function noteWarnings(item) {
  return grammarWarningsByPrefix(item, ["grammar_"], quickWarnings);
}

function noteObservations(item) {
  return grammarWarningsByPrefix(item, ["grammar"], quickSoftWarnings, [noteAnchorFragments(item).join(" || ")]);
}

function analysisWarnings(item) {
  const texts = analysisAnchorFragments(item);
  return quickWarnings.value.filter((warning) => {
    const code = String(warning?.code || "");
    if (!code.startsWith("sentence_analysis_")) return false;
    return warningTargetsItem(warning, item?.sentence_id, texts);
  });
}

function analysisObservations(item) {
  const texts = analysisAnchorFragments(item);
  return quickSoftWarnings.value.filter((warning) => {
    const code = String(warning?.code || "");
    if (!code.startsWith("sentence_analysis_")) return false;
    return warningTargetsItem(warning, item?.sentence_id, texts);
  });
}

function vocabWarnings(item) {
  return vocabularyWarnings(item, "vocab_highlight", [item?.text]);
}

function phraseWarnings(item) {
  return vocabularyWarnings(item, "phrase_gloss", phraseAnchorFragments(item));
}

function contextWarnings(item) {
  return vocabularyWarnings(item, "context_gloss", contextAnchorFragments(item));
}

function evidenceStatus(warnings, observations = []) {
  if (Array.isArray(warnings) && warnings.length > 0) {
    return { label: `${warnings.length} 处待检查`, tone: "warning" };
  }
  if (Array.isArray(observations) && observations.length > 0) {
    return { label: `${observations.length} 条观察`, tone: "neutral" };
  }
  return { label: "命中正常", tone: "success" };
}

function scopedStatus({ sentenceId, fragments, warnings = [], observations = [] }) {
  if (hasQuickValidation.value) {
    return evidenceStatus(warnings, observations);
  }
  return simpleAnchorStatus(sentenceId, fragments);
}

function noteStatus(item) {
  return scopedStatus({
    sentenceId: item?.sentence_id,
    fragments: noteAnchorFragments(item),
    warnings: noteWarnings(item),
    observations: noteObservations(item),
  });
}

function analysisStatus(item) {
  return scopedStatus({
    sentenceId: item?.sentence_id,
    fragments: analysisAnchorFragments(item),
    warnings: analysisWarnings(item),
    observations: analysisObservations(item),
  });
}

function vocabStatus(item) {
  return scopedStatus({
    sentenceId: item?.sentence_id,
    fragments: [item?.text],
    warnings: vocabWarnings(item),
  });
}

function phraseStatus(item) {
  return scopedStatus({
    sentenceId: item?.sentence_id,
    fragments: phraseAnchorFragments(item),
    warnings: phraseWarnings(item),
  });
}

function contextStatus(item) {
  return scopedStatus({
    sentenceId: item?.sentence_id,
    fragments: contextAnchorFragments(item),
    warnings: contextWarnings(item),
  });
}

function simpleAnchorStatus(sentenceId, fragments) {
  const source = sentenceText(sentenceId);
  const anchors = uniqueTexts(fragments);
  if (!source || anchors.length === 0) return { label: "待人工复看", tone: "neutral", missing: [] };
  const missing = anchors.filter((anchor) => locateAnchorStart(source, anchor) < 0);
  if (missing.length) return { label: `${missing.length} 处待检查`, tone: "warning", missing };
  return { label: "命中正常", tone: "success", missing: [] };
}

function legacyMissing(sentenceId, fragments) {
  return simpleAnchorStatus(sentenceId, fragments).missing || [];
}
</script>

<template>
  <div v-if="!output" class="empty-output">{{ emptyText }}</div>

  <div v-else-if="nodeName === 'grammar' && hasStructuredContent" class="output-layout">
    <section v-if="generalValidationWarnings.length || generalSoftObservations.length" class="output-section">
      <article class="output-card">
        <div class="card-head">
          <div class="card-heading">
            <strong>全局校验提示</strong>
            <span class="sentence-id">{{ props.quickValidation?.validator || "未记录" }}</span>
          </div>
          <StatusPill :label="generalValidationStatus.label" :tone="generalValidationStatus.tone" />
        </div>
        <ul v-if="generalValidationWarnings.length" class="warning-list">
          <li v-for="(warning, warningIndex) in generalValidationWarnings" :key="`general-warning-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
        <ul v-if="generalSoftObservations.length" class="observation-list">
          <li v-for="(warning, warningIndex) in generalSoftObservations" :key="`general-observation-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
      </article>
    </section>
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
          <StatusPill :label="noteStatus(item).label" :tone="noteStatus(item).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), noteAnchorFragments(item))" :key="`note-segment-${index}-${segmentIndex}`">
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
            <span v-for="(span, spanIndex) in item.spans" :key="`span-${index}-${spanIndex}`" class="eval-anchor-chip tone-grammar">
              <span class="eval-anchor-chip__text">{{ dash(span?.text) }}</span>
              <span class="eval-anchor-chip__role">{{ roleLabel(span?.role) }}</span>
            </span>
          </div>
        </div>
        <ul v-if="noteWarnings(item).length" class="warning-list">
          <li v-for="(warning, warningIndex) in noteWarnings(item)" :key="`note-warning-${index}-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
        <ul v-if="noteObservations(item).length" class="observation-list">
          <li v-for="(warning, warningIndex) in noteObservations(item)" :key="`note-observation-${index}-${warningIndex}`">
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
          <StatusPill :label="analysisStatus(item).label" :tone="analysisStatus(item).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), analysisAnchorFragments(item))" :key="`analysis-segment-${index}-${segmentIndex}`">
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
        <ul v-if="analysisObservations(item).length" class="observation-list">
          <li v-for="(warning, warningIndex) in analysisObservations(item)" :key="`analysis-observation-${index}-${warningIndex}`">
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
    <section v-if="generalValidationWarnings.length || generalSoftObservations.length" class="output-section">
      <article class="output-card">
        <div class="card-head">
          <div class="card-heading">
            <strong>全局校验提示</strong>
            <span class="sentence-id">{{ props.quickValidation?.validator || "未记录" }}</span>
          </div>
          <StatusPill :label="generalValidationStatus.label" :tone="generalValidationStatus.tone" />
        </div>
        <ul v-if="generalValidationWarnings.length" class="warning-list">
          <li v-for="(warning, warningIndex) in generalValidationWarnings" :key="`general-vocab-warning-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
        <ul v-if="generalSoftObservations.length" class="observation-list">
          <li v-for="(warning, warningIndex) in generalSoftObservations" :key="`general-vocab-observation-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
      </article>
    </section>
    <section v-if="vocabHighlights.length" class="output-section">
      <header>
        <h4>Vocab Highlights</h4>
        <small>{{ vocabHighlights.length }} 条</small>
      </header>
      <article v-for="(item, index) in vocabHighlights" :key="`vocab-${index}`" class="output-card">
        <div class="card-head">
          <div class="card-heading">
            <div class="value-with-meta">
              <strong>{{ dash(item.text) }}</strong>
              <span class="field-source">{{ fieldSourceLabel("vocab_title") }}</span>
            </div>
            <span class="sentence-id">{{ dash(item.sentence_id) }}</span>
          </div>
          <StatusPill :label="vocabStatus(item).label" :tone="vocabStatus(item).tone" />
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
            <span class="evidence-label">{{ fieldSourceLabel("vocab_anchor") }}</span>
            <span class="eval-anchor-chip tone-vocab">{{ dash(item.text) }}</span>
          </div>
        </div>
        <ul v-if="vocabWarnings(item).length" class="warning-list">
          <li v-for="(warning, warningIndex) in vocabWarnings(item)" :key="`vocab-warning-${index}-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
        <ul v-else-if="!hasQuickValidation && legacyMissing(item.sentence_id, [item.text]).length" class="warning-list">
          <li>原文中未找到锚点：{{ legacyMissing(item.sentence_id, [item.text]).join("，") }}</li>
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
            <div class="value-with-meta">
              <strong>{{ dash(item.text) }}</strong>
              <span class="field-source">{{ fieldSourceLabel("phrase_title") }}</span>
            </div>
            <span class="sentence-id">{{ dash(item.sentence_id) }}</span>
          </div>
          <StatusPill :label="phraseStatus(item).label" :tone="phraseStatus(item).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), phraseAnchorFragments(item))" :key="`phrase-segment-${index}-${segmentIndex}`">
              <mark v-if="segment.highlighted" class="anchor-mark">{{ segment.text }}</mark>
              <span v-else>{{ segment.text }}</span>
            </template>
          </p>
        </div>
        <div class="evidence-row">
          <div class="evidence-summary">
            <span class="evidence-label">{{ phraseAnchorLabel(item) }}</span>
            <span v-if="!phraseAnchorFragments(item).length" class="eval-anchor-chip tone-phrase">{{ dash(item.text) }}</span>
          </div>
          <div class="evidence-summary">
            <span class="evidence-label">类型</span>
            <span class="eval-mark-type tone-phrase">{{ phraseTypeLabel(item.phrase_type) }}</span>
          </div>
        </div>
        <div v-if="phraseAnchorFragments(item).length" class="anchor-chip-row">
          <span
            v-for="(fragment, fragmentIndex) in phraseAnchorFragments(item)"
            :key="`phrase-anchor-${index}-${fragmentIndex}`"
            class="eval-anchor-chip tone-phrase"
          >
            {{ fragment }}
          </span>
        </div>
        <ul v-if="phraseWarnings(item).length" class="warning-list">
          <li v-for="(warning, warningIndex) in phraseWarnings(item)" :key="`phrase-warning-${index}-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
        <ul v-else-if="!hasQuickValidation && legacyMissing(item.sentence_id, phraseAnchorFragments(item)).length" class="warning-list">
          <li>原文中未找到锚点：{{ legacyMissing(item.sentence_id, phraseAnchorFragments(item)).join("，") }}</li>
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
            <div class="value-with-meta">
              <strong>{{ dash(item.text) }}</strong>
              <span class="field-source">{{ fieldSourceLabel("context_title") }}</span>
            </div>
            <span class="sentence-id">{{ dash(item.sentence_id) }}</span>
          </div>
          <StatusPill :label="contextStatus(item).label" :tone="contextStatus(item).tone" />
        </div>
        <div v-if="sentenceText(item.sentence_id)" class="sentence-context">
          <span class="context-label">原句</span>
          <p class="context-text">
            <template v-for="(segment, segmentIndex) in highlightSegments(sentenceText(item.sentence_id), contextAnchorFragments(item))" :key="`context-segment-${index}-${segmentIndex}`">
              <mark v-if="segment.highlighted" class="anchor-mark">{{ segment.text }}</mark>
              <span v-else>{{ segment.text }}</span>
            </template>
          </p>
        </div>
        <div class="evidence-row">
          <div class="evidence-summary">
            <span class="evidence-label">{{ fieldSourceLabel("context_anchor") }}</span>
            <span class="eval-anchor-chip tone-context">{{ dash(item.text) }}</span>
          </div>
        </div>
        <ul v-if="contextWarnings(item).length" class="warning-list">
          <li v-for="(warning, warningIndex) in contextWarnings(item)" :key="`context-warning-${index}-${warningIndex}`">
            {{ warning.message }}
          </li>
        </ul>
        <ul v-else-if="!hasQuickValidation && legacyMissing(item.sentence_id, contextAnchorFragments(item)).length" class="warning-list">
          <li>原文中未找到锚点：{{ legacyMissing(item.sentence_id, contextAnchorFragments(item)).join("，") }}</li>
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
      <article v-if="hasText(output.title)" class="output-card">
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
  padding: 8px 12px;
  border-radius: 4px;
  background: var(--theme--background);
  border: none;
  border-left: 3px solid var(--theme--primary-subdued, var(--theme--primary));
}

.context-label {
  display: block;
  margin-bottom: 4px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.context-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  font-family: "Source Serif Pro", Georgia, "Times New Roman", "Noto Serif SC", serif;
  color: var(--theme--foreground, #111827);
  letter-spacing: -0.015em;
}

.value-with-meta {
  display: grid;
  gap: 2px;
}

.field-source {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
}

.anchor-mark {
  background: color-mix(in srgb, var(--theme--warning, #e4b000) 24%, transparent);
  color: inherit;
  border-radius: 4px;
  padding: 0 2px;
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

.eval-anchor-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
  padding: 2px 10px;
  border-radius: 4px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
}

.eval-anchor-chip__text {
  font-size: 11px;
  font-weight: 600;
  color: var(--theme--foreground);
}

.eval-anchor-chip__role {
  font-size: 10px;
  color: var(--theme--foreground-subdued);
}

/* Tone styles for eval-anchor-chip */
.eval-anchor-chip.tone-vocab {
  border-color: color-mix(in srgb, #e4b000 34%, var(--theme--border-color));
  background: color-mix(in srgb, #e4b000 12%, var(--theme--background));
}
.eval-anchor-chip.tone-vocab .eval-anchor-chip__text { color: #785300; }

.eval-anchor-chip.tone-phrase {
  border-color: color-mix(in srgb, #db2777 34%, var(--theme--border-color));
  background: color-mix(in srgb, #db2777 10%, var(--theme--background));
}
.eval-anchor-chip.tone-phrase .eval-anchor-chip__text { color: #9f1239; }

.eval-anchor-chip.tone-context {
  border-color: color-mix(in srgb, #54a7de 34%, var(--theme--border-color));
  background: color-mix(in srgb, #54a7de 10%, var(--theme--background));
}
.eval-anchor-chip.tone-context .eval-anchor-chip__text { color: #285f8d; }

.eval-anchor-chip.tone-grammar {
  border-color: color-mix(in srgb, #746694 38%, var(--theme--border-color));
  background: color-mix(in srgb, #746694 10%, var(--theme--background));
}
.eval-anchor-chip.tone-grammar .eval-anchor-chip__text { color: #554777; }

.eval-anchor-chip.tone-analysis {
  border-color: color-mix(in srgb, #059669 34%, var(--theme--border-color));
  background: color-mix(in srgb, #059669 10%, var(--theme--background));
}
.eval-anchor-chip.tone-analysis .eval-anchor-chip__text { color: #065f46; }

/* Eval Mark Type (e.g. Phrase type badge) */
.eval-mark-type {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  border: 1px solid var(--theme--border-color);
  text-transform: uppercase;
  letter-spacing: 0.02em;
  white-space: nowrap;
}
.eval-mark-type.tone-phrase {
  color: #9f1239;
  border-color: color-mix(in srgb, #db2777 30%, var(--theme--border-color));
  background: color-mix(in srgb, #db2777 6%, var(--theme--background));
}

.warning-list {
  margin: 10px 0 0;
  padding-left: 18px;
  color: var(--theme--warning-color, #b45309);
  font-size: 12px;
  line-height: 1.6;
}

.observation-list {
  margin: 10px 0 0;
  padding-left: 18px;
  color: var(--theme--foreground-subdued);
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
  border-radius: 4px;
  background: var(--theme--background);
  border: none;
  border-left: 3px solid color-mix(in srgb, #059669 40%, var(--theme--border-color));
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
  color: var(--theme--foreground);
}

.chunk-main span {
  font-size: 13px;
  line-height: 1.6;
  color: var(--theme--foreground);
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
