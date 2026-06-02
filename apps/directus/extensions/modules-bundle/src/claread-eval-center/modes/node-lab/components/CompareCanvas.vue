<script setup>
import { computed } from "vue";
import NodeProbeOutputView from "../../../components/NodeProbeOutputView.vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import {
  statusLabel,
  statusTone,
  resultIssue,
  sentenceOrderKey,
  compareSentenceModel,
  sentenceToneClass,
  groupEntriesBySentence,
} from "../composables/useNodeLabFormatting";

const { compareResult, compareSentenceRows, state } = useNodeLabState();

function scopedPreparedSentences(entry, sentenceId) {
  const sentences = Array.isArray(entry?.prepared_sentences) ? entry.prepared_sentences : [];
  return sentences.filter((item) => String(item?.sentence_id || "") === String(sentenceId));
}

function scopedOutputForRow(rowSide, nodeName) {
  if (nodeName === "grammar") {
    return {
      grammar_notes: rowSide.notes || [],
      sentence_analyses: rowSide.analyses || [],
    };
  }
  if (nodeName === "vocabulary") {
    return {
      vocab_highlights: rowSide.vocabHighlights || [],
      phrase_glosses: rowSide.phraseGlosses || [],
      context_glosses: rowSide.contextGlosses || [],
    };
  }
  return {
    title: "",
    sentence_translations: rowSide.translations || [],
  };
}
</script>

<template>
  <div class="compare-canvas">
    <div
      v-for="row in compareSentenceRows"
      :key="row.sentenceId"
      class="compare-row"
      :class="row.toneClass"
    >
      <div class="compare-row__header">
        <span class="compare-row__id">{{ row.sentenceId }}</span>
        <p class="compare-row__sentence">{{ row.sentenceText || '当前未返回原句。' }}</p>
      </div>
      <div class="compare-row__body">
        <div class="compare-column">
          <div class="compare-column__header">
            <h4>Baseline</h4>
            <span class="badge" :class="`badge-${statusTone(compareResult.baseline?.status)}`">{{ statusLabel(compareResult.baseline?.status) }}</span>
          </div>
          <div
            v-if="resultIssue(compareResult?.baseline, 'Baseline')"
            class="execution-alert compact"
            :class="`is-${resultIssue(compareResult?.baseline, 'Baseline').tone}`"
          >
            <div class="execution-alert__header">
              <strong>{{ resultIssue(compareResult?.baseline, 'Baseline').title }}</strong>
            </div>
            <p>{{ resultIssue(compareResult?.baseline, 'Baseline').detail }}</p>
          </div>
          <template v-if="state.activeNode === 'grammar'">
            <NodeProbeOutputView
              v-if="row.baseline.notes.length || row.baseline.analyses.length"
              :node-name="state.activeNode"
              :output="scopedOutputForRow(row.baseline, state.activeNode)"
              :prepared-sentences="scopedPreparedSentences(compareResult?.baseline, row.sentenceId)"
              :quick-validation="compareResult?.baseline?.quick_validation || null"
              empty-text="该句在 Baseline 中没有结构化输出。"
            />
            <div v-else class="compare-empty">该句在 Baseline 中没有结构化输出。</div>
          </template>
          <template v-else-if="state.activeNode === 'vocabulary'">
            <NodeProbeOutputView
              v-if="row.baseline.vocabHighlights.length || row.baseline.phraseGlosses.length || row.baseline.contextGlosses.length"
              :node-name="state.activeNode"
              :output="scopedOutputForRow(row.baseline, state.activeNode)"
              :prepared-sentences="scopedPreparedSentences(compareResult?.baseline, row.sentenceId)"
              empty-text="该句在 Baseline 中没有词汇标注。"
            />
            <div v-else class="compare-empty">该句在 Baseline 中没有词汇标注。</div>
          </template>
          <template v-else>
            <NodeProbeOutputView
              v-if="row.baseline.translations.length"
              :node-name="state.activeNode"
              :output="scopedOutputForRow(row.baseline, state.activeNode)"
              :prepared-sentences="scopedPreparedSentences(compareResult?.baseline, row.sentenceId)"
              empty-text="该句在 Baseline 中没有翻译输出。"
            />
            <div v-else class="compare-empty">该句在 Baseline 中没有翻译输出。</div>
          </template>
        </div>

        <div class="compare-column">
          <div class="compare-column__header">
            <h4>Candidate</h4>
            <span class="badge" :class="`badge-${statusTone(compareResult.candidate?.status)}`">{{ statusLabel(compareResult.candidate?.status) }}</span>
          </div>
          <div
            v-if="resultIssue(compareResult?.candidate, 'Candidate')"
            class="execution-alert compact"
            :class="`is-${resultIssue(compareResult?.candidate, 'Candidate').tone}`"
          >
            <div class="execution-alert__header">
              <strong>{{ resultIssue(compareResult?.candidate, 'Candidate').title }}</strong>
            </div>
            <p>{{ resultIssue(compareResult?.candidate, 'Candidate').detail }}</p>
          </div>
          <template v-if="state.activeNode === 'grammar'">
            <NodeProbeOutputView
              v-if="row.candidate.notes.length || row.candidate.analyses.length"
              :node-name="state.activeNode"
              :output="scopedOutputForRow(row.candidate, state.activeNode)"
              :prepared-sentences="scopedPreparedSentences(compareResult?.candidate, row.sentenceId)"
              :quick-validation="compareResult?.candidate?.quick_validation || null"
              empty-text="该句在 Candidate 中没有结构化输出。"
            />
            <div v-else class="compare-empty">该句在 Candidate 中没有结构化输出。</div>
          </template>
          <template v-else-if="state.activeNode === 'vocabulary'">
            <NodeProbeOutputView
              v-if="row.candidate.vocabHighlights.length || row.candidate.phraseGlosses.length || row.candidate.contextGlosses.length"
              :node-name="state.activeNode"
              :output="scopedOutputForRow(row.candidate, state.activeNode)"
              :prepared-sentences="scopedPreparedSentences(compareResult?.candidate, row.sentenceId)"
              empty-text="该句在 Candidate 中没有词汇标注。"
            />
            <div v-else class="compare-empty">该句在 Candidate 中没有词汇标注。</div>
          </template>
          <template v-else>
            <NodeProbeOutputView
              v-if="row.candidate.translations.length"
              :node-name="state.activeNode"
              :output="scopedOutputForRow(row.candidate, state.activeNode)"
              :prepared-sentences="scopedPreparedSentences(compareResult?.candidate, row.sentenceId)"
              empty-text="该句在 Candidate 中没有翻译输出。"
            />
            <div v-else class="compare-empty">该句在 Candidate 中没有翻译输出。</div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.compare-canvas {
  display: grid;
  gap: 16px;
}

.compare-row {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--color-surface);
}

.compare-row__header {
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-border);
  background: color-mix(in srgb, var(--sentence-tint, #eef2ff) 65%, var(--color-surface));
  display: grid;
  gap: 8px;
}

.compare-row__id {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  background: var(--sentence-accent, #6366f1);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}

.compare-row__sentence {
  margin: 0;
  font-size: 14px;
  line-height: 1.65;
  color: var(--color-text);
}

.compare-row__body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding: 16px;
}

.compare-column {
  min-width: 0;
  display: grid;
  gap: 12px;
}

.compare-column__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.compare-column__header h4 {
  font-size: 14px;
  font-weight: 600;
}

.badge {
  display: inline-flex;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 999px;
  background: var(--color-surface-subdued);
  border: 1px solid var(--color-border);
}

.badge-success { border-color: color-mix(in srgb, var(--theme--success, #10b981) 45%, var(--color-border)); color: var(--theme--success, #10b981); }
.badge-warning { border-color: color-mix(in srgb, #d97706 45%, var(--color-border)); color: #b45309; }
.badge-danger { border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 45%, var(--color-border)); color: var(--theme--danger, #dc2626); }
.badge-neutral { color: var(--color-text-subdued); }

.execution-alert {
  padding: 14px 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: var(--color-surface-subdued);
}

.execution-alert.compact {
  margin-bottom: 12px;
  padding: 12px 14px;
}

.execution-alert.is-warning {
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 32%, var(--color-border));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 7%, var(--color-surface));
}

.execution-alert.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 32%, var(--color-border));
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 7%, var(--color-surface));
}

.execution-alert__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
}

.execution-alert p {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-subdued);
}

.compare-empty {
  min-height: 120px;
  padding: 16px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
  color: var(--color-text-subdued);
  font-size: 13px;
  line-height: 1.55;
}

.compare-row.tone-amber {
  --sentence-accent: #d97706;
  --sentence-tint: #fef3c7;
}

.compare-row.tone-blue {
  --sentence-accent: #2563eb;
  --sentence-tint: #dbeafe;
}

.compare-row.tone-green {
  --sentence-accent: #059669;
  --sentence-tint: #d1fae5;
}

.compare-row.tone-violet {
  --sentence-accent: #7c3aed;
  --sentence-tint: #ede9fe;
}

.compare-row.tone-rose {
  --sentence-accent: #e11d48;
  --sentence-tint: #ffe4e6;
}

.compare-row.tone-slate {
  --sentence-accent: #475569;
  --sentence-tint: #e2e8f0;
}

@media (max-width: 1200px) {
  .compare-row__body {
    grid-template-columns: 1fr;
  }
}
</style>
