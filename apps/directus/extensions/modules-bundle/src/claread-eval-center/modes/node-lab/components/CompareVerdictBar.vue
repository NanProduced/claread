<script setup>
import { computed } from "vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { useNodeLabApi } from "../composables/useNodeLabApi";
import {
  statusBadgeLabel,
  formatDurationMs,
  formatRuntimeTokens,
  compareDeltaTone,
  formatSignedDelta,
  statusTone,
  statusLabel,
  shortId,
  nodeLabel,
  readingGoalLabel,
  readingVariantLabel,
  compareViewSourceLabel,
  compareViewSourceTone,
  normalizePreviewText,
  buildInputPreview,
  trialJudgeCount,
  compareTrialSourceLabel,
  trialReadableTitle,
  trialReadableMeta,
} from "../composables/useNodeLabFormatting";

const {
  compareResult,
  state, currentText, currentReadingGoal, currentReadingVariant,
  activeCompareView, activeCompareTrial,
  currentCompareTrialId, latestCompareTrialId,
  recentTrials,
  loading,
  selectedJudgeRequestDetail,
} = useNodeLabState();
const { openCompareTrialInWorkbench } = useNodeLabApi();

const compareRequestSnapshot = computed(() => {
  return compareResult.value?.request_snapshot || null;
});

const activeCompareInputPreview = computed(() => {
  return String(
    activeCompareView.value?.inputPreview
    || activeCompareTrial.value?.input_excerpt
    || compareRequestSnapshot.value?.source_excerpt
    || ""
  ).trim();
});

const compareSnapshotContextMismatchReason = computed(() => {
  const snapshot = compareRequestSnapshot.value;
  if (!snapshot) return null;

  const snapshotNode = String(snapshot.node_name || compareResult.value?.node_name || "").trim();
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
  const comparePreview = normalizePreviewText(activeCompareInputPreview.value);
  const currentPreview = buildInputPreview(currentText.value);
  if (comparePreview && currentPreview && !currentPreview.startsWith(comparePreview)) {
    return "当前输入文本已变化，但右侧仍显示上一条 Compare 结果";
  }
  return null;
});

const compareResultStatus = computed(() => {
  const result = compareResult.value;
  return result?.compare_summary?.result_status || {};
});

const compareOverviewCards = computed(() => {
  const result = compareResult.value;
  if (!result) return [];
  const latencyDelta = Number.isFinite(result.compare_summary?.latency_delta_ms) ? result.compare_summary.latency_delta_ms : null;
  const tokenDelta = Number.isFinite(result.compare_summary?.token_delta) ? result.compare_summary.token_delta : null;
  const modelChanged = (result.baseline?.model_identity?.model_name || "") !== (result.candidate?.model_identity?.model_name || "");
  const fewShotChanged = (result.baseline?.example_summary?.selection_mode || "未记录") !== (result.candidate?.example_summary?.selection_mode || "未记录");
  return [
    {
      key: "baseline",
      title: "Baseline",
      status: statusBadgeLabel(result.baseline),
      tone: statusTone(result.baseline?.status),
      model: result.baseline?.model_identity?.model_name || "未记录",
      fewShot: result.baseline?.example_summary?.selection_mode || "未记录",
      latency: formatDurationMs(result.compare_summary?.baseline_latency_ms),
      tokens: formatRuntimeTokens(result.baseline?.runtime_summary),
      prompt: result.baseline?.prompt_identity?.prompt_snapshot_hash || "baseline",
      deltaLatency: null,
      deltaTokens: null,
      deltaModel: modelChanged ? "参考基线" : null,
      deltaFewShot: fewShotChanged ? "参考基线" : null,
    },
    {
      key: "candidate",
      title: "Candidate",
      status: statusBadgeLabel(result.candidate),
      tone: statusTone(result.candidate?.status),
      model: result.candidate?.model_identity?.model_name || "未记录",
      fewShot: result.candidate?.example_summary?.selection_mode || "未记录",
      latency: formatDurationMs(result.compare_summary?.candidate_latency_ms),
      tokens: formatRuntimeTokens(result.candidate?.runtime_summary),
      prompt: result.candidate?.prompt_identity?.prompt_snapshot_hash || "baseline",
      deltaLatency: latencyDelta,
      deltaTokens: tokenDelta,
      deltaModel: modelChanged ? `${result.baseline?.model_identity?.model_name || "默认"} → ${result.candidate?.model_identity?.model_name || "默认"}` : null,
      deltaFewShot: fewShotChanged ? `${result.baseline?.example_summary?.selection_mode || "未记录"} → ${result.candidate?.example_summary?.selection_mode || "未记录"}` : null,
    },
  ];
});

const compareTrialAvailability = computed(() => {
  const mismatch = compareSnapshotContextMismatchReason.value;
  if (currentCompareTrialId.value && !mismatch) {
    return {
      id: currentCompareTrialId.value,
      detail: "当前 Compare 已持久化，可直接发起 Judge。",
    };
  }
  if (compareResult.value && !mismatch) {
    return {
      id: "尚未持久化",
      detail: "当前 Compare 结果尚未持久化，可通过 Judge 或加入 Session 自动持久化。",
    };
  }
  if (compareResult.value && mismatch) {
    return {
      id: currentCompareTrialId.value || "尚未持久化",
      detail: `${mismatch}。右侧仍显示上一条 Compare 结果；若继续 Judge，将评估这条结果，而不是当前表单内容。`,
    };
  }
  if (latestCompareTrialId.value) {
    return {
      id: latestCompareTrialId.value,
      detail: "这是历史持久化 Trial，非当前页面内容。",
    };
  }
  return {
    id: "尚未持久化",
    detail: "运行 Compare 后，可通过 Judge 或加入 Session 自动持久化。",
  };
});

const activeCompareRelation = computed(() => {
  if (!compareResult.value) return null;
  const trial = activeCompareTrial.value || null;
  const staleReason = compareSnapshotContextMismatchReason.value;
  const sourceLabel = compareViewSourceLabel(activeCompareView.value, trial);
  const sourceTone = compareViewSourceTone(activeCompareView.value, trial, staleReason);
  const sessionTitle = trial?.session_title
    || (trial?.session_id ? `Session ${shortId(trial.session_id)}` : "");
  const judgeCount = trialJudgeCount(trial) || 0;
  return {
    compareId: shortId(trial?.trial_id || activeCompareView.value?.trialId || "live"),
    sourceLabel,
    sourceTone,
    sessionTitle,
    judgeCount,
    staleReason,
    isPersisted: Boolean(trial?.trial_id),
    isLive: activeCompareView.value?.source === "live" && !trial?.trial_id,
  };
});
</script>

<template>
  <div v-if="loading.compare && !compareResult" class="compare-loading">
    <div class="loading-spinner"></div>
    <span>正在运行 Compare...</span>
  </div>
  <div v-else-if="compareResult">
    <div v-if="activeCompareView?.source && activeCompareView.source !== 'live'" class="return-banner" role="status">
      <span>正在查看历史 Compare 结果</span>
      <v-button small secondary @click="clearActiveCompareView(state.activeNode, { preserveLatestTrial: true })">返回当前 Compare</v-button>
    </div>

    <div v-if="selectedJudgeRequestDetail?.result?.pairwise_result?.pairwise_review" class="pairwise-verdict-panel mb-4 fade-in">
      <div class="pairwise-verdict-header">
        <h3 class="pairwise-title">Judge 综合评估：<span :class="selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.preferred_side === 'candidate' ? 'text-success' : (selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.preferred_side === 'baseline' ? 'text-warning' : 'text-neutral')">{{ selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.preferred_side.toUpperCase() }}</span> 胜出</h3>
      </div>
      <p class="pairwise-verdict-summary">{{ selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.overall_judgment }}</p>
      
      <div class="compare-split mt-3">
        <div class="compare-pane">
          <div class="pane-header"><h4>Baseline</h4></div>
          <ul class="insight-list">
            <li v-for="(item, index) in selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.baseline_strengths || []" :key="`bs-${index}`">
              <strong>优点：</strong>{{ item }}
            </li>
            <li v-for="(item, index) in selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.baseline_risks || []" :key="`br-${index}`">
              <strong>风险：</strong>{{ item }}
            </li>
          </ul>
        </div>
        <div class="compare-pane">
          <div class="pane-header"><h4>Candidate</h4></div>
          <ul class="insight-list">
            <li v-for="(item, index) in selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.candidate_strengths || []" :key="`cs-${index}`">
              <strong>优点：</strong>{{ item }}
            </li>
            <li v-for="(item, index) in selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.candidate_risks || []" :key="`cr-${index}`">
              <strong>风险：</strong>{{ item }}
            </li>
          </ul>
        </div>
      </div>
      <div v-if="selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.manual_check_points?.length" class="mt-3">
        <h5>建议人工复看</h5>
        <ul class="insight-list mt-2">
          <li v-for="(item, index) in selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.manual_check_points" :key="`mc-${index}`">
            {{ item }}
          </li>
        </ul>
      </div>
    </div>

    <div class="compare-overview">
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
          <div class="status-fact">
            <span class="meta-label">Prompt</span>
            <span class="meta-value">
              {{ card.prompt }}
              <small
                v-if="card.key === 'candidate' && compareResult?.compare_summary?.prompt_changed"
                class="delta-inline text-warning"
              >
                已变化
              </small>
            </span>
          </div>

        </div>
      </article>
    </div>

    <div class="compare-status-line">
      <div class="compare-status-line__item">
        <span class="meta-label">Compare 状态</span>
        <strong :class="`text-${statusTone(compareResultStatus.compare_status)}`">{{ statusLabel(compareResultStatus.compare_status) }}</strong>
        <span class="compare-status-line__detail">Baseline {{ statusLabel(compareResultStatus.baseline_status) }} / Candidate {{ statusLabel(compareResultStatus.candidate_status) }}</span>
      </div>
      <div class="compare-status-line__item">
        <span class="meta-label">当前可用 Compare Trial</span>
        <strong>{{ compareTrialAvailability.id }}</strong>
        <span class="compare-status-line__detail">{{ compareTrialAvailability.detail }}</span>
      </div>
    </div>

    <div v-if="activeCompareRelation" class="compare-relation-strip mb-4">
      <div class="relation-chip" :class="`is-${activeCompareRelation.sourceTone}`">
        <span class="relation-label">当前 Compare</span>
        <strong>#{{ activeCompareRelation.compareId }}</strong>
      </div>
      <div class="relation-chip">
        <span class="relation-label">来源</span>
        <strong>{{ activeCompareRelation.sourceLabel }}</strong>
      </div>
      <div class="relation-chip" v-if="activeCompareRelation.sessionTitle">
        <span class="relation-label">所属 Session</span>
        <strong>{{ activeCompareRelation.sessionTitle }}</strong>
      </div>
      <div class="relation-chip">
        <span class="relation-label">Judge</span>
        <strong>{{ activeCompareRelation.judgeCount }} 条</strong>
      </div>
      <div class="relation-chip is-warning" v-if="activeCompareRelation.staleReason">
        <span class="relation-label">状态</span>
        <strong>旧结果</strong>
      </div>
    </div>

    <details v-if="recentTrials.length" class="detail-card detail-card--compact mb-4">
      <summary>历史 Compare 入口（{{ recentTrials.length }}）</summary>
      <div class="detail-content">
        <p class="block-hint mb-3">历史回看优先使用 Sessions。这里只保留当前节点下最近的 Compare Trial 快速入口。</p>
        <div class="request-list">
          <button
            v-for="trial in recentTrials"
            :key="trial.trial_id"
            class="request-item request-item--interactive request-item--verbose"
            :class="{ active: activeCompareTrial?.trial_id === trial.trial_id }"
            :aria-label="'打开历史 Compare: ' + trialReadableTitle(trial)"
            @click="openCompareTrialInWorkbench(trial.trial_id, { source: trial.session_id ? 'session' : 'recent', switchWorkspace: false, openJudge: false })"
          >
            <div class="request-main">
              <span class="request-id">{{ trialReadableTitle(trial) }}</span>
              <span class="request-meta">{{ trialReadableMeta(trial) }}</span>
              <span v-if="trial.display_excerpt" class="request-submeta">「{{ trial.display_excerpt }}」</span>
            </div>
            <div class="request-side">
              <span class="badge badge-sm" :class="trial.session_id ? 'badge-active' : 'badge-neutral'">{{ compareTrialSourceLabel(trial) }}</span>
            </div>
          </button>
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
.return-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 8%, var(--color-surface));
  border: 1px solid color-mix(in srgb, var(--theme--warning, #f59e0b) 30%, var(--color-border));
  margin-bottom: 16px;
  font-size: 13px;
  color: var(--color-text);
}

.compare-overview {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

/* Pairwise Verdict Panel */
.pairwise-verdict-panel {
  padding: 24px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}

.pairwise-verdict-header {
  margin-bottom: 12px;
}

.pairwise-title {
  font-size: 18px;
  font-weight: 700;
  margin: 0;
}

.pairwise-verdict-summary {
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-text);
  margin-bottom: 16px;
}

.compare-split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.compare-pane {
  background: var(--color-surface-subdued);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 16px;
}

.pane-header {
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-border);
}

.pane-header h4 {
  font-size: 13px;
  font-weight: 600;
  margin: 0;
}

.insight-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.insight-list li {
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-subdued);
}

.insight-list strong {
  color: var(--color-text);
}

.fade-in {
  animation: fade-in 0.2s ease-out forwards;
}

@keyframes fade-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.compare-status-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 18px 18px 16px;
  background: var(--color-surface);
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
}

.compare-status-card__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 18px;
}

.status-fact {
  display: grid;
  gap: 4px;
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
.delta-inline.text-success { background: color-mix(in srgb, var(--theme--success, #10b981) 10%, var(--color-surface)); }
.delta-inline.text-warning { background: color-mix(in srgb, var(--theme--warning, #f59e0b) 10%, var(--color-surface)); }
.delta-inline.text-danger { background: color-mix(in srgb, var(--theme--danger, #dc2626) 10%, var(--color-surface)); }

.text-success { color: var(--theme--success, #10b981); }
.text-warning { color: var(--theme--warning, #f59e0b); }
.text-danger { color: var(--theme--danger, #dc2626); }
.text-attention { color: var(--theme--warning, #f59e0b); }
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

.badge-sm { padding: 1px 6px; font-size: 11px; }
.badge-active { background: color-mix(in srgb, var(--color-primary) 10%, var(--color-surface)); border-color: var(--color-primary); color: var(--color-primary); }
.badge-neutral { color: var(--color-text-subdued); }
.badge-success { border-color: color-mix(in srgb, var(--theme--success, #10b981) 45%, var(--color-border)); color: var(--theme--success, #10b981); }
.badge-warning { border-color: color-mix(in srgb, #d97706 45%, var(--color-border)); color: #b45309; }
.badge-danger { border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 45%, var(--color-border)); color: var(--theme--danger, #dc2626); }

.meta-label {
  font-size: 13px;
  color: var(--color-text-subdued);
  font-weight: 500;
}

.meta-value {
  font-size: 14px;
  font-weight: 500;
}

.compare-status-line {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
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
  min-width: 120px;
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

.relation-chip.is-success {
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 30%, var(--color-border));
  background: color-mix(in srgb, var(--theme--success, #10b981) 6%, var(--color-surface));
}

.relation-chip.is-active {
  border-color: color-mix(in srgb, var(--color-primary) 30%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 5%, var(--color-surface));
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

.detail-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  overflow: hidden;
}

.detail-card summary {
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  background: var(--color-surface-subdued);
  transition: background-color 0.15s;
}
.detail-card summary:hover {
  background: var(--theme--background-subdued);
}
.detail-card[open] summary {
  background: var(--theme--background-subdued);
}

.detail-card--compact summary {
  font-size: 12px;
}

.detail-content {
  padding: 16px;
  border-top: 1px solid var(--color-border);
}

.block-hint {
  font-size: 12px;
  color: var(--color-text-subdued);
  line-height: 1.5;
  margin: -8px 0 8px;
}

.request-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.request-item {
  display: flex;
  justify-content: space-between;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.request-item--interactive {
  text-align: left;
  width: 100%;
}

.request-item--interactive:hover {
  border-color: color-mix(in srgb, var(--color-primary) 25%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 4%, var(--color-surface));
}

.request-item--interactive.active {
  border-color: color-mix(in srgb, var(--color-primary) 40%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 6%, var(--color-surface));
}

.request-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.request-id {
  font-weight: 500;
  font-size: 14px;
}

.request-meta {
  font-size: 12px;
  color: var(--color-text-subdued);
}

.request-submeta {
  font-size: 12px;
  line-height: 1.55;
  color: var(--color-text);
}

.request-item--verbose .request-main {
  gap: 6px;
}

.mb-3 { margin-bottom: 12px; }
.mb-4 { margin-bottom: 16px; }

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
  .compare-overview,
  .compare-status-line {
    grid-template-columns: 1fr;
  }
  .compare-relation-strip {
    flex-direction: column;
  }
}
</style>
