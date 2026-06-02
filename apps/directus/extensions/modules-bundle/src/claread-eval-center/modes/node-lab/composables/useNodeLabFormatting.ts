import {
  NODE_OPTIONS,
  WORKSPACE_OPTIONS,
  JUDGE_MODES,
  JUDGE_MODES_BY_NODE,
  READING_GOAL_OPTIONS,
  READING_VARIANTS_BY_GOAL,
} from "../composables/useNodeLabConstants";

export function defaultVariantForGoal(goalId) {
  return READING_VARIANTS_BY_GOAL[goalId]?.[0]?.id || "intermediate_reading";
}

export function normalizeGoal(candidateGoal) {
  return READING_GOAL_OPTIONS.some((item) => item.id === candidateGoal) ? candidateGoal : "daily_reading";
}

export function normalizeVariantForGoal(goalId, candidateVariant) {
  const options = READING_VARIANTS_BY_GOAL[goalId] || [];
  if (options.some((item) => item.id === candidateVariant)) return candidateVariant;
  return defaultVariantForGoal(goalId);
}

export function nodeLabel(nodeName) {
  return NODE_OPTIONS.find((item) => item.id === nodeName)?.label || nodeName;
}

export function workspaceLabel(workspaceId) {
  return WORKSPACE_OPTIONS.find((item) => item.id === workspaceId)?.label || workspaceId;
}

export function readingGoalLabel(goalId) {
  return READING_GOAL_OPTIONS.find((item) => item.id === goalId)?.label || goalId;
}

export function readingVariantLabel(variantId) {
  for (const variants of Object.values(READING_VARIANTS_BY_GOAL)) {
    const found = variants.find((item) => item.id === variantId);
    if (found) return found.label;
  }
  return variantId;
}

export function shortId(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "—";
  return normalized.length <= 10 ? normalized : normalized.slice(-8);
}

export function normalizePreviewText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

export function buildInputPreview(value, limit = 280) {
  return normalizePreviewText(value).slice(0, limit);
}

export function safeJsonParse(raw, fallback) {
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function formatJson(value) {
  if (value == null) return "";
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

export function formatClockTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function isStructuredJsonValue(value) {
  return Array.isArray(value) || (value !== null && typeof value === "object");
}

export function quickValidationLabel(validation) {
  if (!validation) return "未校验";
  if (validation.status === "pass") return "通过";
  if (validation.status === "warning") return `${validation.warning_count || 0} 条警告`;
  if (validation.status === "error") return "校验异常";
  return validation.status || "未校验";
}

export function resultIssue(entry, participantLabel = "") {
  if (!entry || !entry.status || entry.status === "succeeded") return null;
  const isTimeout = entry.status === "timeout";
  const label = participantLabel ? `${participantLabel} ` : "";
  return {
    title: isTimeout ? `${label}执行超时` : `${label}执行失败`,
    detail: entry.error?.message || (isTimeout ? "模型调用超过超时时间，未返回有效结果。" : "模型运行没有返回可用输出。"),
    tone: isTimeout ? "warning" : "danger",
    debug: {
      status: entry.status,
      error: entry.error || null,
      trace_refs: entry.trace_refs || null,
      runtime_summary: entry.runtime_summary || null,
      model_identity: entry.model_identity || null,
      prompt_identity: entry.prompt_identity || null,
      warnings: entry.warnings || [],
    },
  };
}

export function statusLabel(status) {
  const map = {
    succeeded: "成功",
    failed: "失败",
    timeout: "超时",
    cancelled: "已取消",
    queued: "排队中",
    running: "运行中",
    complete: "完整完成",
    partial_failure: "部分失败",
    total_failure: "全部失败",
    drafting: "草稿中",
    active: "进行中",
    paused: "已暂停",
    reviewed: "已复盘",
    archived: "已归档",
    unreviewed: "未评审",
  };
  return map[status] || status || "未记录";
}

export function statusTone(status) {
  if (["succeeded", "complete", "active", "reviewed"].includes(status)) return "success";
  if (["partial_failure", "paused", "queued", "running"].includes(status)) return "warning";
  if (["failed", "total_failure", "cancelled"].includes(status)) return "danger";
  if (["timeout"].includes(status)) return "attention";
  return "neutral";
}

export function hasNodeActivity(nodeName, { singleRunResultsByNode, compareResultsByNode, sessionsByNode, latestPersistedCompareTrialByNode }) {
  return Boolean(
    singleRunResultsByNode[nodeName]
    || compareResultsByNode[nodeName]
    || (sessionsByNode[nodeName] || []).length > 0
    || latestPersistedCompareTrialByNode[nodeName],
  );
}

export function compactFactRows(rows) {
  return rows.filter(([, value]) => value !== undefined && value !== null && value !== "");
}

export function formatRuntimeTokens(summary) {
  const aggregate = summary?.aggregate || {};
  if (!Number.isFinite(aggregate.total_tokens)) return "未记录";
  const input = Number.isFinite(aggregate.input_tokens) ? aggregate.input_tokens : "—";
  const output = Number.isFinite(aggregate.output_tokens) ? aggregate.output_tokens : "—";
  return `${aggregate.total_tokens}（输入 ${input} / 输出 ${output}）`;
}

export function formatDurationMs(value) {
  if (!Number.isFinite(value)) return "未记录";
  return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} s`;
}

export function buildPromptPacketSections(resultEntry) {
  if (!resultEntry) return [];
  return [
    { key: "instructions", title: "发送给模型的说明", value: resultEntry.agent_instructions || "未记录" },
    { key: "runtime_prompt", title: "运行时 Prompt", value: resultEntry.prompt_preview || "未记录" },
    { key: "examples", title: "示例输入", value: formatJson(resultEntry.example_summary || null) },
    { key: "prepared_sentences", title: "预处理后的句子", value: formatJson(resultEntry.prepared_sentences || []) },
  ];
}

export function sentenceOrderKey(sentenceId) {
  const raw = String(sentenceId || "");
  const match = raw.match(/(\d+)/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

export function compareDeltaTone(value, kind) {
  if (!Number.isFinite(value) || value === 0) return "neutral";
  if (kind === "latency") return value < 0 ? "success" : "danger";
  if (kind === "tokens") return value < 0 ? "success" : "warning";
  return "neutral";
}

export function formatSignedDelta(value, suffix = "") {
  if (!Number.isFinite(value) || value === 0) return "持平";
  return `${value > 0 ? "+" : ""}${value}${suffix}`;
}

export function compareTrialSourceLabel(trial) {
  if (!trial) return "未记录";
  return trial.source_kind === "session" || trial.session_id ? "Session" : "独立 Trial";
}

export function compareViewSourceLabel(view, trial) {
  if (view?.source === "live") return "当前结果";
  if (view?.source === "session" || trial?.source_kind === "session" || trial?.session_id) return "Session 内结果";
  if (view?.source === "standalone") return "独立 Trial";
  if (view?.source === "recent") return "历史 Trial";
  return trial?.session_id ? "Session 内结果" : "历史 Trial";
}

export function compareViewSourceTone(view, trial, mismatch) {
  if (mismatch) return "warning";
  if (view?.source === "live") return "success";
  if (view?.source === "session") return "active";
  if (view?.source === "standalone") return "neutral";
  return trial?.session_id ? "active" : "neutral";
}

export function trialJudgeCount(trial) {
  return Number(trial?.judge_request_count || 0);
}

export function trialReadableTitle(trial, fallbackIndex = 0) {
  if (!trial) return "未记录 Trial";
  if (trial.session_title) return `${trial.session_title} · #${shortId(trial.trial_id)}`;
  return `${compareTrialSourceLabel(trial)} · #${shortId(trial.trial_id || fallbackIndex)}`;
}

export function trialReadableMeta(trial) {
  if (!trial) return "未记录";
  const parts = [
    statusLabel(trial.result_summary_json?.result_status?.compare_status || trial.status),
    readingVariantLabel(trial.reading_variant || ""),
    trialJudgeCount(trial) > 0 ? `${trialJudgeCount(trial)} 条 Judge` : "未 Judge",
  ].filter(Boolean);
  return parts.join(" · ");
}

export function sessionCompareCount(session) {
  const aggregate = session?.aggregate_summary_json || {};
  return Number(aggregate.workspace_counts?.baseline_compare || aggregate.trial_count || 0);
}

export function sessionJudgeCount(session, sessionDetailsById) {
  const aggregate = session?.aggregate_summary_json || {};
  const loaded = sessionDetailsById?.[session?.session_id]?.judge_requests?.length;
  return Number(loaded || session?.judge_request_count || aggregate.judge_request_count || 0);
}

export function sessionBaselineLabel(session) {
  return session?.baseline_snapshot_json?.prompt_profile || "baseline";
}

export function statusBadgeLabel(entry) {
  if (!entry) return "未运行";
  return statusLabel(entry.status);
}

export function groupEntriesBySentence(items) {
  const map = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const sentenceId = String(item?.sentence_id || "");
    if (!sentenceId) continue;
    if (!map.has(sentenceId)) map.set(sentenceId, []);
    map.get(sentenceId).push(item);
  }
  return map;
}

export function compareSentenceModel(entry, nodeName) {
  const preparedSentences = Array.isArray(entry?.prepared_sentences) ? entry.prepared_sentences : [];
  const sentenceMap = new Map(
    preparedSentences
      .filter((item) => item && item.sentence_id)
      .map((item) => [String(item.sentence_id), String(item.text || "")]),
  );
  const output = entry?.node_output || {};
  if (nodeName === "grammar") {
    return {
      sentenceMap,
      notes: groupEntriesBySentence(output.grammar_notes),
      analyses: groupEntriesBySentence(output.sentence_analyses),
    };
  }
  if (nodeName === "vocabulary") {
    return {
      sentenceMap,
      vocabHighlights: groupEntriesBySentence(output.vocab_highlights),
      phraseGlosses: groupEntriesBySentence(output.phrase_glosses),
      contextGlosses: groupEntriesBySentence(output.context_glosses),
    };
  }
  return {
    sentenceMap,
    translations: groupEntriesBySentence(output.sentence_translations),
  };
}

export function sentenceToneClass(index) {
  const tones = [
    "tone-amber",
    "tone-blue",
    "tone-green",
    "tone-violet",
    "tone-rose",
    "tone-slate",
  ];
  return tones[index % tones.length];
}

export function defaultJudgeModeForNode(nodeName) {
  return JUDGE_MODES_BY_NODE[nodeName]?.[0] || "rubric_plus_pairwise";
}

export function judgeModeAllowedForNode(nodeName, modeId) {
  return (JUDGE_MODES_BY_NODE[nodeName] || []).includes(modeId);
}

export function defaultCandidateDraft(nodeName, baselineConfig = null) {
  return {
    candidate_id: "",
    label: `${nodeName} candidate`,
    description: "",
    instruction_text: baselineConfig?.agent_instructions || "",
    policy_lines: Array.isArray(baselineConfig?.policy_lines) ? [...baselineConfig.policy_lines] : [],
    few_shot_mode: "baseline",
    examples: Array.isArray(baselineConfig?.baseline_examples) ? [...baselineConfig.baseline_examples] : [],
    examples_edit_mode: "structured",
    examples_raw_text: JSON.stringify(baselineConfig?.baseline_examples || [], null, 2),
    model_profile: "",
    notes: "",
  };
}

export function defaultJudgeDraft(nodeName) {
  return {
    judge_config_id: "",
    preset_id: "",
    judge_strategy: "",
    judge_method: "",
    label: `${nodeName} judge setup`,
    description: "",
    judge_mode: defaultJudgeModeForNode(nodeName),
    preset_summary: "",
    packet_policy_json: "{}",
    rubric_bundle_json: "{}",
    probe_appendix_json: "{}",
    persona_text: "",
    system_prompt: "",
    user_prompt: "",
    rubric_json: JSON.stringify({
      criteria: [
        { criterion_id: "instructional_value", label: "Instructional Value", score_type: "binary", description: "TODO" },
        { criterion_id: "anti_template", label: "Anti-template", score_type: "binary", description: "TODO" },
      ],
    }, null, 2),
    output_schema_json: JSON.stringify({
      rubric_scoring: {},
      pairwise_review: {},
      consensus: {},
    }, null, 2),
    parameters_json: JSON.stringify({ temperature: 0, max_tokens: 2000 }, null, 2),
    judger_models: ["", "", ""],
    notes: "",
  };
}

export function parseNestedJson(obj) {
  if (obj === null || obj === undefined) return obj;
  if (typeof obj === 'string') {
    const trimmed = obj.trim();
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
        (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        return parseNestedJson(JSON.parse(trimmed));
      } catch(e) {
        return obj;
      }
    }
    return obj;
  }
  if (Array.isArray(obj)) {
    return obj.map(item => parseNestedJson(item));
  }
  if (typeof obj === 'object') {
    const newObj = {};
    for (const [key, value] of Object.entries(obj)) {
      newObj[key] = parseNestedJson(value);
    }
    return newObj;
  }
  return obj;
}

export function judgeRequestResultMode(detail) {
  if (detail?.result?.probe_appendix_result && !detail?.result?.rubric_scoring_result) return "probe";
  if (detail?.result?.rubric_scoring_result) return "rubric";
  return "empty";
}

export function judgeAggregatePassRateText(side) {
  const passRate = side?.aggregate?.pass_rate;
  if (!Number.isFinite(passRate)) return "未记录";
  return `${Math.round(passRate * 100)}%`;
}

export function judgeItemResultLabel(item) {
  return item?.label || item?.item_type || item?.item_id || "未命名条目";
}

export function judgeRequestIssue(detail) {
  const error = detail?.request?.error_json;
  if (!error) return null;
  return {
    code: error.code || "JudgeRequestError",
    message: error.message || "Judge request 执行失败。",
  };
}

export function judgeStepRunFacts(stepRun) {
  if (!stepRun) return [];
  return compactFactRows([
    ["状态", statusLabel(stepRun.status)],
    ["耗时", formatDurationMs(stepRun.runtime_summary?.latency_ms)],
    ["Tokens", formatRuntimeTokens(stepRun.runtime_summary)],
    ["模型", stepRun.model_identity?.model_name || "未记录"],
  ]);
}
