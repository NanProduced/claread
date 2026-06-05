<script setup>
import { computed, ref } from "vue";
import NodeProbeOutputView from "../../../components/NodeProbeOutputView.vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { useNodeLabApi } from "../composables/useNodeLabApi";
import {
  shortId,
  statusLabel,
  statusTone,
} from "../../../composables/useEvalFormatting";
import {
  formatClockTime,
  sessionCompareCount,
  sessionJudgeCount,
  sessionBaselineLabel,
  trialReadableTitle,
  trialReadableMeta,
  trialJudgeCount,
  nodeLabel,
  readingGoalLabel,
  readingVariantLabel,
  compactFactRows,
  normalizePreviewText,
  buildInputPreview,
} from "../composables/useNodeLabFormatting";
import {
  SESSION_FLOW_STEPS as sessionFlowSteps,
  HELP_TEXT as helpText,
} from "../composables/useNodeLabConstants";

const {
  currentSessions,
  selectedSessionId,
  selectedSessionDetail,
  selectedSessionTrialId,
  selectedSessionTrialDetail,
  state,
  loading,
  compareResult,
  activeCompareView,
  activeCompareTrial,
  currentText,
  currentReadingGoal,
  currentReadingVariant,
  selectedSessionJudgeRequests,
} = useNodeLabState();

const {
  selectSession,
  updateSession,
  loadSessionDetail,
  loadTrialDetail,
  deleteSession,
  deleteTrial,
  openSessionTrialInCompare,
  createSessionAndAddCurrentCompare,
  addCurrentCompareToSession,
  attachCurrentCompareToSession,
  goStartCompareFromEmpty,
  selectedSessionTrialResult,
} = useNodeLabApi();

const compareRequestSnapshot = computed(() => {
  return compareResult.value?.request_snapshot || null;
});

const sessionSummaryFacts = computed(() => {
  const detail = selectedSessionDetail.value;
  if (!detail?.session) return [];
  const aggregate = detail.session.aggregate_summary_json || {};
  return compactFactRows([
    ["Session 状态", statusLabel(detail.session.status)],
    ["目标", detail.session.goal || "未填写"],
    ["Trial 总数", aggregate.trial_count ?? detail.trials.length],
    ["Baseline", detail.session.baseline_snapshot_json?.prompt_profile || "未记录"],
    ["Candidate Registry", Array.isArray(detail.session.candidate_registry_json) ? `${detail.session.candidate_registry_json.length} 个候选配置` : "未记录"],
    ["Judge Requests", Array.isArray(detail.judge_requests) ? `${detail.judge_requests.length} 条` : "0 条"],
  ]);
});

const sessionNotebookFacts = computed(() => {
  const detail = selectedSessionDetail.value;
  if (!detail?.session) return [];
  const aggregate = detail.session.aggregate_summary_json || {};
  const compareCount = aggregate.workspace_counts?.baseline_compare ?? detail.trials.length;
  const judgeCount = Array.isArray(detail.judge_requests) ? detail.judge_requests.length : 0;
  return compactFactRows([
    ["Compare 数量", `${compareCount} 条`],
    ["已挂 Judge", judgeCount > 0 ? `${judgeCount} 条` : "尚未发起"],
    ["Session 状态", statusLabel(detail.session.status)],
    ["最后更新", formatClockTime(detail.session.date_updated)],
  ]);
});

const sessionProgressBars = computed(() => {
  const detail = selectedSessionDetail.value;
  if (!detail?.session) return [];
  const aggregate = detail.session.aggregate_summary_json || {};
  const trialCount = aggregate.trial_count ?? detail.trials.length;
  const judgeCount = Array.isArray(detail.judge_requests) ? detail.judge_requests.length : 0;
  const bars = [];
  if (trialCount > 0) {
    bars.push({ label: 'Compare 实验', count: trialCount, max: Math.max(trialCount, 5), color: 'primary' });
  }
  if (judgeCount > 0) {
    bars.push({ label: 'Judge 评审', count: judgeCount, max: Math.max(judgeCount, 5), color: 'success' });
  }
  return bars;
});

const sessionMetaLabel = computed(() => (
  state.activeWorkspace === "sessions"
    ? "当前浏览的 Session"
    : "当前 Compare 将写入的 Session"
));

const currentSessionSummary = computed(() => {
  const session = selectedSessionDetail.value?.session;
  if (state.activeWorkspace === "sessions") {
    if (!session) {
      return {
        title: "未打开 Session",
        detail: "Session 只负责 notebook / 历史复盘。请先从左侧选择一本 compare 记录本。",
        tone: "neutral",
      };
    }
    return {
      title: session.title || `Session ${shortId(session.session_id)}`,
      detail: `固定上下文：${nodeLabel(session.node_name)} · ${readingGoalLabel(session.baseline_snapshot_json?.reading_goal)} · ${readingVariantLabel(session.baseline_snapshot_json?.reading_variant)}`,
      tone: statusTone(session.status),
    };
  }

  if (!session) {
    return {
      title: "未指定",
      detail: "当前 Compare 尚未指定写入目标。Session 只在你点击\u201C加入 Session\u201D时才会接收这条结果。",
      tone: "neutral",
    };
  }
  return {
    title: session.title || `Session ${shortId(session.session_id)}`,
    detail: selectedSessionContextMatch.value
      ? `${selectedSessionContextMatch.value}。如需写入，请先切回匹配的 compare。`
      : `当前 Compare 可写入：${nodeLabel(session.node_name)} · ${readingGoalLabel(session.baseline_snapshot_json?.reading_goal)} · ${readingVariantLabel(session.baseline_snapshot_json?.reading_variant)}`,
    tone: selectedSessionContextMatch.value ? "warning" : statusTone(session.status),
  };
});

const sessionAttachmentState = computed(() => {
  const session = selectedSessionDetail.value?.session;
  if (!selectedSessionId.value || !session) {
    return {
      attached: false,
      title: "未选择 Session",
      detail: "Session 是固定上下文的 compare 记录本。只有你显式加入时，当前 compare 才会被收进 notebook。",
      status: "未选择",
      tone: "neutral",
      actionLabel: "前往 Sessions",
    };
  }
  return {
    attached: true,
    title: session.title || `Session ${shortId(session.session_id)}`,
    detail: "这是当前选中的 notebook。后续 compare 只有在你显式点击\u201C加入 Session\u201D时才会写入。",
    status: statusLabel(session.status),
    tone: statusTone(session.status),
    actionLabel: "查看 Session",
  };
});

const sessionDecisionNarrative = computed(() => {
  const detail = selectedSessionDetail.value;
  if (!detail?.session) {
    return "请先在 Baseline Compare 跑出第一条 compare，再选择加入或新建 Session。";
  }
  const aggregate = detail.session.aggregate_summary_json || {};
  if (aggregate.decision_summary) return String(aggregate.decision_summary);
  if (detail.trials.length === 0) {
    return "这个 Session 还没有 compare。请回到 Baseline Compare 跑出第一条结果并选择\u201C新建 Session 并加入\u201D。";
  }
  return `当前已记录 ${detail.trials.length} 条 compare，可以先看左侧时间线，再挑一条展开结构化差异与所挂 judge 结果。`;
});

const sessionPersistActionLabel = computed(() => {
  if (selectedSessionDetail.value?.session?.title) {
    return `加入当前 Session：${selectedSessionDetail.value.session.title}`;
  }
  return "新建 Session 并加入";
});

const canAttachCurrentCompare = computed(() => {
  if (state.activeWorkspace !== "baseline_compare") return false;
  if (!compareResult.value) return false;
  if (!compareRequestSnapshot.value) return false;
  return true;
});

const attachBlockReason = computed(() => {
  if (state.activeWorkspace !== "baseline_compare") {
    return "请先切换到 Baseline Compare";
  }
  if (!compareResult.value) {
    return "请先运行 Compare";
  }
  if (!compareRequestSnapshot.value) {
    return "当前 Compare 结果缺少 request_snapshot。请重新运行 Compare 以获得完整快照。";
  }
  return null;
});

const selectedSessionContextMatch = computed(() => {
  if (!selectedSessionId.value) return null;
  if (!compareRequestSnapshot.value) return null;
  const session = selectedSessionDetail.value?.session;
  if (!session) return null;
  const snapshot = compareRequestSnapshot.value;

  const sessionNode = String(session.node_name || "").trim();
  const snapshotNode = String(snapshot.node_name || "").trim();
  if (sessionNode && snapshotNode && sessionNode !== snapshotNode) {
    return `当前 Session 是 ${nodeLabel(sessionNode)}，与本次 compare 的 ${nodeLabel(snapshotNode)} 不匹配`;
  }

  const sessionBaseline = session.baseline_snapshot_json || {};
  const sessionGoal = String(sessionBaseline.reading_goal || "").trim();
  const sessionVariant = String(sessionBaseline.reading_variant || "").trim();
  const snapshotGoal = String(snapshot.reading_goal || "").trim();
  const snapshotVariant = String(snapshot.reading_variant || "").trim();
  if (sessionGoal && snapshotGoal && sessionGoal !== snapshotGoal) {
    return `当前 Session 阅读目标是 ${readingGoalLabel(sessionGoal)}，与本次 compare 的 ${readingGoalLabel(snapshotGoal)} 不匹配`;
  }
  if (sessionVariant && snapshotVariant && sessionVariant !== snapshotVariant) {
    return `当前 Session 阅读变体是 ${readingVariantLabel(sessionVariant)}，与本次 compare 的 ${readingVariantLabel(snapshotVariant)} 不匹配`;
  }

  const sessionBaselineHash = String(session.baseline_snapshot_hash || "").trim();
  const resultBaselineHash = String(compareResult.value?.baseline?.prompt_identity?.prompt_snapshot_hash || "").trim();
  if (sessionBaselineHash && resultBaselineHash && sessionBaselineHash !== resultBaselineHash) {
    return `当前 Session 的 baseline snapshot 与本次 compare 的 baseline snapshot 不一致`;
  }
  return null;
});

const joinSessionBlockReason = computed(() => {
  if (attachBlockReason.value) return attachBlockReason.value;
  if (!selectedSessionId.value) {
    return "请先选择一条 Session，或点击\u201C新建 Session 并加入\u201D";
  }
  if (selectedSessionContextMatch.value) return selectedSessionContextMatch.value;
  return null;
});

const createSessionAndAddBlockReason = computed(() => {
  if (attachBlockReason.value) return attachBlockReason.value;
  return null;
});

const attachCurrentCompareTooltip = computed(() => {
  if (joinSessionBlockReason.value) return joinSessionBlockReason.value;
  return "把当前 Compare 结果直接加入已选 Session（不会重跑）";
});

const createSessionAndAddTooltip = computed(() => {
  if (createSessionAndAddBlockReason.value) return createSessionAndAddBlockReason.value;
  return "用当前 Compare 结果直接新建 Session 并加入（不会重跑）";
});

function judgeRequestsForTrial(trialId) {
  if (!trialId) return [];
  const list = selectedSessionJudgeRequests.value || [];
  return list.filter((req) => req.trial_id === trialId);
}

function judgeRequestsForTrialCount(trialId) {
  return judgeRequestsForTrial(trialId).length;
}

const pendingDeleteTarget = ref(null); // { type: 'session' | 'trial', id: string, label: string }
const isEditingSessionTitle = ref(false);
const sessionTitleDraft = ref("");
const isTimelineCollapsed = ref(false);

function confirmDelete() {
  if (!pendingDeleteTarget.value) return;
  if (pendingDeleteTarget.value.type === 'session') {
    deleteSession(pendingDeleteTarget.value.id);
  } else {
    deleteTrial(pendingDeleteTarget.value.id);
  }
  pendingDeleteTarget.value = null;
}

function startSessionRename() {
  if (!selectedSessionDetail.value?.session) return;
  sessionTitleDraft.value = selectedSessionDetail.value.session.title || "";
  isEditingSessionTitle.value = true;
}

function cancelSessionRename() {
  isEditingSessionTitle.value = false;
  sessionTitleDraft.value = selectedSessionDetail.value?.session?.title || "";
}

async function saveSessionRename() {
  const session = selectedSessionDetail.value?.session;
  const nextTitle = String(sessionTitleDraft.value || "").trim();
  if (!session) return;
  if (!nextTitle) return;
  if (nextTitle === session.title) {
    cancelSessionRename();
    return;
  }
  const updated = await updateSession(session.session_id, { title: nextTitle });
  if (updated?.session_id) {
    isEditingSessionTitle.value = false;
  }
}

function formatExcerpt(text) {
  if (!text) return "";
  if (text.startsWith("[attached compare") || text.includes("hash=")) {
    return "已关联 Compare 结果 (无明文摘要)";
  }
  return text;
}
</script>

<template>
  <div class="sessions-workspace" :class="{ 'in-session-detail': !!selectedSessionId }">
    <!-- Level 1: Sessions Directory -->
    <template v-if="!selectedSessionId">
      <div class="directory-view">
        <div class="directory-header mb-4">
          <div class="header-title-row">
            <h2 class="directory-title">Compare 实验记录本 (Sessions)</h2>
            <button class="btn-link inline-hint" @click="goStartCompareFromEmpty">没有 Session？先去跑一条 compare</button>
          </div>
          <p class="directory-desc">
            每个 Session 固定特定的分析节点、阅读目标、阅读变体与 baseline 参考系。在此可统一查阅、管理并对比所有的实验运行结果。
          </p>
        </div>

        <div v-if="currentSessions.length" class="session-table-wrapper">
          <table class="session-table">
            <thead>
              <tr>
                <th>记录本名称</th>
                <th>分析节点</th>
                <th>固定阅读上下文 (目标 / 变体)</th>
                <th>参考参考系 (Baseline)</th>
                <th>Compare 实验</th>
                <th>状态</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              <tr 
                v-for="item in currentSessions" 
                :key="item.session_id" 
                @click="selectSession(item.session_id)"
                class="session-table-row"
              >
                <td class="cell-title">{{ item.title }}</td>
                <td class="cell-node">
                  <span class="ctx-chip-sm">{{ nodeLabel(item.node_name) }}</span>
                </td>
                <td class="cell-context">
                  <span class="ctx-chip-sm">{{ readingGoalLabel(item.baseline_snapshot_json?.reading_goal) }}</span>
                  <span class="dot-separator">·</span>
                  <span class="ctx-chip-sm">{{ readingVariantLabel(item.baseline_snapshot_json?.reading_variant) }}</span>
                </td>
                <td class="cell-baseline text-muted">
                  {{ item.baseline_snapshot_json?.prompt_profile || "未记录" }}
                </td>
                <td class="cell-count font-bold">
                  {{ sessionCompareCount(item) }} 条 compare
                  <span v-if="sessionJudgeCount(item, state.sessionDetailsById) > 0" class="judge-badge-sub">
                    / {{ sessionJudgeCount(item, state.sessionDetailsById) }} judge
                  </span>
                </td>
                <td class="cell-status">
                  <span class="badge" :class="`badge-${statusTone(item.status)}`">
                    {{ statusLabel(item.status) }}
                  </span>
                </td>
                <td class="cell-time text-muted">
                  {{ formatClockTime(item.date_updated) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-else-if="!loading.sessions" class="empty-state">
          <p>暂无实验记录本</p>
          <span class="empty-hint">请在 Baseline Compare 跑出第一条 compare 并选择“新建 Session 并加入”。</span>
        </div>

        <div v-if="loading.sessions" class="session-loading">
          <div class="loading-spinner-sm"></div>
          <span>正在加载 Session 列表...</span>
        </div>
      </div>
    </template>

    <!-- Level 2: Active Session Workspace -->
    <template v-else>
      <div class="sessions-main full-width">
        <!-- Back Navigation Breadcrumb -->
        <div class="session-back-nav mb-4">
          <v-button secondary small @click="selectedSessionId = ''">
            ← 返回所有记录本
          </v-button>
        </div>

        <template v-if="selectedSessionDetail?.session">
          <div class="session-hero">
            <div class="hero-main">
              <div class="session-title-row">
                <template v-if="isEditingSessionTitle">
                  <v-input v-model="sessionTitleDraft" class="session-title-input" />
                  <div class="session-title-actions">
                    <v-button small :loading="loading.sessions" @click="saveSessionRename">保存</v-button>
                    <v-button small secondary @click="cancelSessionRename">取消</v-button>
                  </div>
                </template>
                <template v-else>
                  <h2 class="session-title">{{ selectedSessionDetail.session.title }}</h2>
                  <v-button small secondary @click="startSessionRename">重命名</v-button>
                </template>
              </div>
              <span class="badge" :class="`badge-${statusTone(selectedSessionDetail.session.status)}`">{{ statusLabel(selectedSessionDetail.session.status) }}</span>
            </div>
            <p class="session-desc">{{ sessionDecisionNarrative }}</p>
            <div class="action-buttons mt-3">
              <v-button secondary @click="state.activeWorkspace = 'baseline_compare'">返回 Baseline Compare</v-button>
              <v-button class="btn-danger-text" outlined @click="pendingDeleteTarget = { type: 'session', id: selectedSessionDetail.session.session_id, label: selectedSessionDetail.session.title }">删除整个 Session</v-button>
            </div>
            <div v-if="pendingDeleteTarget" class="confirm-banner is-danger" role="alert">
              <p>确认删除{{ pendingDeleteTarget.type === 'session' ? '整个 Session' : '这条 Compare' }}「{{ pendingDeleteTarget.label }}」？此操作不可撤销。</p>
              <div class="confirm-actions">
                <v-button small danger @click="confirmDelete">确认删除</v-button>
                <v-button small secondary @click="pendingDeleteTarget = null">取消</v-button>
              </div>
            </div>

            <div class="notebook-context mt-3">
              <span class="badge badge-locked">固定上下文</span>
              <span class="ctx-chip">Node：{{ nodeLabel(selectedSessionDetail.session.node_name) }}</span>
              <span class="ctx-chip">阅读目标：{{ readingGoalLabel(selectedSessionDetail.session.baseline_snapshot_json?.reading_goal) }}</span>
              <span class="ctx-chip">阅读变体：{{ readingVariantLabel(selectedSessionDetail.session.baseline_snapshot_json?.reading_variant) }}</span>
              <span class="ctx-chip">Baseline：{{ selectedSessionDetail.session.baseline_snapshot_json?.prompt_profile || "未记录" }}</span>
            </div>

            <div class="meta-row mt-3">
              <div class="meta-badge" v-for="[label, value] in sessionNotebookFacts" :key="label">
                <span class="label">{{ label }}</span>
                <span class="value">{{ value }}</span>
              </div>
            </div>

            <div v-if="sessionProgressBars.length" class="progress-bars mt-3">
              <div v-for="bar in sessionProgressBars" :key="bar.label" class="progress-bar-item">
                <span class="progress-label">{{ bar.label }}</span>
                <div class="progress-track">
                  <div class="progress-fill" :class="`is-${bar.color}`" :style="{ width: `${Math.min(100, (bar.count / bar.max) * 100)}%` }"></div>
                </div>
                <span class="progress-count">{{ bar.count }}</span>
              </div>
            </div>
          </div>

          <div class="timeline-container" :class="{ 'is-collapsed': isTimelineCollapsed }">
            <div v-if="!isTimelineCollapsed" class="timeline-sidebar">
              <div class="timeline-sidebar-header mb-3">
                <h4 class="block-title">Compare 时间线</h4>
                <v-button class="btn-collapse" icon secondary small @click="isTimelineCollapsed = true" title="收起时间线">
                  ←
                </v-button>
              </div>
              <p class="block-hint">这里是这本 notebook 的历史入口。点一条 compare，再决定回到 Baseline Compare 查看或重新 Judge。</p>
              <div v-if="selectedSessionDetail.trials.length" class="timeline-list">
                <button
                  v-for="(trial, index) in selectedSessionDetail.trials"
                  :key="trial.trial_id"
                  class="timeline-item"
                  :class="{ active: selectedSessionTrialId === trial.trial_id }"
                  @click="loadTrialDetail(trial.trial_id, selectedSessionId)"
                >
                  <div class="item-header">
                    <span class="item-idx">#{{ index + 1 }}</span>
                    <span class="item-type">Compare</span>
                    <span class="item-id">{{ shortId(trial.trial_id) }}</span>
                  </div>
                  <div class="item-status">
                    <span class="badge badge-sm" :class="`badge-${statusTone(trial.status)}`">{{ statusLabel(trial.status) }}</span>
                    <span class="badge badge-sm badge-neutral">{{ statusLabel(trial.result_summary_json?.result_status?.compare_status) }}</span>
                    <span v-if="judgeRequestsForTrialCount(trial.trial_id) > 0" class="badge badge-sm badge-active">
                      {{ judgeRequestsForTrialCount(trial.trial_id) }} judge
                    </span>
                  </div>
                  <p v-if="trial.display_excerpt || trial.input_excerpt" class="item-excerpt">「{{ formatExcerpt(trial.display_excerpt || trial.input_excerpt) }}」</p>
                </button>
              </div>
              <div v-else class="empty-state compact">
                <p>暂无 compare</p>
                <span class="empty-hint">回到 Baseline Compare 跑出第一条结果并选择"加入 Session"。</span>
              </div>
            </div>

            <div class="timeline-detail">
              <h4 class="block-title mb-3">
                <v-button v-if="isTimelineCollapsed" class="btn-expand mr-2" icon secondary small @click="isTimelineCollapsed = false" title="展开时间线">
                  →
                </v-button>
                Compare 详情
                <span v-if="selectedSessionTrialId" class="text-muted font-normal text-sm ml-2">#{{ shortId(selectedSessionTrialId) }}</span>
              </h4>

              <template v-if="selectedSessionTrialDetail?.trial">
                <div class="action-buttons mb-4">
                  <v-button secondary @click="openSessionTrialInCompare(selectedSessionTrialDetail.trial.trial_id)">在 Baseline Compare 中打开</v-button>
                  <v-button @click="openSessionTrialInCompare(selectedSessionTrialDetail.trial.trial_id, { openJudge: true })">重新 Judge</v-button>
                  <v-button class="btn-danger-text" outlined @click="pendingDeleteTarget = { type: 'trial', id: selectedSessionTrialDetail.trial.trial_id, label: shortId(selectedSessionTrialDetail.trial.trial_id) }">删除这条 compare</v-button>
                </div>

                <div class="meta-grid mb-4">
                  <div class="meta-item">
                    <span class="meta-label">Compare 状态</span>
                    <span class="meta-value">{{ statusLabel(selectedSessionTrialDetail.trial.result_summary_json?.result_status?.compare_status) }}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-label">Baseline 状态</span>
                    <span class="meta-value">{{ statusLabel(selectedSessionTrialDetail.trial.result_summary_json?.result_status?.baseline_status) }}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-label">Candidate 状态</span>
                    <span class="meta-value">{{ statusLabel(selectedSessionTrialDetail.trial.result_summary_json?.result_status?.candidate_status) }}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-label">执行耗时</span>
                    <span class="meta-value">
                      <span v-if="selectedSessionTrialDetail.trial.started_at && selectedSessionTrialDetail.trial.finished_at">
                        {{ formatClockTime(selectedSessionTrialDetail.trial.started_at) }} – {{ formatClockTime(selectedSessionTrialDetail.trial.finished_at) }}
                      </span>
                      <span v-else>未记录</span>
                    </span>
                  </div>
                </div>

                <div v-if="selectedSessionTrialDetail.trial.input_excerpt" class="input-excerpt mb-3">
                  <span class="meta-label">输入摘要</span>
                  <p>{{ formatExcerpt(selectedSessionTrialDetail.trial.input_excerpt) }}</p>
                </div>

                <template v-if="selectedSessionTrialResult()?.baseline && selectedSessionTrialResult()?.candidate">
                  <div class="compare-split">
                    <div class="compare-pane">
                      <div class="pane-header"><h4>Baseline</h4></div>
                      <NodeProbeOutputView
                        :node-name="state.activeNode"
                        :output="selectedSessionTrialResult().baseline?.node_output || null"
                        :prepared-sentences="selectedSessionTrialResult().baseline?.prepared_sentences || []"
                        :quick-validation="selectedSessionTrialResult().baseline?.quick_validation || null"
                        empty-text="尚无输出。"
                      />
                    </div>
                    <div class="compare-pane">
                      <div class="pane-header"><h4>Candidate</h4></div>
                      <NodeProbeOutputView
                        :node-name="state.activeNode"
                        :output="selectedSessionTrialResult().candidate?.node_output || null"
                        :prepared-sentences="selectedSessionTrialResult().candidate?.prepared_sentences || []"
                        :quick-validation="selectedSessionTrialResult().candidate?.quick_validation || null"
                        empty-text="尚无输出。"
                      />
                    </div>
                  </div>
                </template>

                <div v-if="judgeRequestsForTrial(selectedSessionTrialId).length" class="mt-4">
                  <h5 class="block-title">所挂 Judge 结果</h5>
                  <div class="judge-tile-list mt-2">
                    <button
                      v-for="req in judgeRequestsForTrial(selectedSessionTrialId)"
                      :key="req.judge_request_id"
                      class="judge-tile judge-tile--interactive"
                      @click="openSessionTrialInCompare(selectedSessionTrialId, { openJudge: true, judgeRequestId: req.judge_request_id })"
                    >
                      <div class="judge-tile-head">
                        <span class="judge-id">{{ req.judge_request_id }}</span>
                        <span class="badge badge-sm" :class="`badge-${statusTone(req.status)}`">{{ statusLabel(req.status) }}</span>
                      </div>
                      <p class="text-sm text-muted">{{ req.notes || "暂无备注" }}</p>
                    </button>
                  </div>
                </div>
              </template>
              <div v-else class="empty-state">
                <p>请在左侧选择一条 compare 以查看详情。</p>
              </div>
            </div>
          </div>
        </template>
        <template v-else-if="loading.sessions">
          <div class="session-loading">
            <div class="loading-spinner-sm"></div>
            <span>正在加载 Session 详情...</span>
          </div>
        </template>
        <div v-else class="empty-state">
          <p>未找到该 Session，或者加载失败。</p>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sessions-workspace {
  width: 100%;
  font-family: var(--theme--fonts--sans--font-family, -apple-system, BlinkMacSystemFont, sans-serif);
}

/* Level 1: Sessions Directory */
.directory-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.directory-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.header-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.directory-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--theme--foreground, #172940);
  margin: 0;
}
.directory-desc {
  font-size: 13px;
  color: var(--theme--foreground-subdued, #6B7280);
  line-height: 1.6;
  margin: 0;
}
.session-table-wrapper {
  background: var(--theme--background, #ffffff);
  border: 1px solid var(--theme--border-color, #D9DEE7);
  border-radius: 8px;
  overflow: hidden;
}
.session-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}
.session-table th {
  background: var(--theme--background-subdued, #FAFBFC);
  color: var(--theme--foreground-subdued, #6B7280);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 12px 16px;
  border-bottom: 1px solid var(--theme--border-color, #E3E7EE);
}
.session-table td {
  padding: 14px 16px;
  border-bottom: 1px solid var(--theme--border-color, #E3E7EE);
  font-size: 13px;
  color: var(--theme--foreground, #172940);
  vertical-align: middle;
}
.session-table-row {
  cursor: pointer;
  transition: background 0.15s ease;
}
.session-table-row:hover {
  background: var(--theme--background-subdued, #FAFBFC);
}
.session-table-row:last-child td {
  border-bottom: none;
}
.cell-title {
  font-weight: 600;
  color: var(--theme--primary, #2563eb) !important;
}
.ctx-chip-sm {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--theme--background-subdued, #FAFBFC);
  border: 1px solid var(--theme--border-color, #E3E7EE);
  font-size: 11px;
  color: var(--theme--foreground-subdued, #6B7280);
  font-weight: 500;
}
.judge-badge-sub {
  font-size: 11px;
  color: var(--theme--success, #11795B);
  font-weight: 600;
}

/* Level 2: Detail Workspace */
.sessions-main {
  background: var(--theme--background, #ffffff);
  border: 1px solid var(--theme--border-color, #D9DEE7);
  border-radius: 8px;
  padding: 24px;
}
.sessions-main.full-width {
  width: 100%;
}
.session-back-nav {
  display: flex;
  align-items: center;
}

.session-hero { margin-bottom: 24px; padding-bottom: 20px; border-bottom: 1px solid var(--theme--border-color); }
.hero-main { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.session-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}
.session-title { font-size: 20px; font-weight: 600; color: var(--theme--foreground); }
.session-title-input {
  min-width: min(420px, 100%);
  flex: 1 1 320px;
}
.session-title-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.session-desc { font-size: 13px; color: var(--theme--foreground-subdued); line-height: 1.6; }

.block-hint {
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  line-height: 1.55;
  margin: -8px 0 8px;
}

.notebook-context {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
}

.badge {
  display: inline-flex;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 4px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
}
.badge-sm { padding: 1px 6px; font-size: 10px; }
.badge-active { background: color-mix(in srgb, var(--theme--primary, #2563eb) 10%, var(--theme--background)); border-color: var(--theme--primary, #2563eb); color: var(--theme--primary, #2563eb); }
.badge-locked { background: color-mix(in srgb, var(--theme--primary, #2563eb) 10%, var(--theme--background)); border-color: color-mix(in srgb, var(--theme--primary, #2563eb) 35%, var(--theme--border-color)); color: var(--theme--primary, #2563eb); font-weight: 600; }
.badge-success { border-color: color-mix(in srgb, var(--theme--success, #10b981) 45%, var(--theme--border-color)); color: var(--theme--success, #10b981); }
.badge-warning { border-color: color-mix(in srgb, #d97706 45%, var(--theme--border-color)); color: #b45309; }
.badge-danger { border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 45%, var(--theme--border-color)); color: var(--theme--danger, #dc2626); }
.badge-neutral { color: var(--theme--foreground-subdued); }

.ctx-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  font-weight: 500;
}

.input-excerpt {
  padding: 12px 16px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
}
.input-excerpt p {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--theme--foreground);
}

.dot-separator { margin: 0 4px; color: var(--theme--border-color, #E3E7EE); }

.meta-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-top: 16px;
}
.meta-badge {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border-radius: 6px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
  transition: all 0.2s ease;
}
.meta-badge:hover {
  border-color: var(--theme--primary-subdued);
}
.meta-badge .label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--theme--foreground-subdued); }
.meta-badge .value { font-size: 15px; font-weight: 700; color: var(--theme--foreground); }

.progress-bars {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin-top: 16px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 16px;
}
.progress-bar-item {
  display: flex;
  align-items: center;
  gap: 12px;
}
.progress-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--theme--foreground-subdued);
  min-width: 80px;
}
.progress-track {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: var(--theme--background);
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  border-radius: 3px;
  transition: transform 0.3s ease;
  transform-origin: left;
}
.progress-fill.is-primary { background: var(--theme--primary, #2563eb); }
.progress-fill.is-success { background: var(--theme--success, #10b981); }
.progress-count {
  font-size: 12px;
  font-weight: 700;
  min-width: 24px;
  text-align: right;
  color: var(--theme--foreground);
}

.timeline-container {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 28px;
  transition: grid-template-columns 0.2s ease;
}
.timeline-container.is-collapsed {
  grid-template-columns: 1fr;
}

.timeline-sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.btn-collapse {
  min-width: 24px !important;
  height: 24px !important;
  padding: 0 !important;
}
.btn-expand {
  min-width: 24px !important;
  height: 24px !important;
  padding: 0 !important;
  display: inline-flex !important;
  align-items: center;
  justify-content: center;
}

.timeline-list { display: flex; flex-direction: column; gap: 10px; }
.timeline-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border: 1px solid var(--theme--border-color);
  border-left: 3px solid transparent;
  border-radius: 6px;
  background: var(--theme--background);
  text-align: left;
  transition: all 0.2s ease;
}
.timeline-item:hover { border-color: var(--theme--primary-subdued); background: var(--theme--background-subdued); }
.timeline-item.active {
  border-color: var(--theme--primary);
  border-left-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
}
.item-header { display: flex; justify-content: space-between; align-items: center; }
.item-type { font-size: 12px; font-weight: 600; color: var(--theme--foreground); }
.item-id { font-size: 11px; color: var(--theme--foreground-subdued); float: right; }
.item-status { display: flex; gap: 6px; margin-top: 4px; }

.item-idx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 18px;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--theme--primary, #2563eb);
  color: #ffffff;
  font-size: 10px;
  font-weight: 700;
}

.item-excerpt {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--theme--foreground-subdued);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.judge-tile-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.judge-tile {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px 12px;
  background: var(--theme--background-subdued);
}

.judge-tile--interactive {
  width: 100%;
  text-align: left;
}

.judge-tile--interactive:hover {
  border-color: var(--theme--primary-subdued);
  background: color-mix(in srgb, var(--theme--primary, #2563eb) 4%, var(--theme--background));
}

.judge-tile-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.judge-id {
  font-size: 11px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.compare-split { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.compare-pane { border: 1px solid var(--theme--border-color); border-radius: 6px; padding: 14px; background: var(--theme--background); }
.pane-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.pane-header h4 { font-size: 13px; font-weight: 600; color: var(--theme--foreground); }

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 12px;
  padding: 12px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  margin-bottom: 16px;
}
.meta-grid .meta-item { display: flex; flex-direction: column; gap: 4px; }
.meta-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--theme--foreground-subdued); }
.meta-value { font-size: 13px; font-weight: 600; color: var(--theme--foreground); }

.action-buttons { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

.btn-link { color: var(--theme--primary, #2563eb); font-size: 12px; font-weight: 600; padding: 2px 0; border: none; background: transparent; cursor: pointer; }
.btn-link:hover { text-decoration: underline; }
.btn-link.inline-hint { display: inline; padding: 0; margin: 0; font-size: 12px; color: var(--theme--primary, #2563eb); }
.btn-danger-text { color: var(--theme--danger, #dc2626); font-size: 12px; padding: 4px 8px; }

.confirm-banner {
  padding: 12px 14px;
  border-radius: 6px;
  border: 1px solid;
  margin-top: 12px;
}
.confirm-banner.is-danger {
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 8%, var(--theme--background));
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 30%, var(--theme--border-color));
}
.confirm-banner p {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--theme--foreground);
}
.confirm-actions {
  display: flex;
  gap: 8px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  text-align: center;
  border: 1px dashed var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
}
.empty-state.compact { padding: 16px 12px; }
.empty-hint { font-size: 12px; margin-top: 4px; }

.block-title { font-size: 13px; font-weight: 700; color: var(--theme--foreground); margin: 0; }

.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 12px; }
.mt-4 { margin-top: 16px; }
.mb-3 { margin-bottom: 12px; }
.mb-4 { margin-bottom: 16px; }
.ml-2 { margin-left: 8px; }
.mr-2 { margin-right: 8px; }
.text-sm { font-size: 12px; }
.text-muted { color: var(--theme--foreground-subdued); }
.font-normal { font-weight: 400; }
.font-bold { font-weight: 600; }
.text-center { text-align: center; }

@media (max-width: 1200px) {
  .timeline-container { grid-template-columns: 1fr; }
}

.session-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.loading-spinner-sm {
  width: 14px;
  height: 14px;
  border: 2px solid var(--theme--border-color);
  border-top-color: var(--theme--primary, #2563eb);
  border-radius: 50%;
  animation: node-lab-spin 0.8s linear infinite;
}

@keyframes node-lab-spin {
  to { transform: rotate(360deg); }
}
</style>
