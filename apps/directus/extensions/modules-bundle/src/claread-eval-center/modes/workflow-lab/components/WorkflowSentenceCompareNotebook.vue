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
  /**
   * Persisted compare report comparisons — the authoritative source for
   * which sentences changed.  When provided, the sentence list, changed
   * flags and overview counts are all derived from this array; artifacts
   * are only used for rendering the actual content (translations, marks,
   * entries).  When absent, the component falls back to local artifact
   * deep-diffing.
   */
  comparisons: { type: Array, default: () => [] },
  /** Optional judge overlay: Map<caseId, { verdict, summary, reasons, status, error }> */
  judgeOverlay: { type: Object, default: null },
  /** Show only sentences with changes or judge verdicts */
  filterMode: { type: String, default: "all" }, // "all" | "changed" | "judged"
  emptyText: { type: String, default: "选择 baseline 与候选后，这里会逐句显示双边对照。" },
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

function isFieldChanged(bValue, cValue) {
  if (bValue == null && cValue == null) return false;
  if (bValue == null || cValue == null) return true;
  return String(bValue) !== String(cValue);
}

function changedFieldLabels(row) {
  const labels = [];
  if (row.translation.changed) labels.push("翻译变化");
  if (row.marks.changed) labels.push("标注变化");
  if (row.entries.changed) labels.push("条目变化");
  return labels;
}

/** Whether a compare verdict counts as "changed" in the compare report sense. */
function isCompareChanged(verdict) {
  if (!verdict) return false;
  // "win" / "loss" / "manual_review" / "needs_review" all mean the compare
  // detected a difference.  Only a verdict that explicitly means "no diff"
  // (currently none in the schema — all comparisons in the report are there
  // because they were flagged) would return false.
  return true;
}

function compareVerdictLabel(verdict) {
  const map = {
    win: "候选更优",
    loss: "Baseline 更优",
    manual_review: "需复查",
    needs_review: "需复查",
    tie: "持平",
  };
  return map[verdict] || verdict || "—";
}

function compareVerdictTone(verdict) {
  if (verdict === "win") return "success";
  if (verdict === "loss") return "danger";
  if (verdict === "manual_review" || verdict === "needs_review") return "warning";
  if (verdict === "tie") return "neutral";
  return "neutral";
}

function makeRow(sid) {
  const bT = translationFor(baselineBySid.value, sid);
  const cT = translationFor(candidateBySid.value, sid);
  const bM = marksFor(baselineBySid.value, sid);
  const cM = marksFor(candidateBySid.value, sid);
  const bE = entriesFor(baselineBySid.value, sid);
  const cE = entriesFor(candidateBySid.value, sid);
  const changed = isFieldChanged(bT, cT) || JSON.stringify(bM) !== JSON.stringify(cM) || JSON.stringify(bE) !== JSON.stringify(cE);
  const row = {
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
    changed,
    judge: props.judgeOverlay?.get(sid) || null,
    side: "both",
    compareVerdict: null,
  };
  row.changedFields = changedFieldLabels(row);
  return row;
}

/** Build a comparisons lookup Map from the persisted compare report. */
const comparisonsMap = computed(() => {
  const map = new Map();
  for (const c of props.comparisons || []) {
    if (c?.case_id != null) {
      map.set(String(c.case_id), c);
    }
  }
  return map;
});

const hasComparisons = computed(() => comparisonsMap.value.size > 0);

const allSids = computed(() => {
  const set = new Set();
  for (const sid of allBaselineSids.value) set.add(sid);
  for (const sid of allCandidateSids.value) set.add(sid);
  for (const sid of preparedMap.value.keys()) set.add(sid);
  return Array.from(set).sort((a, b) => orderKey(a) - orderKey(b));
});

/**
 * When comparisons are provided, the sentence list is driven by them:
 * - Only sentences present in comparisons are shown
 * - changed / changedFields come from the comparison verdict & reasons
 * - Artifact data is only used for rendering content
 *
 * When comparisons are absent, fall back to local artifact deep-diffing.
 */
const sentenceRows = computed(() => {
  if (hasComparisons.value) {
    // Compare-report-driven mode
    const rows = [];
    for (const [sid, comparison] of comparisonsMap.value) {
      const bT = translationFor(baselineBySid.value, sid);
      const cT = translationFor(candidateBySid.value, sid);
      const bM = marksFor(baselineBySid.value, sid);
      const cM = marksFor(candidateBySid.value, sid);
      const bE = entriesFor(baselineBySid.value, sid);
      const cE = entriesFor(candidateBySid.value, sid);

      const verdict = comparison.verdict;
      const changed = isCompareChanged(verdict);

      // Derive changedFields from local diff for visual hints, but the
      // authoritative "is this sentence a compare object" comes from the
      // report.  If local diff shows no change but the report says it's
      // there, we still mark it as a compare-flagged sentence.
      const localTranslationChanged = isFieldChanged(bT, cT);
      const localMarksChanged = JSON.stringify(bM) !== JSON.stringify(cM);
      const localEntriesChanged = JSON.stringify(bE) !== JSON.stringify(cE);

      const changedFields = [];
      if (localTranslationChanged) changedFields.push("翻译变化");
      if (localMarksChanged) changedFields.push("标注变化");
      if (localEntriesChanged) changedFields.push("条目变化");
      // If local diff found nothing but the compare report still flagged
      // this sentence, add a generic label so the user knows it's a
      // compare object.
      if (changed && !changedFields.length) {
        changedFields.push("信号差异");
      }

      rows.push({
        sid,
        text: preparedMap.value.get(sid)
          || baselineSentenceMap.value.get(sid)
          || candidateSentenceMap.value.get(sid)
          || "—",
        translation: {
          baseline: bT,
          candidate: cT,
          changed: localTranslationChanged,
        },
        marks: {
          baseline: bM,
          candidate: cM,
          changed: localMarksChanged,
        },
        entries: {
          baseline: bE,
          candidate: cE,
          changed: localEntriesChanged,
        },
        changedFields,
        changed,
        judge: props.judgeOverlay?.get(sid) || null,
        side: "both",
        compareVerdict: verdict || null,
      });
    }
    return rows.sort((a, b) => orderKey(a.sid) - orderKey(b.sid));
  }

  // Fallback: local artifact deep-diffing (no compare report available)
  const rows = [];
  for (const sid of allSids.value) {
    const inBaseline = allBaselineSids.value.has(sid);
    const inCandidate = allCandidateSids.value.has(sid);
    if (inBaseline && inCandidate) {
      rows.push(makeRow(sid));
    } else if (inBaseline && !inCandidate) {
      const bT = translationFor(baselineBySid.value, sid);
      const bM = marksFor(baselineBySid.value, sid);
      const bE = entriesFor(baselineBySid.value, sid);
      rows.push({
        sid,
        text: preparedMap.value.get(sid) || baselineSentenceMap.value.get(sid) || "—",
        translation: { baseline: bT, candidate: null, changed: bT != null },
        marks: { baseline: bM, candidate: [], changed: bM.length > 0 },
        entries: { baseline: bE, candidate: [], changed: bE.length > 0 },
        changedFields: bT ? ["翻译变化"] : [],
        changed: bT != null || bM.length > 0 || bE.length > 0,
        judge: props.judgeOverlay?.get(sid) || null,
        side: "baseline_only",
        compareVerdict: null,
      });
    } else {
      const cT = translationFor(candidateBySid.value, sid);
      const cM = marksFor(candidateBySid.value, sid);
      const cE = entriesFor(candidateBySid.value, sid);
      rows.push({
        sid,
        text: preparedMap.value.get(sid) || candidateSentenceMap.value.get(sid) || "—",
        translation: { baseline: null, candidate: cT, changed: cT != null },
        marks: { baseline: [], candidate: cM, changed: cM.length > 0 },
        entries: { baseline: [], candidate: cE, changed: cE.length > 0 },
        changedFields: cT ? ["翻译变化"] : [],
        changed: cT != null || cM.length > 0 || cE.length > 0,
        judge: props.judgeOverlay?.get(sid) || null,
        side: "candidate_only",
        compareVerdict: null,
      });
    }
  }
  return rows;
});

const filteredRows = computed(() => {
  if (props.filterMode === "changed") return sentenceRows.value.filter((r) => r.changed);
  if (props.filterMode === "judged") return sentenceRows.value.filter((r) => r.judge);
  return sentenceRows.value;
});

const changedCount = computed(() => sentenceRows.value.filter((r) => r.changed).length);
const stableCount = computed(() => sentenceRows.value.filter((r) => !r.changed && r.side === "both").length);
const baselineOnlyCount = computed(() => sentenceRows.value.filter((r) => r.side === "baseline_only").length);
const candidateOnlyCount = computed(() => sentenceRows.value.filter((r) => r.side === "candidate_only").length);

function verdictTone(verdict) {
  if (verdict === "candidate_preferred") return "success";
  if (verdict === "baseline_preferred") return "danger";
  if (verdict === "needs_review") return "warning";
  if (verdict === "tie") return "neutral";
  return "neutral";
}

function verdictLabel(verdict) {
  const map = {
    candidate_preferred: "候选更优",
    baseline_preferred: "Baseline 更优",
    tie: "持平",
    needs_review: "需复查",
  };
  return map[verdict] || verdict || "—";
}
</script>

<template>
  <section class="sentence-notebook">
    <div v-if="!hasAnyData" class="empty-state">{{ emptyText }}</div>
    <template v-else>
      <dl v-if="sentenceRows.length" class="overview-grid">
        <div class="overview-card is-primary">
          <dt>发生变化</dt>
          <dd>{{ changedCount }}</dd>
        </div>
        <div class="overview-card is-neutral">
          <dt>两侧一致</dt>
          <dd>{{ stableCount }}</dd>
        </div>
        <div v-if="baselineOnlyCount" class="overview-card is-danger">
          <dt>仅 Baseline</dt>
          <dd>{{ baselineOnlyCount }}</dd>
        </div>
        <div v-if="candidateOnlyCount" class="overview-card is-success">
          <dt>仅候选</dt>
          <dd>{{ candidateOnlyCount }}</dd>
        </div>
      </dl>

      <div v-if="!filteredRows.length" class="empty-state">没有符合条件的句子。</div>

      <ol class="sentence-list">
        <li
          v-for="row in filteredRows"
          :key="row.sid"
          class="sentence-card"
          :class="{
            changed: row.changed && row.side === 'both',
            'baseline-only': row.side === 'baseline_only',
            'candidate-only': row.side === 'candidate_only',
          }"
        >
          <header class="sentence-head">
            <div class="head-meta">
              <span class="sentence-id">{{ row.sid }}</span>
              <span v-if="row.compareVerdict" class="verdict-badge" :class="`is-${compareVerdictTone(row.compareVerdict)}`">{{ compareVerdictLabel(row.compareVerdict) }}</span>
              <span v-else-if="row.changed && row.side === 'both'" class="changed-badge">发生变化</span>
              <span v-else-if="row.side === 'baseline_only'" class="removed-badge">仅 Baseline</span>
              <span v-else-if="row.side === 'candidate_only'" class="added-badge">仅候选</span>
              <span v-else class="stable-badge">无变化</span>
              <template v-if="row.changedFields.length">
                <span v-for="label in row.changedFields" :key="`${row.sid}-${label}`" class="field-badge">{{ label }}</span>
              </template>
            </div>
            <p class="source-line">{{ row.text }}</p>
          </header>

          <!-- Judge overlay -->
          <div v-if="row.judge" class="judge-verdict-bar" :class="`is-${verdictTone(row.judge.verdict)}`">
            <span class="verdict-pill" :class="`is-${verdictTone(row.judge.verdict)}`">{{ verdictLabel(row.judge.verdict) }}</span>
            <span v-if="row.judge.summary" class="judge-summary">{{ row.judge.summary }}</span>
            <ul v-if="row.judge.reasons?.length" class="judge-reasons">
              <li v-for="(reason, ri) in row.judge.reasons" :key="`${row.sid}-reason-${ri}`">{{ reason }}</li>
            </ul>
            <div v-if="row.judge.status === 'error'" class="judge-error" role="alert">
              评审失败：{{ row.judge.error?.message || row.judge.error?.code || "未知错误" }}
            </div>
          </div>

          <div class="diff-rows">
            <div class="diff-row" :class="{ changed: row.translation.changed, identical: !row.translation.changed }">
              <span class="row-label">翻译</span>
              <template v-if="row.translation.changed">
                <div class="side baseline">
                  <span class="side-tag">Baseline</span>
                  <span>{{ row.translation.baseline || "—" }}</span>
                </div>
                <div class="side candidate">
                  <span class="side-tag">候选</span>
                  <span>{{ row.translation.candidate || "—" }}</span>
                </div>
              </template>
              <template v-else>
                <div class="side-unified">
                  <span class="unified-tag">双侧一致</span>
                  <span>{{ row.translation.baseline || "—" }}</span>
                </div>
              </template>
            </div>
            <div class="diff-row" :class="{ changed: row.marks.changed, identical: !row.marks.changed }">
              <span class="row-label">标注</span>
              <template v-if="row.marks.changed">
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
              </template>
              <template v-else>
                <div class="side-unified">
                  <span class="unified-tag">双侧一致</span>
                  <ul v-if="row.marks.baseline.length" class="mini-list inline-list">
                    <li v-for="(mark, i) in row.marks.baseline" :key="`bm-unified-${row.sid}-${i}`">
                      <span class="anchor-chip">{{ mark.anchor }}</span>
                      <span class="mark-type">{{ mark.type }}</span>
                      <span v-if="mark.extra" class="mark-extra">{{ mark.extra }}</span>
                    </li>
                  </ul>
                  <span v-else class="empty-cell">—</span>
                </div>
              </template>
            </div>
            <div class="diff-row" :class="{ changed: row.entries.changed, identical: !row.entries.changed }">
              <span class="row-label">条目</span>
              <template v-if="row.entries.changed">
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
              </template>
              <template v-else>
                <div class="side-unified">
                  <span class="unified-tag">双侧一致</span>
                  <ul v-if="row.entries.baseline.length" class="mini-list inline-list">
                    <li v-for="(entry, i) in row.entries.baseline" :key="`be-unified-${row.sid}-${i}`">
                      <strong>{{ entry.label }}</strong>
                      <span class="entry-content">{{ entry.content }}</span>
                    </li>
                  </ul>
                  <span v-else class="empty-cell">—</span>
                </div>
              </template>
            </div>
          </div>
        </li>
      </ol>
    </template>
  </section>
</template>

<style scoped>
.sentence-notebook {
  display: grid;
  gap: 14px;
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
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

.sentence-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 12px;
}

.sentence-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 14px 16px;
  display: grid;
  gap: 12px;
}

.sentence-card.changed {
  border-color: color-mix(in srgb, var(--theme--primary) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 3%, var(--theme--background));
}

.sentence-card.baseline-only {
  border-color: color-mix(in srgb, var(--theme--danger) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 4%, var(--theme--background));
}

.sentence-card.candidate-only {
  border-color: color-mix(in srgb, var(--theme--success) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--success) 4%, var(--theme--background));
}

.sentence-head {
  display: grid;
  gap: 8px;
  border-bottom: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  padding-bottom: 10px;
}

.head-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
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

.source-line {
  margin: 0;
  color: var(--theme--foreground);
  font-family: "Source Serif Pro", Georgia, "Times New Roman", "Noto Serif SC", serif;
  font-size: 15px;
  line-height: 1.75;
  text-wrap: pretty;
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

.verdict-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}

.verdict-badge.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--success) 8%, var(--theme--background));
}

.verdict-badge.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 8%, var(--theme--background));
}

.verdict-badge.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 8%, var(--theme--background));
}

.verdict-badge.is-neutral {
  color: var(--theme--foreground-subdued);
  border-color: var(--theme--border-color-subdued, var(--theme--border-color));
  background: var(--theme--background-subdued);
}

/* Judge overlay */
.judge-verdict-bar {
  display: grid;
  gap: 6px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background-subdued);
}

.judge-verdict-bar.is-success {
  border-color: color-mix(in srgb, var(--theme--success) 40%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--success) 4%, var(--theme--background-subdued));
}

.judge-verdict-bar.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger) 40%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 4%, var(--theme--background-subdued));
}

.judge-verdict-bar.is-warning {
  border-color: color-mix(in srgb, var(--theme--warning) 40%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 4%, var(--theme--background-subdued));
}

.verdict-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  background: var(--theme--background);
  border-radius: 999px;
}

.verdict-pill.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}

.verdict-pill.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}

.verdict-pill.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}

.verdict-pill.is-neutral {
  color: var(--theme--foreground-subdued);
}

.judge-summary {
  color: var(--theme--foreground);
  font-size: 13px;
  line-height: 1.6;
}

.judge-reasons {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
}

.judge-reasons li {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 999px;
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  line-height: 1.4;
  padding: 2px 8px;
}

.judge-error {
  color: var(--theme--danger);
  font-size: 12px;
  line-height: 1.5;
}

/* Diff rows */
.diff-rows {
  display: grid;
  gap: 8px;
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
}

.diff-row.identical {
  border-color: var(--theme--border-color-subdued, var(--theme--border-color));
  opacity: 0.75;
}

.diff-row.identical:hover {
  opacity: 1;
}

.side-unified {
  grid-column: span 2;
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.unified-tag {
  display: inline-flex;
  align-items: center;
  align-self: start;
  padding: 1px 8px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  border-radius: 999px;
  border: 1px dashed var(--theme--border-color);
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  white-space: nowrap;
}

.inline-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
