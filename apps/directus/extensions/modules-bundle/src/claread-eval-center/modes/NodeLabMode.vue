<script setup>
import { computed, onBeforeUnmount, onMounted, provide, watch } from "vue";
import BaselineReference from "./node-lab/components/BaselineReference.vue";
import CandidateEditor from "./node-lab/components/CandidateEditor.vue";
import SingleRunResult from "./node-lab/components/SingleRunResult.vue";
import CompareVerdictBar from "./node-lab/components/CompareVerdictBar.vue";
import CompareCanvas from "./node-lab/components/CompareCanvas.vue";
import EvidencePanels from "./node-lab/components/EvidencePanels.vue";
import JudgeConfigPanel from "./node-lab/components/JudgeConfigPanel.vue";
import SessionsWorkspace from "./node-lab/components/SessionsWorkspace.vue";
import { createNodeLabState, NODE_LAB_STATE_KEY } from "./node-lab/composables/useNodeLabState";
import { createNodeLabApi, NODE_LAB_API_KEY } from "./node-lab/composables/useNodeLabApi";
import {
  NODE_OPTIONS as nodeOptions,
  WORKSPACE_OPTIONS as workspaceOptions,
  READING_GOAL_OPTIONS as readingGoalOptions,
  HELP_TEXT as helpText,
} from "./node-lab/composables/useNodeLabConstants";
import {
  shortId,
  statusTone,
} from "../composables/useEvalFormatting";
import {
  nodeLabel,
  workspaceLabel,
  readingGoalLabel,
  readingVariantLabel,
  normalizePreviewText,
  buildInputPreview,
  safeJsonParse,
  formatClockTime,
  hasNodeActivity,
  compareViewSourceLabel,
  compareViewSourceTone,
  trialJudgeCount,
  trialReadableTitle,
  trialReadableMeta,
  defaultJudgeModeForNode,
  judgeModeAllowedForNode,
  normalizeVariantForGoal,
} from "./node-lab/composables/useNodeLabFormatting";

const nodeLabState = createNodeLabState();
provide(NODE_LAB_STATE_KEY, nodeLabState);

const nodeLabApi = createNodeLabApi(nodeLabState);
provide(NODE_LAB_API_KEY, nodeLabApi);

const {
  state, loading, feedback,
  currentText, currentReadingGoal, currentReadingVariant,
  comparePanelTab,
  selectedSessionId,
  selectedJudgeRequestId, selectedJudgeRequestDetail,
  currentDraft, currentJudgeDraft,
  currentJudgeRequests,
  singleRunResult, singleRunUiState,
  compareResult, compareUiState,
  activeCompareView, activeCompareTrial,
  recentTrials, selectedSessionDetail,
  currentCompareTrialId,
  availableReadingVariants,
  setFeedback,
  loadPersistedState, persistedStatePayload, persistState,
} = nodeLabState;

const {
  loadModelProfiles, loadBaselineConfig, loadCandidates,
  loadJudgeConfigs, loadJudgePresets, loadJudgeRequests,
  loadSessions, loadRecentTrials,
  openCompareTrialInWorkbench, runSingle, runCompare,
  addCurrentCompareToSession,
  createSessionAndAddCurrentCompare,
  loadJudgeRequestDetail,
  stopJudgeRequestPolling,
  clearSessionAttachment,
} = nodeLabApi;

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

const activeCompareJudgeRequests = computed(() => {
  if (!activeCompareTrial.value?.trial_id) return [];
  return currentJudgeRequests.value || [];
});

const activeCompareRelation = computed(() => {
  if (!compareResult.value) return null;
  const trial = activeCompareTrial.value || null;
  const staleReason = compareSnapshotContextMismatchReason.value;
  const sourceLabel = compareViewSourceLabel(activeCompareView.value, trial);
  const sourceTone = compareViewSourceTone(activeCompareView.value, trial, staleReason);
  const sessionTitle = trial?.session_title
    || (trial?.session_id ? `Session ${shortId(trial.session_id)}` : "");
  const judgeCount = trialJudgeCount(trial) || activeCompareJudgeRequests.value.length || 0;
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

const currentDisplayedCompareSummary = computed(() => {
  const relation = activeCompareRelation.value;
  if (!relation) {
    return {
      title: "未打开 Compare",
      detail: "请先运行 Compare，或从 Sessions 打开一条历史结果。",
      tone: "neutral",
    };
  }
  if (relation.staleReason) {
    return {
      title: "正在查看旧 Compare",
      detail: relation.staleReason,
      tone: "warning",
    };
  }
  if (relation.isPersisted) {
    return {
      title: `${relation.sourceLabel} · ${relation.compareId}`,
      detail: relation.sessionTitle
        ? `已挂到 ${relation.sessionTitle}，当前关联 ${relation.judgeCount} 条 Judge。`
        : `已持久化为独立 Trial，当前关联 ${relation.judgeCount} 条 Judge。`,
      tone: "success",
    };
  }
  return {
    title: "当前 Compare（未持久化）",
    detail: "这是刚跑完的 live compare。你可以直接加入 Session，或创建并执行 Judge。",
    tone: "success",
  };
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

const singleRunRefreshState = computed(() => {
  const uiState = singleRunUiState.value || {};
  const hasResult = Boolean(singleRunResult.value?.run);
  if (loading.run && hasResult) {
    return {
      active: true,
      mode: "refreshing",
      title: "正在刷新本次结果",
      detail: `${uiState.requestLabel || "本次运行"} 已发出请求。当前先保留上一轮结果供参考，完成后会自动替换。`,
    };
  }
  if (loading.run) {
    return {
      active: true,
      mode: "loading",
      title: "正在生成首条结果",
      detail: `${uiState.requestLabel || "本次运行"} 正在执行，结果返回后会显示在右侧。`,
    };
  }
  if (hasResult && uiState.lastCompletedAt) {
    return {
      active: true,
      mode: "updated",
      title: "结果已更新",
      detail: `最近一次完成于 ${formatClockTime(uiState.lastCompletedAt)}。如果内容看起来没有变化，也代表这次运行已经完成。`,
    };
  }
  return {
    active: false,
    mode: "idle",
    title: "",
    detail: "",
  };
});

const compareRefreshState = computed(() => {
  const uiState = compareUiState.value || {};
  const hasResult = Boolean(compareResult.value);
  if (loading.compare && hasResult) {
    return {
      active: true,
      mode: "refreshing",
      title: "正在刷新 Compare 结果",
      detail: `${uiState.requestLabel || "本次 Compare"} 已发出请求。当前先保留上一轮差异供参考，完成后会自动替换。`,
    };
  }
  if (loading.compare) {
    return {
      active: true,
      mode: "loading",
      title: "正在生成首条 Compare 结果",
      detail: `${uiState.requestLabel || "本次 Compare"} 正在执行，结果返回后会显示在右侧。`,
    };
  }
  if (hasResult && uiState.lastCompletedAt) {
    return {
      active: true,
      mode: "updated",
      title: "Compare 结果已更新",
      detail: `最近一次完成于 ${formatClockTime(uiState.lastCompletedAt)}。即使内容变化不大，也代表这次 Compare 已执行完成。`,
    };
  }
  return { active: false, mode: "idle", title: "", detail: "" };
});

const readingGoalOptionsMapped = computed(() => readingGoalOptions.map((g) => ({ text: g.label, value: g.id })));
const availableReadingVariantsMapped = computed(() => (availableReadingVariants.value || []).map((v) => ({ text: v.label, value: v.id })));

watch(persistedStatePayload, persistState, { deep: true });

watch(currentReadingGoal, (goal) => {
  currentReadingVariant.value = normalizeVariantForGoal(goal, currentReadingVariant.value);
});

watch(
  () => state.activeNode,
  (nodeName) => {
    if (!judgeModeAllowedForNode(nodeName, currentJudgeDraft.value.judge_mode)) {
      currentJudgeDraft.value.judge_mode = defaultJudgeModeForNode(nodeName);
    }
  },
  { immediate: true },
);

watch(
  () => state.activeNode,
  async () => {
    stopJudgeRequestPolling();
    const restoredTrialId = state.activeCompareViewByNode[state.activeNode]?.trialId
      || state.currentCompareTrialIdByNode[state.activeNode]
      || "";
    if (restoredTrialId) {
      await openCompareTrialInWorkbench(restoredTrialId, {
        source: state.activeCompareViewByNode[state.activeNode]?.source || "history",
        switchWorkspace: false,
        openJudge: state.comparePanelTabByNode[state.activeNode] === "judge",
      });
    }
  },
);


watch(
  () => [state.activeNode, currentReadingGoal.value, currentReadingVariant.value],
  async () => {
    setFeedback();
    await loadBaselineConfig();
    await Promise.all([loadCandidates(), loadJudgePresets(), loadJudgeConfigs(), loadSessions(), loadRecentTrials()]);
    await loadJudgeRequests({ trialId: activeCompareTrial.value?.trial_id || "" });
  },
  { immediate: false },
);

watch(
  () => currentDraft.value.examples,
  (value) => {
    if (currentDraft.value.examples_edit_mode === "structured") {
      currentDraft.value.examples_raw_text = JSON.stringify(value || [], null, 2);
    }
  },
  { deep: true },
);

watch(
  () => currentDraft.value.examples_edit_mode,
  (mode) => {
    if (mode === "raw") {
      currentDraft.value.examples_raw_text = JSON.stringify(currentDraft.value.examples || [], null, 2);
      return;
    }
    const parsed = safeJsonParse(currentDraft.value.examples_raw_text || "[]", currentDraft.value.examples || []);
    if (Array.isArray(parsed)) {
      currentDraft.value.examples = parsed;
    }
  },
);

onMounted(async () => {
  loadPersistedState();
  await loadModelProfiles();
  await loadBaselineConfig();
  await Promise.all([loadCandidates(), loadJudgePresets(), loadJudgeConfigs(), loadSessions(), loadRecentTrials()]);
  const restoredTrialId = activeCompareView.value?.trialId || currentCompareTrialId.value || "";
  if (restoredTrialId) {
    await openCompareTrialInWorkbench(restoredTrialId, {
      source: activeCompareView.value?.source || "history",
      switchWorkspace: false,
      openJudge: comparePanelTab.value === "judge",
    });
  } else {
    await loadJudgeRequests({ trialId: "" });
  }
  if (selectedJudgeRequestId.value) {
    await loadJudgeRequestDetail(selectedJudgeRequestId.value);
  }
});

onBeforeUnmount(() => {
  stopJudgeRequestPolling();
  const timer = nodeLabState.getPersistTimer();
  if (timer) {
    window.clearTimeout(timer);
    nodeLabState.setPersistTimer(null);
  }
});

</script>


<template>
  <div class="node-lab-container">
    <header class="lab-header">
      <div class="header-main">
        <h2 class="header-title">Node Lab</h2>
        <p class="header-desc">
          当前节点：<strong>{{ nodeLabel(state.activeNode) }}</strong>
          <span class="divider">/</span> 工作区：<strong>{{ workspaceLabel(state.activeWorkspace) }}</strong>
        </p>
      </div>
      <div class="header-meta">
        <div class="meta-item meta-item--primary">
          <span class="meta-label">当前 Compare</span>
          <span class="meta-value" :class="`text-${currentDisplayedCompareSummary.tone}`">{{ currentDisplayedCompareSummary.title }}</span>
          <span class="meta-hint">{{ currentDisplayedCompareSummary.detail }}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">{{ sessionMetaLabel }}</span>
          <span class="meta-value" :class="`text-${currentSessionSummary.tone}`">{{ currentSessionSummary.title }}</span>
          <div class="meta-actions">
            <button v-if="state.activeWorkspace !== 'sessions' && selectedSessionId" class="btn-link" @click="state.activeWorkspace = 'sessions'">打开 Session</button>
            <button v-if="selectedSessionId" class="btn-link" @click="clearSessionAttachment">清空选择</button>
          </div>
          <span class="meta-hint">{{ currentSessionSummary.detail }}</span>
        </div>
      </div>
    </header>

    <nav class="lab-navigation">
      <div class="node-tabs">
        <button
          v-for="node in nodeOptions"
          :key="node.id"
          class="node-tab-btn"
          :class="{ active: state.activeNode === node.id }"
          @click="state.activeNode = node.id"
        >
          {{ node.label }}
          <span v-if="hasNodeActivity(node.id, state)" class="activity-dot"></span>
        </button>
      </div>
      <div class="workspace-modes-bar">
        <span class="modes-bar-label">工作区模式：</span>
        <div class="segmented-control mode-control">
          <button
            v-for="workspace in workspaceOptions"
            :key="workspace.id"
            class="segment-btn"
            :class="{ active: state.activeWorkspace === workspace.id }"
            @click="state.activeWorkspace = workspace.id"
          >
            {{ workspace.label }}
          </button>
        </div>
      </div>
    </nav>

    <div v-if="feedback.error" class="feedback-banner error">{{ feedback.error }}</div>
    <div v-else-if="feedback.info" class="feedback-banner info">{{ feedback.info }}</div>

    <div
      v-if="state.activeWorkspace !== 'sessions'"
      class="workbench"
      :class="{
        'is-compare': state.activeWorkspace === 'baseline_compare',
      }"
    >
      <!-- Left Column: Inputs & Configuration -->
      <div class="panel-column column-editor">
        <section class="panel-section">
          <div class="section-header">
            <h3 class="section-title">场景与输入</h3>
            <span class="help-icon" :title="helpText.reading_goal">?</span>
          </div>
          <div class="form-row">
            <div class="form-field">
              <span class="field-label">阅读目标</span>
              <v-select v-model="currentReadingGoal" :items="readingGoalOptionsMapped" />
            </div>
            <div class="form-field">
              <span class="field-label">阅读变体 <span class="help-icon inline" :title="helpText.reading_variant">?</span></span>
              <v-select v-model="currentReadingVariant" :items="availableReadingVariantsMapped" />
            </div>
          </div>
          <div class="form-field">
            <span class="field-label">输入文本</span>
            <v-textarea v-model="currentText" :rows="5" />
          </div>
          <p class="section-hint">{{ state.activeNode === "grammar" ? "Grammar 支持 baseline、candidate 与 RAG 观测。" : "当前 node 仅支持 baseline / off / candidate 三种 few-shot 模式。" }}</p>
        </section>

        <div :class="{'compare-editor-grid': state.activeWorkspace === 'baseline_compare'}">
          <BaselineReference />
          <CandidateEditor />
        </div>

        <!-- Execution Actions -->
        <section class="panel-section action-section">
          <template v-if="state.activeWorkspace === 'single_run'">
            <div class="action-header">
              <h3 class="section-title">执行操作</h3>
              <span class="help-icon" :title="helpText.session_write">?</span>
            </div>
            <p class="section-hint mb-3">单次快速试跑，不会进入 Session。需要固定上下文的多轮记录，请前往 Baseline Compare。</p>
            <div class="action-buttons">
              <v-button secondary :disabled="loading.run" @click="runSingle({ dryRun: true, useCandidate: true })">预览 Prompt</v-button>
              <v-button secondary :disabled="loading.run" @click="runSingle({ dryRun: false, useCandidate: false })">运行 Baseline</v-button>
              <v-button :disabled="loading.run" @click="runSingle({ dryRun: false, useCandidate: true })">运行 Candidate</v-button>
            </div>
          </template>
          <template v-else-if="state.activeWorkspace === 'baseline_compare'">
            <div class="action-header">
              <h3 class="section-title">执行对比</h3>
              <span class="help-icon" :title="helpText.compare_status">?</span>
            </div>
            <p class="section-hint mb-3">同时运行 Baseline 和 Candidate 以观察差异。完成后再决定是否把当前结果加入 Session。</p>
            <div class="action-buttons">
              <v-button :disabled="loading.compare" @click="runCompare({ persist: false })">运行 Compare</v-button>
              <v-button
                :disabled="!!joinSessionBlockReason"
                :title="attachCurrentCompareTooltip"
                @click="addCurrentCompareToSession()"
              >
                加入 Session
              </v-button>
              <v-button
                outlined
                :disabled="!!createSessionAndAddBlockReason"
                :title="createSessionAndAddTooltip"
                @click="createSessionAndAddCurrentCompare()"
              >
                新建 Session 并加入
              </v-button>
            </div>
            <p v-if="joinSessionBlockReason || createSessionAndAddBlockReason" class="session-block-hint">
              {{ joinSessionBlockReason || createSessionAndAddBlockReason }}
            </p>
            <p class="block-hint mt-3">
              当前 Session 目标：
              <strong>{{ selectedSessionDetail?.session?.title || "未选择" }}</strong>
              <span v-if="selectedSessionDetail?.session">。只有点击"加入 Session"时，当前 compare 才会写入这本 notebook。</span>
            </p>
          </template>
        </section>
      </div>

      <!-- Right Column: Outputs & Results -->
      <div class="panel-column column-output">
        <section class="result-shell">
          <div class="result-header">
            <h3 class="result-title">
              {{ state.activeWorkspace === 'single_run' ? '执行结果' : '对比摘要' }}
            </h3>
            <span
              v-if="state.activeWorkspace === 'single_run' && singleRunRefreshState.active"
              class="result-status-pill"
              :class="`is-${singleRunRefreshState.mode}`"
            >
              {{ singleRunRefreshState.title }}
            </span>
            <span
              v-else-if="state.activeWorkspace === 'baseline_compare' && compareRefreshState.active"
              class="result-status-pill"
              :class="`is-${compareRefreshState.mode}`"
            >
              {{ compareRefreshState.title }}
            </span>
          </div>

          <template v-if="state.activeWorkspace === 'single_run'">
            <SingleRunResult />
          </template>

          <template v-else-if="state.activeWorkspace === 'baseline_compare'">
            <div
              v-if="compareRefreshState.active"
              class="refresh-banner"
              :class="`is-${compareRefreshState.mode}`"
            >
              <div class="refresh-banner__title">
                <span v-if="compareRefreshState.mode === 'refreshing' || compareRefreshState.mode === 'loading'" class="refresh-spinner" aria-hidden="true"></span>
                <strong>{{ compareRefreshState.title }}</strong>
              </div>
              <p>{{ compareRefreshState.detail }}</p>
            </div>
            <CompareVerdictBar v-if="compareResult" />
            <div
              v-if="compareSnapshotContextMismatchReason"
              class="status-banner is-warning mb-4"
            >
              <strong>当前看到的是旧 Compare</strong>
              <p class="text-sm mt-1">{{ compareSnapshotContextMismatchReason }}。如需评审当前表单，请先重新运行 Compare。</p>
            </div>

            <div v-if="compareResult || activeCompareTrial || selectedJudgeRequestDetail?.request" class="compare-panel-tabs">
              <button
                class="segment-btn"
                :class="{ active: comparePanelTab === 'compare' }"
                @click="comparePanelTab = 'compare'"
              >
                Compare
              </button>
              <button
                class="segment-btn"
                :class="{ active: comparePanelTab === 'judge' }"
                @click="comparePanelTab = 'judge'"
              >
                Judge
                <span v-if="activeCompareJudgeRequests.length" class="badge badge-sm badge-neutral ml-1">{{ activeCompareJudgeRequests.length }}</span>
              </button>
            </div>

            <div v-if="compareResult">
              <template v-if="comparePanelTab === 'compare'">
                <CompareCanvas />
                <EvidencePanels />
              </template>

              <div v-if="comparePanelTab === 'judge'" class="judge-panel mt-4 judge-panel--expanded">
                <JudgeConfigPanel />
                <EvidencePanels />
              </div>
            </div>
            <div v-else class="empty-state">
              <p>当前没有打开的 Compare</p>
              <span class="empty-hint">Compare 是 baseline 与 candidate 的并排对比。先运行 Compare，或从 Sessions 里打开一条历史结果。</span>
              <div v-if="recentTrials.length" class="request-list mt-3">
                <button
                  v-for="trial in recentTrials"
                  :key="trial.trial_id"
                  class="request-item request-item--interactive"
                  @click="openCompareTrialInWorkbench(trial.trial_id, { source: trial.session_id ? 'session' : 'recent', switchWorkspace: false })"
                >
                  <div class="request-main">
                    <span class="request-id">{{ trialReadableTitle(trial) }}</span>
                    <span class="request-meta">{{ trialReadableMeta(trial) }}</span>
                  </div>
                </button>
              </div>
            </div>
          </template>
        </section>
      </div>
    </div>

    <SessionsWorkspace v-else />
  </div>
</template>


<style scoped>
/* Base Variables & Setup */
.node-lab-container {
  --color-surface: var(--theme--background, #ffffff);
  --color-surface-subdued: var(--theme--background-page, #f9fafb);
  --color-border: var(--theme--border-color, #e5e7eb);
  --color-text: var(--theme--foreground, #111827);
  --color-text-subdued: var(--theme--foreground-subdued, #6b7280);
  --color-primary: var(--theme--primary, #2563eb);
  --color-primary-text: var(--theme--primary-alt, #ffffff);
  
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;
  
  display: flex;
  flex-direction: column;
  gap: 24px;
  font-family: var(--theme--font-family, system-ui, -apple-system, sans-serif);
  color: var(--color-text);
  line-height: 1.5;
  padding-bottom: 40px;
  max-width: 1440px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

/* Reset typography & controls */
h1, h2, h3, h4, p { margin: 0; }
button { font-family: inherit; cursor: pointer; border: none; background: transparent; padding: 0; }
select, input, textarea { font-family: inherit; font-size: 14px; box-sizing: border-box; }

/* Header */
.lab-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 20px 24px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}

.header-title {
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 4px;
  letter-spacing: -0.01em;
}

.header-desc {
  font-size: 13px;
  color: var(--color-text-subdued);
}
.header-desc .divider { margin: 0 6px; color: var(--color-border); }
.header-desc strong { color: var(--color-text); font-weight: 500; }

.header-meta {
  display: flex;
  gap: 24px;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-width: 280px;
}

.meta-item--primary {
  padding-right: 24px;
  border-right: 1px solid var(--color-border);
}

.meta-label {
  font-size: 11px;
  color: var(--color-text-subdued);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.meta-value {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
}
.meta-actions {
  display: flex;
  gap: 8px;
  margin-top: 2px;
}
.meta-hint {
  font-size: 11px;
  color: var(--color-text-subdued);
  line-height: 1.4;
}

/* Navigation Tab & Modes Bar */
.lab-navigation {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 24px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}

.node-tabs {
  display: flex;
  gap: 24px;
  border-bottom: 1px solid var(--color-border);
  width: 100%;
  padding-bottom: 8px;
}

.node-tab-btn {
  position: relative;
  font-size: 15px;
  font-weight: 500;
  color: var(--color-text-subdued);
  padding: 4px 4px 12px 4px;
  transition: color 0.15s ease;
}

.node-tab-btn:hover {
  color: var(--color-text);
}

.node-tab-btn.active {
  color: var(--color-primary);
  font-weight: 600;
}

.node-tab-btn.active::after {
  content: "";
  position: absolute;
  bottom: -1px;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--color-primary);
  border-radius: 999px;
}

.workspace-modes-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.modes-bar-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued);
}

.segmented-control {
  display: flex;
  gap: 4px;
  background: var(--color-surface-subdued);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 4px;
}

.segment-btn {
  position: relative;
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued);
  border-radius: var(--radius-sm);
  transition: color 0.15s ease, background 0.15s ease;
}

.segment-btn:hover {
  color: var(--color-text);
}

.segment-btn.active {
  color: var(--color-text);
  background: var(--color-surface);
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

.segment-btn:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.activity-dot {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--color-primary);
}



/* Workbench Split Layout */
.workbench {
  display: grid;
  grid-template-columns: minmax(400px, 550px) 1fr;
  gap: 24px;
  align-items: start;
}

.workbench.is-compare {
  grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
}

.judge-panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.panel-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}

.section-hint {
  font-size: 13px;
  color: var(--color-text-subdued);
  margin-top: 12px;
}

.action-section {
  background: color-mix(in srgb, var(--color-primary) 3%, var(--color-surface));
  border-color: color-mix(in srgb, var(--color-primary) 15%, var(--color-border));
}
.action-header { margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
.action-buttons { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }

/* Forms & Inputs */
.form-row {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  margin-bottom: 16px;
}
.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued);
}

/* Buttons */
.btn-link { color: var(--color-primary); font-size: 13px; font-weight: 500; padding: 2px 0; }
.btn-link:hover { text-decoration: underline; }

/* Badges & Feedback */
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
.badge-neutral { color: var(--color-text-subdued); }

.feedback-banner {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
}
.feedback-banner.error { background: color-mix(in srgb, var(--theme--danger, #dc2626) 12%, var(--color-surface)); color: var(--theme--danger, #dc2626); }
.feedback-banner.info { background: color-mix(in srgb, var(--theme--success, #10b981) 12%, var(--color-surface)); }

/* Output & Compare */
.result-shell {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
  position: sticky;
  top: 24px;
}
.result-header {
  margin-bottom: 24px;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.result-title { font-size: 16px; font-weight: 600; }

.result-status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--color-border);
  white-space: nowrap;
}

.result-status-pill.is-refreshing,
.result-status-pill.is-loading {
  color: var(--theme--warning, #f59e0b);
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 8%, var(--color-surface));
}

.result-status-pill.is-updated {
  color: var(--theme--success, #10b981);
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--success, #10b981) 7%, var(--color-surface));
}

.refresh-banner {
  margin-bottom: 16px;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: var(--color-surface-subdued);
}

.refresh-banner.is-refreshing,
.refresh-banner.is-loading {
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 30%, var(--color-border));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 7%, var(--color-surface));
}

.refresh-banner.is-updated {
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 30%, var(--color-border));
  background: color-mix(in srgb, var(--theme--success, #10b981) 6%, var(--color-surface));
}

.refresh-banner__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  margin-bottom: 6px;
}

.refresh-banner p {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-subdued);
  line-height: 1.5;
}

.refresh-spinner {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  border: 2px solid color-mix(in srgb, var(--theme--warning, #f59e0b) 28%, transparent);
  border-top-color: var(--theme--warning, #f59e0b);
  animation: node-lab-spin 0.8s linear infinite;
}

.compare-panel-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
  margin-bottom: 16px;
}

/* Status banners */
.status-banner { padding: 16px; border-radius: var(--radius-md); border: 1px solid; }
.status-banner.is-ready { background: color-mix(in srgb, var(--theme--success, #10b981) 5%, var(--color-surface)); border-color: color-mix(in srgb, var(--theme--success) 30%, var(--color-border)); }
.status-banner.is-warning { background: color-mix(in srgb, var(--theme--warning, #f59e0b) 5%, var(--color-surface)); border-color: color-mix(in srgb, var(--theme--warning) 30%, var(--color-border)); }

/* Empty States */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  text-align: center;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
  color: var(--color-text-subdued);
}
.empty-hint { font-size: 13px; margin-top: 4px; }

/* Skeleton Loading State */
.skeleton-state {
  display: flex;
  flex-direction: column;
  animation: fade-in 0.3s ease-out;
}

.skeleton-header {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 24px;
}

.skeleton-btn {
  width: 140px;
  height: 32px;
  background: var(--color-surface-subdued);
  border-radius: var(--radius-md);
  animation: nl-pulse 1.5s infinite;
}

.skeleton-meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.skeleton-item {
  height: 48px;
  background: var(--color-surface-subdued);
  border-radius: var(--radius-md);
  animation: nl-pulse 1.5s infinite;
}

.skeleton-block {
  padding: 24px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.skeleton-title {
  width: 120px;
  height: 20px;
  background: var(--color-surface-subdued);
  border-radius: 4px;
  margin-bottom: 16px;
  animation: nl-pulse 1.5s infinite;
}

.skeleton-content {
  height: 80px;
  background: var(--color-surface-subdued);
  border-radius: 4px;
  animation: nl-pulse 1.5s infinite;
}

@keyframes nl-pulse {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 0.2; }
}

.fade-in {
  animation: fade-in 0.3s ease-out;
}

@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.request-list { display: flex; flex-direction: column; gap: 8px; }
.request-item { display: flex; justify-content: space-between; padding: 12px; border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.request-item--interactive {
  text-align: left;
  width: 100%;
}
.request-item--interactive:hover {
  border-color: color-mix(in srgb, var(--color-primary) 25%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 4%, var(--color-surface));
}
.request-main { display: flex; flex-direction: column; gap: 4px; }
.request-id { font-weight: 500; font-size: 14px; }
.request-meta { font-size: 12px; color: var(--color-text-subdued); }

/* Help Tooltips */
.help-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  border: 1px solid var(--color-text-subdued);
  color: var(--color-text-subdued);
  font-size: 11px;
  font-weight: 600;
  cursor: help;
}
.help-icon.inline { margin-left: 6px; }

.block-hint {
  font-size: 12px;
  color: var(--color-text-subdued);
  line-height: 1.5;
  margin: -8px 0 8px;
}

.session-block-hint {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-subdued);
}

/* Helpers */
.mt-1 { margin-top: 4px; }
.mt-3 { margin-top: 12px; }
.mt-4 { margin-top: 16px; }
.mb-3 { margin-bottom: 12px; }
.mb-4 { margin-bottom: 16px; }
.text-sm { font-size: 13px; }
.ml-1 { margin-left: 4px; }

/* Tone variables */
.text-success { color: var(--theme--success, #10b981); }
.text-warning { color: var(--theme--warning, #f59e0b); }
.text-danger { color: var(--theme--danger, #dc2626); }
.text-neutral { color: var(--color-text-subdued); }

@keyframes node-lab-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (max-width: 1200px) {
  .workbench { grid-template-columns: 1fr; }
  .result-shell { position: static; }
}
</style>
