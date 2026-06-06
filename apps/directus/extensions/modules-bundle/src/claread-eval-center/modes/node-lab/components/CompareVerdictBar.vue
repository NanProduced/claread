<script setup>
import { computed, ref, watch } from "vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { useNodeLabApi } from "../composables/useNodeLabApi";
import {
  statusTone,
  statusLabel,
  shortId,
} from "../../../composables/useEvalFormatting";
import {
  formatDurationMs,
  formatRuntimeTokens,
  compareDeltaTone,
  formatSignedDelta,
  nodeLabel,
  readingGoalLabel,
  readingVariantLabel,
  compareViewSourceLabel,
  trialJudgeCount,
  normalizePreviewText,
  buildInputPreview,
} from "../composables/useNodeLabFormatting";

const {
  compareResult,
  loading,
  state,
  currentText,
  currentReadingGoal,
  currentReadingVariant,
  activeCompareView,
  activeCompareTrial,
  currentCompareTrialId,
  latestCompareTrialId,
  selectedJudgeRequestDetail,
} = useNodeLabState();
const { loadTrialDetail } = useNodeLabApi();

const hydratedCompareResult = ref(null);

const compareTrialIdForHydration = computed(() => {
  return String(
    activeCompareTrial.value?.trial_id
    || activeCompareView.value?.trialId
    || currentCompareTrialId.value
    || latestCompareTrialId.value
    || "",
  ).trim();
});

function hasRichSideMetadata(entry) {
  if (!entry || typeof entry !== "object") return false;
  const hasStatus = Boolean(entry.status);
  const hasModel = Boolean(entry.model_identity?.model_name || entry.model_identity?.profile_name);
  const hasRuntime = Boolean(entry.runtime_summary?.latency_ms || entry.runtime_summary?.aggregate?.total_tokens);
  const hasPrompt = Boolean(entry.prompt_identity?.prompt_variant_id || entry.prompt_identity?.prompt_version || entry.prompt_identity?.prompt_snapshot_hash);
  const hasExamples = Boolean(entry.example_summary?.selection_mode);
  const hasOutput = Boolean(entry.node_output || (Array.isArray(entry.prepared_sentences) && entry.prepared_sentences.length));
  return hasStatus && (hasModel || hasRuntime || hasPrompt || hasExamples || hasOutput);
}

function compareSideStatus(entry, summary, side) {
  return entry?.status || summary?.result_status?.[`${side}_status`] || null;
}

function compareSideModel(entry) {
  return entry?.model_identity?.model_name || entry?.model_identity?.profile_name || "未记录";
}

function compareSideFewShot(entry, side) {
  if (entry?.example_summary?.selection_mode) return entry.example_summary.selection_mode;
  if (side === "baseline") return "baseline";
  return entry?.candidate_id ? "candidate" : "未记录";
}

function compareSideLatency(entry, summary, side) {
  const summaryValue = summary?.[`${side}_latency_ms`];
  if (Number.isFinite(summaryValue)) return formatDurationMs(summaryValue);
  return formatDurationMs(entry?.runtime_summary?.latency_ms);
}

function compareSidePrompt(entry, side) {
  return entry?.prompt_identity?.prompt_snapshot_hash
    || entry?.prompt_identity?.prompt_variant_id
    || entry?.snapshot_hash
    || (side === "baseline" ? "baseline" : entry?.candidate_id || "baseline");
}

const effectiveCompareResult = computed(() => {
  const live = compareResult.value;
  if (live && (hasRichSideMetadata(live.baseline) || hasRichSideMetadata(live.candidate))) {
    return live;
  }
  return hydratedCompareResult.value || live || null;
});

watch(
  compareTrialIdForHydration,
  async (trialId) => {
    hydratedCompareResult.value = null;
    if (!trialId) return;
    try {
      const detail = await loadTrialDetail(trialId);
      if (detail?.result?.baseline || detail?.result?.candidate) {
        hydratedCompareResult.value = detail.result;
      }
    } catch {
      // ignore hydration failures; UI will fall back to current compare payload
    }
  },
  { immediate: true },
);

const compareRequestSnapshot = computed(() => effectiveCompareResult.value?.request_snapshot || null);

const compareResultStatus = computed(() => {
  return effectiveCompareResult.value?.compare_summary?.result_status || {};
});

const compareContextMismatch = computed(() => {
  const snapshot = compareRequestSnapshot.value;
  if (!snapshot || !effectiveCompareResult.value) return null;

  const snapshotNode = String(snapshot.node_name || effectiveCompareResult.value?.node_name || "").trim();
  const snapshotGoal = String(snapshot.reading_goal || "").trim();
  const snapshotVariant = String(snapshot.reading_variant || "").trim();

  if (snapshotNode && snapshotNode !== state.activeNode) {
    return `当前页面节点是 ${nodeLabel(state.activeNode)}，但右侧结果来自 ${nodeLabel(snapshotNode)}`;
  }
  if (snapshotGoal && snapshotGoal !== currentReadingGoal.value) {
    return `当前页面阅读目标是 ${readingGoalLabel(currentReadingGoal.value)}，但右侧结果来自 ${readingGoalLabel(snapshotGoal)}`;
  }
  if (snapshotVariant && snapshotVariant !== currentReadingVariant.value) {
    return `当前页面阅读变体是 ${readingVariantLabel(currentReadingVariant.value)}，但右侧结果来自 ${readingVariantLabel(snapshotVariant)}`;
  }

  const comparePreview = normalizePreviewText(
    activeCompareView.value?.inputPreview
      || activeCompareTrial.value?.input_excerpt
      || snapshot.source_excerpt
      || "",
  );
  const currentPreview = buildInputPreview(currentText.value);
  if (comparePreview && currentPreview && !currentPreview.startsWith(comparePreview)) {
    return "当前输入文本已变化，但右侧仍显示上一条 Compare 结果";
  }
  return null;
});

const compareOverviewCards = computed(() => {
  const result = effectiveCompareResult.value;
  if (!result) {
    return [
      {
        key: "baseline",
        title: "Baseline",
        tone: "neutral",
        status: "未记录",
        model: "未记录",
        fewShot: "未记录",
        latency: "未记录",
        tokens: "未记录",
        prompt: "未记录",
        deltaLatency: null,
        deltaTokens: null,
        deltaModel: null,
        deltaFewShot: null,
      },
      {
        key: "candidate",
        title: "Candidate",
        tone: "neutral",
        status: "未记录",
        model: "未记录",
        fewShot: "未记录",
        latency: "未记录",
        tokens: "未记录",
        prompt: "未记录",
        deltaLatency: null,
        deltaTokens: null,
        deltaModel: null,
        deltaFewShot: null,
      },
    ];
  }

  const baseline = result.baseline || {};
  const candidate = result.candidate || {};
  const summary = result.compare_summary || {};

  const latencyDelta = Number.isFinite(summary.latency_delta_ms) ? summary.latency_delta_ms : null;
  const tokenDelta = Number.isFinite(summary.token_delta) ? summary.token_delta : null;
  const modelChanged = (baseline.model_identity?.model_name || "") !== (candidate.model_identity?.model_name || "");
  const fewShotChanged = (baseline.example_summary?.selection_mode || "未记录") !== (candidate.example_summary?.selection_mode || "未记录");

  return [
    {
      key: "baseline",
      title: "Baseline",
      tone: statusTone(compareSideStatus(baseline, summary, "baseline")),
      status: statusLabel(compareSideStatus(baseline, summary, "baseline")),
      model: compareSideModel(baseline),
      fewShot: compareSideFewShot(baseline, "baseline"),
      latency: compareSideLatency(baseline, summary, "baseline"),
      tokens: formatRuntimeTokens(baseline.runtime_summary),
      prompt: compareSidePrompt(baseline, "baseline"),
      deltaLatency: null,
      deltaTokens: null,
      deltaModel: modelChanged ? "参考基线" : null,
      deltaFewShot: fewShotChanged ? "参考基线" : null,
    },
    {
      key: "candidate",
      title: "Candidate",
      tone: statusTone(compareSideStatus(candidate, summary, "candidate")),
      status: statusLabel(compareSideStatus(candidate, summary, "candidate")),
      model: compareSideModel(candidate),
      fewShot: compareSideFewShot(candidate, "candidate"),
      latency: compareSideLatency(candidate, summary, "candidate"),
      tokens: formatRuntimeTokens(candidate.runtime_summary),
      prompt: compareSidePrompt(candidate, "candidate"),
      deltaLatency: latencyDelta,
      deltaTokens: tokenDelta,
      deltaModel: modelChanged
        ? `${compareSideModel(baseline) || "默认"} → ${compareSideModel(candidate) || "默认"}`
        : null,
      deltaFewShot: fewShotChanged
        ? `${compareSideFewShot(baseline, "baseline")} → ${compareSideFewShot(candidate, "candidate")}`
        : null,
    },
  ];
});

const hasCompareOverviewCards = computed(() => compareOverviewCards.value.length > 0);

const currentCompareSummary = computed(() => {
  if (!effectiveCompareResult.value) return null;
  const trial = activeCompareTrial.value || null;
  return {
    compareId: shortId(trial?.trial_id || activeCompareView.value?.trialId || currentCompareTrialId.value || latestCompareTrialId.value || "live"),
    source: compareViewSourceLabel(activeCompareView.value, trial),
    sessionTitle: trial?.session_title || "",
    judgeCount: trialJudgeCount(trial) || 0,
    staleReason: compareContextMismatch.value,
  };
});

const pairwiseReview = computed(() => {
  return selectedJudgeRequestDetail.value?.result?.pairwise_result?.pairwise_review || null;
});
</script>

<template>
  <div v-if="loading.compare && !compareResult" class="compare-loading">
    <div class="loading-spinner"></div>
    <span>正在准备 Compare 概览...</span>
  </div>
  <div v-else-if="effectiveCompareResult" class="compare-verdict-bar">
    <div v-if="pairwiseReview" class="pairwise-summary">
      <div class="pairwise-summary__header">
        <strong>Judge 综合倾向</strong>
        <span
          class="badge"
          :class="pairwiseReview.preferred_side === 'candidate'
            ? 'badge-success'
            : pairwiseReview.preferred_side === 'baseline'
              ? 'badge-warning'
              : 'badge-neutral'"
        >
          {{ String(pairwiseReview.preferred_side || "unknown").toUpperCase() }}
        </span>
      </div>
      <p>{{ pairwiseReview.overall_judgment }}</p>
    </div>

    <div v-if="hasCompareOverviewCards" class="compare-overview">
      <article
        v-for="card in compareOverviewCards"
        :key="card.key"
        class="compare-status-card"
        :class="`is-${card.tone}`"
      >
        <div class="compare-status-card__header">
          <h4>{{ card.title }}</h4>
          <span class="badge" :class="`badge-${card.tone}`">{{ card.status }}</span>
        </div>
        <div class="compare-status-card__facts">
          <div class="status-fact">
            <span class="meta-label">模型</span>
            <span class="meta-value">
              {{ card.model }}
              <small v-if="card.key === 'candidate' && card.deltaModel" class="delta-inline text-warning">{{ card.deltaModel }}</small>
            </span>
          </div>
          <div class="status-fact">
            <span class="meta-label">Few-shot</span>
            <span class="meta-value">
              {{ card.fewShot }}
              <small v-if="card.key === 'candidate' && card.deltaFewShot" class="delta-inline text-warning">{{ card.deltaFewShot }}</small>
            </span>
          </div>
          <div class="status-fact">
            <span class="meta-label">延迟</span>
            <span class="meta-value">
              {{ card.latency }}
              <small
                v-if="card.key === 'candidate' && Number.isFinite(card.deltaLatency)"
                class="delta-inline"
                :class="`text-${compareDeltaTone(card.deltaLatency, 'latency')}`"
              >
                {{ formatSignedDelta(Number((card.deltaLatency / 1000).toFixed(card.deltaLatency >= 10000 ? 1 : 2)), " s") }}
              </small>
            </span>
          </div>
          <div class="status-fact">
            <span class="meta-label">Tokens</span>
            <span class="meta-value">
              {{ card.tokens }}
              <small
                v-if="card.key === 'candidate' && Number.isFinite(card.deltaTokens)"
                class="delta-inline"
                :class="`text-${compareDeltaTone(card.deltaTokens, 'tokens')}`"
              >
                {{ formatSignedDelta(card.deltaTokens) }}
              </small>
            </span>
          </div>
          <div class="status-fact fact-span-2">
            <span class="meta-label">Prompt</span>
            <span class="meta-value">{{ card.prompt }}</span>
          </div>
        </div>
      </article>
    </div>
    <div v-else class="compare-overview compare-overview--empty">
      <article class="compare-status-card is-neutral">
        <div class="compare-status-card__header">
          <h4>Baseline</h4>
          <span class="badge badge-neutral">未记录</span>
        </div>
        <div class="compare-status-card__facts">
          <div class="status-fact"><span class="meta-label">模型</span><span class="meta-value">未记录</span></div>
          <div class="status-fact"><span class="meta-label">Few-shot</span><span class="meta-value">未记录</span></div>
          <div class="status-fact"><span class="meta-label">延迟</span><span class="meta-value">未记录</span></div>
          <div class="status-fact"><span class="meta-label">Tokens</span><span class="meta-value">未记录</span></div>
          <div class="status-fact fact-span-2"><span class="meta-label">Prompt</span><span class="meta-value">未记录</span></div>
        </div>
      </article>
      <article class="compare-status-card is-neutral">
        <div class="compare-status-card__header">
          <h4>Candidate</h4>
          <span class="badge badge-neutral">未记录</span>
        </div>
        <div class="compare-status-card__facts">
          <div class="status-fact"><span class="meta-label">模型</span><span class="meta-value">未记录</span></div>
          <div class="status-fact"><span class="meta-label">Few-shot</span><span class="meta-value">未记录</span></div>
          <div class="status-fact"><span class="meta-label">延迟</span><span class="meta-value">未记录</span></div>
          <div class="status-fact"><span class="meta-label">Tokens</span><span class="meta-value">未记录</span></div>
          <div class="status-fact fact-span-2"><span class="meta-label">Prompt</span><span class="meta-value">未记录</span></div>
        </div>
      </article>
    </div>

    <div class="compare-status-line">
      <div class="compare-status-line__item">
        <span class="meta-label">Compare 状态</span>
        <strong :class="`text-${statusTone(compareResultStatus.compare_status)}`">
          {{ statusLabel(compareResultStatus.compare_status) }}
        </strong>
        <span class="compare-status-line__detail">
          Baseline {{ statusLabel(compareResultStatus.baseline_status) }} / Candidate {{ statusLabel(compareResultStatus.candidate_status) }}
        </span>
      </div>
      <div class="compare-status-line__item" v-if="currentCompareSummary">
        <span class="meta-label">当前 Compare</span>
        <strong>#{{ currentCompareSummary.compareId }} · {{ currentCompareSummary.source }}</strong>
        <span class="compare-status-line__detail">
          {{ currentCompareSummary.sessionTitle || "未挂载 Session" }} · Judge {{ currentCompareSummary.judgeCount }} 条
        </span>
      </div>
    </div>

    <div v-if="currentCompareSummary?.staleReason" class="compare-relation-strip">
      <div class="relation-chip is-warning">
        <span class="relation-label">旧结果提示</span>
        <strong>{{ currentCompareSummary.staleReason }}</strong>
      </div>
    </div>
  </div>
</template>

<style scoped>
.compare-loading {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-subdued);
  color: var(--color-text-subdued);
  font-size: 14px;
  margin-bottom: 16px;
}

.loading-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: node-lab-spin 0.8s linear infinite;
}

.compare-verdict-bar {
  display: grid;
  gap: 16px;
  margin-bottom: 16px;
}

.pairwise-summary {
  padding: 16px 18px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
}

.pairwise-summary__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.pairwise-summary p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-subdued);
}

.compare-overview {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.compare-status-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 18px 18px 16px;
  background: var(--color-surface);
}

.compare-status-card.is-success {
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 24%, var(--color-border));
}

.compare-status-card.is-warning {
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 24%, var(--color-border));
}

.compare-status-card.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 24%, var(--color-border));
}

.compare-status-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.compare-status-card__header h4 {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
}

.compare-status-card__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 18px;
}

.status-fact {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.fact-span-2 {
  grid-column: 1 / -1;
}

.compare-status-line {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.compare-status-line__item {
  padding: 14px 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
  display: grid;
  gap: 6px;
}

.compare-status-line__detail {
  font-size: 12px;
  line-height: 1.55;
  color: var(--color-text-subdued);
}

.compare-relation-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.relation-chip {
  display: inline-flex;
  flex-direction: column;
  gap: 2px;
  min-width: 180px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
}

.relation-chip strong {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.relation-chip.is-warning {
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 7%, var(--color-surface));
}

.relation-label {
  font-size: 11px;
  color: var(--color-text-subdued);
  font-weight: 500;
}

.meta-label {
  font-size: 13px;
  color: var(--color-text-subdued);
  font-weight: 500;
}

.meta-value {
  font-size: 14px;
  font-weight: 500;
  min-width: 0;
  word-break: break-word;
}

.delta-inline {
  display: inline-block;
  margin-top: 2px;
  padding: 1px 6px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.45;
  border-radius: 4px;
  background: color-mix(in srgb, var(--color-surface-subdued) 60%, transparent);
}

.delta-inline.text-success {
  background: color-mix(in srgb, var(--theme--success, #10b981) 10%, var(--color-surface));
}

.delta-inline.text-warning {
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 10%, var(--color-surface));
}

.delta-inline.text-danger {
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 10%, var(--color-surface));
}

.text-success { color: var(--theme--success, #10b981); }
.text-warning { color: var(--theme--warning, #f59e0b); }
.text-danger { color: var(--theme--danger, #dc2626); }
.text-neutral { color: var(--color-text-subdued); }

.badge {
  display: inline-flex;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 999px;
  background: var(--color-surface-subdued);
  border: 1px solid var(--color-border);
}

.badge-success {
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 45%, var(--color-border));
  color: var(--theme--success, #10b981);
}

.badge-warning {
  border-color: color-mix(in srgb, #d97706 45%, var(--color-border));
  color: #b45309;
}

.badge-danger {
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 45%, var(--color-border));
  color: var(--theme--danger, #dc2626);
}

.badge-neutral {
  color: var(--color-text-subdued);
}

@keyframes node-lab-spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 1200px) {
  .compare-overview,
  .compare-status-line {
    grid-template-columns: 1fr;
  }
}
</style>
