<script setup>
import { computed } from "vue";
import {
  dash,
  normalizeWorkflowScene,
  sceneInlineMarks,
  sceneSentenceEntries,
  sceneTranslations,
} from "../composables/workflowLabFormatting.js";

const props = defineProps({
  payload: { type: [Object, Array, null], default: null },
  preparedSentences: { type: Array, default: () => [] },
  emptyText: { type: String, default: "当前没有可展示的句子证据。" },
  sideLabel: { type: String, default: "" },
});

const scene = computed(() => normalizeWorkflowScene(props.payload));
const preparedMap = computed(() => {
  const map = new Map();
  for (const item of props.preparedSentences || []) {
    if (item && item.sentence_id) {
      map.set(String(item.sentence_id), String(item.text || ""));
    }
  }
  return map;
});

function orderKey(sentenceId) {
  const raw = String(sentenceId || "");
  const match = raw.match(/(\d+)/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function originalTextFor(sentenceId) {
  return preparedMap.value.get(String(sentenceId || "")) || "";
}

const translationsBySid = computed(() => {
  const map = new Map();
  for (const item of sceneTranslations(scene.value)) {
    if (item && item.sentence_id != null) {
      map.set(String(item.sentence_id), item);
    }
  }
  return map;
});

const marksBySid = computed(() => {
  const map = new Map();
  for (const item of sceneInlineMarks(scene.value)) {
    if (item && item.anchor?.sentence_id != null) {
      const sid = String(item.anchor.sentence_id);
      if (!map.has(sid)) map.set(sid, []);
      map.get(sid).push(item);
    }
  }
  return map;
});

const entriesBySid = computed(() => {
  const map = new Map();
  for (const item of sceneSentenceEntries(scene.value)) {
    if (item && item.sentence_id != null) {
      const sid = String(item.sentence_id);
      if (!map.has(sid)) map.set(sid, []);
      map.get(sid).push(item);
    }
  }
  return map;
});

const sentenceRows = computed(() => {
  const sids = new Set();
  // 1. 任何出现在 preparedSentences 里的句子都要展示 — 哪怕完全没有输出
  for (const sid of preparedMap.value.keys()) sids.add(String(sid));
  // 2. 出现在 outputs 里的 sentence_id 也并入（可能 preparedSentences 没覆盖到的）
  for (const sid of translationsBySid.value.keys()) sids.add(String(sid));
  for (const sid of marksBySid.value.keys()) sids.add(String(sid));
  for (const sid of entriesBySid.value.keys()) sids.add(String(sid));
  return Array.from(sids)
    .map((sid) => {
      const hasTranslation = translationsBySid.value.has(sid);
      const hasMarks = marksBySid.value.has(sid);
      const hasEntries = entriesBySid.value.has(sid);
      const isEmpty = !hasTranslation && !hasMarks && !hasEntries;
      return {
        sentenceId: sid,
        originalText: originalTextFor(sid),
        translation: hasTranslation
          ? dash(translationsBySid.value.get(sid)?.translation_zh, "—")
          : "—",
        marks: hasMarks ? marksBySid.value.get(sid).map(formatMark) : [],
        entries: hasEntries ? entriesBySid.value.get(sid).map(formatEntry) : [],
        isEmpty,
      };
    })
    .sort((a, b) => orderKey(a.sentenceId) - orderKey(b.sentenceId));
});

const MARK_TYPES = {
  vocab_highlight: { label: "词汇", tone: "vocab" },
  phrase_gloss: { label: "短语", tone: "phrase" },
  context_gloss: { label: "语境", tone: "context" },
  grammar_note: { label: "语法", tone: "grammar" },
  sentence_analysis: { label: "句法", tone: "analysis" },
};

function formatMark(mark) {
  const anchor = mark?.anchor?.anchor_text || mark?.anchor?.text || mark?.lookup_text || "—";
  const rawType = mark?.annotation_type || mark?.visual_tone || "mark";
  const typeInfo = MARK_TYPES[rawType] || { label: String(rawType).toUpperCase(), tone: "neutral" };
  const extra = mark?.glossary?.zh || mark?.glossary?.gloss || mark?.glossary?.phrase_type || "";
  return {
    anchor: String(anchor),
    type: typeInfo.label,
    tone: typeInfo.tone,
    extra: extra ? String(extra) : ""
  };
}

function formatEntry(entry) {
  const label = entry?.label || entry?.entry_type || "条目";
  const content = entry?.content || entry?.title || entry?.note_zh || entry?.analysis_zh || "";
  return {
    label: String(label),
    content: content ? String(content) : "—",
  };
}
</script>

<template>
  <section class="sentence-evidence" :class="{ 'has-side': sideLabel }">
    <div v-if="!sentenceRows.length" class="empty-state">{{ emptyText }}</div>
    <ol v-else class="sentence-list">
      <li v-for="row in sentenceRows" :key="row.sentenceId" class="sentence-card" :class="{ 'is-empty': row.isEmpty }">
        <header class="sentence-head">
          <div class="sentence-id">
            <span class="side-label" v-if="sideLabel">{{ sideLabel }}</span>
            <span class="sid-text">{{ row.sentenceId }}</span>
            <span v-if="row.isEmpty" class="empty-badge" title="该句在原文里，但 workflow 没有任何输出（翻译/标注/条目全空）">无输出</span>
          </div>
          <p class="sentence-text">{{ row.originalText || "—" }}</p>
        </header>
        <dl class="sentence-grid">
          <div>
            <dt>翻译</dt>
            <dd>{{ row.translation }}</dd>
          </div>
          <div>
            <dt>标注</dt>
            <dd>
              <ul v-if="row.marks.length" class="mini-list">
                <li v-for="(mark, i) in row.marks" :key="`m-${row.sentenceId}-${i}`">
                  <span class="eval-anchor-chip" :class="mark.tone">{{ mark.anchor }}</span>
                  <span class="eval-mark-type" :class="mark.tone">{{ mark.type }}</span>
                  <span v-if="mark.extra" class="mark-extra">{{ mark.extra }}</span>
                </li>
              </ul>
              <span v-else class="empty-cell">—</span>
            </dd>
          </div>
          <div>
            <dt>条目</dt>
            <dd>
              <ul v-if="row.entries.length" class="mini-list">
                <li v-for="(entry, i) in row.entries" :key="`e-${row.sentenceId}-${i}`">
                  <strong>{{ entry.label }}</strong>
                  <span class="entry-content">{{ entry.content }}</span>
                </li>
              </ul>
              <span v-else class="empty-cell">—</span>
            </dd>
          </div>
        </dl>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.sentence-evidence {
  display: block;
}
.sentence-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}
.sentence-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 12px 14px;
  display: grid;
  gap: 10px;
}

.sentence-card.is-empty {
  border-color: color-mix(in srgb, var(--theme--warning) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 5%, var(--theme--background));
}

.empty-badge {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  border-radius: 999px;
  background: var(--theme--warning-background);
  color: var(--theme--warning);
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}
.sentence-head {
  display: grid;
  gap: 4px;
  border-bottom: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  padding-bottom: 8px;
}
.sentence-id {
  display: flex;
  align-items: center;
  gap: 8px;
}
.side-label {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 6px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  color: var(--theme--foreground-subdued);
  background: var(--theme--background-subdued);
}
.sid-text {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}
.sentence-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--theme--foreground);
}
.sentence-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}
.sentence-grid > div {
  min-width: 0;
  display: grid;
  gap: 6px;
  align-content: start;
}
.sentence-grid dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}
.sentence-grid dd {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--theme--foreground);
  overflow-wrap: anywhere;
}
.empty-cell {
  color: var(--theme--foreground-subdued);
  font-style: italic;
}
.mini-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}
.mini-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 1.55;
}
.anchor-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 999px;
  background: color-mix(in srgb, var(--theme--warning) 16%, var(--theme--background));
  color: var(--theme--foreground);
  font-weight: 600;
}
.mark-type {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.mark-extra {
  color: var(--theme--foreground);
}
.entry-content {
  color: var(--theme--foreground);
}
.empty-state {
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 8px;
  padding: 18px;
  color: var(--theme--foreground-subdued);
}
@media (max-width: 900px) {
  .sentence-grid {
    grid-template-columns: 1fr;
  }
}

.eval-anchor-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.eval-anchor-chip.tone-vocab {
  border-color: color-mix(in srgb, #e4b000 34%, var(--theme--border-color));
  background: color-mix(in srgb, #e4b000 12%, var(--theme--background));
  color: #785300;
}

.eval-anchor-chip.tone-phrase {
  border-color: color-mix(in srgb, #db2777 34%, var(--theme--border-color));
  background: color-mix(in srgb, #db2777 10%, var(--theme--background));
  color: #9f1239;
}

.eval-anchor-chip.tone-context {
  border-color: color-mix(in srgb, #54a7de 34%, var(--theme--border-color));
  background: color-mix(in srgb, #54a7de 10%, var(--theme--background));
  color: #285f8d;
}

.eval-anchor-chip.tone-grammar {
  border-color: color-mix(in srgb, #746694 38%, var(--theme--border-color));
  background: color-mix(in srgb, #746694 10%, var(--theme--background));
  color: #554777;
}

.eval-anchor-chip.tone-analysis {
  border-color: color-mix(in srgb, #059669 34%, var(--theme--border-color));
  background: color-mix(in srgb, #059669 10%, var(--theme--background));
  color: #065f46;
}

.eval-anchor-chip.tone-neutral {
  border-color: var(--theme--border-color);
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
}

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

.eval-mark-type.tone-vocab {
  color: #785300;
  border-color: color-mix(in srgb, #e4b000 30%, var(--theme--border-color));
  background: color-mix(in srgb, #e4b000 6%, var(--theme--background));
}

.eval-mark-type.tone-phrase {
  color: #9f1239;
  border-color: color-mix(in srgb, #db2777 30%, var(--theme--border-color));
  background: color-mix(in srgb, #db2777 6%, var(--theme--background));
}

.eval-mark-type.tone-context {
  color: #285f8d;
  border-color: color-mix(in srgb, #54a7de 30%, var(--theme--border-color));
  background: color-mix(in srgb, #54a7de 6%, var(--theme--background));
}

.eval-mark-type.tone-grammar {
  color: #554777;
  border-color: color-mix(in srgb, #746694 30%, var(--theme--border-color));
  background: color-mix(in srgb, #746694 6%, var(--theme--background));
}

.eval-mark-type.tone-analysis {
  color: #065f46;
  border-color: color-mix(in srgb, #059669 30%, var(--theme--border-color));
  background: color-mix(in srgb, #059669 6%, var(--theme--background));
}

</style>
