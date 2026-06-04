<script setup>
import { computed } from "vue";
import { dash, normalizeWorkflowScene, sceneInlineMarks, sceneSentenceEntries, sceneTranslations } from "../composables/workflowLabFormatting.js";

const props = defineProps({
  payload: { type: [Object, Array, null], default: null },
  preparedSentences: { type: Array, default: () => [] },
  emptyText: { type: String, default: "当前没有可展示的句子证据。" },
});

const scene = computed(() => normalizeWorkflowScene(props.payload));

const sentenceMap = computed(() => {
  const map = new Map();
  const candidates = [
    props.preparedSentences,
    scene.value?.article?.sentences,
  ];
  for (const items of candidates) {
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      if (!item?.sentence_id) continue;
      if (!map.has(String(item.sentence_id))) {
        map.set(String(item.sentence_id), String(item.text || item.source_text || ""));
      }
    }
  }
  return map;
});

const translationsBySid = computed(() => {
  const map = new Map();
  for (const item of sceneTranslations(scene.value)) {
    if (item?.sentence_id == null) continue;
    map.set(String(item.sentence_id), String(item.translation_zh || item.translation || ""));
  }
  return map;
});

const inlineMarksBySid = computed(() => {
  const map = new Map();
  for (const item of sceneInlineMarks(scene.value)) {
    if (item?.anchor?.sentence_id == null) continue;
    const sid = String(item.anchor.sentence_id);
    if (!map.has(sid)) map.set(sid, []);
    map.get(sid).push(item);
  }
  return map;
});

const entriesBySid = computed(() => {
  const map = new Map();
  for (const item of sceneSentenceEntries(scene.value)) {
    if (item?.sentence_id == null) continue;
    const sid = String(item.sentence_id);
    if (!map.has(sid)) map.set(sid, []);
    map.get(sid).push(item);
  }
  return map;
});

function lexicalMarkTypeLabel(mark) {
  switch (mark?.annotation_type) {
    case "vocab_highlight":
      return "词汇";
    case "phrase_gloss":
      return "短语";
    case "context_gloss":
      return "语境";
    default:
      return mark?.annotation_type || "标注";
  }
}

function grammarEntryTypeLabel(entry) {
  switch (entry?.entry_type) {
    case "grammar_note":
      return "语法";
    case "sentence_analysis":
      return "句法";
    default:
      return entry?.entry_type || "条目";
  }
}

function noteAnchorText(item) {
  return item?.anchor?.anchor_text || item?.anchor?.text || item?.lookup_text || item?.label || item?.title || "—";
}

function lexicalMarkSummary(mark) {
  return mark?.glossary?.zh
    || mark?.glossary?.gloss
    || mark?.lookup_text
    || mark?.anchor?.anchor_text
    || "未提供释义";
}

function lexicalMarkDetail(mark) {
  return mark?.glossary?.reason || mark?.glossary?.phrase_type || "";
}

function parseSentenceAnalysisContent(content) {
  const raw = String(content || "").replace(/\r\n/g, "\n").trim();
  if (!raw) return { analysis: "—", chunks: [] };
  const lines = raw.split("\n");
  const analysisLines = [];
  const chunks = [];
  for (const line of lines) {
    const trimmed = line.trim();
    const strictMatch = trimmed.match(/^-+\s*\*\*(\d+)\.\s*(.+?)\*\*[：:]\s*`(.+?)`\s*$/);
    const looseMatch = strictMatch || trimmed.match(/^-+\s*\*\*(\d+)\.\s*(.+?)\*\*[：:]\s*(.+?)\s*$/);
    if (looseMatch) {
      chunks.push({
        order: Number(looseMatch[1]) || chunks.length + 1,
        label: looseMatch[2]?.trim() || "未命名",
        text: looseMatch[3]?.trim() || "—",
      });
      continue;
    }
    analysisLines.push(line);
  }
  return {
    analysis: analysisLines.join("\n").trim() || raw,
    chunks,
  };
}

function lexicalToneFromAnnotationType(annotationType) {
  if (annotationType === "phrase_gloss") return "tone-phrase";
  if (annotationType === "context_gloss") return "tone-context";
  return "tone-vocab";
}

function grammarToneFromEntryType(entryType) {
  if (entryType === "sentence_analysis") return "tone-sentence-analysis";
  return "tone-grammar-note";
}

function orderKey(sentenceId) {
  const raw = String(sentenceId || "");
  const match = raw.match(/(\d+)/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function buildAnchorGroups(marks = []) {
  const groups = new Map();
  for (const mark of marks) {
    const anchorText = noteAnchorText(mark);
    const occurrence = Number(mark?.anchor?.occurrence) || 1;
    const key = `${anchorText}::${occurrence}`;
    if (!groups.has(key)) {
      groups.set(key, {
        anchorText,
        occurrence,
        types: new Set(),
      });
    }
    groups.get(key).types.add(String(mark?.annotation_type || ""));
  }
  return Array.from(groups.values()).map((group) => {
    const types = [...group.types];
    const hasGrammar = types.includes("grammar_note");
    const lexicalTypes = types.filter((type) => type && type !== "grammar_note");
    if (hasGrammar && lexicalTypes.length > 0) {
      return {
        ...group,
        tone: lexicalToneFromAnnotationType(lexicalTypes[0]).replace("tone-", "tone-mixed-"),
      };
    }
    if (hasGrammar) {
      return { ...group, tone: "tone-grammar-note" };
    }
    if (lexicalTypes.length > 1) {
      return { ...group, tone: "tone-lexical-mixed" };
    }
    return { ...group, tone: lexicalToneFromAnnotationType(lexicalTypes[0]) };
  });
}

function locateAnchor(text, anchorText, occurrence = 1) {
  const source = String(text || "");
  const needle = String(anchorText || "");
  if (!source || !needle) return -1;
  const lowerSource = source.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  let fromIndex = 0;
  let hitCount = 0;
  while (fromIndex <= lowerSource.length) {
    const found = lowerSource.indexOf(lowerNeedle, fromIndex);
    if (found < 0) return -1;
    hitCount += 1;
    if (hitCount >= occurrence) return found;
    fromIndex = found + lowerNeedle.length;
  }
  return -1;
}

function highlightSegments(text, fragments) {
  const source = String(text || "");
  const anchors = Array.isArray(fragments)
    ? fragments.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (!source || anchors.length === 0) {
    return [{ text: source || "—", highlighted: false }];
  }

  const matches = [];
  for (const anchor of anchors) {
    const start = source.indexOf(anchor);
    if (start >= 0) {
      matches.push({ start, end: start + anchor.length });
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

function sentenceSegments(text, marks = []) {
  const source = String(text || "");
  if (!source) return [{ text: "—", tone: "plain" }];
  const groups = buildAnchorGroups(marks)
    .map((group) => {
      const start = locateAnchor(source, group.anchorText, group.occurrence);
      return start >= 0
        ? { ...group, start, end: start + group.anchorText.length }
        : null;
    })
    .filter(Boolean)
    .sort((left, right) => left.start - right.start || (right.end - right.start) - (left.end - left.start));

  const ranges = [];
  let cursor = 0;
  for (const group of groups) {
    if (group.start < cursor) continue;
    if (group.start > cursor) {
      ranges.push({ text: source.slice(cursor, group.start), tone: "plain" });
    }
    ranges.push({
      text: source.slice(group.start, group.end),
      tone: group.tone,
      anchorText: group.anchorText,
    });
    cursor = group.end;
  }
  if (cursor < source.length) {
    ranges.push({ text: source.slice(cursor), tone: "plain" });
  }
  return ranges.length ? ranges : [{ text: source, tone: "plain" }];
}

const sentenceRows = computed(() => {
  const allSentenceIds = new Set([
    ...sentenceMap.value.keys(),
    ...translationsBySid.value.keys(),
    ...inlineMarksBySid.value.keys(),
    ...entriesBySid.value.keys(),
  ]);

  return Array.from(allSentenceIds)
    .map((sentenceId) => {
      const originalText = sentenceMap.value.get(sentenceId) || "";
      const marks = inlineMarksBySid.value.get(sentenceId) || [];
      const entries = entriesBySid.value.get(sentenceId) || [];
      const lexicalMarks = marks
        .filter((item) => item?.annotation_type !== "grammar_note")
        .map((item) => ({
          anchor: noteAnchorText(item),
          typeLabel: lexicalMarkTypeLabel(item),
          summary: lexicalMarkSummary(item),
          detail: lexicalMarkDetail(item),
          tone: lexicalToneFromAnnotationType(item?.annotation_type),
        }));
      const grammarMarks = marks
        .filter((item) => item?.annotation_type === "grammar_note")
        .map((item) => ({
          anchor: noteAnchorText(item),
          label: "语法",
          content: lexicalMarkSummary(item),
          detail: lexicalMarkDetail(item),
          tone: "tone-grammar-note",
        }));
      const grammarEntries = entries
        .filter((item) => item?.entry_type === "grammar_note" || item?.entry_type === "sentence_analysis")
        .map((item) => {
          const rawChunks = Array.isArray(item?.chunks)
            ? item.chunks
              .map((chunk) => ({
                order: Number(chunk?.order) || null,
                label: String(chunk?.label || "").trim(),
                text: String(chunk?.text || "").trim(),
                occurrence: Number(chunk?.occurrence) || null,
              }))
              .filter((chunk) => chunk.text)
            : [];
          const structured = item?.entry_type === "sentence_analysis" && rawChunks.length === 0
            ? parseSentenceAnalysisContent(String(item?.content || ""))
            : null;
          const chunks = rawChunks.length
            ? rawChunks
            : Array.isArray(structured?.chunks)
              ? structured.chunks
              : [];
          const missingChunks = chunks
            .map((chunk) => String(chunk?.text || "").trim())
            .filter(Boolean)
            .filter((chunkText) => !originalText || !originalText.includes(chunkText));
          return {
            anchor: noteAnchorText(item),
            label: grammarEntryTypeLabel(item),
            title: item?.label || item?.title || grammarEntryTypeLabel(item),
            content: String(item?.content || "—"),
            tone: grammarToneFromEntryType(item?.entry_type),
            analysisText: String(item?.analysis_text || structured?.analysis || ""),
            chunks,
            missingChunks,
            highlightedSentence: highlightSegments(originalText, chunks.map((chunk) => chunk?.text)),
          };
        });
      const supplementalEntries = entries
        .filter((item) => item?.entry_type !== "grammar_note" && item?.entry_type !== "sentence_analysis")
        .map((item) => ({
          anchor: noteAnchorText(item),
          label: grammarEntryTypeLabel(item),
          title: item?.label || item?.title || grammarEntryTypeLabel(item),
          content: String(item?.content || "—"),
        }));
      return {
        sentenceId,
        originalText,
        originalSegments: sentenceSegments(originalText, marks),
        translation: translationsBySid.value.get(sentenceId) || "",
        lexicalMarks,
        grammarMarks,
        grammarEntries,
        supplementalEntries,
      };
    })
    .sort((left, right) => orderKey(left.sentenceId) - orderKey(right.sentenceId));
});
</script>

<template>
  <section class="sentence-notebook">
    <div v-if="!sentenceRows.length" class="empty-state">{{ emptyText }}</div>
    <ol v-else class="sentence-list">
      <li v-for="row in sentenceRows" :key="row.sentenceId" class="sentence-card">
        <header class="sentence-head">
          <span class="sentence-id">{{ row.sentenceId }}</span>
          <div class="sentence-copy">
            <p class="source-line" aria-label="原句">
              <template v-for="(segment, index) in row.originalSegments" :key="`${row.sentenceId}-${index}`">
                <span class="source-segment" :class="segment.tone">
                  {{ segment.text }}
                </span>
              </template>
            </p>
            <div class="translation-block">
              <span>译文</span>
              <p>{{ dash(row.translation, "—") }}</p>
            </div>
          </div>
        </header>

        <div class="annotation-stack">
          <section class="annotation-group vocab-group">
            <div class="group-head">
              <div class="group-head-main">
                <strong>词汇 / 短语标注</strong>
                <div class="type-legend" aria-label="词汇标注类型">
                  <span class="legend-pill tone-vocab">词汇</span>
                  <span class="legend-pill tone-phrase">短语</span>
                  <span class="legend-pill tone-context">语境</span>
                </div>
              </div>
              <small>{{ row.lexicalMarks.length }} 条</small>
            </div>
            <div v-if="row.lexicalMarks.length" class="note-list">
              <article
                v-for="(mark, index) in row.lexicalMarks"
                :key="`lexical-${row.sentenceId}-${index}`"
                class="note-card lexical-card"
                :class="mark.tone"
              >
                <div class="note-head">
                  <span class="anchor-chip" :class="mark.tone">{{ mark.anchor }}</span>
                  <span class="type-chip" :class="mark.tone">{{ mark.typeLabel }}</span>
                </div>
                <p class="note-body">{{ mark.summary }}</p>
                <p v-if="mark.detail" class="note-detail">{{ mark.detail }}</p>
              </article>
            </div>
            <p v-else class="empty-line">这句没有词汇或短语标注。</p>
          </section>

          <section class="annotation-group grammar-group">
            <div class="group-head">
              <div class="group-head-main">
                <strong>语法 / 句法标注</strong>
                <div class="type-legend" aria-label="语法标注类型">
                  <span class="legend-pill tone-grammar-note">语法</span>
                  <span class="legend-pill tone-sentence-analysis">句法</span>
                </div>
              </div>
              <small>{{ row.grammarMarks.length + row.grammarEntries.length }} 条</small>
            </div>
            <div v-if="row.grammarMarks.length || row.grammarEntries.length" class="note-list">
              <article
                v-for="(mark, index) in row.grammarMarks"
                :key="`grammar-mark-${row.sentenceId}-${index}`"
                class="note-card grammar-card compact"
                :class="mark.tone"
              >
                <div class="note-head">
                  <span class="anchor-chip" :class="mark.tone">{{ mark.anchor }}</span>
                  <span class="type-chip" :class="mark.tone">{{ mark.label }}</span>
                </div>
                <p class="note-body">{{ mark.content }}</p>
                <p v-if="mark.detail" class="note-detail">{{ mark.detail }}</p>
              </article>

              <article
                v-for="(entry, index) in row.grammarEntries"
                :key="`grammar-entry-${row.sentenceId}-${index}`"
                class="note-card grammar-card"
                :class="entry.tone"
              >
                <div class="note-head">
                  <span class="anchor-chip" :class="entry.tone">{{ entry.anchor }}</span>
                  <span class="type-chip" :class="entry.tone">{{ entry.label }}</span>
                </div>
                <strong class="entry-title">{{ entry.title }}</strong>
                <template v-if="entry.tone === 'tone-sentence-analysis'">
                  <div v-if="row.originalText" class="analysis-context">
                    <span class="analysis-context-label">原句定位</span>
                    <p class="analysis-context-text">
                      <template
                        v-for="(segment, segmentIndex) in entry.highlightedSentence"
                        :key="`analysis-segment-${row.sentenceId}-${index}-${segmentIndex}`"
                      >
                        <mark v-if="segment.highlighted" class="analysis-mark">{{ segment.text }}</mark>
                        <span v-else>{{ segment.text }}</span>
                      </template>
                    </p>
                  </div>

                  <div class="analysis-evidence-row">
                    <div class="analysis-evidence-summary">
                      <span class="analysis-evidence-label">拆解块</span>
                      <span class="analysis-evidence-value">{{ entry.chunks.length }} 段</span>
                    </div>
                  </div>

                  <ul v-if="entry.missingChunks.length" class="warning-list">
                    <li v-for="(chunkText, chunkIndex) in entry.missingChunks" :key="`missing-chunk-${row.sentenceId}-${index}-${chunkIndex}`">
                      sentence_analysis: chunk text '{{ chunkText }}' not found in sentence {{ row.sentenceId }}
                    </li>
                  </ul>

                  <p class="note-body preserve-lines">{{ entry.analysisText || entry.content }}</p>

                  <div v-if="entry.chunks.length" class="chunk-list">
                    <div
                      v-for="(chunk, chunkIndex) in entry.chunks"
                      :key="`chunk-${row.sentenceId}-${index}-${chunkIndex}`"
                      class="chunk-row"
                    >
                      <span class="chunk-order">{{ chunk.order || chunkIndex + 1 }}</span>
                      <div class="chunk-main">
                        <strong>{{ chunk.label || "未命名" }}</strong>
                        <span>{{ chunk.text || "—" }}</span>
                      </div>
                    </div>
                  </div>
                </template>
                <p v-else class="note-body preserve-lines">{{ entry.content }}</p>
              </article>
            </div>
            <p v-else class="empty-line">这句没有语法或句法标注。</p>
          </section>

          <section v-if="row.supplementalEntries.length" class="annotation-group extra-group">
            <div class="group-head">
              <strong>补充条目</strong>
              <small>{{ row.supplementalEntries.length }} 条</small>
            </div>
            <div class="note-list">
              <article
                v-for="(entry, index) in row.supplementalEntries"
                :key="`supplemental-${row.sentenceId}-${index}`"
                class="note-card extra-card"
              >
                <div class="note-head">
                  <span class="anchor-chip extra">{{ entry.anchor }}</span>
                  <span class="type-chip extra">{{ entry.label }}</span>
                </div>
                <strong class="entry-title">{{ entry.title }}</strong>
                <p class="note-body preserve-lines">{{ entry.content }}</p>
              </article>
            </div>
          </section>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.sentence-notebook {
  display: grid;
  gap: 14px;
}

.sentence-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 14px;
}

.sentence-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 12px;
  background: color-mix(in srgb, var(--theme--background) 92%, #faf7f0);
  padding: 16px;
  display: grid;
  gap: 16px;
}

.sentence-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.sentence-id {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 36px;
  min-height: 28px;
  border-radius: 999px;
  background: color-mix(in srgb, #e4b000 16%, var(--theme--background));
  color: #8a5900;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.sentence-copy {
  display: grid;
  gap: 12px;
}

.source-line {
  margin: 0;
  color: var(--theme--foreground);
  font-family: "Source Serif Pro", Georgia, "Times New Roman", serif;
  font-size: 16px;
  line-height: 1.8;
  text-wrap: pretty;
  overflow-wrap: anywhere;
}

.source-segment {
  border-radius: 6px;
  padding: 0 1px;
}

.source-segment.tone-vocab {
  background: color-mix(in srgb, #e4b000 22%, var(--theme--background));
}

.source-segment.tone-phrase {
  background: color-mix(in srgb, #ff9f43 20%, var(--theme--background));
}

.source-segment.tone-context {
  background: color-mix(in srgb, #54a7de 18%, var(--theme--background));
}

.source-segment.tone-grammar-note {
  box-shadow: inset 0 -2px 0 0 color-mix(in srgb, #746694 66%, transparent);
}

.source-segment.tone-lexical-mixed {
  background: linear-gradient(
    180deg,
    color-mix(in srgb, #e4b000 18%, var(--theme--background)) 0%,
    color-mix(in srgb, #ff9f43 18%, var(--theme--background)) 100%
  );
}

.source-segment.tone-mixed-vocab {
  background: color-mix(in srgb, #e4b000 16%, var(--theme--background));
  box-shadow: inset 0 -2px 0 0 color-mix(in srgb, #746694 72%, transparent);
}

.source-segment.tone-mixed-phrase {
  background: color-mix(in srgb, #ff9f43 14%, var(--theme--background));
  box-shadow: inset 0 -2px 0 0 color-mix(in srgb, #746694 72%, transparent);
}

.source-segment.tone-mixed-context {
  background: color-mix(in srgb, #54a7de 14%, var(--theme--background));
  box-shadow: inset 0 -2px 0 0 color-mix(in srgb, #746694 72%, transparent);
}

.translation-block {
  position: relative;
  border: 1px solid color-mix(in srgb, var(--theme--primary) 18%, var(--theme--border-color));
  border-radius: 10px;
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
  padding: 12px 14px 12px 20px;
}

.translation-block::before {
  content: "";
  position: absolute;
  top: 14px;
  left: 10px;
  bottom: 14px;
  width: 2px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--theme--primary) 55%, var(--theme--border-color));
}

.translation-block span,
.group-head small,
.empty-line {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.translation-block span {
  display: inline-block;
  margin-bottom: 6px;
  font-weight: 700;
}

.translation-block p,
.note-body,
.note-detail {
  margin: 0;
  overflow-wrap: anywhere;
}

.translation-block p {
  color: var(--theme--foreground);
  font-size: 14px;
  line-height: 1.7;
}

.annotation-stack {
  display: grid;
  gap: 14px;
}

.annotation-group {
  display: grid;
  gap: 10px;
}

.group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.group-head-main {
  display: grid;
  gap: 6px;
}

.group-head strong {
  font-size: 13px;
}

.type-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.legend-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.2;
  border: 1px solid var(--theme--border-color);
}

.note-list {
  display: grid;
  gap: 10px;
}

.note-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 12px 14px;
  display: grid;
  gap: 8px;
}

.lexical-card.tone-vocab {
  border-color: color-mix(in srgb, #e4b000 30%, var(--theme--border-color));
  background: color-mix(in srgb, #e4b000 4%, var(--theme--background));
}

.lexical-card.tone-phrase {
  border-color: color-mix(in srgb, #ff9f43 36%, var(--theme--border-color));
  background: color-mix(in srgb, #ff9f43 6%, var(--theme--background));
}

.lexical-card.tone-context {
  border-color: color-mix(in srgb, #54a7de 34%, var(--theme--border-color));
  background: color-mix(in srgb, #54a7de 5%, var(--theme--background));
}

.grammar-card.tone-grammar-note {
  border-color: color-mix(in srgb, #746694 28%, var(--theme--border-color));
  background: color-mix(in srgb, #746694 4%, var(--theme--background));
}

.grammar-card.tone-sentence-analysis {
  border-color: color-mix(in srgb, #2f8a7d 30%, var(--theme--border-color));
  background: color-mix(in srgb, #2f8a7d 5%, var(--theme--background));
}

.extra-card {
  border-color: color-mix(in srgb, #3c8c68 24%, var(--theme--border-color));
}

.note-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.analysis-context {
  display: grid;
  gap: 6px;
  border: 1px solid color-mix(in srgb, #2f8a7d 20%, var(--theme--border-color));
  border-radius: 10px;
  background: color-mix(in srgb, #2f8a7d 4%, var(--theme--background));
  padding: 10px 12px;
}

.analysis-context-label,
.analysis-evidence-label {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.analysis-context-text {
  margin: 0;
  color: var(--theme--foreground);
  font-family: "Source Serif Pro", Georgia, "Times New Roman", serif;
  font-size: 15px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.analysis-mark {
  border-radius: 6px;
  background: color-mix(in srgb, #2f8a7d 18%, var(--theme--background));
  box-shadow: inset 0 -2px 0 0 color-mix(in srgb, #2f8a7d 66%, transparent);
  color: inherit;
  padding: 0 1px;
}

.analysis-evidence-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.analysis-evidence-summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 26px;
  padding: 0 10px;
  border: 1px solid color-mix(in srgb, #2f8a7d 28%, var(--theme--border-color));
  border-radius: 999px;
  background: color-mix(in srgb, #2f8a7d 6%, var(--theme--background));
}

.analysis-evidence-value {
  color: var(--theme--foreground);
  font-size: 12px;
  font-weight: 700;
}

.warning-list {
  margin: 0;
  padding-left: 18px;
  color: #b86400;
  display: grid;
  gap: 4px;
  font-size: 12px;
  line-height: 1.55;
}

.chunk-list {
  display: grid;
  gap: 8px;
}

.chunk-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  border: 1px solid color-mix(in srgb, #2f8a7d 18%, var(--theme--border-color));
  border-radius: 10px;
  background: color-mix(in srgb, #2f8a7d 3%, var(--theme--background));
  padding: 10px 12px;
}

.chunk-order {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  min-height: 28px;
  border-radius: 8px;
  background: color-mix(in srgb, #2f8a7d 12%, var(--theme--background));
  color: #245f56;
  font-size: 12px;
  font-weight: 800;
}

.chunk-main {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.chunk-main strong {
  color: var(--theme--foreground);
  font-size: 13px;
}

.chunk-main span {
  color: var(--theme--foreground);
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.anchor-chip,
.type-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.2;
}

.anchor-chip {
  border: 1px solid color-mix(in srgb, #e4b000 34%, var(--theme--border-color));
  background: color-mix(in srgb, #e4b000 15%, var(--theme--background));
  color: #785300;
}

.anchor-chip.tone-vocab,
.type-chip.tone-vocab,
.legend-pill.tone-vocab {
  border-color: color-mix(in srgb, #e4b000 34%, var(--theme--border-color));
  background: color-mix(in srgb, #e4b000 15%, var(--theme--background));
  color: #785300;
}

.anchor-chip.tone-phrase,
.type-chip.tone-phrase,
.legend-pill.tone-phrase {
  border-color: color-mix(in srgb, #ff9f43 34%, var(--theme--border-color));
  background: color-mix(in srgb, #ff9f43 12%, var(--theme--background));
  color: #9a4f00;
}

.anchor-chip.tone-context,
.type-chip.tone-context,
.legend-pill.tone-context {
  border-color: color-mix(in srgb, #54a7de 34%, var(--theme--border-color));
  background: color-mix(in srgb, #54a7de 12%, var(--theme--background));
  color: #285f8d;
}

.anchor-chip.tone-grammar-note,
.type-chip.tone-grammar-note,
.legend-pill.tone-grammar-note {
  border-color: color-mix(in srgb, #746694 38%, var(--theme--border-color));
  background: color-mix(in srgb, #746694 12%, var(--theme--background));
  color: #554777;
}

.anchor-chip.tone-sentence-analysis,
.type-chip.tone-sentence-analysis,
.legend-pill.tone-sentence-analysis {
  border-color: color-mix(in srgb, #2f8a7d 34%, var(--theme--border-color));
  background: color-mix(in srgb, #2f8a7d 12%, var(--theme--background));
  color: #245f56;
}

.anchor-chip.extra,
.type-chip.extra {
  border-color: color-mix(in srgb, #3c8c68 28%, var(--theme--border-color));
  background: color-mix(in srgb, #3c8c68 10%, var(--theme--background));
  color: #2d6b4f;
}

.type-chip {
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
}

.entry-title {
  color: var(--theme--foreground);
  font-size: 13px;
}

.note-body {
  color: var(--theme--foreground);
  font-size: 13px;
  line-height: 1.65;
}

.note-detail {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.6;
}

.preserve-lines {
  white-space: pre-wrap;
}

.empty-line,
.empty-state {
  margin: 0;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--theme--background-subdued);
  line-height: 1.6;
}

@media (max-width: 900px) {
  .sentence-head {
    grid-template-columns: 1fr;
  }
}
</style>
