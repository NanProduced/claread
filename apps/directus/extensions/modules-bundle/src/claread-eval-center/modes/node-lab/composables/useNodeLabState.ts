import { reactive, computed, ref, inject, type InjectionKey } from "vue";
import {
  STORAGE_KEY,
  NODE_OPTIONS,
  JUDGE_MODES,
  JUDGE_MODES_BY_NODE,
  READING_VARIANTS_BY_GOAL,
} from "./useNodeLabConstants";
import {
  defaultVariantForGoal,
  normalizeGoal,
  normalizeVariantForGoal,
  defaultCandidateDraft,
  defaultJudgeDraft,
  judgeModeAllowedForNode,
  defaultJudgeModeForNode,
} from "./useNodeLabFormatting";

export type NodeLabState = ReturnType<typeof createNodeLabState>;
export const NODE_LAB_STATE_KEY: InjectionKey<NodeLabState> = Symbol("node-lab-state");

export function createNodeLabState() {
  const state = reactive({
    activeNode: "grammar",
    activeWorkspace: "single_run",
    textsByNode: {
      grammar: "Although the plan looked simple, it required careful coordination across several teams.",
      vocabulary: "Although the plan looked simple, it required careful coordination across several teams.",
      translation: "Although the plan looked simple, it required careful coordination across several teams.",
    },
    readingGoalByNode: {
      grammar: "daily_reading",
      vocabulary: "daily_reading",
      translation: "daily_reading",
    },
    readingVariantByNode: {
      grammar: "intermediate_reading",
      vocabulary: "intermediate_reading",
      translation: "intermediate_reading",
    },
    baselineConfigsByNode: {},
    candidateDraftsByNode: {},
    singleRunResultsByNode: {},
    singleRunUiStateByNode: {},
    compareResultsByNode: {},
    activeCompareViewByNode: {},
    compareUiStateByNode: {},
    comparePanelTabByNode: {},
    savedCandidatesByNode: {},
    savedJudgeConfigsByNode: {},
    judgePresetsByNode: {},
    judgeDraftsByNode: {},
    judgeRequestsByNode: {},
    judgeRequestDetailsById: {},
    selectedJudgeRequestIdByNode: {},
    pendingJudgeRequestIdByNode: {},
    recentTrialsByNode: {},
    sessionsByNode: {},
    sessionDetailsById: {},
    selectedSessionIdByNode: {},
    selectedTrialDetailsById: {},
    selectedTrialIdBySession: {},
    latestPersistedCompareTrialByNode: {},
    currentCompareTrialIdByNode: {},
    evidenceExpandedByNode: {},
    judgePanelOpenByNode: {},
  });

  const loading = reactive({
    baseline: false,
    modelProfiles: false,
    run: false,
    compare: false,
    saveCandidate: false,
    saveJudgeConfig: false,
    queueJudge: false,
    executeJudge: false,
    createSession: false,
    sessions: false,
  });

  const feedback = reactive({
    error: "",
    info: "",
  });

  const modelProfiles = ref([]);
  let persistTimer = null;
  let judgePollTimer = null;
  let judgePollRequestId = "";

  // Two-way binding computed properties
  const currentText = computed({
    get: () => state.textsByNode[state.activeNode] || "",
    set: (value) => { state.textsByNode[state.activeNode] = value; },
  });

  const currentReadingGoal = computed({
    get: () => state.readingGoalByNode[state.activeNode] || "daily_reading",
    set: (value) => { state.readingGoalByNode[state.activeNode] = value; },
  });

  const currentReadingVariant = computed({
    get: () => state.readingVariantByNode[state.activeNode] || defaultVariantForGoal(currentReadingGoal.value),
    set: (value) => { state.readingVariantByNode[state.activeNode] = value; },
  });

  const comparePanelTab = computed({
    get: () => state.comparePanelTabByNode[state.activeNode] || "compare",
    set: (value) => { state.comparePanelTabByNode[state.activeNode] = value || "compare"; },
  });

  const pendingJudgeRequestId = computed({
    get: () => state.pendingJudgeRequestIdByNode[state.activeNode] || "",
    set: (value) => { state.pendingJudgeRequestIdByNode[state.activeNode] = value || ""; },
  });

  const selectedSessionId = computed({
    get: () => state.selectedSessionIdByNode[state.activeNode] || "",
    set: (value) => { state.selectedSessionIdByNode[state.activeNode] = value; },
  });

  const selectedSessionTrialId = computed({
    get: () => state.selectedTrialIdBySession[selectedSessionId.value] || "",
    set: (value) => { state.selectedTrialIdBySession[selectedSessionId.value] = value; },
  });

  const selectedJudgeRequestId = computed({
    get: () => state.selectedJudgeRequestIdByNode?.[state.activeNode] || "",
    set: (value) => {
      if (!state.selectedJudgeRequestIdByNode) state.selectedJudgeRequestIdByNode = {};
      state.selectedJudgeRequestIdByNode[state.activeNode] = value || "";
    },
  });

  const evidenceExpanded = computed({
    get: () => state.evidenceExpandedByNode[state.activeNode] || { runtime: false, prompt: false, judge: false },
    set: (value) => { state.evidenceExpandedByNode[state.activeNode] = value; },
  });

  const judgePanelOpen = computed({
    get: () => state.judgePanelOpenByNode[state.activeNode] || false,
    set: (value) => { state.judgePanelOpenByNode[state.activeNode] = value; },
  });

  const selectedCandidateValue = computed({
    get: () => currentDraft.value.candidate_id || "",
    set: (value) => { currentDraft.value.candidate_id = value || ""; },
  });

  const selectedJudgeConfigValue = computed({
    get: () => currentJudgeDraft.value.judge_config_id || "",
    set: (value) => { currentJudgeDraft.value.judge_config_id = value || ""; },
  });

  // Read-only computed properties
  const availableReadingVariants = computed(() => READING_VARIANTS_BY_GOAL[currentReadingGoal.value] || []);
  const baselineConfig = computed(() => state.baselineConfigsByNode[state.activeNode] || null);
  const currentDraft = computed(() => {
    if (!state.candidateDraftsByNode[state.activeNode]) {
      state.candidateDraftsByNode[state.activeNode] = defaultCandidateDraft(state.activeNode, baselineConfig.value);
    }
    return state.candidateDraftsByNode[state.activeNode];
  });
  const currentJudgeDraft = computed(() => {
    if (!state.judgeDraftsByNode[state.activeNode]) {
      state.judgeDraftsByNode[state.activeNode] = defaultJudgeDraft(state.activeNode);
    }
    return state.judgeDraftsByNode[state.activeNode];
  });
  const currentSavedCandidates = computed(() => state.savedCandidatesByNode[state.activeNode] || []);
  const currentSavedJudgeConfigs = computed(() => state.savedJudgeConfigsByNode[state.activeNode] || []);
  const currentJudgePresets = computed(() => state.judgePresetsByNode[state.activeNode] || []);
  const currentJudgeRequests = computed(() => state.judgeRequestsByNode[state.activeNode] || []);
  const currentSessions = computed(() => state.sessionsByNode[state.activeNode] || []);
  const availableJudgeModes = computed(() => {
    const allowed = new Set(JUDGE_MODES_BY_NODE[state.activeNode] || []);
    return JUDGE_MODES.filter((mode) => allowed.has(mode.id));
  });
  const singleRunResult = computed(() => state.singleRunResultsByNode[state.activeNode] || null);
  const singleRunUiState = computed(() => state.singleRunUiStateByNode[state.activeNode] || null);
  const compareResult = computed(() => state.activeCompareViewByNode[state.activeNode]?.result || state.compareResultsByNode[state.activeNode] || null);
  const compareUiState = computed(() => state.compareUiStateByNode[state.activeNode] || null);
  const activeCompareView = computed(() => state.activeCompareViewByNode[state.activeNode] || null);
  const activeCompareTrial = computed(() => activeCompareView.value?.trial || null);
  const recentTrials = computed(() => state.recentTrialsByNode[state.activeNode] || []);
  const selectedSessionDetail = computed(() => state.sessionDetailsById[selectedSessionId.value] || null);
  const selectedSessionTrialDetail = computed(() => state.selectedTrialDetailsById[selectedSessionTrialId.value] || null);
  const latestCompareTrialId = computed(() => state.latestPersistedCompareTrialByNode[state.activeNode] || "");
  const currentCompareTrialId = computed(() => state.currentCompareTrialIdByNode[state.activeNode] || "");
  const selectedSessionJudgeRequests = computed(() => selectedSessionDetail.value?.judge_requests || []);
  const selectedJudgeRequestDetail = computed(() => state.judgeRequestDetailsById[selectedJudgeRequestId.value] || null);

  // State management functions
  function setFeedback({ error = "", info = "" } = {}) {
    feedback.error = error;
    feedback.info = info;
  }

  function setActiveCompareView(nodeName, payload = null) {
    if (!payload) {
      state.activeCompareViewByNode[nodeName] = null;
      return;
    }
    state.activeCompareViewByNode[nodeName] = {
      trialId: payload.trialId || payload.trial?.trial_id || "",
      sessionId: payload.sessionId || payload.trial?.session_id || "",
      source: payload.source || "live",
      trial: payload.trial || null,
      result: payload.result || null,
    };
  }

  function clearActiveCompareView(nodeName, { preserveLatestTrial = false } = {}) {
    setActiveCompareView(nodeName, null);
    state.compareResultsByNode[nodeName] = null;
    if (!preserveLatestTrial) {
      state.currentCompareTrialIdByNode[nodeName] = "";
    }
    state.comparePanelTabByNode[nodeName] = "compare";
  }

  function loadPersistedState() {
    try {
      const raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved?.activeNode) state.activeNode = saved.activeNode;
      if (saved?.activeWorkspace) {
        state.activeWorkspace = saved.activeWorkspace === "judge_compare" ? "baseline_compare" : saved.activeWorkspace;
      }
      Object.assign(state.textsByNode, saved?.textsByNode || {});
      Object.assign(state.readingGoalByNode, saved?.readingGoalByNode || {});
      Object.assign(state.readingVariantByNode, saved?.readingVariantByNode || {});
      Object.assign(state.candidateDraftsByNode, saved?.candidateDraftsByNode || {});
      Object.assign(state.judgeDraftsByNode, saved?.judgeDraftsByNode || {});
      Object.assign(state.selectedSessionIdByNode, saved?.selectedSessionIdByNode || {});
      Object.assign(state.latestPersistedCompareTrialByNode, saved?.latestPersistedCompareTrialByNode || {});
      Object.assign(state.currentCompareTrialIdByNode, saved?.currentCompareTrialIdByNode || {});
      Object.assign(state.selectedJudgeRequestIdByNode, saved?.selectedJudgeRequestIdByNode || {});
      Object.assign(state.pendingJudgeRequestIdByNode, saved?.pendingJudgeRequestIdByNode || {});
      Object.assign(state.comparePanelTabByNode, saved?.comparePanelTabByNode || {});
      Object.assign(state.activeCompareViewByNode, saved?.activeCompareViewByNode || {});
      Object.assign(state.selectedTrialIdBySession, saved?.selectedTrialIdBySession || {});
      for (const nodeName of NODE_OPTIONS.map((item) => item.id)) {
        const goal = normalizeGoal(state.readingGoalByNode[nodeName] || "daily_reading");
        state.readingGoalByNode[nodeName] = goal;
        state.readingVariantByNode[nodeName] = normalizeVariantForGoal(goal, state.readingVariantByNode[nodeName]);
        if (state.judgeDraftsByNode[nodeName] && !judgeModeAllowedForNode(nodeName, state.judgeDraftsByNode[nodeName].judge_mode)) {
          state.judgeDraftsByNode[nodeName].judge_mode = defaultJudgeModeForNode(nodeName);
        }
      }
    } catch {
      // Ignore session cache corruption.
    }
  }

  function persistedStatePayload() {
    return {
      activeNode: state.activeNode,
      activeWorkspace: state.activeWorkspace,
      textsByNode: state.textsByNode,
      readingGoalByNode: state.readingGoalByNode,
      readingVariantByNode: state.readingVariantByNode,
      candidateDraftsByNode: state.candidateDraftsByNode,
      judgeDraftsByNode: state.judgeDraftsByNode,
      selectedSessionIdByNode: state.selectedSessionIdByNode,
      selectedTrialIdBySession: state.selectedTrialIdBySession,
      latestPersistedCompareTrialByNode: state.latestPersistedCompareTrialByNode,
      currentCompareTrialIdByNode: state.currentCompareTrialIdByNode,
      selectedJudgeRequestIdByNode: state.selectedJudgeRequestIdByNode,
      pendingJudgeRequestIdByNode: state.pendingJudgeRequestIdByNode,
      comparePanelTabByNode: state.comparePanelTabByNode,
      activeCompareViewByNode: Object.fromEntries(
        Object.entries(state.activeCompareViewByNode || {}).map(([nodeName, view]) => [
          nodeName,
          view
            ? {
                trialId: view.trialId || "",
                sessionId: view.sessionId || "",
                source: view.source || "live",
              }
            : null,
        ]),
      ),
    };
  }

  function persistState() {
    if (typeof window === "undefined") return;
    if (persistTimer) window.clearTimeout(persistTimer);
    persistTimer = window.setTimeout(() => {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(persistedStatePayload()));
      persistTimer = null;
    }, 300);
  }

  return {
    state, loading, feedback, modelProfiles,
    // Timer setters (needed by API layer)
    setPersistTimer: (v) => { persistTimer = v; },
    setJudgePollTimer: (v) => { judgePollTimer = v; },
    setJudgePollRequestId: (v) => { judgePollRequestId = v; },
    getPersistTimer: () => persistTimer,
    getJudgePollTimer: () => judgePollTimer,
    getJudgePollRequestId: () => judgePollRequestId,
    // Two-way binding computed
    currentText, currentReadingGoal, currentReadingVariant,
    comparePanelTab, pendingJudgeRequestId,
    selectedSessionId, selectedSessionTrialId,
    selectedJudgeRequestId, evidenceExpanded, judgePanelOpen,
    selectedCandidateValue, selectedJudgeConfigValue,
    // Read-only computed
    baselineConfig, currentDraft, currentJudgeDraft,
    currentSavedCandidates, currentSavedJudgeConfigs,
    currentJudgePresets, currentJudgeRequests,
    currentSessions, availableJudgeModes,
    singleRunResult, singleRunUiState,
    compareResult, compareUiState,
    activeCompareView, activeCompareTrial,
    recentTrials, selectedSessionDetail,
    selectedSessionTrialDetail, latestCompareTrialId,
    currentCompareTrialId, selectedSessionJudgeRequests,
    selectedJudgeRequestDetail, availableReadingVariants,
    // Functions
    setFeedback, setActiveCompareView, clearActiveCompareView,
    loadPersistedState, persistedStatePayload, persistState,
  };
}

export function useNodeLabState() {
  const injected = inject(NODE_LAB_STATE_KEY);
  if (!injected) throw new Error("NodeLab state not provided");
  return injected;
}
