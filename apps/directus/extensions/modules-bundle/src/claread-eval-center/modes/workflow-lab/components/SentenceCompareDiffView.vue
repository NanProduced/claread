<script setup>
import { computed } from "vue";
import {
  normalizeWorkflowScene,
  sceneInlineMarks,
  sceneSentenceEntries,
  sceneTranslations,
} from "../composables/workflowLabFormatting.js";

const props = defineProps({
  baselineArtifact: { type: [Object, Array, null], default: null },
  candidateArtifact: { type: [Object, Array, null], default: null },
  preparedSentences: { type: Array, default: () => [] },
  compareCase: { type: Object, default: null },
  emptyText: { type: String, default: "选择 baseline 与候选差异句后，这里会逐句显示差异。" },
});

const baselineScene = computed(() => normalizeWorkflowScene(props.baselineArtifact));
const candidateScene = computed(() => normalizeWorkflowScene(props.candidateArtifact));

function sceneSentenceTextMap(artifact, scene) {
  const map = new Map();
  const candidates = [
    scene?.article?.sentences,
    artifact?.output?.article?.sentences,
    artifact?.render_scene?.article?.sentences,
    artifact?.input_snapshot?.article?.sentences,
    artifact?.input_snapshot?.prepared_sentences,
    artifact?.prepared_sentences,
  ];
  for (const candidate of candidates) {
    if (!Array.isArray(candidate)) continue;
    for (const item of candidate) {
      const sid = item?.sentence_id;
      const text = item?.text || item?.source_text || item?.original_text || "";
      if (sid != null && text && !map.has(String(sid))) {
        map.set(String(sid), String(text));
      }
    }
  }
  return map;
}

const preparedMap = computed(() => {
  const map = new Map();
  for (const item of props.preparedSentences || []) {
    if (item && item.sentence_id != null) {
      map.set(String(item.sentence_id), String(item.text || ""));
    }
  }
  return map;
});

const baselineSentenceMap = computed(() => sceneSentenceTextMap(props.baselineArtifact, baselineScene.value));
const candidateSentenceMap = computed(() => sceneSentenceTextMap(props.candidateArtifact, candidateScene.value));

function orderKey(sentenceId) {
  const raw = String(sentenceId || "");
  const match = raw.match(/(\d+)/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function collectBySentence(scene) {
  const translations = new Map();
  for (const item of sceneTranslations(scene)) {
    if (item && item.sentence_id != null) translations.set(String(item.sentence_id), item);
  }
  const marks = new Map();
  for (const item of sceneInlineMarks(scene)) {
    if (item && item.anchor?.sentence_id != null) {
      const sid = String(item.anchor.sentence_id);
      if (!marks.has(sid)) marks.set(sid, []);
      marks.get(sid).push(item);
    }
  }
  const entries = new Map();
  for (const item of sceneSentenceEntries(scene)) {
    if (item && item.sentence_id != null) {
      const sid = String(item.sentence_id);
      if (!entries.has(sid)) entries.set(sid, []);
      entries.get(sid).push(item);
    }
  }
  return { translations, marks, entries };
}

const baselineBySid = computed(() => collectBySentence(baselineScene.value));
const candidateBySid = computed(() => collectBySentence(candidateScene.value));

function formatMark(mark) {
  const anchor = mark?.anchor?.anchor_text || mark?.anchor?.text || mark?.lookup_text || "—";
  const type = mark?.annotation_type || mark?.visual_tone || "mark";
  const extra = mark?.glossary?.zh || mark?.glossary?.gloss || mark?.glossary?.phrase_type || "";
  return { anchor: String(anchor), type: String(type), extra: extra ? String(extra) : "" };
}

function formatEntry(entry) {
  const label = entry?.label || entry?.entry_type || "条目";
  const content = entry?.content || entry?.title || entry?.note_zh || entry?.analysis_zh || "";
  return { label: String(label), content: content ? String(content) : "—" };
}

function translationFor(bySid, sid) {
  return bySid.translations.get(sid)?.translation_zh || null;
}

function marksFor(bySid, sid) {
  return (bySid.marks.get(sid) || []).map(formatMark);
}

function entriesFor(bySid, sid) {
  return (bySid.entries.get(sid) || []).map(formatEntry);
}

const hasAnyData = computed(() => Boolean(
  baselineScene.value
  || candidateScene.value
  || (props.baselineArtifact && (props.baselineArtifact.translations || props.baselineArtifact.render_scene))
  || (props.candidateArtifact && (props.candidateArtifact.translations || props.candidateArtifact.render_scene))
));

const allBaselineSids = computed(() => {
  const set = new Set();
  for (const sid of baselineBySid.value.translations.keys()) set.add(sid);
  for (const sid of baselineBySid.value.marks.keys()) set.add(sid);
  for (const sid of baselineBySid.value.entries.keys()) set.add(sid);
  return set;
});

const allCandidateSids = computed(() => {
  const set = new Set();
  for (const sid of candidateBySid.value.translations.keys()) set.add(sid);
  for (const sid of candidateBySid.value.marks.keys()) set.add(sid);
  for (const sid of candidateBySid.value.entries.keys()) set.add(sid);
  return set;
});

const sharedSids = computed(() => {
  // 真正的 intersection:在 baseline 和 candidate 两边都至少有一类输出
  const result = [];
  for (const sid of allBaselineSids.value) {
    if (allCandidateSids.value.has(sid)) result.push(sid);
  }
  return result.sort((a, b) => orderKey(a) - orderKey(b));
});

const baselineOnlySids = computed(() => {
  // baseline 有但 candidate 完全没有（任何字段都没有）
  const result = [];
  for (const sid of allBaselineSids.value) {
    if (!allCandidateSids.value.has(sid)) result.push(sid);
  }
  return result.sort((a, b) => orderKey(a) - orderKey(b));
});

const candidateOnlySids = computed(() => {
  // candidate 有但 baseline 完全没有
  const result = [];
  for (const sid of allCandidateSids.value) {
    if (!allBaselineSids.value.has(sid)) result.push(sid);
  }
  return result.sort((a, b) => orderKey(a) - orderKey(b));
});

function isFieldChanged(bValue, cValue) {
  if (bValue == null && cValue == null) return false;
  if (bValue == null || cValue == null) return true;
  return String(bValue) !== String(cValue);
}

function changedFieldLabels(row) {
  const labels = [];
  if (row.translation.changed) labels.push("翻译");
  if (row.marks.changed) labels.push("标注");
  if (row.entries.changed) labels.push("条目");
  return labels;
}

function makeRow(sid) {
  const bT = translationFor(baselineBySid.value, sid);
  const cT = translationFor(candidateBySid.value, sid);
  const bM = marksFor(baselineBySid.value, sid);
  const cM = marksFor(candidateBySid.value, sid);
  const bE = entriesFor(baselineBySid.value, sid);
  const cE = entriesFor(candidateBySid.value, sid);
  return {
    sid,
    text: preparedMap.value.get(sid)
      || baselineSentenceMap.value.get(sid)
      || candidateSentenceMap.value.get(sid)
      || "—",
    translation: {
      baseline: bT,
      candidate: cT,
      changed: isFieldChanged(bT, cT),
    },
    marks: {
      baseline: bM,
      candidate: cM,
      changed: JSON.stringify(bM) !== JSON.stringify(cM),
    },
    entries: {
      baseline: bE,
      candidate: cE,
      changed: JSON.stringify(bE) !== JSON.stringify(cE),
    },
    changedFields: [],
    changed: isFieldChanged(bT, cT) || JSON.stringify(bM) !== JSON.stringify(cM) || JSON.stringify(bE) !== JSON.stringify(cE),
  };
}

const sharedRows = computed(() => sharedSids.value.map((sid) => {
  const row = makeRow(sid);
  row.changedFields = changedFieldLabels(row);
  return row;
}));
const baselineOnlyRows = computed(() => baselineOnlySids.value.map((sid) => ({
  sid,
  text: preparedMap.value.get(sid) || baselineSentenceMap.value.get(sid) || candidateSentenceMap.value.get(sid) || "—",
  translation: translationFor(baselineBySid.value, sid),
  marks: marksFor(baselineBySid.value, sid),
  entries: entriesFor(baselineBySid.value, sid),
})));
const candidateOnlyRows = computed(() => candidateOnlySids.value.map((sid) => ({
  sid,
  text: preparedMap.value.get(sid) || candidateSentenceMap.value.get(sid) || baselineSentenceMap.value.get(sid) || "—",
  translation: translationFor(candidateBySid.value, sid),
  marks: marksFor(candidateBySid.value, sid),
  entries: entriesFor(candidateBySid.value, sid),
})));

const changedRows = computed(() => sharedRows.value.filter((row) => row.changed));
const stableRows = computed(() => sharedRows.value.filter((row) => !row.changed));
const overviewCards = computed(() => ([
  { key: "changed", label: "发生变化", value: changedRows.value.length, tone: "primary" },
  { key: "stable", label: "两侧一致", value: stableRows.value.length, tone: "neutral" },
  { key: "baseline_only", label: "仅 baseline", value: baselineOnlyRows.value.length, tone: "danger" },
  { key: "candidate_only", label: "仅 candidate", value: candidateOnlyRows.value.length, tone: "success" },
]).filter((item) => item.value > 0));
</script>

<template>
  <section class="sentence-diff-view">
    <div v-if="!hasAnyData" class="empty-state">{{ emptyText }}</div>
    <template v-else>
      <dl v-if="overviewCards.length" class="overview-grid">
        <div v-for="card in overviewCards" :key="card.key" :class="`overview-card is-${card.tone}`">
          <dt>{{ card.label }}</dt>
          <dd>{{ card.value }}</dd>
        </div>
      </dl>

      <section v-if="changedRows.length" class="focus-section">
        <h4>双侧都有且发生变化（{{ changedRows.length }}）</h4>
        <ol class="diff-list">
        <li
          v-for="row in changedRows"
          :key="`s-${row.sid}`"
          class="diff-card"
          :class="{ changed: row.changed }"
        >
          <header class="diff-head">
            <div class="head-meta">
              <span class="sid-text">{{ row.sid }}</span>
              <span v-if="row.changed" class="changed-badge">发生变化</span>
              <span v-else class="stable-badge">无变化</span>
            </div>
            <div class="sentence-block">
              <span class="sentence-label">原句</span>
              <p class="sentence-text">{{ row.text }}</p>
            </div>
            <div class="changed-fields" :class="{ empty: !row.changedFields.length }">
              <template v-if="row.changedFields.length">
                <span v-for="label in row.changedFields" :key="`${row.sid}-${label}`" class="field-badge">{{ label }}</span>
              </template>
              <span v-else>该句两侧输出一致。</span>
            </div>
          </header>
          <div class="diff-rows">
            <div class="diff-row" :class="{ changed: row.translation.changed }">
              <span class="row-label">翻译</span>
              <div class="side baseline"><span class="side-tag">Baseline</span><span>{{ row.translation.baseline || "—" }}</span></div>
              <div class="side candidate"><span class="side-tag">候选</span><span>{{ row.translation.candidate || "—" }}</span></div>
            </div>
            <div class="diff-row" :class="{ changed: row.marks.changed }">
              <span class="row-label">标注</span>
              <div class="side baseline">
                <span class="side-tag">Baseline</span>
                <ul v-if="row.marks.baseline.length" class="mini-list">
                  <li v-for="(mark, i) in row.marks.baseline" :key="`bm-${row.sid}-${i}`">
                    <span class="anchor-chip">{{ mark.anchor }}</span>
                    <span class="mark-type">{{ mark.type }}</span>
                    <span v-if="mark.extra" class="mark-extra">{{ mark.extra }}</span>
                  </li>
                </ul>
                <span v-else class="empty-cell">—</span>
              </div>
              <div class="side candidate">
                <span class="side-tag">候选</span>
                <ul v-if="row.marks.candidate.length" class="mini-list">
                  <li v-for="(mark, i) in row.marks.candidate" :key="`cm-${row.sid}-${i}`">
                    <span class="anchor-chip">{{ mark.anchor }}</span>
                    <span class="mark-type">{{ mark.type }}</span>
                    <span v-if="mark.extra" class="mark-extra">{{ mark.extra }}</span>
                  </li>
                </ul>
                <span v-else class="empty-cell">—</span>
              </div>
            </div>
            <div class="diff-row" :class="{ changed: row.entries.changed }">
              <span class="row-label">条目</span>
              <div class="side baseline">
                <span class="side-tag">Baseline</span>
                <ul v-if="row.entries.baseline.length" class="mini-list">
                  <li v-for="(entry, i) in row.entries.baseline" :key="`be-${row.sid}-${i}`">
                    <strong>{{ entry.label }}</strong>
                    <span class="entry-content">{{ entry.content }}</span>
                  </li>
                </ul>
                <span v-else class="empty-cell">—</span>
              </div>
              <div class="side candidate">
                <span class="side-tag">候选</span>
                <ul v-if="row.entries.candidate.length" class="mini-list">
                  <li v-for="(entry, i) in row.entries.candidate" :key="`ce-${row.sid}-${i}`">
                    <strong>{{ entry.label }}</strong>
                    <span class="entry-content">{{ entry.content }}</span>
                  </li>
                </ul>
                <span v-else class="empty-cell">—</span>
              </div>
            </div>
          </div>
        </li>
        </ol>
      </section>

      <details v-if="stableRows.length" class="stable-section">
        <summary>双侧一致（{{ stableRows.length }}）</summary>
        <ol class="diff-list stable-list">
          <li
            v-for="row in stableRows"
            :key="`stable-${row.sid}`"
            class="diff-card stable"
          >
            <header class="diff-head">
              <div class="head-meta">
                <span class="sid-text">{{ row.sid }}</span>
                <span class="stable-badge">无变化</span>
              </div>
              <div class="sentence-block">
                <span class="sentence-label">原句</span>
                <p class="sentence-text">{{ row.text }}</p>
              </div>
              <div class="changed-fields empty">
                <span>该句两侧输出一致。</span>
              </div>
            </header>
            <div class="diff-rows">
              <div class="diff-row">
                <span class="row-label">翻译</span>
                <div class="side baseline"><span class="side-tag">Baseline</span><span>{{ row.translation.baseline || "—" }}</span></div>
                <div class="side candidate"><span class="side-tag">候选</span><span>{{ row.translation.candidate || "—" }}</span></div>
              </div>
              <div class="diff-row">
                <span class="row-label">标注</span>
                <div class="side baseline">
                  <span class="side-tag">Baseline</span>
                  <ul v-if="row.marks.baseline.length" class="mini-list">
                    <li v-for="(mark, i) in row.marks.baseline" :key="`sbm-${row.sid}-${i}`">
                      <span class="anchor-chip">{{ mark.anchor }}</span>
                      <span class="mark-type">{{ mark.type }}</span>
                      <span v-if="mark.extra" class="mark-extra">{{ mark.extra }}</span>
                    </li>
                  </ul>
                  <span v-else class="empty-cell">—</span>
                </div>
                <div class="side candidate">
                  <span class="side-tag">候选</span>
                  <ul v-if="row.marks.candidate.length" class="mini-list">
                    <li v-for="(mark, i) in row.marks.candidate" :key="`scm-${row.sid}-${i}`">
                      <span class="anchor-chip">{{ mark.anchor }}</span>
                      <span class="mark-type">{{ mark.type }}</span>
                      <span v-if="mark.extra" class="mark-extra">{{ mark.extra }}</span>
                    </li>
                  </ul>
                  <span v-else class="empty-cell">—</span>
                </div>
              </div>
              <div class="diff-row">
                <span class="row-label">条目</span>
                <div class="side baseline">
                  <span class="side-tag">Baseline</span>
                  <ul v-if="row.entries.baseline.length" class="mini-list">
                    <li v-for="(entry, i) in row.entries.baseline" :key="`sbe-${row.sid}-${i}`">
                      <strong>{{ entry.label }}</strong>
                      <span class="entry-content">{{ entry.content }}</span>
                    </li>
                  </ul>
                  <span v-else class="empty-cell">—</span>
                </div>
                <div class="side candidate">
                  <span class="side-tag">候选</span>
                  <ul v-if="row.entries.candidate.length" class="mini-list">
                    <li v-for="(entry, i) in row.entries.candidate" :key="`sce-${row.sid}-${i}`">
                      <strong>{{ entry.label }}</strong>
                      <span class="entry-content">{{ entry.content }}</span>
                    </li>
                  </ul>
                  <span v-else class="empty-cell">—</span>
                </div>
              </div>
            </div>
          </li>
        </ol>
      </details>

      <section v-if="baselineOnlyRows.length" class="only-section">
        <h4>仅 baseline（{{ baselineOnlyRows.length }}）</h4>
        <ol class="diff-list only-list">
          <li v-for="row in baselineOnlyRows" :key="`b-${row.sid}`" class="diff-card removed">
            <header class="diff-head">
              <span class="sid-text">{{ row.sid }}</span>
              <div class="sentence-block">
                <span class="sentence-label">原句</span>
                <p class="sentence-text">{{ row.text }}</p>
              </div>
              <span class="removed-badge">仅 baseline</span>
            </header>
            <div class="only-fields">
              <div>
                <dt>翻译</dt>
                <dd>{{ row.translation || "—" }}</dd>
              </div>
              <div>
                <dt>标注</dt>
                <dd>
                  <ul v-if="row.marks.length" class="mini-list">
                    <li v-for="(mark, i) in row.marks" :key="`bm-${row.sid}-${i}`">
                      <span class="anchor-chip">{{ mark.anchor }}</span>
                      <span class="mark-type">{{ mark.type }}</span>
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
                    <li v-for="(entry, i) in row.entries" :key="`be-${row.sid}-${i}`">
                      <strong>{{ entry.label }}</strong>
                      <span class="entry-content">{{ entry.content }}</span>
                    </li>
                  </ul>
                  <span v-else class="empty-cell">—</span>
                </dd>
              </div>
            </div>
          </li>
        </ol>
      </section>

      <section v-if="candidateOnlyRows.length" class="only-section">
        <h4>仅 candidate（{{ candidateOnlyRows.length }}）</h4>
        <ol class="diff-list only-list">
          <li v-for="row in candidateOnlyRows" :key="`c-${row.sid}`" class="diff-card added">
            <header class="diff-head">
              <span class="sid-text">{{ row.sid }}</span>
              <div class="sentence-block">
                <span class="sentence-label">原句</span>
                <p class="sentence-text">{{ row.text }}</p>
              </div>
              <span class="added-badge">仅 candidate</span>
            </header>
            <div class="only-fields">
              <div>
                <dt>翻译</dt>
                <dd>{{ row.translation || "—" }}</dd>
              </div>
              <div>
                <dt>标注</dt>
                <dd>
                  <ul v-if="row.marks.length" class="mini-list">
                    <li v-for="(mark, i) in row.marks" :key="`cm-${row.sid}-${i}`">
                      <span class="anchor-chip">{{ mark.anchor }}</span>
                      <span class="mark-type">{{ mark.type }}</span>
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
                    <li v-for="(entry, i) in row.entries" :key="`ce-${row.sid}-${i}`">
                      <strong>{{ entry.label }}</strong>
                      <span class="entry-content">{{ entry.content }}</span>
                    </li>
                  </ul>
                  <span v-else class="empty-cell">—</span>
                </dd>
              </div>
            </div>
          </li>
        </ol>
      </section>
    </template>
  </section>
</template>

<style scoped>
.sentence-diff-view {
  display: grid;
  gap: 14px;
}
.overview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
  margin: 0;
}
.overview-card {
  background: var(--theme--background);
  padding: 10px 12px;
}
.overview-card dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.overview-card dd {
  margin: 4px 0 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--theme--foreground);
}
.overview-card.is-primary dd {
  color: var(--theme--primary);
}
.overview-card.is-danger dd {
  color: var(--theme--danger);
}
.overview-card.is-success dd {
  color: var(--theme--success);
}
.focus-section,
.only-section,
.stable-section {
  display: grid;
  gap: 10px;
}
.focus-section h4,
.only-section h4 {
  margin: 0;
  font-size: 13px;
  color: var(--theme--foreground-subdued);
}
.stable-section summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  font-weight: 700;
}
.diff-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 12px;
}
.diff-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 12px 14px;
  display: grid;
  gap: 10px;
}
.diff-card.changed {
  border-color: color-mix(in srgb, var(--theme--primary) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 3%, var(--theme--background));
}
.diff-card.stable {
  background: var(--theme--background-subdued);
}
.diff-card.removed {
  border-color: color-mix(in srgb, var(--theme--danger) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 4%, var(--theme--background));
}
.diff-card.added {
  border-color: color-mix(in srgb, var(--theme--success) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--success) 4%, var(--theme--background));
}
.diff-head {
  display: grid;
  gap: 8px;
  border-bottom: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  padding-bottom: 8px;
}
.head-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.sentence-block {
  display: grid;
  gap: 4px;
}
.sentence-label {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}
.sid-text {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}
.sentence-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.65;
  color: var(--theme--foreground);
  overflow-wrap: anywhere;
}
.changed-badge,
.stable-badge,
.added-badge,
.removed-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--theme--border-color);
  white-space: nowrap;
}
.changed-badge {
  color: var(--theme--primary);
  border-color: color-mix(in srgb, var(--theme--primary) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 8%, var(--theme--background));
}
.stable-badge {
  color: var(--theme--foreground-subdued);
  border-color: var(--theme--border-color-subdued, var(--theme--border-color));
  background: var(--theme--background-subdued);
}
.added-badge {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--success) 8%, var(--theme--background));
}
.removed-badge {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 8%, var(--theme--background));
}
.diff-rows {
  display: grid;
  gap: 8px;
}
.changed-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
.changed-fields.empty {
  color: var(--theme--foreground-subdued);
}
.field-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--theme--primary) 40%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
  color: var(--theme--primary);
  font-size: 11px;
  font-weight: 700;
}
.diff-row {
  display: grid;
  grid-template-columns: 56px repeat(2, minmax(0, 1fr));
  gap: 10px;
  align-items: start;
  padding: 8px;
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  background: var(--theme--background);
}
.diff-row.changed {
  border-color: color-mix(in srgb, var(--theme--primary) 40%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
}
.row-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--theme--foreground-subdued);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  align-self: start;
  padding-top: 4px;
}
.side {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.side-tag {
  display: inline-flex;
  align-items: center;
  align-self: start;
  padding: 1px 8px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  border-radius: 999px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
}
.side.baseline .side-tag {
  color: var(--theme--foreground);
}
.side.candidate .side-tag {
  color: var(--theme--primary);
  border-color: color-mix(in srgb, var(--theme--primary) 45%, var(--theme--border-color));
}
.side > span:not(.side-tag) {
  font-size: 13px;
  line-height: 1.6;
  color: var(--theme--foreground);
  overflow-wrap: anywhere;
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
  line-height: 1.5;
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
.mark-extra,
.entry-content {
  color: var(--theme--foreground);
}
.empty-cell {
  color: var(--theme--foreground-subdued);
  font-style: italic;
}
.removed-translation,
.added-translation {
  margin: 0;
  font-size: 13px;
  color: var(--theme--foreground);
}

.only-fields {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.only-fields > div {
  display: grid;
  gap: 4px;
  padding: 8px 10px;
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  background: var(--theme--background);
  position: relative;
}
.only-fields > div::before {
  content: "";
  position: absolute;
  top: 10px;
  left: 10px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--theme--foreground-subdued);
}
.only-fields > div {
  padding-left: 24px;
}
.diff-card.removed .only-fields > div {
  border-left-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}
.diff-card.added .only-fields > div {
  border-left-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}

.only-fields dt {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--theme--foreground-subdued);
}

.only-fields dd {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--theme--foreground);
  overflow-wrap: anywhere;
}

@media (min-width: 760px) {
  .only-fields {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
.empty-state {
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 8px;
  padding: 18px;
  color: var(--theme--foreground-subdued);
}
@media (max-width: 900px) {
  .overview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .diff-row {
    grid-template-columns: 1fr;
  }
  .changed-badge,
  .stable-badge,
  .added-badge,
  .removed-badge {
    grid-column: 1 / -1;
  }
}
</style>
