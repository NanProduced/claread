import { inject, computed, type InjectionKey } from "vue";
import { API_ENDPOINTS, DEFAULT_TIMEOUT_SECONDS, TERMINAL_JUDGE_STATUSES } from "./useNodeLabConstants";
import {
  shortId,
  statusLabel,
} from "../../../composables/useEvalFormatting";
import {
  safeJsonParse,
  buildInputPreview,
  nodeLabel,
  defaultCandidateDraft,
  defaultJudgeDraft,
  judgeModeAllowedForNode,
  defaultJudgeModeForNode,
  readingGoalLabel,
  readingVariantLabel,
  normalizePreviewText,
} from "./useNodeLabFormatting";
import type { NodeLabState } from "./useNodeLabState";

export type NodeLabApi = ReturnType<typeof createNodeLabApi>;
export const NODE_LAB_API_KEY: InjectionKey<NodeLabApi> = Symbol("node-lab-api");

export function createNodeLabApi(deps: NodeLabState) {
  const {
    state, loading, feedback, modelProfiles,
    currentText, currentReadingGoal, currentReadingVariant,
    comparePanelTab, pendingJudgeRequestId,
    selectedSessionId, selectedSessionTrialId,
    selectedJudgeRequestId, evidenceExpanded, judgePanelOpen,
    selectedCandidateValue, selectedJudgeConfigValue,
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
    setFeedback, setActiveCompareView, clearActiveCompareView,
    loadPersistedState, persistedStatePayload, persistState,
  } = deps;

  // Computed properties that are UI-layer but needed by API functions
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

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload?.errors?.[0]?.message || `Request failed: ${response.status}`;
      throw new Error(message);
    }
    return payload?.data;
  }

  function buildCandidateOverride() {
    if (!baselineConfig.value) return null;
    const draft = currentDraft.value;
    const cleanPolicyLines = (draft.policy_lines || [])
      .map((line) => String(line || "").trim())
      .filter(Boolean);
    const cleanExamples = draft.examples_edit_mode === "raw"
      ? safeJsonParse(draft.examples_raw_text || "[]", [])
      : draft.examples;
    const baselinePolicy = JSON.stringify(baselineConfig.value.policy_lines || []);
    const candidatePolicy = JSON.stringify(cleanPolicyLines);
    const instructionsChanged = draft.instruction_text.trim() !== String(baselineConfig.value.agent_instructions || "").trim();
    const policyChanged = baselinePolicy !== candidatePolicy;
    const modelSelection = draft.model_profile
      ? { default_profile: draft.model_profile }
      : null;
    return {
      candidate_id: draft.candidate_id || `node-lab-${state.activeNode}`,
      node_name: state.activeNode,
      instruction_override: instructionsChanged
        ? { mode: "override_text", text: draft.instruction_text }
        : { mode: "baseline" },
      policy_override: policyChanged
        ? { mode: "override_lines", lines: cleanPolicyLines }
        : { mode: "baseline" },
      few_shot_override: {
        few_shot_mode: draft.few_shot_mode,
        examples: draft.few_shot_mode === "candidate" ? cleanExamples : [],
      },
      model_selection: modelSelection,
      snapshot_hash: compareResult.value?.candidate?.prompt_identity?.prompt_snapshot_hash || null,
    };
  }

  function buildRunRequest({ dryRun = false, useCandidate = true } = {}) {
    return {
      node_name: state.activeNode,
      text: currentText.value,
      reading_goal: currentReadingGoal.value,
      reading_variant: currentReadingVariant.value,
      source_type: "user_input",
      timeout_seconds: DEFAULT_TIMEOUT_SECONDS,
      dry_run: dryRun,
      candidate_override: useCandidate ? buildCandidateOverride() : null,
    };
  }

  function buildJudgeConfigSnapshot() {
    const draft = currentJudgeDraft.value;
    const preset = currentJudgePresets.value.find((item) => item.preset_id === draft.preset_id) || null;
    const parameters = safeJsonParse(draft.parameters_json, {});
    const judgerModels = draft.judger_models
      .map((profileName) => String(profileName || "").trim())
      .filter(Boolean)
      .slice(0, 3)
      .map((profileName) => ({ profile_name: profileName }));
    return {
      judge_mode: draft.judge_mode,
      preset_id: draft.preset_id || null,
      judge_strategy: draft.judge_strategy || preset?.strategy || null,
      judge_method: draft.judge_method || preset?.method || null,
      packet_policy_json: safeJsonParse(draft.packet_policy_json || "{}", {}),
      rubric_bundle_json: safeJsonParse(draft.rubric_bundle_json || "{}", {}),
      probe_appendix_json: safeJsonParse(draft.probe_appendix_json || "{}", {}),
      rubric_source_json: safeJsonParse(draft.rubric_json, { criteria: [] }),
      persona_json: draft.persona_text ? { description: draft.persona_text } : null,
      prompt_templates_json: {
        system_prompt: draft.system_prompt,
        user_prompt: draft.user_prompt,
      },
      output_schema_json: safeJsonParse(draft.output_schema_json, {}),
      parameters_json: parameters,
      judger_models_json: judgerModels,
      label: draft.label,
      description: draft.description,
    };
  }

  function applyJudgeModeTemplate(mode, { preservePreset = false } = {}) {
    const templates = {
      rubric_score_only: {
        system: "请严格按给定 rubric 做三档打分，每条只返回 0/1/2、简短理由和必要证据，不要自己汇总 aggregate。",
        user: "请分别评估 baseline 与 candidate，并输出结构化 rubric scoring 结果。",
        persona: "",
      },
      rubric_plus_pairwise: {
        system: "请先完成 rubric scoring，再基于原文与精选标注信息给出整体对比评估意见。",
        user: "请不要复查锚点和结构 JSON，只比较两版整体讲解质量、帮助感和策略适配度。",
        persona: "",
      },
      anti_template_probe: {
        system: "请只回答给定的反模板化 probe 问题，重点检查机械重复、同质化与僵硬解释。",
        user: "请观察 baseline 与 candidate 的样本，判断 candidate 是否减少了固定套路和单一标注模式。",
        persona: "",
      },
      raw: {
        system: "",
        user: "",
        persona: "",
      },
    };
    const preset = templates[mode] || templates.rubric_plus_pairwise;
    currentJudgeDraft.value.judge_mode = mode;
    currentJudgeDraft.value.judge_method = mode === "anti_template_probe"
      ? "anti_template_probe"
      : mode === "raw"
        ? "raw"
        : "rubric_plus_pairwise";
    if (!preservePreset) {
      currentJudgeDraft.value.preset_id = "";
    }
    currentJudgeDraft.value.system_prompt = preset.system;
    currentJudgeDraft.value.user_prompt = preset.user;
    currentJudgeDraft.value.persona_text = preset.persona;
  }

  function applyJudgePreset(presetId) {
    const preset = currentJudgePresets.value.find((item) => item.preset_id === presetId);
    if (!preset) return;
    const draft = currentJudgeDraft.value;
    draft.preset_id = preset.preset_id;
    draft.judge_strategy = preset.strategy;
    draft.judge_method = preset.method;
    draft.judge_mode = preset.method === "anti_template_probe" ? "anti_template_probe" : preset.method === "raw" ? "raw" : "rubric_plus_pairwise";
    draft.label = preset.title;
    draft.description = `${nodeLabel(preset.node_name)} · ${preset.ui_label}`;
    draft.preset_summary = preset.ui_label || "";
    draft.packet_policy_json = JSON.stringify(preset.packet_policy || {}, null, 2);
    draft.rubric_bundle_json = JSON.stringify(preset.rubric_bundle || {}, null, 2);
    draft.probe_appendix_json = JSON.stringify(preset.probe_appendix || {}, null, 2);
    applyJudgeModeTemplate(draft.judge_mode, { preservePreset: true });
    draft.persona_text = "";
    draft.rubric_json = JSON.stringify(preset.rubric_bundle || {}, null, 2);
    draft.output_schema_json = JSON.stringify(preset.output_schema || {}, null, 2);
  }

  async function loadModelProfiles() {
    loading.modelProfiles = true;
    try {
      modelProfiles.value = await fetchJson(API_ENDPOINTS.modelProfiles, { method: "GET" });
    } finally {
      loading.modelProfiles = false;
    }
  }

  async function loadBaselineConfig() {
    loading.baseline = true;
    try {
      const data = await fetchJson(API_ENDPOINTS.baselineConfig, {
        method: "POST",
        body: JSON.stringify({
          node_name: state.activeNode,
          reading_goal: currentReadingGoal.value,
          reading_variant: currentReadingVariant.value,
        }),
      });
      state.baselineConfigsByNode[state.activeNode] = data;
      if (!state.candidateDraftsByNode[state.activeNode]) {
        state.candidateDraftsByNode[state.activeNode] = defaultCandidateDraft(state.activeNode, data);
      } else {
        const draft = state.candidateDraftsByNode[state.activeNode];
        if (!draft.instruction_text) draft.instruction_text = data.agent_instructions || "";
        if (!Array.isArray(draft.policy_lines) || draft.policy_lines.length === 0) {
          draft.policy_lines = [...(data.policy_lines || [])];
        }
        if (!draft.examples_raw_text) {
          draft.examples_raw_text = JSON.stringify(data.baseline_examples || [], null, 2);
        }
        if (!Array.isArray(draft.examples) || draft.examples.length === 0) {
          draft.examples = [...(data.baseline_examples || [])];
        }
      }
    } catch (error) {
      setFeedback({ error: error.message });
    } finally {
      loading.baseline = false;
    }
  }

  async function loadCandidates() {
    try {
      const rows = await fetchJson(`${API_ENDPOINTS.candidates}?node_name=${encodeURIComponent(state.activeNode)}`, { method: "GET" });
      state.savedCandidatesByNode[state.activeNode] = rows;
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function loadJudgeConfigs() {
    try {
      const rows = await fetchJson(`${API_ENDPOINTS.judgeConfigs}?node_name=${encodeURIComponent(state.activeNode)}`, { method: "GET" });
      state.savedJudgeConfigsByNode[state.activeNode] = rows;
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function loadJudgePresets() {
    try {
      const rows = await fetchJson(`${API_ENDPOINTS.judgePresets}?node_name=${encodeURIComponent(state.activeNode)}`, { method: "GET" });
      state.judgePresetsByNode[state.activeNode] = rows;
      if (!rows?.length) {
        currentJudgeDraft.value.preset_id = "";
        return;
      }
      if (
        !currentJudgeDraft.value.preset_id
        || !rows.some((item) => item.preset_id === currentJudgeDraft.value.preset_id)
      ) {
        applyJudgePreset(rows[0].preset_id);
      }
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function loadJudgeRequests({ trialId = activeCompareTrial.value?.trial_id || "" } = {}) {
    try {
      if (!trialId) {
        state.judgeRequestsByNode[state.activeNode] = [];
        selectedJudgeRequestId.value = "";
        return [];
      }
      const query = new URLSearchParams({
        node_name: state.activeNode,
        trial_id: trialId,
        limit: "20",
      });
      const rows = await fetchJson(`${API_ENDPOINTS.judgeRequests}?${query.toString()}`, { method: "GET" });
      state.judgeRequestsByNode[state.activeNode] = rows;
      if (
        selectedJudgeRequestId.value
        && !rows.some((item) => item.judge_request_id === selectedJudgeRequestId.value)
      ) {
        selectedJudgeRequestId.value = "";
      }
      return rows || [];
    } catch (error) {
      setFeedback({ error: error.message });
    }
    return [];
  }

  async function syncSelectedJudgeRequestForActiveCompare({ preferredId = "", autoLoadDetail = false } = {}) {
    const rows = state.judgeRequestsByNode[state.activeNode] || [];
    const nextId = (
      (preferredId && rows.some((item) => item.judge_request_id === preferredId) && preferredId)
      || (selectedJudgeRequestId.value && rows.some((item) => item.judge_request_id === selectedJudgeRequestId.value) && selectedJudgeRequestId.value)
      || rows[0]?.judge_request_id
      || ""
    );
    if (!nextId) {
      selectedJudgeRequestId.value = "";
      return null;
    }
    selectedJudgeRequestId.value = nextId;
    if (
      autoLoadDetail
      && selectedJudgeRequestDetail.value?.request?.judge_request_id !== nextId
    ) {
      return await loadJudgeRequestDetail(nextId);
    }
    return selectedJudgeRequestDetail.value?.request?.judge_request_id === nextId
      ? selectedJudgeRequestDetail.value
      : null;
  }

  async function loadSessions() {
    loading.sessions = true;
    try {
      const rows = await fetchJson(`${API_ENDPOINTS.sessions}?node_name=${encodeURIComponent(state.activeNode)}`, { method: "GET" });
      state.sessionsByNode[state.activeNode] = rows;
      if (selectedSessionId.value && rows.some((item) => item.session_id === selectedSessionId.value)) {
        await loadSessionDetail(selectedSessionId.value);
      } else if (selectedSessionId.value && !rows.some((item) => item.session_id === selectedSessionId.value)) {
        selectedSessionId.value = "";
      }
    } catch (error) {
      setFeedback({ error: error.message });
    } finally {
      loading.sessions = false;
    }
  }

  async function loadRecentTrials() {
    try {
      const query = new URLSearchParams({
        node_name: state.activeNode,
        workspace_type: "baseline_compare",
        reading_goal: currentReadingGoal.value,
        reading_variant: currentReadingVariant.value,
        limit: "8",
      });
      const rows = await fetchJson(`${API_ENDPOINTS.trials}?${query.toString()}`, { method: "GET" });
      state.recentTrialsByNode[state.activeNode] = rows || [];
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function loadSessionDetail(sessionId) {
    if (!sessionId) return;
    try {
      const detail = await fetchJson(`${API_ENDPOINTS.sessions}/${encodeURIComponent(sessionId)}`, { method: "GET" });
      state.sessionDetailsById[sessionId] = detail;
      const preferredTrialId = state.selectedTrialIdBySession[sessionId];
      if (preferredTrialId && detail?.trials?.some((trial) => trial.trial_id === preferredTrialId)) {
        await loadTrialDetail(preferredTrialId, sessionId);
      } else if (detail?.trials?.[0]?.trial_id) {
        state.selectedTrialIdBySession[sessionId] = detail.trials[0].trial_id;
        await loadTrialDetail(detail.trials[0].trial_id, sessionId);
      } else {
        state.selectedTrialIdBySession[sessionId] = "";
      }
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function loadTrialDetail(trialId, sessionId = "") {
    try {
      const detail = await fetchJson(`${API_ENDPOINTS.trials}/${encodeURIComponent(trialId)}`, { method: "GET" });
      state.selectedTrialDetailsById[trialId] = detail;
      if (sessionId) {
        state.selectedTrialIdBySession[sessionId] = trialId;
      }
      return detail;
    } catch (error) {
      setFeedback({ error: error.message });
    }
    return null;
  }

  async function openCompareTrialInWorkbench(trialId, { source = "recent", switchWorkspace = true, openJudge = false, judgeRequestId = "" } = {}) {
    if (!trialId) return null;
    const detail = await loadTrialDetail(trialId);
    if (!detail?.trial || !detail?.result) return null;
    state.compareResultsByNode[state.activeNode] = detail.result;
    setActiveCompareView(state.activeNode, {
      source,
      trial: detail.trial,
      result: detail.result,
      trialId: detail.trial.trial_id,
      sessionId: detail.trial.session_id || "",
      inputPreview: detail.trial.input_excerpt || "",
    });
    state.currentCompareTrialIdByNode[state.activeNode] = detail.trial.trial_id;
    state.latestPersistedCompareTrialByNode[state.activeNode] = detail.trial.trial_id;
    if (switchWorkspace) state.activeWorkspace = "baseline_compare";
    comparePanelTab.value = openJudge ? "judge" : "compare";
    await loadJudgeRequests({ trialId: detail.trial.trial_id });
    await syncSelectedJudgeRequestForActiveCompare({
      preferredId: judgeRequestId,
      autoLoadDetail: openJudge || Boolean(judgeRequestId),
    });
    return detail;
  }

  function goStartCompareFromEmpty() {
    state.activeWorkspace = "baseline_compare";
    setFeedback({
      info: "请先跑一条 compare，再在结果区选择\u201C新建 Session 并加入\u201D。",
    });
  }

  async function openCurrentSessionWorkspace() {
    state.activeWorkspace = "sessions";
    if (selectedSessionId.value) {
      await loadSessionDetail(selectedSessionId.value);
    }
  }

  function clearSessionAttachment() {
    selectedSessionId.value = "";
  }

  async function selectSession(sessionId, { switchWorkspace = false } = {}) {
    selectedSessionId.value = sessionId;
    await loadSessionDetail(sessionId);
    if (switchWorkspace) state.activeWorkspace = "sessions";
  }

  async function updateSession(sessionId, patch = {}) {
    if (!sessionId) {
      setFeedback({ error: "缺少 session_id，无法更新 Session。" });
      return null;
    }
    setFeedback();
    try {
      const data = await fetchJson(`${API_ENDPOINTS.sessions}/${encodeURIComponent(sessionId)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      await loadSessions();
      await loadSessionDetail(sessionId);
      setFeedback({ info: `Session 已更新：${data.title || sessionId}` });
      return data;
    } catch (error) {
      setFeedback({ error: error.message });
    }
    return null;
  }

  async function saveCandidateDraft() {
    loading.saveCandidate = true;
    setFeedback();
    try {
      const draft = currentDraft.value;
      const payload = {
        candidate_id: draft.candidate_id || undefined,
        node_name: state.activeNode,
        label: draft.label,
        description: draft.description,
        instruction_layer_json: {
          mode: "override_text",
          text: draft.instruction_text,
        },
        policy_layer_json: {
          mode: "override_lines",
          lines: draft.policy_lines,
        },
        few_shot_layer_json: {
          few_shot_mode: draft.few_shot_mode,
          edit_mode: draft.examples_edit_mode,
          examples: draft.examples_edit_mode === "raw"
            ? safeJsonParse(draft.examples_raw_text || "[]", [])
            : draft.examples,
        },
        model_layer_json: {
          model_profile: draft.model_profile || null,
        },
        normalized_manifest_json: buildCandidateOverride(),
        notes: draft.notes,
      };
      const existingId = draft.candidate_id && currentSavedCandidates.value.some((item) => item.candidate_id === draft.candidate_id);
      const data = await fetchJson(
        existingId ? `${API_ENDPOINTS.candidates}/${encodeURIComponent(draft.candidate_id)}` : API_ENDPOINTS.candidates,
        {
          method: existingId ? "PATCH" : "POST",
          body: JSON.stringify(payload),
        },
      );
      state.candidateDraftsByNode[state.activeNode].candidate_id = data.candidate_id;
      await loadCandidates();
      setFeedback({ info: `已保存 Candidate Draft：${data.label}` });
    } catch (error) {
      setFeedback({ error: error.message });
    } finally {
      loading.saveCandidate = false;
    }
  }

  async function saveJudgeConfig() {
    loading.saveJudgeConfig = true;
    setFeedback();
    try {
      const draft = currentJudgeDraft.value;
      const payload = {
        judge_config_id: draft.judge_config_id || undefined,
        node_name: state.activeNode,
        label: draft.label,
        description: draft.description,
        judge_mode: draft.judge_mode,
        rubric_source_json: safeJsonParse(draft.rubric_json, { criteria: [] }),
        persona_json: draft.persona_text ? { description: draft.persona_text } : null,
        prompt_templates_json: {
          system_prompt: draft.system_prompt,
          user_prompt: draft.user_prompt,
        },
        output_schema_json: safeJsonParse(draft.output_schema_json, {}),
        parameters_json: safeJsonParse(draft.parameters_json, {}),
        judger_models_json: draft.judger_models
          .map((profileName) => String(profileName || "").trim())
          .filter(Boolean)
          .slice(0, 3)
          .map((profileName) => ({ profile_name: profileName })),
        normalized_config_json: buildJudgeConfigSnapshot(),
        notes: draft.notes,
      };
      const existingId = draft.judge_config_id && currentSavedJudgeConfigs.value.some((item) => item.judge_config_id === draft.judge_config_id);
      const data = await fetchJson(
        existingId ? `${API_ENDPOINTS.judgeConfigs}/${encodeURIComponent(draft.judge_config_id)}` : API_ENDPOINTS.judgeConfigs,
        {
          method: existingId ? "PATCH" : "POST",
          body: JSON.stringify(payload),
        },
      );
      state.judgeDraftsByNode[state.activeNode].judge_config_id = data.judge_config_id;
      await loadJudgeConfigs();
      setFeedback({ info: `已保存 Judge Setup：${data.label}` });
    } catch (error) {
      setFeedback({ error: error.message });
    } finally {
      loading.saveJudgeConfig = false;
    }
  }

  async function runSingle({ dryRun = false, useCandidate = true, persist = false } = {}) {
    if (persist) {
      setFeedback({
        error: "Single Run 不再写入 Session。请前往 Baseline Compare 跑 compare 后再加入。",
      });
      return;
    }
    const nodeName = state.activeNode;
    const requestLabel = dryRun
      ? "预览 Prompt"
      : useCandidate
        ? "运行 Candidate"
        : "运行 Baseline";
    const requestPayload = buildRunRequest({ dryRun, useCandidate });
    state.singleRunUiStateByNode[nodeName] = {
      ...(state.singleRunUiStateByNode[nodeName] || {}),
      requestLabel,
      lastStartedAt: new Date().toISOString(),
      requestPayload,
    };
    loading.run = true;
    setFeedback();
    try {
      const data = await fetchJson(API_ENDPOINTS.run, {
        method: "POST",
        body: JSON.stringify({
          request: requestPayload,
          persist_trial: false,
        }),
      });
      state.singleRunResultsByNode[nodeName] = data.result;
      state.singleRunUiStateByNode[nodeName] = {
        ...(state.singleRunUiStateByNode[nodeName] || {}),
        requestLabel,
        lastCompletedAt: new Date().toISOString(),
        requestPayload,
      };
      setFeedback({
        info: dryRun
          ? "Candidate Prompt 预览已更新。"
          : "Single Run 已完成。结果会保留在当前页面；如需留存，可另存到 Run History。",
      });
    } catch (error) {
      setFeedback({ error: error.message });
    } finally {
      loading.run = false;
    }
  }

  async function saveSingleRunToHistory() {
    const result = singleRunResult.value;
    if (!result?.run) {
      setFeedback({ error: "暂无可保存的 Single Run 结果。" });
      return null;
    }
    const nodeName = state.activeNode;
    const requestPayload = singleRunUiState.value?.requestPayload || buildRunRequest({
      dryRun: false,
      useCandidate: result.run.participant_label !== "baseline",
    });
    loading.saveRunHistory = true;
    setFeedback();
    try {
      const data = await fetchJson(API_ENDPOINTS.runHistorySingleRun, {
        method: "POST",
        body: JSON.stringify({
          request: requestPayload,
          result,
        }),
      });
      state.singleRunUiStateByNode[nodeName] = {
        ...(state.singleRunUiStateByNode[nodeName] || {}),
        requestPayload,
        lastSavedAt: new Date().toISOString(),
        savedTrialId: data?.trial?.trial_id || state.singleRunUiStateByNode[nodeName]?.savedTrialId || "",
      };
      setFeedback({
        info: data?.duplicate
          ? `Single Run 已存在于 Run History：${data?.trial?.trial_id || "standalone trial"}`
          : `Single Run 已保存到 Run History：${data?.trial?.trial_id || "standalone trial"}`,
      });
      return data;
    } catch (error) {
      setFeedback({ error: error.message });
    } finally {
      loading.saveRunHistory = false;
    }
    return null;
  }

  function buildCandidateRegistryEntryFromResult(result, snapshot) {
    if (!result || !snapshot) return null;
    const candidatePromptIdentity = result?.candidate?.prompt_identity || {};
    const candidateModelIdentity = result?.candidate?.model_identity || {};
    const fallbackId = `attached-${shortId(
      String(snapshot.request_id || snapshot.source_text_hash || Date.now().toString()).trim()
      || Date.now().toString()
    )}`;
    const candidateId = String(candidatePromptIdentity.prompt_variant_id || "").trim() || fallbackId;
    const snapshotHash = String(candidatePromptIdentity.prompt_snapshot_hash || "").trim();
    const nodeName = String(snapshot.node_name || result?.node_name || "").trim();
    return {
      candidate_id: candidateId,
      node_name: nodeName,
      label: candidateId,
      description: "从当前已运行的 compare result 派生的 candidate 痕迹",
      source_kind: "attached_compare",
      normalized_manifest_json: {
        snapshot_hash: snapshotHash || null,
        source: "attached_compare",
        model_profile: candidateModelIdentity?.profile_name || null,
        model_name: candidateModelIdentity?.model_name || null,
        request_id: snapshot.request_id || null,
        source_text_hash: snapshot.source_text_hash || null,
      },
    };
  }

  async function attachCurrentCompareToSession({ createNewSession = false } = {}) {
    if (state.activeWorkspace !== "baseline_compare") {
      setFeedback({ error: "请先切换到 Baseline Compare 再加入 Session。" });
      return;
    }
    if (!compareResult.value) {
      setFeedback({ error: "请先运行 Compare，再把这条结果加入 Session。" });
      return;
    }
    const snapshot = compareRequestSnapshot.value;
    if (!snapshot) {
      setFeedback({
        error: "当前 Compare 结果缺少 request_snapshot，请重新运行 Compare 后再加入 Session。",
      });
      return;
    }
    if (!createNewSession && !selectedSessionId.value) {
      setFeedback({ error: "当前没有挂载的 Session。请先选择一条 Session，或新建 Session。" });
      return;
    }
    try {
      const compareRequestFromSnapshot = {
        node_name: String(snapshot.node_name || compareResult.value?.node_name || "").trim(),
        reading_goal: String(snapshot.reading_goal || "").trim(),
        reading_variant: String(snapshot.reading_variant || "").trim(),
        source_type: snapshot.source_type || "user_input",
      };
      const body = {
        request: compareRequestFromSnapshot,
        result: compareResult.value,
      };
      if (createNewSession) {
        const candidateEntry = buildCandidateRegistryEntryFromResult(compareResult.value, snapshot);
        body.session = {
          node_name: compareRequestFromSnapshot.node_name,
          title: `Compare 实验记录本 · ${compareRequestFromSnapshot.node_name} · ${compareRequestFromSnapshot.reading_goal} · ${compareRequestFromSnapshot.reading_variant}`,
          goal: `Compare 实验记录本：${compareRequestFromSnapshot.reading_goal} · ${compareRequestFromSnapshot.reading_variant}`,
          baseline_snapshot_json: {
            reading_goal: compareRequestFromSnapshot.reading_goal,
            reading_variant: compareRequestFromSnapshot.reading_variant,
            source_text_hash: snapshot.source_text_hash || null,
            prompt_snapshot_hash: compareResult.value?.baseline?.prompt_identity?.prompt_snapshot_hash || null,
            prompt_profile: compareResult.value?.baseline?.prompt_identity?.prompt_variant_id || null,
          },
          candidate_registry_json: candidateEntry ? [candidateEntry] : [],
        };
      } else {
        body.session_id = selectedSessionId.value;
      }
      const data = await fetchJson(`${API_ENDPOINTS.compare}/attach`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (data?.trial?.trial_id) {
        state.latestPersistedCompareTrialByNode[state.activeNode] = data.trial.trial_id;
        state.currentCompareTrialIdByNode[state.activeNode] = data.trial.trial_id;
      }
      if (data?.session?.session_id) {
        selectedSessionId.value = data.session.session_id;
        await loadSessions();
        await loadSessionDetail(data.session.session_id);
      }
      if (data?.trial?.trial_id) {
        await loadTrialDetail(data.trial.trial_id, data.trial.session_id);
        setActiveCompareView(state.activeNode, {
          source: "session",
          trial: data.trial,
          result: compareResult.value,
          trialId: data.trial.trial_id,
          sessionId: data.trial.session_id || "",
          inputPreview: data.trial.input_excerpt || buildInputPreview(currentText.value),
        });
        comparePanelTab.value = "compare";
      }
      await loadRecentTrials();
      setFeedback({
        info: createNewSession
          ? "已新建 Session，并把当前 Compare 结果加入。"
          : "当前 Compare 结果已加入 Session（未重跑）。",
      });
      return data;
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function addCurrentCompareToSession() {
    return attachCurrentCompareToSession({ createNewSession: false });
  }

  async function createSessionAndAddCurrentCompare() {
    return attachCurrentCompareToSession({ createNewSession: true });
  }

  function stopJudgeRequestPolling() {
    const timer = deps.getJudgePollTimer();
    if (timer) {
      window.clearInterval(timer);
      deps.setJudgePollTimer(null);
    }
  }

  function startJudgeRequestPolling() {
    stopJudgeRequestPolling();
    const pollInterval = window.setInterval(async () => {
      const trialId = deps.currentCompareTrialId.value || deps.activeCompareTrial.value?.trial_id;
      if (!trialId) return;

      try {
        await loadJudgeRequests({ trialId });

        const selectedId = deps.selectedJudgeRequestId.value;
        if (selectedId) {
          const currentRequest = deps.state.judgeRequestsByNode[deps.state.activeNode]?.find(r => r.judge_request_id === selectedId);
          if (currentRequest && !TERMINAL_JUDGE_STATUSES.has(currentRequest.status)) {
            await loadJudgeRequestDetail(selectedId);
          }
        }

        const requests = deps.state.judgeRequestsByNode[deps.state.activeNode] || [];
        const hasActive = requests.some(r => !TERMINAL_JUDGE_STATUSES.has(r.status));
        if (!hasActive) {
          stopJudgeRequestPolling();
        }
      } catch {
        // Silently continue polling on error
      }
    }, 4000);

    deps.setJudgePollTimer(pollInterval);
  }

  async function loadJudgeRequestDetail(judgeRequestId) {
    if (!judgeRequestId) return;
    try {
      const detail = await fetchJson(`${API_ENDPOINTS.judgeRequests}/${encodeURIComponent(judgeRequestId)}`, { method: "GET" });
      state.judgeRequestDetailsById[judgeRequestId] = detail;
      selectedJudgeRequestId.value = judgeRequestId;
      if (detail?.request?.trial_id && activeCompareTrial.value?.trial_id !== detail.request.trial_id) {
        await openCompareTrialInWorkbench(detail.request.trial_id, {
          source: detail.request.session_id ? "session" : "standalone",
          switchWorkspace: false,
          openJudge: true,
        });
        await loadJudgeRequests({ trialId: detail.request.trial_id });
      }
      comparePanelTab.value = "judge";
      return detail;
    } catch (error) {
      setFeedback({ error: error.message });
    }
    return null;
  }

  async function cancelJudgeRequest(judgeRequestId) {
    if (!judgeRequestId) return;
    try {
      await fetchJson(`${API_ENDPOINTS.judgeRequests}/${encodeURIComponent(judgeRequestId)}/cancel`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadJudgeRequests({ trialId: activeCompareTrial.value?.trial_id || "" });
      await loadJudgeRequestDetail(judgeRequestId);
      setFeedback({ info: `Judge request 已取消：${judgeRequestId}` });
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function retryJudgeRequest(judgeRequestId) {
    if (!judgeRequestId) return;
    try {
      const created = await fetchJson(`${API_ENDPOINTS.judgeRequests}/${encodeURIComponent(judgeRequestId)}/retry`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadJudgeRequests({ trialId: activeCompareTrial.value?.trial_id || "" });
      await loadJudgeRequestDetail(created.judge_request_id);
      setFeedback({ info: `Judge request 已重新排队：${created.judge_request_id}` });
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function executeJudgeRequest(judgeRequestId) {
    if (!judgeRequestId) return;
    loading.executeJudge = true;
    setFeedback();
    try {
      const result = await fetchJson(`${API_ENDPOINTS.judgeRequests}/${encodeURIComponent(judgeRequestId)}/execute`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadJudgeRequests({ trialId: activeCompareTrial.value?.trial_id || "" });
      await loadJudgeRequestDetail(judgeRequestId);
      if (selectedSessionId.value) {
        await loadSessionDetail(selectedSessionId.value);
      }
      comparePanelTab.value = "judge";
      startJudgeRequestPolling();
      setFeedback({ info: `Judge request 已执行完成：${judgeRequestId}，状态：${statusLabel(result.request?.status || "unknown")}` });
    } catch (error) {
      setFeedback({ error: error.message });
    } finally {
      loading.executeJudge = false;
    }
  }

  async function runCompare({ persist = false } = {}) {
    if (persist) {
      const error = new Error(
        "runCompare({ persist: true }) 已被禁用：旧路径会先建空 Session 再持久化 compare，" +
        "与 Sessions 重构后的设计冲突。请改用 Baseline Compare 的\u201C加入 Session\u201D / \u201C新建 Session 并加入\u201D。"
      );
      setFeedback({ error: error.message });
      throw error;
    }
    loading.compare = true;
    setFeedback();
    try {
      state.compareUiStateByNode[state.activeNode] = {
        ...(state.compareUiStateByNode[state.activeNode] || {}),
        requestLabel: "当前 Compare",
      };
      const data = await fetchJson(API_ENDPOINTS.compare, {
        method: "POST",
        body: JSON.stringify({
          request: {
            node_name: state.activeNode,
            text: currentText.value,
            reading_goal: currentReadingGoal.value,
            reading_variant: currentReadingVariant.value,
            source_type: "user_input",
            timeout_seconds: DEFAULT_TIMEOUT_SECONDS,
            candidate_override: buildCandidateOverride(),
          },
          persist_trial: false,
        }),
      });
      state.compareResultsByNode[state.activeNode] = data.result;
      state.currentCompareTrialIdByNode[state.activeNode] = "";
      state.judgeRequestsByNode[state.activeNode] = [];
      setActiveCompareView(state.activeNode, {
        source: "live",
        trial: null,
        result: data.result,
        inputPreview: buildInputPreview(currentText.value),
      });
      pendingJudgeRequestId.value = "";
      selectedJudgeRequestId.value = "";
      comparePanelTab.value = "compare";
      state.compareUiStateByNode[state.activeNode] = {
        ...(state.compareUiStateByNode[state.activeNode] || {}),
        requestLabel: "当前 Compare",
        lastCompletedAt: new Date().toISOString(),
      };
      await loadRecentTrials();
      setFeedback({ info: "Compare 已完成。" });
      return data;
    } catch (error) {
      setFeedback({ error: error.message });
      throw error;
    } finally {
      loading.compare = false;
    }
  }

  async function queueJudgeCompare({ autoExecute = false } = {}) {
    loading.queueJudge = true;
    setFeedback();
    try {
      const mismatch = compareSnapshotContextMismatchReason.value;
      let trialId = currentCompareTrialId.value && !mismatch ? currentCompareTrialId.value : "";
      let trialSourceLabel = trialId ? "当前 Compare 对应的 Trial" : "";
      if (!trialId && compareResult.value && !mismatch) {
        const attachBody = {
          result: compareResult.value,
          persist_without_session: true,
        };
        const attachData = await fetchJson(`${API_ENDPOINTS.compare}/attach`, {
          method: "POST",
          body: JSON.stringify(attachBody),
        });
        trialId = attachData?.trial?.trial_id;
        if (trialId) {
          state.latestPersistedCompareTrialByNode[state.activeNode] = trialId;
          state.currentCompareTrialIdByNode[state.activeNode] = trialId;
          setActiveCompareView(state.activeNode, {
            source: "standalone",
            trial: attachData.trial,
            result: compareResult.value,
            trialId: attachData.trial?.trial_id,
            sessionId: "",
            inputPreview: attachData.trial?.input_excerpt || buildInputPreview(currentText.value),
          });
          trialSourceLabel = "当前 Compare 自动保存的独立 Trial";
        }
      }
      if (!trialId && compareResult.value) {
        if (currentCompareTrialId.value) {
          trialId = currentCompareTrialId.value;
          trialSourceLabel = "当前页面仍显示的上一条 Compare 对应 Trial";
        } else {
          const attachBody = {
            result: compareResult.value,
            persist_without_session: true,
          };
          const attachData = await fetchJson(`${API_ENDPOINTS.compare}/attach`, {
            method: "POST",
            body: JSON.stringify(attachBody),
          });
          trialId = attachData?.trial?.trial_id;
          if (trialId) {
            state.latestPersistedCompareTrialByNode[state.activeNode] = trialId;
            state.currentCompareTrialIdByNode[state.activeNode] = trialId;
            setActiveCompareView(state.activeNode, {
              source: "standalone",
              trial: attachData.trial,
              result: compareResult.value,
              trialId: attachData.trial?.trial_id,
              sessionId: "",
              inputPreview: attachData.trial?.input_excerpt || buildInputPreview(currentText.value),
            });
            trialSourceLabel = "上一条 Compare 自动保存的独立 Trial";
          }
        }
      }
      if (!trialId && latestCompareTrialId.value) {
        trialId = latestCompareTrialId.value;
        trialSourceLabel = "历史持久化 Trial";
      }
      if (!trialId) throw new Error("没有可用于 Judge 的 compare 结果。请先运行 Compare，再排队 Judge Request。");
      const request = await fetchJson(API_ENDPOINTS.judgeRequests, {
        method: "POST",
        body: JSON.stringify({
          trial_id: trialId,
          judge_config_snapshot_json: buildJudgeConfigSnapshot(),
          notes: currentJudgeDraft.value.notes,
        }),
      });
      pendingJudgeRequestId.value = request.judge_request_id;
      await loadJudgeRequests({ trialId });
      if (!autoExecute) {
        await syncSelectedJudgeRequestForActiveCompare({
          preferredId: request.judge_request_id,
          autoLoadDetail: true,
        });
      }
      await loadRecentTrials();
      if (selectedSessionId.value) {
        await loadSessionDetail(selectedSessionId.value);
      }
      comparePanelTab.value = "judge";
      if (autoExecute) {
        await executeJudgeRequest(request.judge_request_id);
        startJudgeRequestPolling();
        return;
      }
      startJudgeRequestPolling();
      setFeedback({ info: `Judge request 已创建：${request.judge_request_id}（来源：${trialSourceLabel || trialId}）。点击"执行这条 Request"开始评审。` });
    } catch (error) {
      setFeedback({ error: error.message });
    } finally {
      loading.queueJudge = false;
    }
  }

  async function openSessionTrialInCompare(trialId, { openJudge = false, judgeRequestId = "" } = {}) {
    await openCompareTrialInWorkbench(trialId, {
      source: "session",
      switchWorkspace: true,
      openJudge,
      judgeRequestId,
    });
  }

  async function deleteSession(sessionId) {
    if (!sessionId) return;
    const confirmed = window.confirm("删除整个 Session 会同时删除其下全部 compare 与 Judge 结果，且不可恢复。确认删除？");
    if (!confirmed) return;
    setFeedback();
    try {
      await fetchJson(`${API_ENDPOINTS.sessions}/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
      const deletedActiveCompare = activeCompareTrial.value?.session_id === sessionId;
      delete state.sessionDetailsById[sessionId];
      if (selectedSessionId.value === sessionId) {
        selectedSessionId.value = "";
      }
      for (const [trialId, detail] of Object.entries(state.selectedTrialDetailsById)) {
        if (detail?.trial?.session_id === sessionId) {
          delete state.selectedTrialDetailsById[trialId];
        }
      }
      if (deletedActiveCompare) {
        clearActiveCompareView(state.activeNode, { preserveLatestTrial: false });
        selectedJudgeRequestId.value = "";
        pendingJudgeRequestId.value = "";
      }
      await loadSessions();
      await loadJudgeRequests({ trialId: activeCompareTrial.value?.trial_id || "" });
      await loadRecentTrials();
      setFeedback({ info: "Session 已删除，对应 compare 与 Judge 结果已一并移除。" });
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  async function deleteTrial(trialId) {
    if (!trialId) return;
    const confirmed = window.confirm("删除这条 compare 会同时删除其挂载的 Judge 结果与 artifact，且不可恢复。确认删除？");
    if (!confirmed) return;
    setFeedback();
    try {
      await fetchJson(`${API_ENDPOINTS.trials}/${encodeURIComponent(trialId)}`, { method: "DELETE" });
      if (activeCompareTrial.value?.trial_id === trialId) {
        clearActiveCompareView(state.activeNode, { preserveLatestTrial: false });
        selectedJudgeRequestId.value = "";
        pendingJudgeRequestId.value = "";
      }
      if (selectedSessionTrialId.value === trialId && selectedSessionId.value) {
        state.selectedTrialIdBySession[selectedSessionId.value] = "";
      }
      delete state.selectedTrialDetailsById[trialId];
      await loadJudgeRequests({ trialId: activeCompareTrial.value?.trial_id || "" });
      await loadRecentTrials();
      if (selectedSessionId.value) {
        await loadSessionDetail(selectedSessionId.value);
      }
      setFeedback({ info: "这条 compare 已删除，所挂 Judge 结果也已一并移除。" });
    } catch (error) {
      setFeedback({ error: error.message });
    }
  }

  function resetDraftToBaseline() {
    if (!baselineConfig.value) return;
    state.candidateDraftsByNode[state.activeNode] = defaultCandidateDraft(state.activeNode, baselineConfig.value);
  }

  function selectSavedCandidate(candidateId) {
    const selected = currentSavedCandidates.value.find((item) => item.candidate_id === candidateId);
    if (!selected) return;
    const manifest = selected.normalized_manifest_json || {};
    const draft = currentDraft.value;
    draft.candidate_id = selected.candidate_id;
    draft.label = selected.label;
    draft.description = selected.description || "";
    draft.instruction_text = manifest?.instruction_override?.text || selected.instruction_layer_json?.text || draft.instruction_text;
    draft.policy_lines = manifest?.policy_override?.lines || selected.policy_layer_json?.lines || [];
    draft.few_shot_mode = manifest?.few_shot_override?.few_shot_mode || selected.few_shot_layer_json?.few_shot_mode || "baseline";
    draft.examples = manifest?.few_shot_override?.examples || selected.few_shot_layer_json?.examples || [];
    draft.examples_edit_mode = selected.few_shot_layer_json?.edit_mode || "structured";
    draft.examples_raw_text = JSON.stringify(draft.examples || [], null, 2);
    draft.model_profile = manifest?.model_selection?.default_profile || selected.model_layer_json?.model_profile || "";
    draft.notes = selected.notes || "";
  }

  function selectSavedJudgeConfig(judgeConfigId) {
    const selected = currentSavedJudgeConfigs.value.find((item) => item.judge_config_id === judgeConfigId);
    if (!selected) return;
    const normalized = selected.normalized_config_json || {};
    if (normalized.preset_id && currentJudgePresets.value.some((item) => item.preset_id === normalized.preset_id)) {
      applyJudgePreset(normalized.preset_id);
    }
    const draft = currentJudgeDraft.value;
    draft.judge_config_id = selected.judge_config_id;
    draft.label = selected.label;
    draft.description = selected.description || "";
    draft.judge_mode = judgeModeAllowedForNode(state.activeNode, selected.judge_mode)
      ? selected.judge_mode
      : defaultJudgeModeForNode(state.activeNode);
    draft.preset_id = normalized.preset_id || "";
    draft.judge_strategy = normalized.judge_strategy || "";
    draft.judge_method = normalized.judge_method || "";
    draft.preset_summary = normalized.preset_summary || "";
    draft.packet_policy_json = JSON.stringify(normalized.packet_policy_json || {}, null, 2);
    draft.rubric_bundle_json = JSON.stringify(normalized.rubric_bundle_json || {}, null, 2);
    draft.probe_appendix_json = JSON.stringify(normalized.probe_appendix_json || {}, null, 2);
    draft.persona_text = selected.persona_json?.description || "";
    draft.system_prompt = selected.prompt_templates_json?.system_prompt || "";
    draft.user_prompt = selected.prompt_templates_json?.user_prompt || "";
    draft.rubric_json = JSON.stringify(selected.rubric_source_json || {}, null, 2);
    draft.output_schema_json = JSON.stringify(selected.output_schema_json || {}, null, 2);
    draft.parameters_json = JSON.stringify(selected.parameters_json || {}, null, 2);
    draft.judger_models = [
      selected.judger_models_json?.[0]?.profile_name || "",
      selected.judger_models_json?.[1]?.profile_name || "",
      selected.judger_models_json?.[2]?.profile_name || "",
    ];
    draft.notes = selected.notes || "";
  }

  function selectedTrialResult(trialId) {
    return state.selectedTrialDetailsById[trialId]?.result || null;
  }

  function selectedSessionTrialResult() {
    return state.selectedTrialDetailsById[selectedSessionTrialId.value]?.result || null;
  }

  return {
    fetchJson, buildCandidateOverride, buildRunRequest,
    buildJudgeConfigSnapshot, applyJudgeModeTemplate, applyJudgePreset,
    loadModelProfiles, loadBaselineConfig, loadCandidates,
    loadJudgeConfigs, loadJudgePresets, loadJudgeRequests,
    syncSelectedJudgeRequestForActiveCompare,
    loadSessions, loadRecentTrials, loadSessionDetail, loadTrialDetail,
    openCompareTrialInWorkbench, goStartCompareFromEmpty,
    openCurrentSessionWorkspace, clearSessionAttachment, selectSession, updateSession,
    saveCandidateDraft, saveJudgeConfig, runSingle, saveSingleRunToHistory,
    buildCandidateRegistryEntryFromResult, attachCurrentCompareToSession,
    addCurrentCompareToSession, createSessionAndAddCurrentCompare,
    stopJudgeRequestPolling, startJudgeRequestPolling,
    loadJudgeRequestDetail, cancelJudgeRequest, retryJudgeRequest,
    executeJudgeRequest, runCompare, queueJudgeCompare,
    openSessionTrialInCompare, deleteSession, deleteTrial,
    resetDraftToBaseline, selectSavedCandidate, selectSavedJudgeConfig,
    selectedTrialResult, selectedSessionTrialResult,
  };
}

export function useNodeLabApi() {
  const injected = inject(NODE_LAB_API_KEY);
  if (!injected) throw new Error("NodeLab API not provided");
  return injected;
}
