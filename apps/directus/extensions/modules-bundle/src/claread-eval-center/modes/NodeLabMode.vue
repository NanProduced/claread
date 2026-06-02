<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import JsonTreeView from "../components/JsonTreeView.vue";
import NodeProbeOutputView from "../components/NodeProbeOutputView.vue";
import ResultBlock from "../components/ResultBlock.vue";
import XmlPromptViewer from "../components/XmlPromptViewer.vue";

const modelProfilesEndpoint = "/eval-center/article-analysis/model-profiles";
const baselineConfigEndpoint = "/eval-center/node-lab/baseline-config";
const candidatesEndpoint = "/eval-center/node-lab/candidates";
const sessionsEndpoint = "/eval-center/node-lab/sessions";
const runEndpoint = "/eval-center/node-lab/run";
const compareEndpoint = "/eval-center/node-lab/compare";
const trialsEndpoint = "/eval-center/node-lab/trials";
const judgeConfigsEndpoint = "/eval-center/node-lab/judge-configs";
const judgePresetsEndpoint = "/eval-center/node-lab/judge-presets";
const judgeRequestsEndpoint = "/eval-center/node-lab/judge-requests";
const storageKey = "claread-eval-center:node-lab:v4";
const defaultNodeLabTimeoutSeconds = 150;

const nodeOptions = [
  { id: "grammar", label: "Grammar", description: "语法与长难句拆解实验。" },
  { id: "vocabulary", label: "Vocabulary", description: "词汇与语境释义实验。" },
  { id: "translation", label: "Translation", description: "句级翻译与语气策略实验。" },
];

const workspaceOptions = [
  { id: "single_run", label: "Single Run", description: "先看单次输出是否朝正确方向变化。" },
  { id: "baseline_compare", label: "Baseline Compare", description: "同一输入下比较 baseline 与 candidate。" },
  { id: "sessions", label: "Sessions", description: "查看该 node 的实验历史与复盘。" },
];

const judgeModes = [
  { id: "rubric_score_only", label: "只按规则打分（逐项过线检查）" },
  { id: "rubric_plus_pairwise", label: "规则打分 + 整体对比评估（先评分，再给整体意见）" },
  { id: "anti_template_probe", label: "反模板化专项诊断（Grammar 专用）" },
  { id: "raw", label: "完全自定义（自己写 Judge Prompt / Schema）" },
];

const judgeModesByNode = {
  grammar: ["rubric_score_only", "rubric_plus_pairwise", "anti_template_probe", "raw"],
  vocabulary: ["rubric_score_only", "rubric_plus_pairwise", "raw"],
  translation: ["rubric_score_only", "rubric_plus_pairwise", "raw"],
};

const readingGoalOptions = [
  { id: "daily_reading", label: "日常阅读", description: "用于新闻、通识文章和长期阅读训练。" },
  { id: "exam", label: "考试阅读", description: "用于 CET、考研、雅思托福等应试型场景。" },
];

const readingVariantsByGoal = {
  daily_reading: [
    { id: "beginner_reading", label: "入门阅读" },
    { id: "intermediate_reading", label: "中阶阅读" },
    { id: "intensive_reading", label: "精读模式" },
  ],
  exam: [
    { id: "gaokao", label: "高考阅读" },
    { id: "cet", label: "四六级阅读" },
    { id: "kaoyan", label: "考研阅读" },
    { id: "tem", label: "专四专八" },
    { id: "ielts_toefl", label: "雅思 / 托福" },
  ],
};

const helpText = {
  reading_goal: "先选阅读目标，再缩小到具体变体。goal 会影响 prompt profile、语法颗粒度、词汇策略和翻译风格。",
  reading_variant: "阅读变体决定当前实验使用哪套阅读规则。这里只展示后端实际支持的变体，避免前端可选但运行时报错。",
  baseline_snapshot: "Baseline 是 Claread 当前真实配置的只读快照，用来做参考和对比，不在这里直接编辑。",
  candidate_delta: "这里显示 Candidate 相对 baseline 的变化轴。先看哪些层被改动，再决定是否运行或写入 Session。",
  prompt_snapshot: "Prompt Snapshot 是本次运行对应的快照标识。baseline 没有 candidate snapshot 时会显示为 baseline。",
  few_shot_mode: "Few-shot 只控制当前 node 的示例来源。grammar 支持 RAG 观测，其他 node 仍只支持 baseline / off / candidate。",
  compare_status: "Compare Status 关注这次对比是否完整完成，而不是只看 candidate 一侧是否成功。",
  latency: "Single Run 看单次延迟。Compare 看 baseline 与 candidate 的各自延迟，以及两者差值。",
  session_write: "Single Run 不再进入 Session。Session 仅在 Baseline Compare 中由 compare 结果加入，固定 node、阅读目标/变体与 baseline 参考系。",
  prompt_packet: "这里展示真正发给模型的关键信息，包括说明文本、示例输入和预处理后的句子。",
  judge_prerequisite: "Judge 不是重新跑 compare，而是基于一条已保存的 Compare 结果继续做评审。先人工看 compare 是否值得，再决定是否花 token 发起 judge。",
};

const sessionFlowSteps = [
  { key: "start", title: "先跑出一条 Compare", detail: "Baseline Compare 是主工作台，先确认这次差异值不值得保存或评审。" },
  { key: "record", title: "再决定是否加入 Session", detail: "Session 是固定上下文的 compare 记录本，只收 compare trial。" },
  { key: "review", title: "最后回来复盘 / Judge", detail: "在 Sessions 里回看时间线，也可以回到 Compare 页继续做 Judge。" },
];

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

function defaultVariantForGoal(goalId) {
  return readingVariantsByGoal[goalId]?.[0]?.id || "intermediate_reading";
}

function normalizeGoal(candidateGoal) {
  return readingGoalOptions.some((item) => item.id === candidateGoal) ? candidateGoal : "daily_reading";
}

function normalizeVariantForGoal(goalId, candidateVariant) {
  const options = readingVariantsByGoal[goalId] || [];
  if (options.some((item) => item.id === candidateVariant)) return candidateVariant;
  return defaultVariantForGoal(goalId);
}

function nodeLabel(nodeName) {
  return nodeOptions.find((item) => item.id === nodeName)?.label || nodeName;
}

function workspaceLabel(workspaceId) {
  return workspaceOptions.find((item) => item.id === workspaceId)?.label || workspaceId;
}

function readingGoalLabel(goalId) {
  return readingGoalOptions.find((item) => item.id === goalId)?.label || goalId;
}

function readingVariantLabel(variantId) {
  for (const variants of Object.values(readingVariantsByGoal)) {
    const found = variants.find((item) => item.id === variantId);
    if (found) return found.label;
  }
  return variantId;
}

function shortId(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "—";
  return normalized.length <= 10 ? normalized : normalized.slice(-8);
}

function normalizePreviewText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function buildInputPreview(value, limit = 280) {
  return normalizePreviewText(value).slice(0, limit);
}

function safeJsonParse(raw, fallback) {
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function formatJson(value) {
  if (value == null) return "";
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

function formatClockTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function isStructuredJsonValue(value) {
  return Array.isArray(value) || (value !== null && typeof value === "object");
}

function quickValidationLabel(validation) {
  if (!validation) return "未校验";
  if (validation.status === "pass") return "通过";
  if (validation.status === "warning") return `${validation.warning_count || 0} 条警告`;
  if (validation.status === "error") return "校验异常";
  return validation.status || "未校验";
}

function resultIssue(entry, participantLabel = "") {
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

function statusLabel(status) {
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

function statusTone(status) {
  if (["succeeded", "complete", "active", "reviewed"].includes(status)) return "success";
  if (["partial_failure", "paused", "queued", "running"].includes(status)) return "warning";
  if (["failed", "total_failure", "cancelled"].includes(status)) return "danger";
  if (["timeout"].includes(status)) return "attention";
  return "neutral";
}

function hasNodeActivity(nodeName) {
  return Boolean(
    state.singleRunResultsByNode[nodeName]
    || state.compareResultsByNode[nodeName]
    || (state.sessionsByNode[nodeName] || []).length > 0
    || state.latestPersistedCompareTrialByNode[nodeName],
  );
}

function compactFactRows(rows) {
  return rows.filter(([, value]) => value !== undefined && value !== null && value !== "");
}

function formatRuntimeTokens(summary) {
  const aggregate = summary?.aggregate || {};
  if (!Number.isFinite(aggregate.total_tokens)) return "未记录";
  const input = Number.isFinite(aggregate.input_tokens) ? aggregate.input_tokens : "—";
  const output = Number.isFinite(aggregate.output_tokens) ? aggregate.output_tokens : "—";
  return `${aggregate.total_tokens}（输入 ${input} / 输出 ${output}）`;
}

function formatDurationMs(value) {
  if (!Number.isFinite(value)) return "未记录";
  return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} s`;
}

function buildPromptPacketSections(resultEntry) {
  if (!resultEntry) return [];
  return [
    { key: "instructions", title: "发送给模型的说明", value: resultEntry.agent_instructions || "未记录" },
    { key: "runtime_prompt", title: "运行时 Prompt", value: resultEntry.prompt_preview || "未记录" },
    { key: "examples", title: "示例输入", value: formatJson(resultEntry.example_summary || null) },
    { key: "prepared_sentences", title: "预处理后的句子", value: formatJson(resultEntry.prepared_sentences || []) },
  ];
}

function sentenceOrderKey(sentenceId) {
  const raw = String(sentenceId || "");
  const match = raw.match(/(\d+)/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function compareDeltaTone(value, kind) {
  if (!Number.isFinite(value) || value === 0) return "neutral";
  if (kind === "latency") return value < 0 ? "success" : "danger";
  if (kind === "tokens") return value < 0 ? "success" : "warning";
  return "neutral";
}

function formatSignedDelta(value, suffix = "") {
  if (!Number.isFinite(value) || value === 0) return "持平";
  return `${value > 0 ? "+" : ""}${value}${suffix}`;
}

function compareTrialSourceLabel(trial) {
  if (!trial) return "未记录";
  return trial.source_kind === "session" || trial.session_id ? "Session" : "独立 Trial";
}

function compareViewSourceLabel(view, trial) {
  if (view?.source === "live") return "当前结果";
  if (view?.source === "session" || trial?.source_kind === "session" || trial?.session_id) return "Session 内结果";
  if (view?.source === "standalone") return "独立 Trial";
  if (view?.source === "recent") return "历史 Trial";
  return trial?.session_id ? "Session 内结果" : "历史 Trial";
}

function compareViewSourceTone(view, trial, mismatch) {
  if (mismatch) return "warning";
  if (view?.source === "live") return "success";
  if (view?.source === "session") return "active";
  if (view?.source === "standalone") return "neutral";
  return trial?.session_id ? "active" : "neutral";
}

function trialJudgeCount(trial) {
  return Number(trial?.judge_request_count || 0);
}

function trialReadableTitle(trial, fallbackIndex = 0) {
  if (!trial) return "未记录 Trial";
  if (trial.session_title) return `${trial.session_title} · #${shortId(trial.trial_id)}`;
  return `${compareTrialSourceLabel(trial)} · #${shortId(trial.trial_id || fallbackIndex)}`;
}

function trialReadableMeta(trial) {
  if (!trial) return "未记录";
  const parts = [
    statusLabel(trial.result_summary_json?.result_status?.compare_status || trial.status),
    readingVariantLabel(trial.reading_variant || ""),
    trialJudgeCount(trial) > 0 ? `${trialJudgeCount(trial)} 条 Judge` : "未 Judge",
  ].filter(Boolean);
  return parts.join(" · ");
}

function sessionCompareCount(session) {
  const aggregate = session?.aggregate_summary_json || {};
  return Number(aggregate.workspace_counts?.baseline_compare || aggregate.trial_count || 0);
}

function sessionJudgeCount(session) {
  const aggregate = session?.aggregate_summary_json || {};
  const loaded = state.sessionDetailsById?.[session?.session_id]?.judge_requests?.length;
  return Number(loaded || session?.judge_request_count || aggregate.judge_request_count || 0);
}

function sessionBaselineLabel(session) {
  return session?.baseline_snapshot_json?.prompt_profile || "baseline";
}

function statusBadgeLabel(entry) {
  if (!entry) return "未运行";
  return statusLabel(entry.status);
}

function groupEntriesBySentence(items) {
  const map = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const sentenceId = String(item?.sentence_id || "");
    if (!sentenceId) continue;
    if (!map.has(sentenceId)) map.set(sentenceId, []);
    map.get(sentenceId).push(item);
  }
  return map;
}

function compareSentenceModel(entry, nodeName) {
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

function sentenceToneClass(index) {
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

function defaultJudgeModeForNode(nodeName) {
  return judgeModesByNode[nodeName]?.[0] || "rubric_plus_pairwise";
}

function judgeModeAllowedForNode(nodeName, modeId) {
  return (judgeModesByNode[nodeName] || []).includes(modeId);
}

function defaultCandidateDraft(nodeName, baselineConfig = null) {
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

function defaultJudgeDraft(nodeName) {
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

const availableReadingVariants = computed(() => readingVariantsByGoal[currentReadingGoal.value] || []);
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
  const allowed = new Set(judgeModesByNode[state.activeNode] || []);
  return judgeModes.filter((mode) => allowed.has(mode.id));
});
const singleRunResult = computed(() => state.singleRunResultsByNode[state.activeNode] || null);
const singleRunUiState = computed(() => state.singleRunUiStateByNode[state.activeNode] || null);
const compareResult = computed(() => state.activeCompareViewByNode[state.activeNode]?.result || state.compareResultsByNode[state.activeNode] || null);
const compareUiState = computed(() => state.compareUiStateByNode[state.activeNode] || null);
const activeCompareView = computed(() => state.activeCompareViewByNode[state.activeNode] || null);
const activeCompareTrial = computed(() => activeCompareView.value?.trial || null);
const comparePanelTab = computed({
  get: () => state.comparePanelTabByNode[state.activeNode] || "compare",
  set: (value) => { state.comparePanelTabByNode[state.activeNode] = value || "compare"; },
});
const pendingJudgeRequestId = computed({
  get: () => state.pendingJudgeRequestIdByNode[state.activeNode] || "",
  set: (value) => { state.pendingJudgeRequestIdByNode[state.activeNode] = value || ""; },
});
const recentTrials = computed(() => state.recentTrialsByNode[state.activeNode] || []);

const selectedSessionId = computed({
  get: () => state.selectedSessionIdByNode[state.activeNode] || "",
  set: (value) => { state.selectedSessionIdByNode[state.activeNode] = value; },
});

const selectedSessionDetail = computed(() => state.sessionDetailsById[selectedSessionId.value] || null);
const selectedSessionTrialId = computed({
  get: () => state.selectedTrialIdBySession[selectedSessionId.value] || "",
  set: (value) => { state.selectedTrialIdBySession[selectedSessionId.value] = value; },
});

const selectedSessionTrialDetail = computed(() => state.selectedTrialDetailsById[selectedSessionTrialId.value] || null);
const latestCompareTrialId = computed(() => state.latestPersistedCompareTrialByNode[state.activeNode] || "");
const currentCompareTrialId = computed(() => state.currentCompareTrialIdByNode[state.activeNode] || "");
const selectedSessionJudgeRequests = computed(() => selectedSessionDetail.value?.judge_requests || []);
const selectedJudgeRequestId = computed({
  get: () => state.selectedJudgeRequestIdByNode?.[state.activeNode] || "",
  set: (value) => {
    if (!state.selectedJudgeRequestIdByNode) state.selectedJudgeRequestIdByNode = {};
    state.selectedJudgeRequestIdByNode[state.activeNode] = value || "";
  },
});
const selectedJudgeRequestDetail = computed(() => state.judgeRequestDetailsById[selectedJudgeRequestId.value] || null);

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
    detail: "这是当前选中的 notebook。后续 compare 只有在你显式点击“加入 Session”时才会写入。",
    status: statusLabel(session.status),
    tone: statusTone(session.status),
    actionLabel: "查看 Session",
  };
});

const sessionWorkspaceSummary = computed(() => {
  const detail = selectedSessionDetail.value;
  if (!detail?.session) return [];
  const workspaceCounts = detail.trials.reduce((accumulator, trial) => {
    const key = trial.workspace_type || "unknown";
    accumulator[key] = (accumulator[key] || 0) + 1;
    return accumulator;
  }, {});
  return compactFactRows([
    ["Single Run", workspaceCounts.single_run ? `${workspaceCounts.single_run} 条` : "0 条"],
    ["Baseline Compare", workspaceCounts.baseline_compare ? `${workspaceCounts.baseline_compare} 条` : "0 条"],
    ["Judge Requests", selectedSessionJudgeRequests.value.length ? `${selectedSessionJudgeRequests.value.length} 条` : "0 条"],
  ]);
});

const sessionDecisionNarrative = computed(() => {
  const detail = selectedSessionDetail.value;
  if (!detail?.session) {
    return "请先在 Baseline Compare 跑出第一条 compare，再选择加入或新建 Session。";
  }
  const aggregate = detail.session.aggregate_summary_json || {};
  if (aggregate.decision_summary) return String(aggregate.decision_summary);
  if (detail.trials.length === 0) {
    return "这个 Session 还没有 compare。请回到 Baseline Compare 跑出第一条结果并选择“新建 Session 并加入”。";
  }
  return `当前已记录 ${detail.trials.length} 条 compare，可以先看左侧时间线，再挑一条展开结构化差异与所挂 judge 结果。`;
});

const sessionPersistActionLabel = computed(() => {
  if (selectedSessionDetail.value?.session?.title) {
    return `加入当前 Session：${selectedSessionDetail.value.session.title}`;
  }
  return "新建 Session 并加入";
});

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
    return "请先选择一条 Session，或点击“新建 Session 并加入”";
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
      detail: "当前 Compare 尚未指定写入目标。Session 只在你点击“加入 Session”时才会接收这条结果。",
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

const contextFacts = computed(() => compactFactRows([
  ["当前 Node", nodeLabel(state.activeNode)],
  ["当前工作区", workspaceLabel(state.activeWorkspace)],
  ["阅读目标", readingGoalLabel(currentReadingGoal.value)],
  ["阅读变体", readingVariantLabel(currentReadingVariant.value)],
  ["当前 Candidate", currentDraft.value.label || "未命名 Candidate"],
  ["当前 Session", selectedSessionDetail.value?.session?.title || "未加入"],
]));

const latestRunSummary = computed(() => {
  if (state.activeWorkspace === "baseline_compare") {
    const result = compareResult.value;
    if (!result) return { title: "最近结果", value: "尚未运行 Compare", tone: "neutral", detail: "先运行 Compare，才能看差异摘要与 judge 前置状态。" };
    const resultStatus = result.compare_summary?.result_status || {};
    return {
      title: "最近 Compare",
      value: statusLabel(resultStatus.compare_status || "complete"),
      tone: statusTone(resultStatus.compare_status || "complete"),
      detail: `Baseline ${statusLabel(resultStatus.baseline_status)}，Candidate ${statusLabel(resultStatus.candidate_status)}。`,
    };
  }
  const result = singleRunResult.value?.run;
  if (!result) return { title: "最近 Single Run", value: "尚未运行", tone: "neutral", detail: "先运行 baseline 或 candidate，结果保留在页面状态中，不进入 Session。" };
  const refreshedAt = singleRunUiState.value?.lastCompletedAt
    ? ` · ${formatClockTime(singleRunUiState.value.lastCompletedAt)}`
    : "";
  return {
    title: "最近 Single Run",
    value: statusLabel(result.status),
    tone: statusTone(result.status),
    detail: `${result.participant_label === "baseline" ? "Baseline" : "Candidate"} · ${result.model_identity?.model_name || "默认模型"}${refreshedAt}。`,
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

const baselineSummaryFacts = computed(() => {
  const baseline = baselineConfig.value;
  if (!baseline) return [];
  return compactFactRows([
    ["Prompt Profile", baseline.prompt_profile || "未记录"],
    ["Policy Focus", baseline.policy_focus || "未记录"],
    ["Baseline Model", baseline.baseline_model_profile || "未记录"],
    ["Few-shot 来源", Array.isArray(baseline.baseline_examples) && baseline.baseline_examples.length > 0 ? `本地 examples（${baseline.baseline_examples.length} 条）` : "无本地 examples"],
  ]);
});

const candidateDiffFacts = computed(() => {
  const baseline = baselineConfig.value;
  if (!baseline) return [];
  const draft = currentDraft.value;
  const cleanPolicyLines = (draft.policy_lines || []).map((line) => String(line || "").trim()).filter(Boolean);
  const baselinePolicy = JSON.stringify(baseline.policy_lines || []);
  const candidatePolicy = JSON.stringify(cleanPolicyLines);
  const exampleCount = draft.few_shot_mode === "candidate"
    ? (draft.examples_edit_mode === "raw"
      ? (safeJsonParse(draft.examples_raw_text || "[]", []) || []).length
      : (draft.examples || []).length)
    : 0;
  return [
    {
      key: "instructions",
      label: "说明文本",
      changed: draft.instruction_text.trim() !== String(baseline.agent_instructions || "").trim(),
      value: draft.instruction_text.trim() !== String(baseline.agent_instructions || "").trim() ? "已修改" : "沿用 baseline",
    },
    {
      key: "policy",
      label: "Policy Lines",
      changed: baselinePolicy !== candidatePolicy,
      value: baselinePolicy !== candidatePolicy ? `${cleanPolicyLines.length} 行已调整` : "沿用 baseline",
    },
    {
      key: "few_shot",
      label: "Few-shot",
      changed: draft.few_shot_mode !== "baseline",
      value: draft.few_shot_mode === "candidate" ? `Candidate examples（${exampleCount} 条）` : draft.few_shot_mode === "off" ? "已关闭" : draft.few_shot_mode === "rag" ? "RAG 观测" : "沿用 baseline",
    },
    {
      key: "model",
      label: "模型",
      changed: Boolean(draft.model_profile),
      value: draft.model_profile || "沿用 baseline route",
    },
  ];
});

const singleRunSummaryFacts = computed(() => {
  const result = singleRunResult.value?.run;
  if (!result) return [];
  const facts = [
    ["参与者", result.participant_label === "baseline" ? "Baseline" : "Candidate"],
    ["状态", statusLabel(result.status)],
    ["模型", result.model_identity?.model_name || "未记录"],
    ["Few-shot", result.example_summary?.selection_mode || "未记录"],
    ["Prompt Snapshot", result.prompt_identity?.prompt_snapshot_hash || "baseline"],
    ["延迟", formatDurationMs(result.runtime_summary?.latency_ms)],
    ["Tokens", formatRuntimeTokens(result.runtime_summary)],
  ];
  return compactFactRows(facts);
});

const singleRunGrammarValidation = computed(() => {
  if (state.activeNode !== "grammar") return null;
  return singleRunResult.value?.run?.quick_validation || null;
});

const singleRunIssue = computed(() => resultIssue(singleRunResult.value?.run));

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

const comparePromptSections = computed(() => {
  const baselineSections = buildPromptPacketSections(compareResult.value?.baseline);
  const candidateSections = buildPromptPacketSections(compareResult.value?.candidate);
  const orderedKeys = [];
  const sectionMap = new Map();

  for (const section of [...baselineSections, ...candidateSections]) {
    if (!sectionMap.has(section.key)) {
      sectionMap.set(section.key, {
        key: section.key,
        title: section.title,
        baseline: "未记录",
        candidate: "未记录",
      });
      orderedKeys.push(section.key);
    }
  }

  for (const section of baselineSections) {
    sectionMap.get(section.key).baseline = section.value;
  }

  for (const section of candidateSections) {
    sectionMap.get(section.key).candidate = section.value;
  }

  return orderedKeys.map((key) => sectionMap.get(key));
});

const compareSentenceRows = computed(() => {
  const result = compareResult.value;
  if (!result) return [];
  const baselineModel = compareSentenceModel(result.baseline, state.activeNode);
  const candidateModel = compareSentenceModel(result.candidate, state.activeNode);
  const sentenceIds = new Set();
  if (state.activeNode === "grammar") {
    [...baselineModel.notes.keys(), ...baselineModel.analyses.keys(), ...candidateModel.notes.keys(), ...candidateModel.analyses.keys()].forEach((id) => sentenceIds.add(id));
  } else if (state.activeNode === "vocabulary") {
    [...baselineModel.vocabHighlights.keys(), ...baselineModel.phraseGlosses.keys(), ...baselineModel.contextGlosses.keys(), ...candidateModel.vocabHighlights.keys(), ...candidateModel.phraseGlosses.keys(), ...candidateModel.contextGlosses.keys()].forEach((id) => sentenceIds.add(id));
  } else {
    [...baselineModel.translations.keys(), ...candidateModel.translations.keys()].forEach((id) => sentenceIds.add(id));
  }

  return [...sentenceIds]
    .sort((left, right) => sentenceOrderKey(left) - sentenceOrderKey(right) || String(left).localeCompare(String(right)))
    .map((sentenceId, index) => ({
      sentenceId,
      sentenceText: baselineModel.sentenceMap.get(sentenceId) || candidateModel.sentenceMap.get(sentenceId) || "",
      toneClass: sentenceToneClass(index),
      baseline: {
        notes: baselineModel.notes?.get(sentenceId) || [],
        analyses: baselineModel.analyses?.get(sentenceId) || [],
        vocabHighlights: baselineModel.vocabHighlights?.get(sentenceId) || [],
        phraseGlosses: baselineModel.phraseGlosses?.get(sentenceId) || [],
        contextGlosses: baselineModel.contextGlosses?.get(sentenceId) || [],
        translations: baselineModel.translations?.get(sentenceId) || [],
      },
      candidate: {
        notes: candidateModel.notes?.get(sentenceId) || [],
        analyses: candidateModel.analyses?.get(sentenceId) || [],
        vocabHighlights: candidateModel.vocabHighlights?.get(sentenceId) || [],
        phraseGlosses: candidateModel.phraseGlosses?.get(sentenceId) || [],
        contextGlosses: candidateModel.contextGlosses?.get(sentenceId) || [],
        translations: candidateModel.translations?.get(sentenceId) || [],
      },
    }));
});

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

const judgePrerequisite = computed(() => {
  const hasPreset = Boolean(currentJudgeDraft.value.preset_id);
  const hasJudgerModel = currentJudgeDraft.value.judger_models.some((value) => String(value || "").trim());
  const mismatch = compareSnapshotContextMismatchReason.value;
  if (!hasPreset) {
    return {
      ready: false,
      title: "请先选择一个 Judge 预设",
      detail: "Judge 首版优先使用系统预设。先选定本节点的评测预设，再排队 Judge Request。",
    };
  }
  if (!hasJudgerModel) {
    return {
      ready: false,
      title: "请至少选择一个 Judger 模型",
      detail: "Judge Request 需要至少一个 Judger 模型 profile，才能真正发起评审。",
    };
  }
  if (currentCompareTrialId.value && !mismatch) {
    return {
      ready: true,
      title: "当前 Compare 已持久化",
      detail: `将基于当前 Compare 对应的 Trial ${currentCompareTrialId.value} 发起 Judge 评审。`,
    };
  }
  if (compareResult.value && !mismatch) {
    return {
      ready: true,
      title: "当前 Compare 结果可用于 Judge",
      detail: "Compare 结果尚未持久化，排队 Judge 时会自动保存为独立 Trial（不绑定 Session）。",
    };
  }
  if (compareResult.value && mismatch) {
    return {
      ready: true,
      title: "当前页面上下文已变化",
      detail: `${mismatch}。Judge 将评估右侧仍显示的上一条 Compare 结果；如需评当前表单，请先重新运行 Compare。`,
    };
  }
  if (latestCompareTrialId.value) {
    return {
      ready: true,
      title: "有历史持久化 Trial 可用",
      detail: `当前无 Compare 结果，但有历史 Trial ${latestCompareTrialId.value}。注意：Judge 将评历史结果，非当前页面内容。`,
    };
  }
  return {
    ready: false,
    title: "还没有可用的 Compare 结果",
    detail: "Judge 需要先运行一次 Compare。跑出结果后即可排队 Judge Request。",
  };
});

const activeCompareJudgeRequests = computed(() => {
  if (!activeCompareTrial.value?.trial_id) return [];
  return currentJudgeRequests.value || [];
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

function judgeRequestsForTrial(trialId) {
  if (!trialId) return [];
  const list = selectedSessionJudgeRequests.value || [];
  return list.filter((req) => req.trial_id === trialId);
}

function judgeRequestsForTrialCount(trialId) {
  return judgeRequestsForTrial(trialId).length;
}

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
    const raw = window.sessionStorage.getItem(storageKey);
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
    for (const nodeName of nodeOptions.map((item) => item.id)) {
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
    window.sessionStorage.setItem(storageKey, JSON.stringify(persistedStatePayload()));
    persistTimer = null;
  }, 300);
}

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
    snapshot_hash: null,
  };
}

function buildRunRequest({ dryRun = false, useCandidate = true } = {}) {
  return {
    node_name: state.activeNode,
    text: currentText.value,
    reading_goal: currentReadingGoal.value,
    reading_variant: currentReadingVariant.value,
    source_type: "user_input",
    timeout_seconds: defaultNodeLabTimeoutSeconds,
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

function applyJudgeModeTemplate(mode) {
  const templates = {
    rubric_score_only: {
      system: "请严格按给定 rubric 做二元打分，每条只返回 0/1、简短理由和必要证据。",
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
  currentJudgeDraft.value.judge_method = mode === "rubric_score_only"
    ? "rubric_only"
    : mode === "anti_template_probe"
      ? "anti_template_probe"
      : mode === "raw"
        ? "raw"
        : "rubric_plus_pairwise";
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
  draft.judge_mode = preset.method === "anti_template_probe" ? "anti_template_probe" : "rubric_plus_pairwise";
  draft.label = preset.title;
  draft.description = `${nodeLabel(preset.node_name)} · ${preset.ui_label}`;
  draft.preset_summary = preset.ui_label || "";
  draft.packet_policy_json = JSON.stringify(preset.packet_policy || {}, null, 2);
  draft.rubric_bundle_json = JSON.stringify(preset.rubric_bundle || {}, null, 2);
  draft.probe_appendix_json = JSON.stringify(preset.probe_appendix || {}, null, 2);
  applyJudgeModeTemplate(draft.judge_mode);
  draft.persona_text = "";
  draft.rubric_json = JSON.stringify(preset.rubric_bundle || {}, null, 2);
  draft.output_schema_json = JSON.stringify(preset.output_schema || {}, null, 2);
}

async function loadModelProfiles() {
  loading.modelProfiles = true;
  try {
    modelProfiles.value = await fetchJson(modelProfilesEndpoint, { method: "GET" });
  } finally {
    loading.modelProfiles = false;
  }
}

async function loadBaselineConfig() {
  loading.baseline = true;
  try {
    const data = await fetchJson(baselineConfigEndpoint, {
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
    const rows = await fetchJson(`${candidatesEndpoint}?node_name=${encodeURIComponent(state.activeNode)}`, { method: "GET" });
    state.savedCandidatesByNode[state.activeNode] = rows;
  } catch (error) {
    setFeedback({ error: error.message });
  }
}

async function loadJudgeConfigs() {
  try {
    const rows = await fetchJson(`${judgeConfigsEndpoint}?node_name=${encodeURIComponent(state.activeNode)}`, { method: "GET" });
    state.savedJudgeConfigsByNode[state.activeNode] = rows;
  } catch (error) {
    setFeedback({ error: error.message });
  }
}

async function loadJudgePresets() {
  try {
    const rows = await fetchJson(`${judgePresetsEndpoint}?node_name=${encodeURIComponent(state.activeNode)}`, { method: "GET" });
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
    const rows = await fetchJson(`${judgeRequestsEndpoint}?${query.toString()}`, { method: "GET" });
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
    const rows = await fetchJson(`${sessionsEndpoint}?node_name=${encodeURIComponent(state.activeNode)}`, { method: "GET" });
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
    const rows = await fetchJson(`${trialsEndpoint}?${query.toString()}`, { method: "GET" });
    state.recentTrialsByNode[state.activeNode] = rows || [];
  } catch (error) {
    setFeedback({ error: error.message });
  }
}

async function loadSessionDetail(sessionId) {
  if (!sessionId) return;
  try {
    const detail = await fetchJson(`${sessionsEndpoint}/${encodeURIComponent(sessionId)}`, { method: "GET" });
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
    const detail = await fetchJson(`${trialsEndpoint}/${encodeURIComponent(trialId)}`, { method: "GET" });
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
    info: "请先跑一条 compare，再在结果区选择“新建 Session 并加入”。",
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
      existingId ? `${candidatesEndpoint}/${encodeURIComponent(draft.candidate_id)}` : candidatesEndpoint,
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
      existingId ? `${judgeConfigsEndpoint}/${encodeURIComponent(draft.judge_config_id)}` : judgeConfigsEndpoint,
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
  state.singleRunUiStateByNode[nodeName] = {
    ...(state.singleRunUiStateByNode[nodeName] || {}),
    requestLabel,
    lastStartedAt: new Date().toISOString(),
  };
  loading.run = true;
  setFeedback();
  try {
    const data = await fetchJson(runEndpoint, {
      method: "POST",
      body: JSON.stringify({
        request: buildRunRequest({ dryRun, useCandidate }),
        persist_trial: false,
      }),
    });
    state.singleRunResultsByNode[nodeName] = data.result;
    state.singleRunUiStateByNode[nodeName] = {
      ...(state.singleRunUiStateByNode[nodeName] || {}),
      requestLabel,
      lastCompletedAt: new Date().toISOString(),
    };
    setFeedback({
      info: dryRun
        ? "Candidate Prompt 预览已更新。"
        : "Single Run 已完成。结果保留在页面状态中，不进入 Session。",
    });
  } catch (error) {
    setFeedback({ error: error.message });
  } finally {
    loading.run = false;
  }
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
        },
        candidate_registry_json: candidateEntry ? [candidateEntry] : [],
      };
    } else {
      body.session_id = selectedSessionId.value;
    }
    const data = await fetchJson(`${compareEndpoint}/attach`, {
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

async function loadJudgeRequestDetail(judgeRequestId) {
  if (!judgeRequestId) return;
  try {
    const detail = await fetchJson(`${judgeRequestsEndpoint}/${encodeURIComponent(judgeRequestId)}`, { method: "GET" });
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
    await fetchJson(`${judgeRequestsEndpoint}/${encodeURIComponent(judgeRequestId)}/cancel`, {
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
    const created = await fetchJson(`${judgeRequestsEndpoint}/${encodeURIComponent(judgeRequestId)}/retry`, {
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
    const result = await fetchJson(`${judgeRequestsEndpoint}/${encodeURIComponent(judgeRequestId)}/execute`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadJudgeRequests({ trialId: activeCompareTrial.value?.trial_id || "" });
    await loadJudgeRequestDetail(judgeRequestId);
    if (selectedSessionId.value) {
      await loadSessionDetail(selectedSessionId.value);
    }
    comparePanelTab.value = "judge";
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
      "与 Sessions 重构后的设计冲突。请改用 Baseline Compare 的“加入 Session” / “新建 Session 并加入”。"
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
    const data = await fetchJson(compareEndpoint, {
      method: "POST",
      body: JSON.stringify({
        request: {
          node_name: state.activeNode,
          text: currentText.value,
          reading_goal: currentReadingGoal.value,
          reading_variant: currentReadingVariant.value,
          source_type: "user_input",
          timeout_seconds: defaultNodeLabTimeoutSeconds,
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
      const attachData = await fetchJson(`${compareEndpoint}/attach`, {
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
        const attachData = await fetchJson(`${compareEndpoint}/attach`, {
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
    const request = await fetchJson(judgeRequestsEndpoint, {
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
      return;
    }
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
    await fetchJson(`${sessionsEndpoint}/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
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
    await fetchJson(`${trialsEndpoint}/${encodeURIComponent(trialId)}`, { method: "DELETE" });
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
  if (persistTimer) {
    window.clearTimeout(persistTimer);
    persistTimer = null;
  }
});

function parseNestedJson(obj) {
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

const selectedCandidateValue = computed({
  get: () => currentDraft.value.candidate_id || "",
  set: (value) => { currentDraft.value.candidate_id = value || ""; },
});

const selectedJudgeConfigValue = computed({
  get: () => currentJudgeDraft.value.judge_config_id || "",
  set: (value) => { currentJudgeDraft.value.judge_config_id = value || ""; },
});

const readingGoalOptionsMapped = computed(() => readingGoalOptions.map((g) => ({ text: g.label, value: g.id })));
const availableReadingVariantsMapped = computed(() => (availableReadingVariants.value || []).map((v) => ({ text: v.label, value: v.id })));
const modelProfilesMapped = computed(() => [
  { text: '使用 Claread baseline route', value: '' },
  ...(modelProfiles.value || []).map((p) => ({ text: `${p.profile_name} · ${p.model_name}`, value: p.profile_name })),
]);
const savedCandidatesMapped = computed(() => [
  { text: '载入已保存 Candidate', value: '' },
  ...(currentSavedCandidates.value || []).map((c) => ({ text: c.label, value: c.candidate_id })),
]);
const fewShotModesMapped = computed(() => {
  const modes = [
    { text: '使用 Claread 本地 examples', value: 'baseline' },
    { text: '关闭 few-shot', value: 'off' },
    { text: '使用 Candidate examples', value: 'candidate' },
  ];
  if (state.activeNode === 'grammar') {
    modes.push({ text: '开启 RAG 观测', value: 'rag' });
  }
  return modes;
});
const judgeModesMapped = computed(() => availableJudgeModes.value.map((m) => ({ text: m.label, value: m.id })));
const exampleEditModesMapped = [
  { text: '结构化列表', value: 'structured' },
  { text: 'Raw JSON', value: 'raw' }
];
const exampleTypesMapped = computed(() => {
  if (state.activeNode === 'translation') return [{ text: 'translation', value: 'translation' }];
  if (state.activeNode === 'vocabulary') return [{ text: 'vocab', value: 'vocab' }];
  return [
    { text: 'grammar', value: 'grammar' },
    { text: 'sentence_analysis', value: 'sentence_analysis' },
    { text: 'vocab', value: 'vocab' },
    { text: 'phrase', value: 'phrase' },
    { text: 'context', value: 'context' },
    { text: 'translation', value: 'translation' }
  ];
});
const judgerModelOptions = computed(() => [
  { text: '不启用', value: '' },
  ...(modelProfiles.value || []).map(p => ({ text: p.model_name, value: p.profile_name }))
]);
const judgePresetOptions = computed(() => currentJudgePresets.value.map((preset) => ({
  text: `${preset.ui_label || preset.title}`,
  value: preset.preset_id,
})));
const savedJudgeConfigsMapped = computed(() => [
  { text: '载入已保存配置', value: '' },
  ...(currentSavedJudgeConfigs.value || []).map(c => ({ text: c.label, value: c.judge_config_id }))
]);

const currentJudgePreset = computed(() => {
  const presetId = currentJudgeDraft.value.preset_id;
  return currentJudgePresets.value.find((item) => item.preset_id === presetId) || null;
});

const judgeRequestSummaryFacts = computed(() => {
  const detail = selectedJudgeRequestDetail.value;
  if (!detail?.request) return [];
  const result = detail.result || {};
  return compactFactRows([
    ["请求状态", statusLabel(detail.request.status)],
    ["Judge 方法", detail.request.judge_method || detail.request.judge_config_snapshot_json?.judge_method || "未记录"],
    ["Preset", detail.request.judge_config_snapshot_json?.preset_id || "未记录"],
    ["Trial", shortId(detail.request.trial_id)],
    ["更新时间", formatClockTime(detail.request.date_updated || detail.request.finished_at || detail.request.started_at)],
  ]);
});

function judgeRequestResultMode(detail) {
  if (detail?.result?.probe_appendix_result && !detail?.result?.rubric_scoring_result) return "probe";
  if (detail?.result?.rubric_scoring_result) return "rubric";
  return "empty";
}

function judgeAggregatePassRateText(side) {
  const passRate = side?.aggregate?.pass_rate;
  if (!Number.isFinite(passRate)) return "未记录";
  return `${Math.round(passRate * 100)}%`;
}

function judgeItemResultLabel(item) {
  return item?.label || item?.item_type || item?.item_id || "未命名条目";
}

function judgeRequestIssue(detail) {
  const error = detail?.request?.error_json;
  if (!error) return null;
  return {
    code: error.code || "JudgeRequestError",
    message: error.message || "Judge request 执行失败。",
  };
}

const judgeStepRuns = computed(() => {
  const stepRuns = selectedJudgeRequestDetail.value?.result?.step_runs || {};
  const steps = [
    { key: "rubric", label: "Rubric", value: stepRuns.rubric || null },
    { key: "pairwise", label: "Pairwise", value: stepRuns.pairwise || null },
    { key: "probe", label: "Probe", value: stepRuns.probe || null },
  ];
  return steps.filter((item) => item.value);
});

function judgeStepRunFacts(stepRun) {
  if (!stepRun) return [];
  return compactFactRows([
    ["状态", statusLabel(stepRun.status)],
    ["耗时", formatDurationMs(stepRun.runtime_summary?.latency_ms)],
    ["Tokens", formatRuntimeTokens(stepRun.runtime_summary)],
    ["模型", stepRun.model_identity?.model_name || "未记录"],
  ]);
}

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
        <div class="meta-item">
          <span class="meta-label">当前显示的 Compare</span>
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
      <div class="segmented-control">
        <button
          v-for="node in nodeOptions"
          :key="node.id"
          class="segment-btn"
          :class="{ active: state.activeNode === node.id }"
          @click="state.activeNode = node.id"
        >
          {{ node.label }}
          <span v-if="hasNodeActivity(node.id)" class="activity-dot"></span>
        </button>
      </div>
      <div class="nav-divider"></div>
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


        <section class="panel-section section-readonly">
          <div class="section-header">
            <h3 class="section-title">Baseline 参考</h3>
            <div class="header-actions">
              <span class="badge badge-readonly">Read-only</span>
              <span class="help-icon" :title="helpText.baseline_snapshot">?</span>
            </div>
          </div>
          <div v-if="baselineConfig" class="meta-grid">
            <div class="meta-item" v-for="[label, value] in baselineSummaryFacts" :key="label">
              <span class="meta-label">{{ label }}</span>
              <span class="meta-value">{{ value }}</span>
            </div>
          </div>
          <div v-if="baselineConfig" class="details-group">
            <details class="detail-card">
              <summary>Baseline 说明文本</summary>
              <div class="detail-content"><pre>{{ baselineConfig.agent_instructions }}</pre></div>
            </details>
            <details class="detail-card">
              <summary>Baseline Policy</summary>
              <div class="detail-content">
                <ul class="policy-list">
                  <li v-for="(line, index) in baselineConfig.policy_lines" :key="`baseline-policy-${index}`">{{ line }}</li>
                </ul>
              </div>
            </details>
            <details class="detail-card">
              <summary>Baseline Examples</summary>
              <div class="detail-content">
                <JsonTreeView :value="parseNestedJson(baselineConfig.baseline_examples || [])" empty-text="暂无 baseline examples。" />
              </div>
            </details>
          </div>
        </section>

        
        <section class="panel-section">
          <div class="section-header">
            <h3 class="section-title">Candidate 编辑</h3>
            <span class="help-icon" :title="helpText.candidate_delta">?</span>
          </div>
          <div class="toolbar mb-4">
            <v-select class="toolbar-select" v-model="selectedCandidateValue" :items="savedCandidatesMapped" @update:modelValue="selectSavedCandidate($event)" placeholder="载入已保存 Candidate" />
            <div class="toolbar-actions">
              <v-button class="btn-ghost" small @click="resetDraftToBaseline">重置草稿</v-button>
              <v-button secondary small :disabled="loading.saveCandidate" @click="saveCandidateDraft">保存草稿</v-button>
            </div>
          </div>

          <div class="meta-grid highlight-changes mb-4">
            <div class="meta-item" v-for="item in candidateDiffFacts" :key="item.key">
              <span class="meta-label">{{ item.label }}</span>
              <span class="meta-value" :class="{ 'text-changed': item.changed }">{{ item.value }}</span>
            </div>
          </div>

          <div class="form-row">
            <div class="form-field">
              <span class="field-label">Few-shot 模式 <span class="help-icon inline" :title="helpText.few_shot_mode">?</span></span>
              <v-select v-model="currentDraft.few_shot_mode" :items="fewShotModesMapped" />
            </div>
            <div class="form-field">
              <span class="field-label">模型 Profile</span>
              <v-select v-model="currentDraft.model_profile" :items="modelProfilesMapped" />
            </div>
          </div>

          <div class="form-field mb-4">
            <span class="field-label">Agent Instructions</span>
            <v-textarea v-model="currentDraft.instruction_text" :rows="6" />
          </div>

          <div class="form-field mb-4">
            <span class="field-label">Policy Lines</span>
            <div class="list-editor">
              <div v-for="(line, index) in currentDraft.policy_lines" :key="`policy-${index}`" class="list-row">
                <v-input class="flex-1" v-model="currentDraft.policy_lines[index]" />
                <v-button icon small class="btn-danger-text" @click="currentDraft.policy_lines.splice(index, 1)">
                  <v-icon name="delete" />
                </v-button>
              </div>
              <v-button class="btn-ghost align-start" small @click="currentDraft.policy_lines.push('')">+ 新增 Policy Line</v-button>
            </div>
          </div>

          <div v-if="currentDraft.few_shot_mode === 'candidate'" class="form-field mb-4">
            <div class="field-header mb-2">
              <span class="field-label">Candidate Examples</span>
              <v-select class="w-auto" style="min-width: 140px;" v-model="currentDraft.examples_edit_mode" :items="exampleEditModesMapped" />
            </div>
            <div v-if="currentDraft.examples_edit_mode === 'structured'" class="list-editor">
              <div v-for="(example, index) in currentDraft.examples" :key="`example-${index}`" class="example-card">
                <div class="example-header">
                  <v-select class="w-auto" style="min-width: 160px;" v-model="currentDraft.examples[index].example_type" :items="exampleTypesMapped" />
                  <v-button icon small class="btn-danger-text" @click="currentDraft.examples.splice(index, 1)">
                    <v-icon name="delete" />
                  </v-button>
                </div>
                <v-input placeholder="示例原句" v-model="currentDraft.examples[index].sentence_text" />
                <v-textarea placeholder="输出片段" v-model="currentDraft.examples[index].output_fragment" :rows="3" />
              </div>
              <v-button class="btn-ghost align-start" small @click="currentDraft.examples.push({ example_type: state.activeNode === 'translation' ? 'translation' : state.activeNode === 'vocabulary' ? 'vocab' : 'grammar', sentence_text: '', output_fragment: '' })">
                + 新增 Example
              </v-button>
            </div>
            <v-textarea v-else class="code-font" v-model="currentDraft.examples_raw_text" :rows="10" />
          </div>

          <details class="detail-card mt-3">
            <summary>草稿元数据管理</summary>
            <div class="detail-content">
              <div class="form-row">
                <div class="form-field">
                  <span class="field-label">Label</span>
                  <v-input v-model="currentDraft.label" />
                </div>
                <div class="form-field">
                  <span class="field-label">Description</span>
                  <v-input v-model="currentDraft.description" />
                </div>
              </div>
              <div class="form-field mt-3">
                <span class="field-label">Notes</span>
                <v-textarea v-model="currentDraft.notes" :rows="2" />
              </div>
            </div>
          </details>
        </section>


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
            <p class="block-hint mt-3">
              当前 Session 目标：
              <strong>{{ selectedSessionDetail?.session?.title || "未选择" }}</strong>
              <span v-if="selectedSessionDetail?.session">。只有点击“加入 Session”时，当前 compare 才会写入这本 notebook。</span>
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
            <div
              v-if="singleRunRefreshState.active"
              class="refresh-banner"
              :class="`is-${singleRunRefreshState.mode}`"
            >
              <div class="refresh-banner__title">
                <span v-if="singleRunRefreshState.mode === 'refreshing' || singleRunRefreshState.mode === 'loading'" class="refresh-spinner" aria-hidden="true"></span>
                <strong>{{ singleRunRefreshState.title }}</strong>
              </div>
              <p>{{ singleRunRefreshState.detail }}</p>
            </div>
            <div v-if="singleRunResult?.run">
              <div
                class="single-run-surface"
                :class="{ 'is-stale': loading.run && singleRunResult?.run }"
              >
                <div class="meta-grid">
                  <div class="meta-item" v-for="[label, value] in singleRunSummaryFacts" :key="label">
                    <span class="meta-label">{{ label }}</span>
                    <span class="meta-value">{{ value }}</span>
                  </div>
                </div>
                <div
                  v-if="singleRunIssue"
                  class="execution-alert mt-4"
                  :class="`is-${singleRunIssue.tone}`"
                >
                  <div class="execution-alert__header">
                    <strong>{{ singleRunIssue.title }}</strong>
                    <span class="badge badge-sm" :class="`badge-${singleRunIssue.tone}`">{{ statusLabel(singleRunResult.run.status) }}</span>
                  </div>
                  <p>{{ singleRunIssue.detail }}</p>
                </div>
                <div class="output-block mt-4">
                  <div class="output-block__header">
                    <h4 class="block-title">结构化输出</h4>
                    <div
                      v-if="singleRunGrammarValidation"
                      class="validation-summary"
                      :class="`is-${singleRunGrammarValidation.status === 'pass' ? 'success' : singleRunGrammarValidation.status === 'warning' ? 'warning' : 'danger'}`"
                    >
                      <strong>Grammar 快速校验</strong>
                      <span>{{ quickValidationLabel(singleRunGrammarValidation) }}</span>
                    </div>
                  </div>
                  <p
                    v-if="singleRunGrammarValidation?.status === 'warning'"
                    class="validation-hint"
                  >
                    这次输出里有锚点或拆解块需要人工复看。先看原句高亮，再决定是否信任这条解释。
                  </p>
                  <NodeProbeOutputView
                    :node-name="state.activeNode"
                    :output="singleRunResult.run.node_output || null"
                    :prepared-sentences="singleRunResult.run.prepared_sentences || []"
                    :quick-validation="singleRunResult.run.quick_validation || null"
                    empty-text="当前没有结构化输出。"
                  />
                </div>
                <div class="details-group mt-4">
                  <ResultBlock
                    v-if="singleRunIssue"
                    title="调试信息"
                    :open="true"
                  >
                    <div class="packet-list">
                      <div class="packet-item">
                        <div class="packet-title">运行失败摘要</div>
                        <div class="packet-content">
                          <ul class="insight-list">
                            <li><strong>状态：</strong>{{ statusLabel(singleRunResult.run.status) }}</li>
                            <li><strong>错误码：</strong>{{ singleRunResult.run.error?.code || "未记录" }}</li>
                            <li><strong>错误信息：</strong>{{ singleRunResult.run.error?.message || "未记录" }}</li>
                            <li><strong>Trace Request：</strong>{{ singleRunResult.run.trace_refs?.request_id || "未记录" }}</li>
                            <li><strong>耗时：</strong>{{ formatDurationMs(singleRunResult.run.runtime_summary?.latency_ms) }}</li>
                          </ul>
                        </div>
                      </div>
                      <div class="packet-item">
                        <div class="packet-title">调试原始信息</div>
                        <JsonTreeView :value="parseNestedJson(singleRunIssue.debug)" empty-text="暂无调试信息。" />
                      </div>
                    </div>
                  </ResultBlock>
                  <ResultBlock title="发送给模型的内容" :open="false">
                    <div class="packet-list">
                      <div class="packet-item" v-for="section in buildPromptPacketSections(singleRunResult?.run)" :key="section.key">
                        <div class="packet-title">{{ section.title }}</div>
                        <JsonTreeView
                          v-if="isStructuredJsonValue(parseNestedJson(section.value))"
                          :value="parseNestedJson(section.value)"
                          :empty-text="`${section.title} 暂无数据。`"
                        />
                        <XmlPromptViewer v-else-if="section.key === 'runtime_prompt'" :text="String(section.value || '')" />
                        <pre v-else class="packet-content">{{ formatJson(section.value) }}</pre>
                      </div>
                    </div>
                  </ResultBlock>
                  <ResultBlock title="完整结果 JSON" :open="false">
                    <JsonTreeView :value="parseNestedJson(singleRunResult)" empty-text="暂无结果 JSON。" />
                  </ResultBlock>
                </div>
              </div>
            </div>
            <div v-else class="empty-state">
              <p>暂无执行结果</p>
              <span class="empty-hint">请在左侧点击“运行”以查看输出。</span>
            </div>
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
                      <span class="meta-value">{{ card.model }}</span>
                    </div>
                    <div class="status-fact">
                      <span class="meta-label">Few-shot</span>
                      <span class="meta-value">{{ card.fewShot }}</span>
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
                    <div class="status-fact">
                      <span class="meta-label">模型 / Few-shot</span>
                      <span class="meta-value">
                        {{ card.model }}
                        <small v-if="card.key === 'candidate' && card.deltaModel" class="delta-inline text-warning">{{ card.deltaModel }}</small>
                        <small v-if="card.key === 'candidate' && card.deltaFewShot" class="delta-inline text-warning">{{ card.deltaFewShot }}</small>
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

              <div class="details-group mt-4">
                <ResultBlock title="Prompt Packet 对比" :open="false">
                  <div class="compare-prompt-stack">
                    <section
                      v-for="section in comparePromptSections"
                      :key="section.key"
                      class="compare-prompt-row"
                    >
                      <header class="compare-prompt-row__title">
                        <h4>{{ section.title }}</h4>
                      </header>
                      <div class="compare-row__body compare-prompt-grid">
                        <div class="compare-column">
                          <div class="compare-column__header">
                            <h5>Baseline</h5>
                          </div>
                          <div class="packet-item">
                            <JsonTreeView
                              v-if="isStructuredJsonValue(parseNestedJson(section.baseline))"
                              :value="parseNestedJson(section.baseline)"
                              :empty-text="`${section.title} 暂无数据。`"
                            />
                            <XmlPromptViewer v-else-if="section.key === 'runtime_prompt'" :text="String(section.baseline || '')" />
                            <pre v-else class="packet-content">{{ formatJson(section.baseline) }}</pre>
                          </div>
                        </div>
                        <div class="compare-column">
                          <div class="compare-column__header">
                            <h5>Candidate</h5>
                          </div>
                          <div class="packet-item">
                            <JsonTreeView
                              v-if="isStructuredJsonValue(parseNestedJson(section.candidate))"
                              :value="parseNestedJson(section.candidate)"
                              :empty-text="`${section.title} 暂无数据。`"
                            />
                            <XmlPromptViewer v-else-if="section.key === 'runtime_prompt'" :text="String(section.candidate || '')" />
                            <pre v-else class="packet-content">{{ formatJson(section.candidate) }}</pre>
                          </div>
                        </div>
                      </div>
                    </section>
                  </div>
                </ResultBlock>
                <ResultBlock
                  v-if="resultIssue(compareResult?.baseline, 'Baseline') || resultIssue(compareResult?.candidate, 'Candidate')"
                  title="Compare 调试信息"
                  :open="true"
                >
                  <div class="packet-list">
                    <div v-if="resultIssue(compareResult?.baseline, 'Baseline')" class="packet-item">
                      <div class="packet-title">Baseline 调试信息</div>
                      <JsonTreeView :value="parseNestedJson(resultIssue(compareResult?.baseline, 'Baseline').debug)" empty-text="暂无调试信息。" />
                    </div>
                    <div v-if="resultIssue(compareResult?.candidate, 'Candidate')" class="packet-item">
                      <div class="packet-title">Candidate 调试信息</div>
                      <JsonTreeView :value="parseNestedJson(resultIssue(compareResult?.candidate, 'Candidate').debug)" empty-text="暂无调试信息。" />
                    </div>
                  </div>
                </ResultBlock>
                <ResultBlock title="Compare 原始结果 JSON" :open="false">
                  <JsonTreeView :value="parseNestedJson(compareResult)" empty-text="暂无 Compare 结果 JSON。" />
                </ResultBlock>
              </div>
              </template>

              <div v-if="comparePanelTab === 'judge'" class="judge-panel mt-4 judge-panel--expanded">
                <div class="judge-panel__body">
                  <div class="form-field">
                    <span class="field-label">系统预设</span>
                    <v-select
                      v-model="currentJudgeDraft.preset_id"
                      :items="judgePresetOptions"
                      @update:modelValue="applyJudgePreset($event)"
                    />
                  </div>

                  <div v-if="currentJudgePreset" class="status-banner is-ready mt-3">
                    <strong>{{ currentJudgePreset.ui_label || currentJudgePreset.title }}</strong>
                    <p class="text-sm mt-1">
                      适用节点：{{ nodeLabel(currentJudgePreset.node_name) }}
                      <span class="divider">/</span>
                      Strategy：{{ currentJudgePreset.strategy }}
                      <span class="divider">/</span>
                      Method：{{ currentJudgePreset.method }}
                    </p>
                  </div>

                  <div class="form-row triple mt-3">
                    <div class="form-field" v-for="slot in [0, 1, 2]" :key="`judger-${slot}`">
                      <span class="field-label">Judger {{ slot + 1 }}</span>
                      <v-select v-model="currentJudgeDraft.judger_models[slot]" :items="judgerModelOptions" />
                    </div>
                  </div>

                  <div class="status-banner mt-3" :class="judgePrerequisite.ready ? 'is-ready' : 'is-warning'">
                    <strong>{{ judgePrerequisite.title }}</strong>
                    <p class="text-sm mt-1">{{ judgePrerequisite.detail }}</p>
                  </div>

                  <div class="action-buttons mt-3">
                    <v-button :disabled="loading.queueJudge || !judgePrerequisite.ready" @click="queueJudgeCompare({ autoExecute: true })">创建并执行 Judge</v-button>
                  </div>

                  <details class="detail-card detail-card--compact mt-3">
                    <summary>调试操作</summary>
                    <div class="detail-content">
                      <div class="action-buttons mb-3">
                        <v-button secondary :disabled="loading.queueJudge || !judgePrerequisite.ready" @click="queueJudgeCompare()">仅创建 Request</v-button>
                        <v-button v-if="pendingJudgeRequestId" :disabled="loading.executeJudge" @click="executeJudgeRequest(pendingJudgeRequestId)">执行这条 Request</v-button>
                      </div>
                      <p class="block-hint mb-3">仅在调试 Judge 链路时使用。日常流程优先直接点“创建并执行 Judge”。</p>
                      <div class="form-field mb-3">
                        <span class="field-label">Judge 方式</span>
                        <v-select v-model="currentJudgeDraft.judge_mode" :items="judgeModesMapped" @update:modelValue="applyJudgeModeTemplate($event)" />
                      </div>
                      <div class="form-field mb-3">
                        <span class="field-label">Persona</span>
                        <v-textarea v-model="currentJudgeDraft.persona_text" :rows="2" />
                      </div>
                      <div class="form-field mb-3">
                        <span class="field-label">System Prompt</span>
                        <v-textarea v-model="currentJudgeDraft.system_prompt" :rows="3" />
                      </div>
                      <div class="form-field mb-3">
                        <span class="field-label">User Prompt</span>
                        <v-textarea v-model="currentJudgeDraft.user_prompt" :rows="3" />
                      </div>
                      <div class="form-field">
                        <span class="field-label">Rubric Bundle JSON</span>
                        <v-textarea class="code-font" v-model="currentJudgeDraft.rubric_bundle_json" :rows="6" />
                      </div>
                      <div class="form-field mt-3">
                        <span class="field-label">Packet Policy JSON</span>
                        <v-textarea class="code-font" v-model="currentJudgeDraft.packet_policy_json" :rows="6" />
                      </div>
                      <div class="form-field mt-3">
                        <span class="field-label">Probe Appendix JSON</span>
                        <v-textarea class="code-font" v-model="currentJudgeDraft.probe_appendix_json" :rows="5" />
                      </div>
                      <div class="form-field mt-3">
                        <span class="field-label">Rubric Source JSON</span>
                        <v-textarea class="code-font" v-model="currentJudgeDraft.rubric_json" :rows="6" />
                      </div>
                      <div class="form-field mt-3">
                        <span class="field-label">Output Schema JSON</span>
                        <v-textarea class="code-font" v-model="currentJudgeDraft.output_schema_json" :rows="6" />
                      </div>
                    </div>
                  </details>

                  <div v-if="activeCompareJudgeRequests.length" class="output-block mt-4">
                    <h4 class="block-title">Judge Requests</h4>
                    <div class="request-list mt-3">
                      <button
                        class="request-item request-item--interactive"
                        :class="{ active: selectedJudgeRequestId === item.judge_request_id }"
                        v-for="item in activeCompareJudgeRequests"
                        :key="item.judge_request_id"
                        @click="loadJudgeRequestDetail(item.judge_request_id)"
                      >
                        <div class="request-main">
                          <span class="request-id">{{ item.judge_request_id }}</span>
                          <span class="request-meta">Trial {{ shortId(item.trial_id) }}</span>
                        </div>
                        <div class="request-side">
                          <span class="badge">{{ statusLabel(item.status) }}</span>
                        </div>
                      </button>
                    </div>
                  </div>
                  <div v-else class="empty-state compact mt-4">
                    <p>当前 Compare 还没有 Judge Request</p>
                    <span class="empty-hint">Judge tab 只显示这条 Compare 的评审记录，不再混入当前 node 的历史 requests。</span>
                  </div>

                  <div v-if="selectedJudgeRequestDetail?.request" class="output-block mt-4">
                    <h4 class="block-title">Judge 结果详情</h4>
                    <div class="meta-grid mt-3">
                      <div class="meta-item" v-for="[label, value] in judgeRequestSummaryFacts" :key="label">
                        <span class="meta-label">{{ label }}</span>
                        <span class="meta-value">{{ value }}</span>
                      </div>
                    </div>

                    <div class="action-buttons mt-3">
                      <v-button
                        secondary
                        small
                        v-if="['queued', 'running'].includes(selectedJudgeRequestDetail.request.status)"
                        @click="cancelJudgeRequest(selectedJudgeRequestDetail.request.judge_request_id)"
                      >
                        取消 Request
                      </v-button>
                      <v-button
                        secondary
                        small
                        v-if="['queued'].includes(selectedJudgeRequestDetail.request.status)"
                        :disabled="loading.executeJudge"
                        @click="executeJudgeRequest(selectedJudgeRequestDetail.request.judge_request_id)"
                      >
                        执行这条 Request
                      </v-button>
                      <v-button
                        secondary
                        small
                        v-if="['failed', 'cancelled'].includes(selectedJudgeRequestDetail.request.status)"
                        @click="retryJudgeRequest(selectedJudgeRequestDetail.request.judge_request_id)"
                      >
                        重新排队
                      </v-button>
                    </div>

                    <div v-if="judgeStepRuns.length" class="compare-overview mt-4">
                      <article
                        v-for="step in judgeStepRuns"
                        :key="step.key"
                        class="compare-status-card"
                        :class="`is-${statusTone(step.value?.status)}`"
                      >
                        <div class="compare-status-card__header">
                          <h4>{{ step.label }}</h4>
                          <span class="badge" :class="`badge-${statusTone(step.value?.status)}`">{{ statusLabel(step.value?.status) }}</span>
                        </div>
                        <div class="compare-status-card__facts">
                          <div class="status-fact" v-for="[label, value] in judgeStepRunFacts(step.value)" :key="`${step.key}-${label}`">
                            <span class="meta-label">{{ label }}</span>
                            <span class="meta-value">{{ value }}</span>
                          </div>
                        </div>
                        <p v-if="step.value?.error?.message" class="text-sm mt-2 text-danger">
                          {{ step.value.error.message }}
                        </p>
                      </article>
                    </div>

                    <div v-if="judgeRequestIssue(selectedJudgeRequestDetail)" class="execution-alert is-danger mt-4">
                      <div class="execution-alert__header">
                        <strong>Judge 执行失败</strong>
                        <span class="badge badge-danger">{{ judgeRequestIssue(selectedJudgeRequestDetail).code }}</span>
                      </div>
                      <p>{{ judgeRequestIssue(selectedJudgeRequestDetail).message }}</p>
                      <p class="text-sm mt-1">如果下面已有 rubric / pairwise / probe 结果，说明这是部分失败，可继续参考已成功部分。</p>
                    </div>

                    <div v-if="judgeRequestResultMode(selectedJudgeRequestDetail) === 'rubric'" class="compare-overview mt-4">
                      <article
                        v-for="side in [
                          { key: 'baseline', title: 'Baseline', value: selectedJudgeRequestDetail.result.rubric_scoring_result?.baseline },
                          { key: 'candidate', title: 'Candidate', value: selectedJudgeRequestDetail.result.rubric_scoring_result?.candidate },
                        ]"
                        :key="side.key"
                        class="compare-status-card is-neutral"
                      >
                        <div class="compare-status-card__header">
                          <h4>{{ side.title }}</h4>
                          <span class="badge badge-neutral">Judge</span>
                        </div>
                        <div class="compare-status-card__facts">
                          <div class="status-fact">
                            <span class="meta-label">条目数</span>
                            <span class="meta-value">{{ side.value?.aggregate?.item_count ?? 0 }}</span>
                          </div>
                          <div class="status-fact">
                            <span class="meta-label">通过</span>
                            <span class="meta-value">{{ side.value?.aggregate?.passed ?? 0 }}</span>
                          </div>
                          <div class="status-fact">
                            <span class="meta-label">失败</span>
                            <span class="meta-value">{{ side.value?.aggregate?.failed ?? 0 }}</span>
                          </div>
                          <div class="status-fact">
                            <span class="meta-label">通过率</span>
                            <span class="meta-value">{{ judgeAggregatePassRateText(side.value) }}</span>
                          </div>
                        </div>
                      </article>
                    </div>

                    <div
                      v-if="selectedJudgeRequestDetail.result?.pairwise_result?.pairwise_review"
                      class="status-banner is-ready mt-4"
                    >
                      <strong>Pairwise 整体评估倾向：{{ selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.preferred_side }}</strong>
                      <p class="text-sm mt-1">{{ selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.overall_judgment }}</p>
                    </div>

                    <div
                      v-if="selectedJudgeRequestDetail.result?.probe_appendix_result"
                      class="status-banner is-warning mt-4"
                    >
                      <strong>专项 Probe：{{ selectedJudgeRequestDetail.result.probe_appendix_result.probe_type }}</strong>
                      <p class="text-sm mt-1">{{ selectedJudgeRequestDetail.result.probe_appendix_result.summary || "请展开下方问题列表查看细节。" }}</p>
                    </div>

                    <div class="details-group mt-4">
                      <ResultBlock
                        v-if="selectedJudgeRequestDetail.result?.rubric_scoring_result"
                        title="Rubric 逐项结果"
                        :open="true"
                      >
                        <div class="compare-split">
                          <div class="compare-pane" v-for="side in [
                            { key: 'baseline', title: 'Baseline', value: selectedJudgeRequestDetail.result.rubric_scoring_result.baseline },
                            { key: 'candidate', title: 'Candidate', value: selectedJudgeRequestDetail.result.rubric_scoring_result.candidate },
                          ]" :key="side.key">
                            <div class="pane-header"><h4>{{ side.title }}</h4></div>
                            <div v-if="side.value?.items?.length" class="packet-list">
                              <div v-for="item in side.value.items" :key="item.item_id" class="packet-item">
                                <div class="packet-title">{{ judgeItemResultLabel(item) }}</div>
                                <ul class="insight-list">
                                  <li v-for="criterion in item.criteria" :key="`${item.item_id}-${criterion.criterion_id}`">
                                    <strong>{{ criterion.criterion_id }}</strong>
                                    <span> · {{ criterion.score ? "通过" : "未通过" }}</span>
                                    <span>：{{ criterion.reason }}</span>
                                  </li>
                                </ul>
                              </div>
                            </div>
                            <div v-else class="empty-state compact mt-2">
                              <p>暂无逐项结果</p>
                            </div>
                          </div>
                        </div>
                      </ResultBlock>

                      <ResultBlock
                        v-if="selectedJudgeRequestDetail.result?.pairwise_result?.pairwise_review"
                        title="Pairwise 整体评估意见"
                        :open="true"
                      >
                        <div class="compare-split">
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
                      </ResultBlock>

                      <ResultBlock
                        v-if="selectedJudgeRequestDetail.result?.probe_appendix_result"
                        title="Probe Findings"
                        :open="true"
                      >
                        <div class="packet-list">
                          <div
                            v-for="question in selectedJudgeRequestDetail.result.probe_appendix_result.questions || []"
                            :key="question.question_id"
                            class="packet-item"
                          >
                            <div class="packet-title">
                              {{ question.question_id }}
                              <span class="badge badge-sm" :class="question.detected ? 'badge-warning' : 'badge-success'">
                                {{ question.detected ? "发现问题" : "未发现" }}
                              </span>
                            </div>
                            <p class="packet-content">{{ question.description }}</p>
                            <ul v-if="question.evidence?.length" class="insight-list mt-2">
                              <li v-for="(evidence, index) in question.evidence" :key="`${question.question_id}-${index}`">{{ evidence }}</li>
                            </ul>
                          </div>
                        </div>
                      </ResultBlock>

                      <ResultBlock title="Judge Artifact JSON" :open="false">
                        <JsonTreeView :value="parseNestedJson(selectedJudgeRequestDetail)" empty-text="暂无 Judge 结果。" />
                      </ResultBlock>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="empty-state">
              <p>当前没有打开的 Compare</p>
              <span class="empty-hint">先运行 Compare，或从 Sessions 里打开一条历史结果。</span>
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

    <div v-else class="sessions-workspace">
      <div class="sessions-sidebar">
        <div class="sidebar-header">
          <h3 class="sidebar-title">Compare 实验记录本</h3>
        </div>
        <p class="sidebar-hint">
          每个 Session 固定 node、阅读目标/变体与 baseline 参考系。
          <button class="btn-link inline-hint" @click="goStartCompareFromEmpty">没有 Session？先去跑一条 compare</button>
        </p>
        <div class="session-list">
          <button
            v-for="item in currentSessions"
            :key="item.session_id"
            class="session-nav-item"
            :class="{ active: selectedSessionId === item.session_id }"
            @click="selectSession(item.session_id)"
          >
            <div class="item-header">
              <span class="item-title">{{ item.title }}</span>
              <span v-if="selectedSessionId === item.session_id" class="badge badge-active">当前</span>
            </div>
            <div class="item-meta">
              <span>{{ statusLabel(item.status) }}</span>
              <span class="dot-separator">·</span>
              <span>{{ sessionCompareCount(item) }} compare</span>
              <span class="dot-separator">·</span>
              <span>{{ sessionJudgeCount(item) }} judge</span>
            </div>
            <div class="item-meta">
              <span>{{ nodeLabel(item.node_name) }}</span>
              <span class="dot-separator">·</span>
              <span>{{ readingGoalLabel(item.baseline_snapshot_json?.reading_goal) }}</span>
              <span class="dot-separator">·</span>
              <span>{{ readingVariantLabel(item.baseline_snapshot_json?.reading_variant) }}</span>
            </div>
            <div class="item-meta">
              <span>Baseline {{ sessionBaselineLabel(item) }}</span>
            </div>
          </button>
          <div v-if="!currentSessions.length" class="empty-state compact">
            <p>暂无记录本</p>
            <span class="empty-hint">请在 Baseline Compare 跑出第一条 compare 后新建 Session。</span>
          </div>
        </div>
      </div>

      <div class="sessions-main">
        <template v-if="selectedSessionDetail?.session">
          <div class="session-hero">
            <div class="hero-main">
              <h2 class="session-title">{{ selectedSessionDetail.session.title }}</h2>
              <span class="badge" :class="`badge-${statusTone(selectedSessionDetail.session.status)}`">{{ statusLabel(selectedSessionDetail.session.status) }}</span>
            </div>
            <p class="session-desc">{{ sessionDecisionNarrative }}</p>
            <div class="action-buttons mt-3">
              <v-button secondary @click="state.activeWorkspace = 'baseline_compare'">返回 Baseline Compare</v-button>
              <v-button class="btn-danger-text" outlined @click="deleteSession(selectedSessionDetail.session.session_id)">删除整个 Session</v-button>
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
          </div>

          <div class="timeline-container">
            <div class="timeline-sidebar">
              <h4 class="block-title mb-3">Compare 时间线</h4>
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
                  <p v-if="trial.display_excerpt || trial.input_excerpt" class="item-excerpt">「{{ trial.display_excerpt || trial.input_excerpt }}」</p>
                </button>
              </div>
              <div v-else class="empty-state compact">
                <p>暂无 compare</p>
                <span class="empty-hint">回到 Baseline Compare 跑出第一条结果并选择“加入 Session”。</span>
              </div>
            </div>

            <div class="timeline-detail">
              <h4 class="block-title mb-3">
                Compare 详情
                <span v-if="selectedSessionTrialId" class="text-muted font-normal text-sm ml-2">#{{ shortId(selectedSessionTrialId) }}</span>
              </h4>

              <template v-if="selectedSessionTrialDetail?.trial">
                <div class="action-buttons mb-4">
                  <v-button secondary @click="openSessionTrialInCompare(selectedSessionTrialDetail.trial.trial_id)">在 Baseline Compare 中打开</v-button>
                  <v-button @click="openSessionTrialInCompare(selectedSessionTrialDetail.trial.trial_id, { openJudge: true })">重新 Judge</v-button>
                  <v-button class="btn-danger-text" outlined @click="deleteTrial(selectedSessionTrialDetail.trial.trial_id)">删除这条 compare</v-button>
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
                  <p>{{ selectedSessionTrialDetail.trial.input_excerpt }}</p>
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
        <div v-else class="empty-state">
          <p>请先在 Baseline Compare 跑出第一条 compare，再选择加入或新建 Session。</p>
          <span class="empty-hint">Session 是固定实验上下文的 compare 记录本，Single Run 不再进入 Session。</span>
        </div>
      </div>
    </div>
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
  gap: 32px;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-label {
  font-size: 12px;
  color: var(--color-text-subdued);
  font-weight: 500;
}

.meta-value {
  font-size: 14px;
  font-weight: 500;
}
.meta-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

/* Navigation Segmented Control */
.lab-navigation {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px;
  background: var(--color-surface-subdued);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  width: max-content;
}

.segmented-control {
  display: flex;
  gap: 4px;
}

.segment-btn {
  position: relative;
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued);
  border-radius: var(--radius-sm);
  transition: all 0.15s ease;
}

.segment-btn:hover {
  color: var(--color-text);
}

.segment-btn.active {
  color: var(--color-text);
  background: var(--color-surface);
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
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

.nav-divider {
  width: 1px;
  height: 20px;
  background: var(--color-border);
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

.judge-panel__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  cursor: pointer;
  user-select: none;
  background: color-mix(in srgb, var(--color-primary) 3%, var(--color-surface));
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s ease;
}

.judge-panel__header:hover {
  background: color-mix(in srgb, var(--color-primary) 5%, var(--color-surface));
}

.judge-panel.judge-panel--expanded .judge-panel__header {
  border-bottom-color: var(--color-border);
}

.judge-panel__toggle {
  font-size: 13px;
  color: var(--color-primary);
  font-weight: 500;
}

.judge-panel__body {
  padding: 24px;
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

.section-readonly {
  background: var(--color-surface-subdued);
  border-style: dashed;
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
.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
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

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.toolbar-select { max-width: 200px; }
.toolbar-actions { display: flex; gap: 8px; align-items: center; }

/* Forms & Inputs */
.form-row {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.form-row.triple {
  grid-template-columns: repeat(3, 1fr);
  display: grid;
}
.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  margin-bottom: 16px;
}
.field-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued);
}
.form-control {
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  transition: border-color 0.15s ease;
}
.form-control:focus {
  outline: none;
  border-color: var(--color-primary);
}
.form-control-sm { padding: 4px 8px; font-size: 13px; }
.w-auto { width: auto; }
.code-font { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }

/* Buttons */
.btn-primary, .btn-secondary, .btn-outline, .btn-ghost {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8px 16px;
  font-size: 14px;
  font-weight: 500;
  border-radius: var(--radius-md);
  transition: all 0.15s ease;
}
.btn-primary {
  background: var(--color-primary);
  color: var(--color-primary-text);
}
.btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-secondary {
  background: var(--color-surface-subdued);
  border: 1px solid var(--color-border);
  color: var(--color-text);
}
.btn-secondary:hover:not(:disabled) { background: var(--color-border); }
.btn-secondary:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-outline {
  background: transparent;
  border: 1px solid var(--color-primary);
  color: var(--color-primary);
}
.btn-ghost {
  color: var(--color-text-subdued);
}
.btn-ghost:hover { color: var(--color-text); background: var(--color-surface-subdued); }
.btn-sm { padding: 6px 12px; font-size: 13px; }
.btn-link { color: var(--color-primary); font-size: 13px; font-weight: 500; padding: 2px 0; }
.btn-link:hover { text-decoration: underline; }
.btn-danger-text { color: var(--theme--danger, #dc2626); font-size: 13px; padding: 4px 8px; }

/* Meta Grid (Replacing Summary Strip) */
.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px;
  padding: 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}
.highlight-changes {
  background: color-mix(in srgb, var(--color-surface-subdued) 50%, var(--color-surface));
}
.meta-grid .meta-item { display: flex; flex-direction: column; gap: 4px; }
.meta-grid .meta-label { font-size: 12px; color: var(--color-text-subdued); }
.meta-grid .meta-value { font-size: 14px; font-weight: 500; color: var(--color-text); }
.text-changed { color: var(--color-primary); }
.text-status { font-weight: 600; }

/* Helpers */
.mt-1 { margin-top: 4px; }
.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 12px; }
.mt-4 { margin-top: 16px; }
.mb-2 { margin-bottom: 8px; }
.mb-3 { margin-bottom: 12px; }
.mb-4 { margin-bottom: 16px; }
.text-muted { color: var(--color-text-subdued); }
.text-sm { font-size: 13px; }
.font-normal { font-weight: 400; }
.ml-2 { margin-left: 8px; }

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
.badge-readonly { background: transparent; border-style: dashed; color: var(--color-text-subdued); }
.badge-active { background: color-mix(in srgb, var(--color-primary) 10%, var(--color-surface)); border-color: var(--color-primary); color: var(--color-primary); }
.badge-sm { padding: 1px 6px; font-size: 11px; }

.feedback-banner {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
}
.feedback-banner.error { background: color-mix(in srgb, var(--theme--danger, #dc2626) 12%, var(--color-surface)); color: var(--theme--danger, #dc2626); }
.feedback-banner.info { background: color-mix(in srgb, var(--theme--success, #10b981) 12%, var(--color-surface)); }

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

/* Details & JSON */
.details-group { display: flex; flex-direction: column; gap: 8px; }
.detail-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  overflow: hidden;
}
.detail-card summary {
  padding: 12px 16px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  background: var(--color-surface-subdued);
}
.detail-card--compact summary {
  font-size: 12px;
}
.detail-content { padding: 16px; border-top: 1px solid var(--color-border); }
pre { margin: 0; white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 12px; color: var(--color-text-subdued); }

/* List Editor */
.list-editor { display: flex; flex-direction: column; gap: 8px; }
.list-row { display: flex; gap: 8px; align-items: center; }
.example-card { border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: 16px; display: flex; flex-direction: column; gap: 12px; background: var(--color-surface-subdued); }
.example-header { display: flex; justify-content: space-between; }
.align-start { align-self: flex-start; }

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
.block-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; }

.output-block__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.output-block__header .block-title {
  margin-bottom: 0;
}

.validation-summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-subdued);
  font-size: 12px;
  white-space: nowrap;
}

.validation-summary strong {
  font-weight: 600;
}

.validation-summary.is-success {
  color: var(--theme--success, #10b981);
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--success, #10b981) 7%, var(--color-surface));
}

.validation-summary.is-warning {
  color: var(--theme--warning, #f59e0b);
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 7%, var(--color-surface));
}

.validation-summary.is-danger {
  color: var(--theme--danger, #dc2626);
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 7%, var(--color-surface));
}

.validation-hint {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-subdued);
}

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

.single-run-surface {
  position: relative;
  transition: opacity 120ms ease;
}

.single-run-surface.is-stale {
  opacity: 0.62;
}

.compare-split { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.compare-pane { border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: 16px; }
.pane-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.pane-header h4 { font-size: 14px; font-weight: 500; }

.compare-overview {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
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

.delta-inline {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.45;
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

.compare-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.compare-metric-card {
  padding: 14px 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
  display: grid;
  gap: 6px;
}

.compare-metric-card__value {
  font-size: 16px;
  font-weight: 650;
  color: var(--color-text);
}

.compare-metric-card__detail {
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-subdued);
}

.compare-metric-card.is-success {
  background: color-mix(in srgb, var(--theme--success, #10b981) 6%, var(--color-surface));
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 22%, var(--color-border));
}

.compare-metric-card.is-success .compare-metric-card__value {
  color: var(--theme--success, #10b981);
}

.compare-metric-card.is-warning {
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 6%, var(--color-surface));
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 22%, var(--color-border));
}

.compare-metric-card.is-warning .compare-metric-card__value {
  color: var(--theme--warning, #d97706);
}

.compare-metric-card.is-danger {
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 6%, var(--color-surface));
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 22%, var(--color-border));
}

.compare-metric-card.is-danger .compare-metric-card__value {
  color: var(--theme--danger, #dc2626);
}

.compare-canvas {
  display: grid;
  gap: 16px;
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

.compare-prompt-grid {
  padding: 0;
}

.compare-prompt-stack {
  display: grid;
  gap: 16px;
}

.compare-prompt-row {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  overflow: hidden;
}

.compare-prompt-row__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border-subdued);
  background: var(--color-surface-subdued);
}

.compare-prompt-row__title h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--color-text-subdued);
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

.compare-column__header h5 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-subdued);
}

.compare-entry-list {
  display: grid;
  gap: 10px;
}

.compare-entry-card {
  border: 1px solid color-mix(in srgb, var(--sentence-accent, #6366f1) 20%, var(--color-border));
  border-radius: var(--radius-md);
  padding: 14px;
  background: color-mix(in srgb, var(--sentence-tint, #eef2ff) 38%, var(--color-surface));
  display: grid;
  gap: 8px;
}

.compare-entry-card.is-analysis {
  background: color-mix(in srgb, var(--sentence-tint, #eef2ff) 24%, var(--color-surface));
}

.compare-entry-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.compare-entry-card__head strong {
  font-size: 14px;
  line-height: 1.45;
}

.compare-entry-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--sentence-accent, #6366f1) 12%, var(--color-surface));
  color: color-mix(in srgb, var(--sentence-accent, #6366f1) 72%, #1f2937);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.compare-entry-anchor {
  font-size: 12px;
  line-height: 1.55;
  color: var(--color-text-subdued);
}

.compare-entry-card p {
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
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

.packet-list { display: flex; flex-direction: column; gap: 12px; }
.packet-item { border: 1px solid var(--color-border); border-radius: var(--radius-md); overflow: hidden; }
.packet-title { font-size: 12px; font-weight: 600; padding: 8px 12px; background: var(--color-surface-subdued); border-bottom: 1px solid var(--color-border); }
.packet-content { padding: 12px; }

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
.request-item--interactive.active {
  border-color: color-mix(in srgb, var(--color-primary) 40%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 6%, var(--color-surface));
}
.request-main { display: flex; flex-direction: column; gap: 4px; }
.request-id { font-weight: 500; font-size: 14px; }
.request-meta { font-size: 12px; color: var(--color-text-subdued); }
.request-submeta {
  font-size: 12px;
  line-height: 1.55;
  color: var(--color-text);
}
.request-item--verbose .request-main {
  gap: 6px;
}

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
.empty-state.compact { padding: 20px 12px; }
.empty-hint { font-size: 13px; margin-top: 4px; }

/* Status banners */
.status-banner { padding: 16px; border-radius: var(--radius-md); border: 1px solid; }
.status-banner.is-ready { background: color-mix(in srgb, var(--theme--success, #10b981) 5%, var(--color-surface)); border-color: color-mix(in srgb, var(--theme--success) 30%, var(--color-border)); }
.status-banner.is-warning { background: color-mix(in srgb, var(--theme--warning, #f59e0b) 5%, var(--color-surface)); border-color: color-mix(in srgb, var(--theme--warning) 30%, var(--color-border)); }

/* Sessions Workspace */
.sessions-workspace {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 24px;
  align-items: start;
}
.sessions-sidebar {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.sidebar-header { display: flex; justify-content: space-between; align-items: center; }
.sidebar-title { font-size: 14px; font-weight: 600; }
.session-list { display: flex; flex-direction: column; gap: 8px; }

.sidebar-hint {
  font-size: 12px;
  color: var(--color-text-subdued);
  line-height: 1.5;
  margin: -8px 0 4px;
}

.block-hint {
  font-size: 12px;
  color: var(--color-text-subdued);
  line-height: 1.5;
  margin: -8px 0 8px;
}

.notebook-context {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 12px 14px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
}

.badge-locked {
  background: color-mix(in srgb, var(--color-primary) 10%, var(--color-surface));
  border-color: color-mix(in srgb, var(--color-primary) 35%, var(--color-border));
  color: var(--color-primary);
  font-weight: 600;
}

.ctx-chip {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  font-size: 12px;
  color: var(--color-text);
  font-weight: 500;
}

.input-excerpt {
  padding: 10px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
}
.input-excerpt p {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text);
}

.item-idx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--color-primary);
  color: var(--color-primary-text);
  font-size: 11px;
  font-weight: 700;
}

.item-excerpt {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-subdued);
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
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  background: var(--color-surface-subdued);
}

.judge-tile--interactive {
  width: 100%;
  text-align: left;
}

.judge-tile--interactive:hover {
  border-color: color-mix(in srgb, var(--color-primary) 25%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 4%, var(--color-surface));
}

.judge-tile-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.judge-id {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text);
}

.meta-hint {
  font-size: 11px;
  color: var(--color-text-subdued);
  margin-top: 2px;
}

.btn-link.inline-hint {
  display: inline;
  padding: 0;
  margin: 0;
  font-size: 12px;
  color: var(--color-primary);
}

.session-nav-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  text-align: left;
}
.session-nav-item:hover { background: var(--color-surface-subdued); }
.session-nav-item.active { background: color-mix(in srgb, var(--color-primary) 4%, var(--color-surface)); border-color: color-mix(in srgb, var(--color-primary) 20%, var(--color-border)); }
.item-header { display: flex; justify-content: space-between; align-items: center; }
.item-title { font-size: 14px; font-weight: 500; color: var(--color-text); }
.item-meta { display: flex; gap: 8px; font-size: 12px; color: var(--color-text-subdued); align-items: center; }
.dot-separator { margin: 0 4px; }

.sessions-main {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 32px;
}
.session-hero { margin-bottom: 32px; padding-bottom: 24px; border-bottom: 1px solid var(--color-border); }
.hero-main { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.session-title { font-size: 24px; font-weight: 600; }
.session-desc { font-size: 14px; color: var(--color-text-subdued); line-height: 1.6; }
.meta-row { display: flex; gap: 24px; flex-wrap: wrap; }
.meta-badge { display: flex; flex-direction: column; gap: 4px; }
.meta-badge .label { font-size: 12px; color: var(--color-text-subdued); }
.meta-badge .value { font-size: 14px; font-weight: 500; }

.timeline-container {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 32px;
}
.timeline-list { display: flex; flex-direction: column; gap: 12px; }
.timeline-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: left;
}
.timeline-item:hover { border-color: var(--color-text-subdued); }
.timeline-item.active { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary); }
.item-type { font-size: 13px; font-weight: 500; }
.item-id { font-size: 12px; color: var(--color-text-subdued); float: right; }
.item-status { display: flex; gap: 8px; margin-top: 4px; }

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

@keyframes node-lab-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Tone variables */
.text-success { color: var(--theme--success, #10b981); }
.text-warning { color: var(--theme--warning, #f59e0b); }
.text-danger { color: var(--theme--danger, #dc2626); }
.text-attention { color: var(--theme--warning, #f59e0b); }
.text-neutral { color: var(--color-text-subdued); }

.badge-success { border-color: color-mix(in srgb, var(--theme--success, #10b981) 45%, var(--color-border)); color: var(--theme--success, #10b981); }
.badge-warning { border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 45%, var(--color-border)); color: var(--theme--warning, #f59e0b); }
.badge-danger { border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 45%, var(--color-border)); color: var(--theme--danger, #dc2626); }
.badge-attention { border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 45%, var(--color-border)); color: var(--theme--warning, #f59e0b); }
.badge-neutral { color: var(--color-text-subdued); }
.badge-warning { border-color: color-mix(in srgb, #d97706 45%, var(--color-border)); color: #b45309; }
.badge-danger { border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 45%, var(--color-border)); color: var(--theme--danger, #dc2626); }
.badge-attention { border-color: color-mix(in srgb, #f59e0b 45%, var(--color-border)); color: #c2410c; }
.badge-neutral { color: var(--color-text-subdued); }

@media (max-width: 1200px) {
  .workbench { grid-template-columns: 1fr; }
  .result-shell { position: static; }
  .sessions-workspace { grid-template-columns: 1fr; }
  .timeline-container { grid-template-columns: 1fr; }
  .compare-relation-strip {
    flex-direction: column;
  }
  .compare-overview,
  .compare-status-line,
  .compare-row__body {
    grid-template-columns: 1fr;
  }
}

.list-row {
  display: flex;
  gap: 12px;
  align-items: center;
  width: 100%;
}
.flex-1 {
  flex: 1;
  min-width: 0;
}

</style>

