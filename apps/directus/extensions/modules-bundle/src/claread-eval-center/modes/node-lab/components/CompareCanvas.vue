<script setup>
import { computed, ref } from "vue";
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
  judgeItemResultLabel,
} from "../composables/useNodeLabFormatting";

const { compareResult, compareSentenceRows, state, loading, selectedJudgeRequestDetail } = useNodeLabState();

const showInlineJudge = ref(true);

const rubricScoringItems = computed(() => {
  const result = selectedJudgeRequestDetail.value?.result?.rubric_scoring_result;
  if (!result) return { baseline: [], candidate: [] };
  return {
    baseline: result.baseline?.items || [],
    candidate: result.candidate?.items || []
  };
});

function getJudgeItemsForSentence(side, sentenceId) {
  return rubricScoringItems.value[side].filter(item => String(item.sentence_id || item.item_id) === String(sentenceId));
}

function getDeltaBadge(baselineItems, candidateItems) {
  if (!baselineItems?.length || !candidateItems?.length) return null;
  const bPass = baselineItems.every(item => item.criteria.every(c => c.score));
  const cPass = candidateItems.every(item => item.criteria.every(c => c.score));
  if (!bPass && cPass) return { tone: 'success', label: '+ 修复' };
  if (bPass && !cPass) return { tone: 'danger', label: '- 劣化' };
  return null;
}

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
  <div class="compare-canvas-header" v-if="compareResult">
    <div class="canvas-actions" v-if="selectedJudgeRequestDetail?.result?.rubric_scoring_result">
      <label class="toggle-switch">
        <input type="checkbox" v-model="showInlineJudge" />
        <span class="toggle-slider"></span>
        <span class="toggle-label">内联显示 Judge 评分</span>
      </label>
    </div>
  </div>

  <div v-if="loading.compare && !compareResult" class="compare-loading">
    <div class="loading-spinner"></div>
    <span>正在运行 Compare，结果加载后将显示逐句对比...</span>
  </div>
  <div v-else-if="compareResult" class="compare-canvas">
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
        <div class="compare-column" role="region" aria-label="Baseline">
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

          <div v-if="showInlineJudge && getJudgeItemsForSentence('baseline', row.sentenceId).length" class="inline-judge-panel fade-in">
            <div class="inline-judge-header">
              <span class="inline-judge-title">Baseline 评分</span>
            </div>
            <div v-for="judgeItem in getJudgeItemsForSentence('baseline', row.sentenceId)" :key="judgeItem.item_id" class="judge-item-group mt-2">
              <div class="judge-item-label">{{ judgeItemResultLabel(judgeItem) }}</div>
              <ul class="insight-list">
                <li v-for="criterion in judgeItem.criteria" :key="criterion.criterion_id">
                  <span class="rubric-indicator" :class="criterion.score ? 'is-pass' : 'is-fail'">
                    {{ criterion.score ? '✓' : '✗' }}
                  </span>
                  <strong>{{ criterion.criterion_id }}</strong>
                  <span>：{{ criterion.reason }}</span>
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div class="compare-column" role="region" aria-label="Candidate">
          <div class="compare-column__header">
            <h4>Candidate</h4>
            <div class="header-badges">
              <span v-if="showInlineJudge && getDeltaBadge(getJudgeItemsForSentence('baseline', row.sentenceId), getJudgeItemsForSentence('candidate', row.sentenceId))" 
                    class="badge badge-sm fade-in delta-badge" 
                    :class="`badge-${getDeltaBadge(getJudgeItemsForSentence('baseline', row.sentenceId), getJudgeItemsForSentence('candidate', row.sentenceId)).tone}`">
                {{ getDeltaBadge(getJudgeItemsForSentence('baseline', row.sentenceId), getJudgeItemsForSentence('candidate', row.sentenceId)).label }}
              </span>
              <span class="badge" :class="`badge-${statusTone(compareResult.candidate?.status)}`">{{ statusLabel(compareResult.candidate?.status) }}</span>
            </div>
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
          
          <div v-if="showInlineJudge && getJudgeItemsForSentence('candidate', row.sentenceId).length" class="inline-judge-panel fade-in">
            <div class="inline-judge-header">
              <span class="inline-judge-title">Candidate 评分</span>
            </div>
            <div v-for="judgeItem in getJudgeItemsForSentence('candidate', row.sentenceId)" :key="judgeItem.item_id" class="judge-item-group mt-2">
              <div class="judge-item-label">{{ judgeItemResultLabel(judgeItem) }}</div>
              <ul class="insight-list">
                <li v-for="criterion in judgeItem.criteria" :key="criterion.criterion_id">
                  <span class="rubric-indicator" :class="criterion.score ? 'is-pass' : 'is-fail'">
                    {{ criterion.score ? '✓' : '✗' }}
                  </span>
                  <strong>{{ criterion.criterion_id }}</strong>
                  <span>：{{ criterion.reason }}</span>
                </li>
              </ul>
            </div>
          </div>
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

.compare-loading {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 32px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-subdued);
  color: var(--color-text-subdued);
  font-size: 14px;
}
.loading-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: node-lab-spin 0.8s linear infinite;
}
@keyframes node-lab-spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 1200px) {
  .compare-row__body {
    grid-template-columns: 1fr;
  }
}
</style>
