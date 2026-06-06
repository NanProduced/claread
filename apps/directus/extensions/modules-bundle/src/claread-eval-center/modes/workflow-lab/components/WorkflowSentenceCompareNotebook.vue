<script setup>
import { computed } from "vue";
import {
  chipSideLabel,
  chipTooltip,
  extractHealthSignals,
  mergeSentenceWarningChips,
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

const baselineWarningsGrouped = computed(() => extractHealthSignals(props.baselineArtifact).warningsGrouped);
const candidateWarningsGrouped = computed(() => extractHealthSignals(props.candidateArtifact).warningsGrouped);

function chipsFor(sid) {
  return mergeSentenceWarningChips(baselineWarningsGrouped.value, candidateWarningsGrouped.value, sid);
}

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

const MARK_TYPES = {
  vocab_highlight: { label: "词汇", tone: "vocab" },
  phrase_gloss: { label: "短语", tone: "phrase" },
  context_gloss: { label: "语境", tone: "context" },
  grammar_note: { label: "语法", tone: "grammar" },
  sentence_analysis: { label: "句法", tone: "analysis" },
};

function noteAnchorText(item) {
  return item?.anchor?.anchor_text || item?.anchor?.text || item?.lookup_text || item?.label || item?.title || "—";
}

function linkedIdKey(rawId) {
  const value = String(rawId || "").trim();
  return value ? value.replace(/^[a-z]+_/, "") : "";
}

function matchGrammarMark(entry, marks = []) {
  const grammarMarks = marks.filter((item) => item?.annotation_type === "grammar_note");
  const entryKey = linkedIdKey(entry?.id);
  if (entryKey) {
    const exact = grammarMarks.find((item) => linkedIdKey(item?.id) === entryKey);
    if (exact) return exact;
  }
  if (grammarMarks.length === 1) return grammarMarks[0];
  return null;
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function renderSimpleMarkdown(value) {
  const normalized = String(value || "").replace(/\r\n/g, "\n").trim();
  if (!normalized) return "<p>—</p>";

  const blocks = normalized.split(/\n\s*\n/).filter(Boolean);
  return blocks.map((block) => {
    const lines = block.split("\n").map((line) => line.trimEnd());
    const isList = lines.every((line) => /^\-\s+/.test(line.trim()));
    if (isList) {
      const items = lines
        .map((line) => line.replace(/^\-\s+/, ""))
        .map((line) => `<li>${renderInlineMarkdown(line)}</li>`)
        .join("");
      return `<ul>${items}</ul>`;
    }
    return `<p>${lines.map((line) => renderInlineMarkdown(line)).join("<br>")}</p>`;
  }).join("");
}

function formatMark(mark) {
  const anchor = noteAnchorText(mark);
  const rawType = mark?.annotation_type || mark?.visual_tone || "mark";
  const typeInfo = MARK_TYPES[rawType] || { label: String(rawType).toUpperCase(), tone: "neutral" };
  const primary = mark?.glossary?.zh || mark?.glossary?.gloss || mark?.lookup_text || "";
  const detail = mark?.glossary?.reason || mark?.glossary?.phrase_type || "";
  return {
    anchor: String(anchor),
    type: typeInfo.label,
    tone: typeInfo.tone,
    primary: primary ? String(primary) : "",
    detail: detail ? String(detail) : "",
    rawType: String(rawType),
  };
}

function formatEntry(entry, marks = []) {
  const entryType = String(entry?.entry_type || "");
  const label = entry?.label || entryType || "语法";
  const content = entry?.content || entry?.title || entry?.note_zh || entry?.analysis_zh || "";
  const linkedMark = entryType === "grammar_note" ? matchGrammarMark(entry, marks) : null;
  return {
    label: String(label),
    content: content ? String(content) : "—",
    anchor: linkedMark ? noteAnchorText(linkedMark) : "",
    tone: entryType === "sentence_analysis" ? "analysis" : entryType === "grammar_note" ? "grammar" : "neutral",
    isSentenceAnalysis: entryType === "sentence_analysis",
    html: entryType === "sentence_analysis" ? renderSimpleMarkdown(content) : "",
    entryType: entryType,
  };
}

function getGroupedMarks(marks) {
  const vocab = [];
  const phrase = [];
  const context = [];
  const other = [];
  for (const m of marks || []) {
    if (m.tone === "vocab") vocab.push(m);
    else if (m.tone === "phrase") phrase.push(m);
    else if (m.tone === "context") context.push(m);
    else other.push(m);
  }
  return {
    vocab,
    phrase,
    context,
    other,
    hasAny: (marks || []).length > 0
  };
}

function getGroupedEntries(entries) {
  const grammar = [];
  const analysis = [];
  const other = [];
  for (const e of entries || []) {
    if (e.entryType === "grammar_note") grammar.push(e);
    else if (e.entryType === "sentence_analysis") analysis.push(e);
    else other.push(e);
  }
  return {
    grammar,
    analysis,
    other,
    hasAny: (entries || []).length > 0
  };
}

function translationFor(bySid, sid) {
  return bySid.translations.get(sid)?.translation_zh || null;
}

function marksFor(bySid, sid) {
  return (bySid.marks.get(sid) || [])
    .filter((item) => item?.annotation_type !== "grammar_note")
    .map(formatMark);
}

function entriesFor(bySid, sid) {
  const marks = bySid.marks.get(sid) || [];
  return (bySid.entries.get(sid) || []).map((entry) => formatEntry(entry, marks));
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
  if (row.marks.changed) labels.push("词汇变化");
  if (row.entries.changed) labels.push("语法变化");
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
      if (localMarksChanged) changedFields.push("词汇变化");
      if (localEntriesChanged) changedFields.push("语法变化");
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
              <template v-if="chipsFor(row.sid).length">
                <span
                  v-for="chip in chipsFor(row.sid)"
                  :key="`cn-chip-${row.sid}-${chip.code}`"
                  :class="['warn-chip', `is-${chip.tone}`, `is-${chip.category}`, chipSideLabel(chip.sides) && `is-side-${chipSideLabel(chip.sides)}`]"
                  :title="chipTooltip(chip)"
                ><span v-if="chipSideLabel(chip.sides)" :class="['chip-side', `is-${chipSideLabel(chip.sides)}`]">{{ chipSideLabel(chip.sides) }}</span>{{ chip.text }}</span>
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
              <span class="row-label">词汇</span>
              <template v-if="row.marks.changed">
                <div class="side baseline">
                  <span class="side-tag">Baseline</span>
                  <div class="grouped-marks-list" v-if="row.marks.baseline.length">
                    <!-- Vocab group -->
                    <div class="mark-subgroup tone-vocab" v-if="getGroupedMarks(row.marks.baseline).vocab.length">
                      <span class="subgroup-badge">词解</span>
                      <ul class="subgroup-items">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.baseline).vocab" :key="`bm-vocab-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-vocab">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Phrase group -->
                    <div class="mark-subgroup tone-phrase" v-if="getGroupedMarks(row.marks.baseline).phrase.length">
                      <span class="subgroup-badge">短语</span>
                      <ul class="subgroup-items">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.baseline).phrase" :key="`bm-phrase-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-phrase">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Context group -->
                    <div class="mark-subgroup tone-context" v-if="getGroupedMarks(row.marks.baseline).context.length">
                      <span class="subgroup-badge">语境</span>
                      <ul class="subgroup-items">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.baseline).context" :key="`bm-context-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-context">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                  </div>
                  <span v-else class="empty-cell">—</span>
                </div>
                <div class="side candidate">
                  <span class="side-tag">候选</span>
                  <div class="grouped-marks-list" v-if="row.marks.candidate.length">
                    <!-- Vocab group -->
                    <div class="mark-subgroup tone-vocab" v-if="getGroupedMarks(row.marks.candidate).vocab.length">
                      <span class="subgroup-badge">词解</span>
                      <ul class="subgroup-items">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.candidate).vocab" :key="`cm-vocab-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-vocab">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Phrase group -->
                    <div class="mark-subgroup tone-phrase" v-if="getGroupedMarks(row.marks.candidate).phrase.length">
                      <span class="subgroup-badge">短语</span>
                      <ul class="subgroup-items">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.candidate).phrase" :key="`cm-phrase-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-phrase">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Context group -->
                    <div class="mark-subgroup tone-context" v-if="getGroupedMarks(row.marks.candidate).context.length">
                      <span class="subgroup-badge">语境</span>
                      <ul class="subgroup-items">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.candidate).context" :key="`cm-context-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-context">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                  </div>
                  <span v-else class="empty-cell">—</span>
                </div>
              </template>
              <template v-else>
                <div class="side-unified">
                  <span class="unified-tag">双侧一致</span>
                  <div class="grouped-marks-list" v-if="row.marks.baseline.length">
                    <!-- Vocab group -->
                    <div class="mark-subgroup tone-vocab" v-if="getGroupedMarks(row.marks.baseline).vocab.length">
                      <span class="subgroup-badge">词解</span>
                      <ul class="subgroup-items inline-list">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.baseline).vocab" :key="`bm-vocab-unified-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-vocab">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Phrase group -->
                    <div class="mark-subgroup tone-phrase" v-if="getGroupedMarks(row.marks.baseline).phrase.length">
                      <span class="subgroup-badge">短语</span>
                      <ul class="subgroup-items inline-list">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.baseline).phrase" :key="`bm-phrase-unified-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-phrase">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Context group -->
                    <div class="mark-subgroup tone-context" v-if="getGroupedMarks(row.marks.baseline).context.length">
                      <span class="subgroup-badge">语境</span>
                      <ul class="subgroup-items inline-list">
                        <li v-for="(mark, i) in getGroupedMarks(row.marks.baseline).context" :key="`bm-context-unified-${row.sid}-${i}`">
                          <span class="eval-anchor-chip tone-context">{{ mark.anchor }}</span>
                          <span v-if="mark.primary" class="mark-extra">{{ mark.primary }}</span>
                          <span v-if="mark.detail" class="mark-detail">{{ mark.detail }}</span>
                        </li>
                      </ul>
                    </div>
                  </div>
                  <span v-else class="empty-cell">—</span>
                </div>
              </template>
            </div>
            <div class="diff-row" :class="{ changed: row.entries.changed, identical: !row.entries.changed }">
              <span class="row-label">语法</span>
              <template v-if="row.entries.changed">
                <div class="side baseline">
                  <span class="side-tag">Baseline</span>
                  <div class="grouped-entries-list" v-if="row.entries.baseline.length">
                    <!-- Grammar note group -->
                    <div class="entry-subgroup tone-grammar" v-if="getGroupedEntries(row.entries.baseline).grammar.length">
                      <span class="subgroup-badge">语法注解</span>
                      <ul class="subgroup-items">
                        <li v-for="(entry, i) in getGroupedEntries(row.entries.baseline).grammar" :key="`be-grammar-${row.sid}-${i}`" class="entry-item">
                          <div class="entry-head">
                            <span v-if="entry.anchor" class="eval-anchor-chip tone-grammar">{{ entry.anchor }}</span>
                            <strong>{{ entry.label }}</strong>
                          </div>
                          <span class="entry-content">{{ entry.content }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Sentence analysis group -->
                    <div class="entry-subgroup tone-analysis" v-if="getGroupedEntries(row.entries.baseline).analysis.length">
                      <span class="subgroup-badge">句法分析</span>
                      <ul class="subgroup-items">
                        <li v-for="(entry, i) in getGroupedEntries(row.entries.baseline).analysis" :key="`be-analysis-${row.sid}-${i}`" class="entry-item">
                          <div class="entry-head">
                            <span v-if="entry.anchor" class="eval-anchor-chip tone-analysis">{{ entry.anchor }}</span>
                            <strong>{{ entry.label }}</strong>
                          </div>
                          <div class="entry-markdown markdown-body" v-html="entry.html"></div>
                        </li>
                      </ul>
                    </div>
                  </div>
                  <span v-else class="empty-cell">—</span>
                </div>
                <div class="side candidate">
                  <span class="side-tag">候选</span>
                  <div class="grouped-entries-list" v-if="row.entries.candidate.length">
                    <!-- Grammar note group -->
                    <div class="entry-subgroup tone-grammar" v-if="getGroupedEntries(row.entries.candidate).grammar.length">
                      <span class="subgroup-badge">语法注解</span>
                      <ul class="subgroup-items">
                        <li v-for="(entry, i) in getGroupedEntries(row.entries.candidate).grammar" :key="`ce-grammar-${row.sid}-${i}`" class="entry-item">
                          <div class="entry-head">
                            <span v-if="entry.anchor" class="eval-anchor-chip tone-grammar">{{ entry.anchor }}</span>
                            <strong>{{ entry.label }}</strong>
                          </div>
                          <span class="entry-content">{{ entry.content }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Sentence analysis group -->
                    <div class="entry-subgroup tone-analysis" v-if="getGroupedEntries(row.entries.candidate).analysis.length">
                      <span class="subgroup-badge">句法分析</span>
                      <ul class="subgroup-items">
                        <li v-for="(entry, i) in getGroupedEntries(row.entries.candidate).analysis" :key="`ce-analysis-${row.sid}-${i}`" class="entry-item">
                          <div class="entry-head">
                            <span v-if="entry.anchor" class="eval-anchor-chip tone-analysis">{{ entry.anchor }}</span>
                            <strong>{{ entry.label }}</strong>
                          </div>
                          <div class="entry-markdown markdown-body" v-html="entry.html"></div>
                        </li>
                      </ul>
                    </div>
                  </div>
                  <span v-else class="empty-cell">—</span>
                </div>
              </template>
              <template v-else>
                <div class="side-unified">
                  <span class="unified-tag">双侧一致</span>
                  <div class="grouped-entries-list" v-if="row.entries.baseline.length">
                    <!-- Grammar note group -->
                    <div class="entry-subgroup tone-grammar" v-if="getGroupedEntries(row.entries.baseline).grammar.length">
                      <span class="subgroup-badge">语法注解</span>
                      <ul class="subgroup-items">
                        <li v-for="(entry, i) in getGroupedEntries(row.entries.baseline).grammar" :key="`be-grammar-unified-${row.sid}-${i}`" class="entry-item">
                          <div class="entry-head">
                            <span v-if="entry.anchor" class="eval-anchor-chip tone-grammar">{{ entry.anchor }}</span>
                            <strong>{{ entry.label }}</strong>
                          </div>
                          <span class="entry-content">{{ entry.content }}</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Sentence analysis group -->
                    <div class="entry-subgroup tone-analysis" v-if="getGroupedEntries(row.entries.baseline).analysis.length">
                      <span class="subgroup-badge">句法分析</span>
                      <ul class="subgroup-items">
                        <li v-for="(entry, i) in getGroupedEntries(row.entries.baseline).analysis" :key="`be-analysis-unified-${row.sid}-${i}`" class="entry-item">
                          <div class="entry-head">
                            <span v-if="entry.anchor" class="eval-anchor-chip tone-analysis">{{ entry.anchor }}</span>
                            <strong>{{ entry.label }}</strong>
                          </div>
                          <div class="entry-markdown markdown-body" v-html="entry.html"></div>
                        </li>
                      </ul>
                    </div>
                  </div>
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

.entry-item {
  display: grid;
  gap: 6px;
}

.entry-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
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

.mark-detail {
  color: var(--theme--foreground-subdued);
}

.markdown-body :deep(p),
.markdown-body :deep(ul) {
  margin: 0;
}

.markdown-body :deep(ul) {
  padding-left: 18px;
}

.markdown-body :deep(code) {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--theme--background-subdued);
  font-family: var(--theme--fonts--monospace--font-family, monospace);
}

.markdown-body :deep(strong) {
  font-weight: 700;
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

/* Subgroup styles */
.grouped-marks-list,
.grouped-entries-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.mark-subgroup,
.entry-subgroup {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border-left: 3px solid var(--subgroup-color, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--background-subdued) 30%, transparent);
  border-radius: 0 6px 6px 0;
}

.mark-subgroup.tone-vocab {
  --subgroup-color: #e4b000;
}
.mark-subgroup.tone-phrase {
  --subgroup-color: #db2777;
}
.mark-subgroup.tone-context {
  --subgroup-color: #54a7de;
}

.entry-subgroup.tone-grammar {
  --subgroup-color: #746694;
}
.entry-subgroup.tone-analysis {
  --subgroup-color: #059669;
}

.subgroup-badge {
  align-self: flex-start;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  border: 1px solid var(--subgroup-color, var(--theme--border-color));
  background: color-mix(in srgb, var(--subgroup-color) 8%, var(--theme--background));
  color: var(--subgroup-color);
}

.subgroup-items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.subgroup-items.inline-list {
  flex-direction: row;
  flex-wrap: wrap;
  gap: 8px;
}

.warn-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.02em;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  white-space: nowrap;
}

.warn-chip.is-anchor {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 8%, var(--theme--background));
}

.warn-chip.is-chunks {
  color: #065f46;
  border-color: color-mix(in srgb, #059669 45%, var(--theme--border-color));
  background: color-mix(in srgb, #059669 6%, var(--theme--background));
}

.warn-chip.is-draft,
.warn-chip.is-schema,
.warn-chip.is-fallback {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 8%, var(--theme--background));
}

.warn-chip.is-repair {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 10%, var(--theme--background));
}

.warn-chip.is-repair-fail {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 70%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 18%, var(--theme--background));
}

.chip-side {
  display: inline-flex;
  align-items: center;
  margin-right: 4px;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.04em;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
  color: var(--theme--foreground-subdued);
  text-transform: uppercase;
}

.chip-side.is-B {
  color: var(--theme--foreground);
  border-color: color-mix(in srgb, var(--theme--foreground-subdued) 45%, var(--theme--border-color));
}

.chip-side.is-C {
  color: var(--theme--primary);
  border-color: color-mix(in srgb, var(--theme--primary) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 8%, var(--theme--background));
}

.chip-side.is-B\+C {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 10%, var(--theme--background));
}

</style>
