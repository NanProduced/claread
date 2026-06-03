export const API_ENDPOINTS = {
  modelProfiles: "/eval-center/article-analysis/model-profiles",
  baselineConfig: "/eval-center/node-lab/baseline-config",
  candidates: "/eval-center/node-lab/candidates",
  sessions: "/eval-center/node-lab/sessions",
  run: "/eval-center/node-lab/run",
  compare: "/eval-center/node-lab/compare",
  runHistorySingleRun: "/eval-center/node-lab/run-history/single-run",
  trials: "/eval-center/node-lab/trials",
  judgeConfigs: "/eval-center/node-lab/judge-configs",
  judgePresets: "/eval-center/node-lab/judge-presets",
  judgeRequests: "/eval-center/node-lab/judge-requests",
} as const;

export const STORAGE_KEY = "claread-eval-center:node-lab:v4" as const;

export const DEFAULT_TIMEOUT_SECONDS = 150 as const;

export const NODE_OPTIONS = [
  { id: "grammar", label: "Grammar", description: "语法与长难句拆解实验。" },
  { id: "vocabulary", label: "Vocabulary", description: "词汇与语境释义实验。" },
  { id: "translation", label: "Translation", description: "句级翻译与语气策略实验。" },
] as const;

export const WORKSPACE_OPTIONS = [
  { id: "single_run", label: "Single Run", description: "先看单次输出是否朝正确方向变化。" },
  { id: "baseline_compare", label: "Baseline Compare", description: "同一输入下比较 baseline 与 candidate。" },
  { id: "sessions", label: "Sessions", description: "查看该 node 的实验历史与复盘。" },
] as const;

export const JUDGE_MODES = [
  { id: "rubric_score_only", label: "只按规则打分（逐项过线检查）" },
  { id: "rubric_plus_pairwise", label: "规则打分 + 整体对比评估（先评分，再给整体意见）" },
  { id: "anti_template_probe", label: "反模板化专项诊断（Grammar 专用）" },
] as const;

export const JUDGE_MODES_BY_NODE = {
  grammar: ["rubric_score_only", "rubric_plus_pairwise", "anti_template_probe"],
  vocabulary: ["rubric_score_only", "rubric_plus_pairwise"],
  translation: ["rubric_score_only", "rubric_plus_pairwise"],
} as const;

export const READING_GOAL_OPTIONS = [
  { id: "daily_reading", label: "日常阅读", description: "用于新闻、通识文章和长期阅读训练。" },
  { id: "exam", label: "考试阅读", description: "用于 CET、考研、雅思托福等应试型场景。" },
] as const;

export const READING_VARIANTS_BY_GOAL = {
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
} as const;

export const HELP_TEXT = {
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
} as const;

export const SESSION_FLOW_STEPS = [
  { key: "start", title: "先跑出一条 Compare", detail: "Baseline Compare 是主工作台，先确认这次差异值不值得保存或评审。" },
  { key: "record", title: "再决定是否加入 Session", detail: "Session 是固定上下文的 compare 记录本，只收 compare trial。" },
  { key: "review", title: "最后回来复盘 / Judge", detail: "在 Sessions 里回看时间线，也可以回到 Compare 页继续做 Judge。" },
] as const;

export const TERMINAL_JUDGE_STATUSES = new Set(["succeeded", "failed", "cancelled"] as const);
