<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import RagDebugSnapshotPanel from "./RagDebugSnapshotPanel.vue";
import { buildInspectorVm } from "./inspector-adapters.js";
import { loadInspectorBundle } from "./inspector-data.js";

const route = useRoute();
const router = useRouter();

const loading = ref(false);
const error = ref("");
const bundle = ref(null);
const selectedSentenceId = ref("");
const sentenceQuery = ref("");
const activeSectionId = ref("section-summary");

const recordId = computed(() => {
  const raw = route.query.record;
  return typeof raw === "string" ? raw : "";
});

const resultId = computed(() => {
  const raw = route.query.result;
  return typeof raw === "string" ? raw : "";
});

const taskId = computed(() => {
  const raw = route.query.task;
  return typeof raw === "string" ? raw : "";
});

const inspector = computed(() => {
  if (!bundle.value?.record) return null;
  return buildInspectorVm(bundle.value);
});

const scene = computed(() => inspector.value?.scene ?? null);
const normalized = computed(() => inspector.value?.normalized ?? null);
const adapter = computed(() => inspector.value?.adapter ?? null);

const pageTitle = computed(() => bundle.value?.record?.title || bundle.value?.record?.id || "Render Scene Inspector");

const breadcrumbItems = computed(() => {
  if (!bundle.value?.record?.id) return [];
  return [
    {
      name: "Parse Run Observability",
      to: "/content/analysis_records",
    },
    {
      name: bundle.value.record.title || shortId(bundle.value.record.id),
      to: `/claread-render-scene-inspector?record=${encodeURIComponent(bundle.value.record.id)}`,
    },
  ];
});

const sectionLinks = [
  { id: "section-summary", label: "结果概览" },
  { id: "section-usage", label: "调用消耗" },
  { id: "section-highlights", label: "重点异常" },
  { id: "section-triage", label: "句级排查" },
  { id: "section-runtime", label: "运行证据" },
  { id: "section-snapshot", label: "调试快照" },
  { id: "section-raw", label: "原始数据" },
];

const groupedUsageEvents = computed(() => {
  const events = Array.isArray(bundle.value?.usageEvents) ? bundle.value.usageEvents : [];
  return {
    analysis: events.filter((item) => item.capability_code === "analysis_full"),
    overview: events.filter((item) => item.capability_code === "analysis_overview_hint"),
  };
});

const VOCABULARY_MARK_TYPES = new Set(["vocab_highlight", "phrase_gloss", "context_gloss"]);
const GRAMMAR_ENTRY_TYPES = new Set(["grammar_note", "sentence_analysis"]);

function isVocabularyMark(mark) {
  return VOCABULARY_MARK_TYPES.has(String(mark?.annotation_type || ""));
}

function isGrammarMark(mark) {
  return String(mark?.annotation_type || "") === "grammar_note";
}

function isGrammarEntry(entry) {
  return GRAMMAR_ENTRY_TYPES.has(String(entry?.entry_type || ""));
}

function hasGrammarSupportGap(marks, entries) {
  const markList = Array.isArray(marks) ? marks : [];
  const entryList = Array.isArray(entries) ? entries : [];
  const hasGrammarMark = markList.some((item) => isGrammarMark(item));
  const hasExplanationEntry = entryList.some((item) => isGrammarEntry(item));
  return hasGrammarMark && !hasExplanationEntry;
}

function buildGrammarEvidenceRows(marks, entries) {
  const grammarMarks = (Array.isArray(marks) ? marks : []).filter((item) => isGrammarMark(item));
  const grammarEntries = (Array.isArray(entries) ? entries : []).filter((item) => isGrammarEntry(item));

  if (grammarEntries.length > 0) {
    return grammarEntries.map((entry, index) => {
      const anchorSource = entry.entry_type === "grammar_note" ? grammarMarks[index] : null;
      return {
        id: entry.id || `grammar-${index}`,
        type: translateEntryType(entry.entry_type),
        anchor: anchorSource ? describeAnchor(anchorSource.anchor) : "—",
        label: entry.label || entry.title || "未命名",
        content: entry.content?.trim() || "空内容",
      };
    });
  }

  return grammarMarks.map((mark, index) => ({
    id: mark.id || `grammar-mark-${index}`,
    type: "语法讲解",
    anchor: describeAnchor(mark.anchor),
    label: "缺句级讲解",
    content: "当前只有句内语法定位，没有对应的句级讲解入口。",
  }));
}

const allSentenceRows = computed(() => {
  if (!normalized.value) return [];

  return normalized.value.sentences.map((sentence, index) => {
    const translation = normalized.value.translationsBySentence[sentence.sentence_id] ?? null;
    const marks = normalized.value.marksBySentence[sentence.sentence_id] ?? [];
    const entries = normalized.value.entriesBySentence[sentence.sentence_id] ?? [];
    const warnings = normalized.value.warningsBySentence[sentence.sentence_id] ?? [];
    const vocabularyMarks = marks.filter((item) => isVocabularyMark(item));
    const grammarMarks = marks.filter((item) => isGrammarMark(item));
    const grammarEvidenceRows = buildGrammarEvidenceRows(marks, entries);
    const missingTranslation = !translation?.translation_zh;
    const topWarning = warnings[0]?.code || warnings[0]?.level || "";
    const warningCount = warnings.length;
    const markCount = vocabularyMarks.length;
    const entryCount = grammarEvidenceRows.length;
    const density = markCount + entryCount;
    const emptyEntryCount = grammarEvidenceRows.filter((item) => !String(item?.content || "").trim() || item.content === "空内容").length;
    const grammarSupportGap = hasGrammarSupportGap(marks, entries);
    const densityObservation = density >= (adapter.value?.densityThreshold ?? 4);
    const primaryIssue = summarizeSentenceIssue({
      missingTranslation,
      warningCount,
      topWarning,
      density,
      markCount,
      entryCount,
      emptyEntryCount,
      grammarSupportGap,
      densityObservation,
    });
    const issueLevel =
      missingTranslation || warningCount > 0
        ? "warning"
        : emptyEntryCount > 0 || grammarSupportGap
          ? "attention"
          : densityObservation
            ? "observation"
            : "normal";

    return {
      sentence,
      sentence_id: sentence.sentence_id,
      sentenceIndex: index + 1,
      text: sentence.text || "",
      translation,
      marks,
      entries,
      vocabularyMarks,
      grammarEvidenceRows,
      warnings,
      missingTranslation,
      topWarning,
      warningCount,
      markCount,
      entryCount,
      emptyEntryCount,
      grammarSupportGap,
      density,
      densityObservation,
      primaryIssue,
      vocabularyMarkCount: vocabularyMarks.length,
      grammarEvidenceCount: grammarEvidenceRows.length,
      issueLevel,
    };
  });
});

const sentenceRows = computed(() => {
  const query = sentenceQuery.value.trim().toLowerCase();
  const rows = [...allSentenceRows.value];

  if (!query) return rows;

  return rows.filter((row) => {
    const haystacks = [
      row.sentence_id,
      row.text,
      row.translation?.translation_zh,
      row.primaryIssue.label,
      row.topWarning,
      row.warnings.map((item) => item.message).join(" "),
      row.vocabularyMarks.map((item) => describeMarkContent(item)).join(" "),
      row.grammarEvidenceRows.map((item) => `${item.label} ${item.content}`).join(" "),
    ];
    return haystacks.some((value) => String(value || "").toLowerCase().includes(query));
  });
});

const selectedSentence = computed(() => {
  if (!sentenceRows.value.length) return null;
  if (!selectedSentenceId.value) return sentenceRows.value[0];
  return sentenceRows.value.find((item) => item.sentence_id === selectedSentenceId.value) ?? sentenceRows.value[0];
});

const sentenceStats = computed(() => {
  const rows = allSentenceRows.value;
  const missingTranslation = rows.filter((item) => item.missingTranslation).length;
  const entryGaps = rows.filter((item) => item.grammarSupportGap || item.emptyEntryCount > 0).length;
  const warningSentences = rows.filter((item) => item.warningCount > 0).length;
  return {
    total: rows.length,
    missingTranslation,
    entryGaps,
    warningSentences,
  };
});

const sourceWordCount = computed(() => {
  const text =
    scene.value?.article?.render_text ||
    scene.value?.article?.source_text ||
    bundle.value?.record?.source_text ||
    "";
  return countEnglishWords(text);
});

const usageSummary = computed(() => {
  const runtime = bundle.value?.snapshot?.runtime_summary_json;
  const aggregate = runtime?.aggregate && typeof runtime.aggregate === "object" ? runtime.aggregate : runtime;
  const analysisUsage = groupedUsageEvents.value.analysis[0];
  const inputTokens = firstDefined(aggregate, ["input_tokens", "prompt_tokens", "total_input_tokens"]);
  const outputTokens = firstDefined(aggregate, ["output_tokens", "completion_tokens", "total_output_tokens"]);
  const totalTokens = firstDefined(aggregate, ["total_tokens", "token_total", "tokens_total"]);
  const latencyMs = analysisUsage?.latency_ms ?? firstDefined(runtime, ["latency_ms", "elapsed_ms", "duration_ms"]);
  return {
    inputTokens: typeof inputTokens === "number" ? inputTokens : null,
    outputTokens: typeof outputTokens === "number" ? outputTokens : null,
    totalTokens: typeof totalTokens === "number" ? totalTokens : null,
    latencyMs: typeof latencyMs === "number" ? latencyMs : null,
  };
});

const summaryHelpText = {
  scaleCost:
    "原文词数来自正文英文词计数；句子数来自 render scene；耗时优先取 runtime_summary_json.latency_ms；输入与输出 Tokens 优先取 runtime_summary_json.aggregate，缺失时回退到 usage 记录。",
  annotationDistribution:
    "这里看的是 render scene 最终保留的标注，不是 agent 草稿原样输出。词汇标注对比原文词数，语法标注对比句子数，归一化处理对比候选标注总量。",
  highlights:
    "这里只列真实异常和证据缺口，例如任务失败、缺翻译、句内告警、调试快照缺失、概览未回写。高密度句这类抽查信号不放在这里。",
  triage:
    "句级排查按句子顺序展示证据。这里只标记缺翻译、句内告警、空入口内容、语法讲解缺口和可抽查句；词汇标注与语法标注分开统计，不重复展示同一条语法信息。",
  runtime:
    "运行证据先看主任务和概览任务的状态、失败码、时长和版本，再看事件时间线。事件载荷只提取 worker、版本、积分、tokens 和失败信息，不直接堆原始 JSON。",
  usage:
    "调用消耗先看总 tokens、输入输出、积分和总耗时，再看主解析与概览提示分别占了多少。分组卡显示模型、Prompt 版本和单次调用明细，耗时统一按秒展示。",
  snapshot:
    "调试快照按预处理、标准化、丢弃日志、运行时、RAG、学术质量和 trace 分层查看。RAG 检索单独看查询句、命中样例和召回链路，其余分区先看结构化事实，再回查原始 JSON。",
};

const metricHelpText = {
  原文词数:
    "优先取 article.render_text，其次回退 article.source_text 和 record.source_text，按英文词粒度计数。",
  句子数:
    "基于当前 render scene 的句子行数统计，与句级排查表总句数一致。",
  总耗时:
    "优先取 runtime_summary_json.latency_ms；缺失时回退到主解析 usage event 的 latency_ms。句均耗时 = 总耗时 / 句子数。",
  "输入 Tokens":
    "优先取 runtime_summary_json.aggregate.input_tokens；缺失时回退到其他输入 token 字段。句均值 = 输入 Tokens / 句子数。",
  "输出 Tokens":
    "优先取 runtime_summary_json.aggregate.output_tokens；缺失时回退到其他输出 token 字段。句均值 = 输出 Tokens / 句子数。",
};

const summaryConfigRows = computed(() => {
  const currentBundle = bundle.value;
  const currentResult = currentBundle?.result;
  const currentSnapshot = currentBundle?.snapshot;

  return [
    ["阅读目标", translateReadingGoal(currentBundle?.record?.reading_goal)],
    ["阅读变体", translateReadingVariant(currentBundle?.record?.reading_variant)],
    ["Schema", inspector.value?.schemaVersion || currentResult?.schema_version || "未记录"],
    ["Workflow", currentResult?.workflow_version || "未记录"],
    ["Prompt 版本", currentSnapshot?.prompt_version || "未记录"],
  ];
});

const summaryMetaRows = computed(() => {
  const currentBundle = bundle.value;
  const taskId = currentBundle?.selectedTask?.id || currentBundle?.snapshot?.task_id || "";

  return [
    ["记录 ID", currentBundle?.record?.id || "未记录"],
    ["任务 ID", taskId || "未记录"],
  ];
});

const overviewReferenceStats = computed(() => {
  const totalSentences = sentenceStats.value.total;
  const wordCount = sourceWordCount.value;
  const inputTokens = usageSummary.value.inputTokens;
  const outputTokens = usageSummary.value.outputTokens;
  const latencyMs = usageSummary.value.latencyMs;

  return [
    {
      label: "原文词数",
      value: formatInteger(wordCount),
      detail: totalSentences > 0 ? `${formatNumber(wordCount / totalSentences, 1)} 词/句` : "未记录",
    },
    {
      label: "句子数",
      value: formatInteger(totalSentences),
      detail: sentenceStats.value.missingTranslation > 0 ? `缺翻译 ${sentenceStats.value.missingTranslation} 句` : "翻译覆盖完整",
    },
    {
      label: "总耗时",
      value: formatSeconds(latencyMs),
      detail: totalSentences > 0 && typeof latencyMs === "number"
        ? `${formatSecondsShort(latencyMs / totalSentences)} /句`
        : "未记录",
    },
    {
      label: "输入 Tokens",
      value: formatInteger(inputTokens),
      detail: totalSentences > 0 && typeof inputTokens === "number"
        ? `${formatInteger(Math.round(inputTokens / totalSentences))} /句`
        : "未记录",
    },
    {
      label: "输出 Tokens",
      value: formatInteger(outputTokens),
      detail: totalSentences > 0 && typeof outputTokens === "number"
        ? `${formatInteger(Math.round(outputTokens / totalSentences))} /句`
        : "未记录",
    },
  ];
});

const annotationBreakdownRows = computed(() => {
  const inlineMarks = Array.isArray(scene.value?.inline_marks) ? scene.value.inline_marks : [];
  const entries = Array.isArray(scene.value?.sentence_entries) ? scene.value.sentence_entries : [];
  const wordBase = sourceWordCount.value;
  const sentenceBase = sentenceStats.value.total;
  const currentSnapshot = bundle.value?.snapshot;
  const adapterKind = adapter.value?.kind || "learning";

  const countMarks = (type) => inlineMarks.filter((item) => item?.annotation_type === type).length;
  const countEntries = (type) => entries.filter((item) => item?.entry_type === type).length;

  if (adapterKind === "academic") {
    const termMarks = countMarks("term_note");
    const logicMarks = countMarks("logic_note");
    const interpretationEntries = countEntries("interpretation_note");
    const summaryEntries = countEntries("content_summary");

    return [
      {
        group: "术语标注",
        label: "术语锚点",
        count: termMarks,
        baseLabel: "原文词数",
        baseValue: wordBase,
        percent: formatPercent(termMarks, wordBase),
        detail: `术语标注 ${termMarks} 处`,
      },
      {
        group: "逻辑标注",
        label: "逻辑提示",
        count: logicMarks,
        baseLabel: "句子数",
        baseValue: sentenceBase,
        percent: formatPercent(logicMarks, sentenceBase),
        detail: `逻辑锚点 ${logicMarks} 处`,
      },
      {
        group: "解读入口",
        label: "解读与概要",
        count: interpretationEntries + summaryEntries,
        baseLabel: "句子数",
        baseValue: sentenceBase,
        percent: formatPercent(interpretationEntries + summaryEntries, sentenceBase),
        detail: `解读提示 ${interpretationEntries} / 内容概要 ${summaryEntries}`,
      },
    ];
  }

  const vocabHighlightCount = countMarks("vocab_highlight");
  const phraseGlossCount = countMarks("phrase_gloss");
  const contextGlossCount = countMarks("context_gloss");
  const grammarNoteCount = countEntries("grammar_note");
  const sentenceAnalysisCount = countEntries("sentence_analysis");
  const vocabularyTotal = vocabHighlightCount + phraseGlossCount + contextGlossCount;
  const grammarTotal = grammarNoteCount + sentenceAnalysisCount;
  const keptAnnotationCount = currentSnapshot?.normalize_summary_json?.annotation_count;
  const totalDropCount = currentSnapshot?.drop_log_summary_json?.total_drop_count;
  const qualityDropCount = currentSnapshot?.drop_log_summary_json?.quality_drop_count;
  const densityDropCount =
    typeof totalDropCount === "number" && typeof qualityDropCount === "number"
      ? Math.max(totalDropCount - qualityDropCount, 0)
      : null;
  const normalizeBase =
    typeof keptAnnotationCount === "number" && typeof totalDropCount === "number"
      ? keptAnnotationCount + totalDropCount
      : null;
  const normalizeDetail = [];
  if (typeof qualityDropCount === "number") normalizeDetail.push(`质量丢弃 ${qualityDropCount}`);
  if (typeof densityDropCount === "number") normalizeDetail.push(`密度裁剪 ${densityDropCount}`);
  if (currentSnapshot?.normalize_summary_json?.repair_attempted) {
    normalizeDetail.push(currentSnapshot.normalize_summary_json.repair_succeeded ? "已走修复代理" : "修复代理未成功");
  }

  const rows = [
    {
      group: "词汇标注",
      label: "词汇分布",
      count: vocabularyTotal,
      baseLabel: "原文词数",
      baseValue: wordBase,
      percent: formatPercent(vocabularyTotal, wordBase),
      detail: `词汇高亮 ${vocabHighlightCount} / 短语讲解 ${phraseGlossCount} / 语境说明 ${contextGlossCount}`,
    },
    {
      group: "语法标注",
      label: "语法分布",
      count: grammarTotal,
      baseLabel: "句子数",
      baseValue: sentenceBase,
      percent: formatPercent(grammarTotal, sentenceBase),
      detail: `语法讲解 ${grammarNoteCount} / 句子拆析 ${sentenceAnalysisCount}`,
    },
  ];

  if (typeof totalDropCount === "number") {
    rows.push({
      group: "归一化处理",
      label: "筛除比例",
      count: totalDropCount,
      baseLabel: "候选标注",
      baseValue: normalizeBase,
      percent: formatPercent(totalDropCount, normalizeBase),
      detail: normalizeDetail.join(" / ") || "未记录",
    });
  }

  return rows;
});

const summaryPathState = computed(() => {
  const currentBundle = bundle.value;
  const currentTask = currentBundle?.selectedTask;
  const currentSnapshot = currentBundle?.snapshot;
  const taskStatus = currentSnapshot?.task_status || currentTask?.status || currentBundle?.record?.analysis_status;
  const failureCode = currentSnapshot?.failure_code || currentTask?.failure_code || "";
  const failureMessage = currentSnapshot?.failure_message || currentTask?.failure_message || "";

  if (failureCode) {
    return {
      value: "失败",
      tone: "danger",
      code: failureCode,
      detail: failureMessage || "任务执行失败",
    };
  }

  if (!scene.value && (taskStatus === "succeeded" || taskStatus === "ready")) {
    return {
      value: "结果缺失",
      tone: "warning",
      code: "",
      detail: "任务已结束，但当前没有 render scene 结果",
    };
  }

  if (!scene.value && !currentTask && !currentSnapshot) {
    return {
      value: "未触发",
      tone: "warning",
      code: "",
      detail: "当前没有关联任务",
    };
  }

  if (scene.value) {
    return {
      value: "正常",
      tone: "success",
      code: "",
      detail: "结果快照已生成，可继续检查输出质量",
    };
  }

  return {
    value: translateStatus(taskStatus),
    tone: statusTone(taskStatus),
    code: "",
    detail: "解析仍在进行中或证据未齐备",
  };
});

const summaryPathFacts = computed(() => {
  const currentBundle = bundle.value;
  const currentTask = currentBundle?.selectedTask;
  const currentSnapshot = currentBundle?.snapshot;
  const taskStatus = currentSnapshot?.task_status || currentTask?.status || "未记录";
  const failureCode = currentSnapshot?.failure_code || currentTask?.failure_code || "";

  return [
    ["记录状态", translateStatus(currentBundle?.record?.analysis_status)],
    ["用户态", translateStatus(currentBundle?.record?.user_facing_state)],
    ["任务状态", translateStatus(taskStatus)],
    ...(failureCode ? [["失败码", failureCode]] : []),
  ];
});

const evidenceItems = computed(() => {
  const currentBundle = bundle.value;
  const overviewLane = inspector.value?.derived.overviewLane;
  const usageCount = Array.isArray(currentBundle?.usageEvents) ? currentBundle.usageEvents.length : 0;

  return [
    {
      label: "结果快照",
      value: scene.value ? "已生成" : "缺失",
      tone: scene.value ? "success" : "warning",
    },
    {
      label: "调用记录",
      value: usageCount > 0 ? `${usageCount} 条` : "缺失",
      tone: usageCount > 0 ? "info" : "warning",
    },
    {
      label: "调试快照",
      value: currentBundle?.snapshot ? "已关联" : "缺失",
      tone: currentBundle?.snapshot ? "info" : "warning",
    },
    {
      label: "概览联动",
      value: overviewLane?.label || "未记录",
      tone: overviewLane?.tone || "neutral",
    },
  ];
});

const highlightItems = computed(() => {
  const items = [];
  const currentBundle = bundle.value;
  const currentNormalized = normalized.value;
  const rows = allSentenceRows.value;
  const missingTranslations = rows.filter((item) => item.missingTranslation);
  const sentenceWarnings = rows.filter((item) => item.warningCount > 0);

  if (currentBundle?.selectedTask?.failure_code || currentBundle?.snapshot?.failure_code) {
    items.push({
      label: "任务失败",
      tone: "danger",
      value: currentBundle.snapshot?.failure_code || currentBundle.selectedTask?.failure_code,
      detail: currentBundle.snapshot?.failure_message || currentBundle.selectedTask?.failure_message || "任务执行失败。",
    });
  }

  if (missingTranslations.length > 0) {
    items.push({
      label: "缺翻译句",
      tone: "warning",
      value: String(missingTranslations.length),
      detail: compactSentenceList(missingTranslations.map((item) => item.sentence_id).join(", ")),
    });
  }

  if (sentenceWarnings.length > 0) {
    items.push({
      label: "句内告警",
      tone: "warning",
      value: String(sentenceWarnings.length),
      detail: compactSentenceList(sentenceWarnings.map((item) => item.sentence_id).join(", ")),
    });
  }

  if (currentNormalized?.globalWarnings?.length) {
    items.push({
      label: "全局告警",
      tone: "warning",
      value: String(currentNormalized.globalWarnings.length),
      detail: currentNormalized.globalWarnings
        .map((item) => translateWarningCode(item.code || item.level || "warning"))
        .join("、"),
    });
  }

  if (!currentBundle?.snapshot) {
    items.push({
      label: "调试快照缺失",
      tone: "warning",
      value: "需补证据",
      detail: "当前记录未选中 debug snapshot，无法直接定位 preprocess / RAG / runtime 层。",
    });
  }

  if (!currentBundle?.result?.page_state_json?.derived?.overview_hint && currentBundle?.overviewTask?.status === "succeeded") {
    items.push({
      label: "概览未回写",
      tone: "warning",
      value: "已成功未落表",
      detail: "overview task 已成功，但 page_state_json 中没有 overview_hint。",
    });
  }

  if (items.length === 0) {
    items.push({
      label: "结构信号",
      tone: "success",
      value: "未见明显异常",
      detail: "当前 run 没有暴露出明显结构问题，可抽查高密度句。",
    });
  }

  return items;
});

const attentionCount = computed(() => highlightItems.value.filter((item) => item.tone !== "success").length);

function toTimestamp(value) {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function resolveDurationMs({ startedAt, finishedAt, usageSummary, fallbackMs }) {
  const started = toTimestamp(startedAt);
  const finished = toTimestamp(finishedAt);
  if (started != null && finished != null && finished >= started) return finished - started;

  const usageDuration = firstDefined(usageSummary, ["latency_ms", "elapsed_ms", "duration_ms", "aggregate.latency_ms"]);
  if (typeof usageDuration === "number" && Number.isFinite(usageDuration)) return usageDuration;

  return typeof fallbackMs === "number" && Number.isFinite(fallbackMs) ? fallbackMs : null;
}

function summarizeWorkflowVersion(resultVersion, snapshotVersion) {
  if (resultVersion && snapshotVersion && resultVersion !== snapshotVersion) {
    return `结果 ${resultVersion} / 快照 ${snapshotVersion}`;
  }
  return resultVersion || snapshotVersion || "未记录";
}

function translateEventType(type) {
  const raw = String(type || "");
  const map = {
    task_submitted: "已提交",
    task_started: "开始执行",
    task_finalizing: "收尾中",
    task_succeeded: "已成功",
    task_failed: "已失败",
  };
  return map[raw] || raw || "未记录";
}

function formatTaskRunTime(task) {
  return formatDateTime(task?.started_at || task?.queued_at || task?.finished_at || task?.created_at);
}

function summarizeTaskOption(task, { latest = false } = {}) {
  const status = translateStatus(task?.status);
  const time = formatTaskRunTime(task);
  return latest ? `最新任务 · ${status} · ${time}` : `${status} · ${time}`;
}

function formatModelRef(item) {
  const provider = String(item?.model_provider || "").trim();
  const name = String(item?.model_name || "").trim();
  if (provider && name) return `${provider}/${name}`;
  return provider || name || "未记录";
}

function aggregateUsageStatus(items) {
  const statuses = (Array.isArray(items) ? items : [])
    .map((item) => String(item?.status || "").toLowerCase())
    .filter(Boolean);
  if (!statuses.length) return "unknown";
  if (statuses.some((status) => ["failed", "error"].includes(status))) return "failed";
  if (statuses.some((status) => ["running", "queued", "finalizing", "pending"].includes(status))) return "running";
  if (statuses.every((status) => ["succeeded", "ready"].includes(status))) return "succeeded";
  return statuses[0];
}

function sumNumeric(items, key) {
  return (Array.isArray(items) ? items : []).reduce((sum, item) => {
    const value = item?.[key];
    return typeof value === "number" && Number.isFinite(value) ? sum + value : sum;
  }, 0);
}

function uniqueValues(items, getter) {
  return [...new Set((Array.isArray(items) ? items : []).map(getter).filter(Boolean))];
}

function eventTone(type, payload) {
  const raw = String(type || "");
  if (raw === "task_failed" || payload?.failure_code || payload?.error_code) return "danger";
  if (raw === "task_succeeded") return "success";
  if (raw === "task_finalizing") return "info";
  if (raw === "task_started") return "info";
  return "neutral";
}

function buildEventFacts(payload) {
  const safePayload = payload && typeof payload === "object" ? payload : {};
  const facts = [];
  const usage = safePayload.usage_summary && typeof safePayload.usage_summary === "object" ? safePayload.usage_summary : null;

  if (safePayload.workflow_version) facts.push(`Workflow ${safePayload.workflow_version}`);
  if (safePayload.schema_version) facts.push(`Schema ${safePayload.schema_version}`);
  if (safePayload.prompt_version) facts.push(`Prompt ${safePayload.prompt_version}`);
  if (safePayload.status) facts.push(`状态 ${translateStatus(safePayload.status)}`);
  if (typeof safePayload.cost_points === "number") facts.push(`${formatInteger(safePayload.cost_points)} 积分`);
  if (usage) {
    const inputTokens = firstDefined(usage, ["input_tokens", "prompt_tokens", "aggregate.input_tokens"]);
    const outputTokens = firstDefined(usage, ["output_tokens", "completion_tokens", "aggregate.output_tokens"]);
    const totalTokens = firstDefined(usage, ["total_tokens", "token_total", "aggregate.total_tokens"]);
    if (typeof inputTokens === "number") facts.push(`输入 ${formatInteger(inputTokens)}`);
    if (typeof outputTokens === "number") facts.push(`输出 ${formatInteger(outputTokens)}`);
    if (typeof totalTokens === "number") facts.push(`总 ${formatInteger(totalTokens)}`);
  }
  if (safePayload.failure_code) facts.push(`失败码 ${safePayload.failure_code}`);

  return facts.slice(0, 6);
}

function buildEventIdentifiers(payload) {
  const safePayload = payload && typeof payload === "object" ? payload : {};
  const identifiers = [];

  if (safePayload.worker_token) {
    identifiers.push({
      label: "执行器 ID",
      value: String(safePayload.worker_token),
    });
  }

  return identifiers;
}

function compactFactRows(rows) {
  return rows.filter(([, value]) => value && value !== "未记录");
}

function buildEventDetail(payload) {
  if (!payload || typeof payload !== "object") return "";
  return payload.failure_message || payload.error_message || "";
}

function buildEventTimelineItem(event) {
  const payload = event?.event_payload_json && typeof event.event_payload_json === "object" ? event.event_payload_json : {};
  return {
    id: event?.id || `${event?.event_type}-${event?.created_at}`,
    time: formatDateTime(event?.created_at),
    label: translateEventType(event?.event_type),
    tone: eventTone(event?.event_type, payload),
    facts: buildEventFacts(payload),
    identifiers: buildEventIdentifiers(payload),
    detail: buildEventDetail(payload),
  };
}

const runtimeSummaryCard = computed(() => {
  const currentBundle = bundle.value;
  const currentSnapshot = currentBundle?.snapshot;
  const currentTask = currentBundle?.selectedTask;
  const status = currentSnapshot?.task_status || currentTask?.status || "unknown";
  const failureCode = currentSnapshot?.failure_code || currentTask?.failure_code || "";
  const failureMessage = currentSnapshot?.failure_message || currentTask?.failure_message || "";
  const durationMs = resolveDurationMs({
    startedAt: currentTask?.started_at || currentTask?.queued_at,
    finishedAt: currentTask?.finished_at || currentSnapshot?.updated_at,
    usageSummary: currentTask?.usage_summary_json,
    fallbackMs: firstDefined(currentSnapshot?.runtime_summary_json, ["latency_ms", "elapsed_ms", "duration_ms"]),
  });

  let detail = "主解析任务已完成，可结合任务事件和调试快照继续下钻。";
  if (failureCode) {
    detail = failureMessage || "主解析任务失败。";
  } else if (status === "queued" || status === "running" || status === "finalizing" || status === "pending") {
    detail = "主解析任务仍在进行中，重点查看最新事件和 worker 状态。";
  } else if (!currentTask && !currentSnapshot) {
    detail = "当前没有关联主任务。";
  }

  return {
    title: "主任务",
    value: translateStatus(status),
    tone: statusTone(status),
    code: failureCode,
    detail,
    facts: compactFactRows([
      ["任务 ID", currentTask?.id || currentSnapshot?.task_id || "未记录"],
      ["开始时间", formatDateTime(currentTask?.started_at || currentTask?.queued_at)],
      ["结束时间", formatDateTime(currentTask?.finished_at || currentSnapshot?.updated_at)],
      ["总耗时", durationMs != null ? formatSeconds(durationMs) : "未记录"],
      ["Workflow", summarizeWorkflowVersion(currentBundle?.result?.workflow_version, currentSnapshot?.workflow_version)],
      ["Prompt 版本", currentSnapshot?.prompt_version || "未记录"],
    ]),
  };
});

const overviewSummaryCard = computed(() => {
  const currentBundle = bundle.value;
  const lane = inspector.value?.derived.overviewLane;
  const overviewTask = currentBundle?.overviewTask;
  const durationMs = resolveDurationMs({
    startedAt: overviewTask?.started_at || overviewTask?.queued_at,
    finishedAt: overviewTask?.finished_at || currentBundle?.overviewHint?.updated_at,
    usageSummary: overviewTask?.usage_summary_json,
  });

  let detail = lane?.detail || "当前没有 overview 证据。";
  if (lane?.code === "ready") {
    detail = "概览派生已完成，可确认更新时间和事件链路。";
  }

  return {
    title: "概览任务",
    value: lane?.label || "未记录",
    tone: lane?.tone || "neutral",
    code: overviewTask?.failure_code || "",
    detail,
    facts: compactFactRows([
      ["任务 ID", overviewTask?.id || "未记录"],
      ["更新时间", formatDateTime(currentBundle?.overviewHint?.updated_at)],
      ["失败码", overviewTask?.failure_code || "未记录"],
      ["总耗时", durationMs != null ? formatSeconds(durationMs) : "未记录"],
    ]),
  };
});

const runtimeEventItems = computed(() => (Array.isArray(bundle.value?.taskEvents) ? bundle.value.taskEvents : []).map(buildEventTimelineItem));
const overviewEventItems = computed(() => (Array.isArray(bundle.value?.overviewTaskEvents) ? bundle.value.overviewTaskEvents : []).map(buildEventTimelineItem));
const showTaskSwitcher = computed(() => (Array.isArray(bundle.value?.tasks) ? bundle.value.tasks.length : 0) > 1);
const taskSwitcherOptions = computed(() => {
  const items = Array.isArray(bundle.value?.tasks) ? bundle.value.tasks : [];
  return items.map((item, index) => ({
    value: item.id,
    label: summarizeTaskOption(item, { latest: index === 0 }),
  }));
});

const usageOverviewStats = computed(() => {
  const items = Array.isArray(bundle.value?.usageEvents) ? bundle.value.usageEvents : [];
  const totalInput = sumNumeric(items, "input_tokens");
  const totalOutput = sumNumeric(items, "output_tokens");
  const totalTokens = sumNumeric(items, "total_tokens");
  const totalPoints = sumNumeric(items, "billed_points");
  const totalLatency = sumNumeric(items, "latency_ms");

  return [
    {
      label: "总调用",
      value: formatInteger(items.length),
      detail: items.length > 0 ? `${formatInteger(uniqueValues(items, (item) => formatModelRef(item)).length)} 个模型组合` : "当前没有调用",
    },
    {
      label: "总 Tokens",
      value: formatInteger(totalTokens),
      detail: totalTokens > 0 ? `输入 ${formatInteger(totalInput)} / 输出 ${formatInteger(totalOutput)}` : "未记录",
    },
    {
      label: "总积分",
      value: formatInteger(totalPoints),
      detail: totalPoints > 0 ? "按 usage 事件累加" : "未计费或未记录",
    },
    {
      label: "总耗时",
      value: formatSeconds(totalLatency),
      detail: totalLatency > 0 ? `${formatInteger(items.length)} 次调用累计` : "未记录",
    },
  ];
});

const usageGroups = computed(() => {
  const definitions = [
    {
      key: "analysis",
      title: "主解析调用",
      detail: "对应 render scene 主任务的主模型消耗。",
      items: groupedUsageEvents.value.analysis,
    },
    {
      key: "overview",
      title: "概览提示调用",
      detail: "对应 overview_hint 派生任务的补充消耗。",
      items: groupedUsageEvents.value.overview,
    },
  ];

  const allItems = Array.isArray(bundle.value?.usageEvents) ? bundle.value.usageEvents : [];
  const allTokens = sumNumeric(allItems, "total_tokens");
  const allLatency = sumNumeric(allItems, "latency_ms");

  return definitions.map((definition) => {
    const items = Array.isArray(definition.items) ? definition.items : [];
    const totalInput = sumNumeric(items, "input_tokens");
    const totalOutput = sumNumeric(items, "output_tokens");
    const totalTokens = sumNumeric(items, "total_tokens");
    const totalPoints = sumNumeric(items, "billed_points");
    const totalLatency = sumNumeric(items, "latency_ms");
    const models = uniqueValues(items, (item) => formatModelRef(item));
    const prompts = uniqueValues(items, (item) => String(item?.prompt_version || "").trim());
    const workflowPairs = uniqueValues(items, (item) => {
      const workflow = String(item?.workflow_version || "").trim();
      const schema = String(item?.schema_version || "").trim();
      if (!workflow && !schema) return "";
      return `${workflow || "?"} / ${schema || "?"}`;
    });
    const status = aggregateUsageStatus(items);

    return {
      key: definition.key,
      title: definition.title,
      detail: definition.detail,
      items,
      status,
      statusLabel: translateStatus(status),
      tone: statusTone(status),
      count: items.length,
      facts: [
        { label: "输入 Tokens", value: formatInteger(totalInput), detail: allTokens > 0 && totalInput > 0 ? `占总 Tokens ${formatRatio(totalInput / allTokens)}` : "" },
        { label: "输出 Tokens", value: formatInteger(totalOutput), detail: allTokens > 0 && totalOutput > 0 ? `占总 Tokens ${formatRatio(totalOutput / allTokens)}` : "" },
        { label: "总 Tokens", value: formatInteger(totalTokens), detail: allTokens > 0 && totalTokens > 0 ? `占全局 ${formatRatio(totalTokens / allTokens)}` : "" },
        { label: "积分", value: formatInteger(totalPoints), detail: totalPoints > 0 ? `${formatInteger(items.length)} 次调用累计` : "未计费或未记录" },
        { label: "总耗时", value: formatSeconds(totalLatency), detail: allLatency > 0 && totalLatency > 0 ? `占全局 ${formatRatio(totalLatency / allLatency)}` : "" },
      ],
      metaRows: [
        ["模型", models.length === 0 ? "未记录" : models.length === 1 ? models[0] : `${models.length} 个模型组合`],
        ["Prompt 版本", prompts.length === 0 ? "未记录" : prompts.length === 1 ? prompts[0] : `${prompts.length} 个版本`],
        ["Workflow / Schema", workflowPairs.length === 0 ? "未记录" : workflowPairs.length === 1 ? workflowPairs[0] : `${workflowPairs.length} 组版本组合`],
      ],
      rows: items.map((item) => ({
        id: item.id,
        status: translateStatus(item.status),
        tone: statusTone(item.status),
        model: formatModelRef(item),
        inputTokens: formatInteger(item.input_tokens),
        outputTokens: formatInteger(item.output_tokens),
        totalTokens: formatInteger(item.total_tokens),
        billedPoints: formatInteger(item.billed_points),
        latency: formatSeconds(item.latency_ms),
        promptVersion: item.prompt_version || "未记录",
      })),
    };
  });
});

const snapshotSections = computed(() => {
  const snapshot = bundle.value?.snapshot;
  if (!snapshot) return [];

  return [
    {
      key: "preprocess_summary_json",
      label: "预处理摘要",
      value: snapshot.preprocess_summary_json,
      summary: summarizeSnapshotSection("preprocess", snapshot.preprocess_summary_json),
    },
    {
      key: "normalize_summary_json",
      label: "标准化摘要",
      value: snapshot.normalize_summary_json,
      summary: summarizeSnapshotSection("normalize", snapshot.normalize_summary_json),
    },
    {
      key: "drop_log_summary_json",
      label: "丢弃日志",
      value: snapshot.drop_log_summary_json,
      summary: summarizeSnapshotSection("drop", snapshot.drop_log_summary_json),
    },
    {
      key: "runtime_summary_json",
      label: "运行时摘要",
      value: snapshot.runtime_summary_json,
      summary: summarizeSnapshotSection("runtime", snapshot.runtime_summary_json),
    },
    {
      key: "rag_debug_json",
      label: "RAG 检索",
      value: snapshot.rag_debug_json,
      summary: summarizeSnapshotSection("rag", snapshot.rag_debug_json),
    },
    {
      key: "academic_quality_json",
      label: "学术质量摘要",
      value: snapshot.academic_quality_json,
      summary: summarizeSnapshotSection("academic", snapshot.academic_quality_json),
    },
    {
      key: "trace_refs_json",
      label: "Trace 引用",
      value: snapshot.trace_refs_json,
      summary: summarizeSnapshotSection("trace", snapshot.trace_refs_json),
    },
  ].map((section) => ({
    ...section,
    structured: buildSnapshotStructuredSection(section.key, section.value),
  }));
});

const rawPanels = computed(() => {
  const current = bundle.value;
  if (!current) return [];
  return [
    { label: "request_payload_json", value: current.record?.request_payload_json },
    { label: "render_scene_json", value: current.result?.render_scene_json },
    { label: "page_state_json", value: current.result?.page_state_json },
    { label: "debug_snapshot", value: current.snapshot },
    { label: "analysis_usage_events", value: current.usageEvents },
  ];
});

function objectEntries(value) {
  return Object.entries(value && typeof value === "object" ? value : {}).sort((left, right) => Number(right[1]) - Number(left[1]));
}

function countBy(items, key) {
  const counts = {};

  for (const item of Array.isArray(items) ? items : []) {
    if (!item || typeof item !== "object") continue;
    const raw = item[key];
    const label = raw == null || raw === "" ? "unknown" : String(raw);
    counts[label] = (counts[label] ?? 0) + 1;
  }

  return counts;
}

function formatDateTime(value) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function shortId(value) {
  if (!value) return "未记录";
  const raw = String(value);
  return raw.length > 12 ? `${raw.slice(0, 8)}...${raw.slice(-4)}` : raw;
}

function prettyJson(value) {
  if (value == null) return "null";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function previewText(value, maxLength = 96) {
  const raw = value == null ? "" : String(value).replace(/\s+/g, " ").trim();
  if (raw.length <= maxLength) return raw;
  return `${raw.slice(0, maxLength)}...`;
}

function countEnglishWords(value) {
  const raw = String(value || "");
  const matches = raw.match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g);
  return matches ? matches.length : 0;
}

function formatInteger(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未记录";
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatNumber(value, maximumFractionDigits = 1) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未记录";
  const digits = Number.isInteger(value) ? 0 : maximumFractionDigits;
  return new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: digits > 0 ? Math.min(digits, 1) : 0,
    maximumFractionDigits: digits,
  }).format(value);
}

function formatSeconds(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未记录";
  return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} 秒`;
}

function formatSecondsShort(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未记录";
  return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} 秒`;
}

function formatMilliseconds(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未记录";
  return `${formatNumber(value, value >= 100 ? 0 : 1)} ms`;
}

function formatRatio(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未记录";
  return `${formatNumber(value * 100, value === 0 ? 0 : 1)}%`;
}

function formatPercent(count, base) {
  if (!base || typeof count !== "number" || !Number.isFinite(count)) return "0%";
  return `${((count / base) * 100).toFixed(base >= 100 ? 1 : 0)}%`;
}

function annotationRowHelp(item) {
  if (!item) return "";
  if (item.group === "词汇标注") {
    return "词汇标注统计最终保留的词汇高亮、短语讲解和语境说明，并用原文词数作为基数，帮助判断标注密度是否偏稀或偏密。";
  }
  if (item.group === "语法标注") {
    return "语法标注统计最终保留的语法讲解和句子拆析，并用句子数作为基数，帮助判断句级解释覆盖是否均匀。";
  }
  if (item.group === "归一化处理") {
    return "归一化处理统计 normalize_and_ground 阶段被筛除的候选标注。候选标注 = 最终保留标注 + drop_log 中的筛除项，用于看结构性丢弃与密度裁剪。";
  }
  if (item.group === "术语标注") {
    return "术语标注统计 academic 模式下最终保留的 term_note 锚点，并用原文词数作为基数。";
  }
  if (item.group === "逻辑标注") {
    return "逻辑标注统计 academic 模式下最终保留的 logic_note 锚点，并用句子数作为基数。";
  }
  if (item.group === "解读入口") {
    return "解读入口统计 academic 模式下的 interpretation_note 和 content_summary 入口，用于看后续阅读支持是否齐备。";
  }
  return "";
}

function summarizeSentenceIssue({
  missingTranslation,
  warningCount,
  topWarning,
  density,
  markCount,
  entryCount,
  emptyEntryCount,
  grammarSupportGap,
  densityObservation,
}) {
  if (missingTranslation) {
    return {
      label: "缺翻译",
      tone: "danger",
      detail: "当前句没有生成译文，优先回查 agent 产出和投影结果。",
    };
  }

  if (warningCount > 0) {
    return {
      label: "告警句",
      tone: "warning",
      detail: `${translateWarningCode(topWarning)}，共 ${warningCount} 条告警。`,
    };
  }

  if (emptyEntryCount > 0) {
    return {
      label: "入口内容为空",
      tone: "warning",
      detail: `当前句有 ${emptyEntryCount} 个语法标注项没有正文内容，需要回查投影结果。`,
    };
  }

  if (grammarSupportGap) {
    return {
      label: "语法讲解缺口",
      tone: "info",
      detail: "当前句有语法标注，但没有对应的句级讲解入口。",
    };
  }

  if (densityObservation) {
    return {
      label: "可抽查",
      tone: "info",
      detail: `当前句聚合了 ${markCount} 个标注和 ${entryCount} 个入口，可抽查是否过密。`,
    };
  }

  if (markCount === 0 && entryCount === 0) {
    return {
      label: "结构正常",
      tone: "neutral",
      detail: "当前句未命中标注，也没有句内告警。",
    };
  }

  return {
    label: "结构正常",
    tone: "neutral",
    detail: "当前句没有暴露出明显结构问题。",
  };
}

function compactSentenceList(value) {
  const raw = String(value || "");
  if (!raw) return "未提供详情";
  const parts = raw.split(",").map((item) => item.trim()).filter(Boolean);
  if (parts.length <= 8) return parts.join("、");
  return `${parts.slice(0, 8).join("、")} 等 ${parts.length} 项`;
}

function statusTone(status) {
  const raw = String(status || "").toLowerCase();
  if (["succeeded", "ready", "complete", "normal"].includes(raw)) return "success";
  if (["queued", "running", "finalizing", "pending"].includes(raw)) return "info";
  if (["failed", "error", "degraded", "degraded_heavy"].includes(raw)) return "danger";
  if (["unavailable", "missing", "stale", "degraded_light"].includes(raw)) return "warning";
  return "neutral";
}

function translateStatus(status) {
  const raw = String(status || "").toLowerCase();
  const map = {
    ready: "就绪",
    succeeded: "成功",
    failed: "失败",
    running: "运行中",
    queued: "排队中",
    finalizing: "收尾中",
    pending: "处理中",
    unavailable: "无内容",
    stale: "结果过期",
    normal: "正常",
    degraded_light: "轻度降级",
    degraded_heavy: "重度降级",
    not_triggered: "未触发",
    missing: "缺失",
    unknown: "未知",
  };
  return map[raw] || status || "未记录";
}

function translateMarkType(type) {
  const raw = String(type || "");
  const map = {
    vocab_highlight: "词汇高亮",
    phrase_gloss: "短语讲解",
    context_gloss: "语境说明",
    grammar_note: "语法讲解",
    term: "术语标注",
    logic: "逻辑标注",
    interpretation: "理解提示",
  };
  return map[raw] || raw || "未标记";
}

function translateEntryType(type) {
  const raw = String(type || "");
  const map = {
    sentence_analysis: "句子拆析",
    grammar_note: "语法讲解",
    content_summary: "内容概要",
    term: "术语解释",
    logic: "逻辑说明",
    interpretation: "理解说明",
  };
  return map[raw] || raw || "未标记";
}

function translateWarningCode(code) {
  const raw = String(code || "");
  const map = {
    DRAFT_VALIDATION: "草稿校验",
    translation_coverage_incomplete: "翻译覆盖不完整",
    missing_translation: "缺翻译",
    empty_entry: "空讲解",
  };
  return map[raw] || raw || "未标记";
}

function translateReadingGoal(goal) {
  const raw = String(goal || "");
  const map = {
    exam: "考试精读",
    academic: "学术精读",
    daily_reading: "日常精读",
  };
  return map[raw] || raw || "未记录";
}

function translateReadingVariant(variant) {
  const raw = String(variant || "");
  const map = {
    cet: "CET",
    exam: "考试",
    academic: "学术",
    intermediate_reading: "通用中阶",
  };
  return map[raw] || raw || "未记录";
}

function translateAgentId(agentId) {
  const raw = String(agentId || "");
  const map = {
    vocabulary: "词汇代理",
    grammar: "语法代理",
    translation: "翻译代理",
    repair: "修复代理",
    term: "术语代理",
    understanding: "理解代理",
  };
  return map[raw] || raw || "未命名代理";
}

function translateFewShotMode(mode) {
  const raw = String(mode || "");
  const map = {
    baseline: "基线",
    rag: "RAG",
    rag_fallback: "RAG 回退",
    manual: "手动",
  };
  return map[raw] || raw || "未记录";
}

function translateDebugOutputType(outputType) {
  const raw = String(outputType || "");
  const map = {
    grammar_note: "语法讲解",
    sentence_analysis: "句子拆析",
  };
  return map[raw] || raw || "未记录";
}

function translateIssueLevel(level) {
  const map = {
    warning: "高优先",
    attention: "需检查",
    observation: "可抽查",
    normal: "正常",
  };
  return map[level] || level || "正常";
}

function issueLevelTone(level) {
  const map = {
    warning: "danger",
    attention: "warning",
    observation: "info",
    normal: "neutral",
  };
  return map[level] || "neutral";
}

function describeAnchor(anchor) {
  if (!anchor || typeof anchor !== "object") return "未记录";
  if (anchor.kind === "multi_text" && Array.isArray(anchor.parts)) {
    return anchor.parts.map((item) => item?.anchor_text).filter(Boolean).join(" / ") || "未记录";
  }
  return anchor.anchor_text || "未记录";
}

function describeMarkContent(mark) {
  if (!mark || typeof mark !== "object") return "未记录";
  const glossary = mark.glossary && typeof mark.glossary === "object" ? mark.glossary : null;
  if (glossary?.zh) return glossary.zh;
  if (glossary?.gloss && glossary?.reason) return `${glossary.gloss}；${glossary.reason}`;
  if (glossary?.gloss) return glossary.gloss;
  if (glossary?.reason) return glossary.reason;
  if (mark.annotation_type === "vocab_highlight") return "";
  if (mark.annotation_type === "grammar_note") return "";
  return "";
}

function summarizeVocabularyMarks(marks) {
  const counts = countBy(Array.isArray(marks) ? marks : [], "annotation_type");
  const order = ["vocab_highlight", "phrase_gloss", "context_gloss"];
  const parts = order
    .filter((type) => counts[type] > 0)
    .map((type) => `${translateMarkType(type)} ${counts[type]}`);
  return parts.length ? parts.join(" · ") : "—";
}

function summarizeGrammarEvidence(rows) {
  const counts = {};
  for (const item of Array.isArray(rows) ? rows : []) {
    const label = String(item?.type || "");
    if (!label) continue;
    counts[label] = (counts[label] ?? 0) + 1;
  }
  const order = ["语法讲解", "句子拆析"];
  const parts = order
    .filter((type) => counts[type] > 0)
    .map((type) => `${type} ${counts[type]}`);
  return parts.length ? parts.join(" · ") : "—";
}

function summarizeWarningEvidence(row) {
  if (!row?.warningCount) return "—";
  return `${translateWarningCode(row.topWarning)} ${row.warningCount} 条`;
}

function evidenceTone(kind, count) {
  if (!count) return "neutral";
  if (kind === "warning") return "danger";
  if (kind === "grammar") return "info";
  return "success";
}

function summarizeJsonBlock(value) {
  if (value == null) return "当前无数据";
  if (Array.isArray(value)) return `数组 ${value.length} 项`;
  if (typeof value === "object") {
    const keys = Object.keys(value);
    if (keys.length === 0) return "空对象";
    return `${keys.length} 个键：${keys.slice(0, 3).join("、")}`;
  }
  return String(value);
}

function summarizeSnapshotSection(kind, value) {
  if (value == null) return "当前无数据";

  if (kind === "preprocess" && value && typeof value === "object") {
    const textType = firstDefined(value, ["text_type"]);
    const language = firstDefined(value, ["language_detected"]);
    const fastPath = firstDefined(value, ["fast_path"]);
    return [textType, language, fastPath === true ? "快速路径" : fastPath === false ? "完整路径" : ""].filter(Boolean).join(" · ") || summarizeJsonBlock(value);
  }

  if (kind === "normalize" && value && typeof value === "object") {
    if (value.mode === "learning") {
      return `学习解析 · 标注 ${presentValue(value.annotation_count)} · 翻译 ${presentValue(value.translation_count)}`;
    }
    if (value.mode === "academic") {
      return `学术解析 · 术语 ${presentValue(value.term_annotation_count)} · 解读 ${presentValue(value.interpretation_note_count)}`;
    }
  }

  if (kind === "drop" && value && typeof value === "object") {
    return `总丢弃 ${presentValue(value.total_drop_count)} · 质量筛除 ${presentValue(value.quality_drop_count)}`;
  }

  if (kind === "runtime" && value && typeof value === "object") {
    const perAgent = value.per_agent && typeof value.per_agent === "object" ? Object.keys(value.per_agent) : [];
    const totalTokens = firstDefined(value, ["aggregate.total_tokens", "total_tokens", "token_total"]);
    const tokenPart = totalTokens ? `总 tokens ${formatInteger(totalTokens)}` : "未记录总 tokens";
    return perAgent.length > 0 ? `${perAgent.length} 个代理有调用，${tokenPart}` : tokenPart;
  }

  if (kind === "rag" && value && typeof value === "object") {
    const grammarAgents =
      value.agents?.grammar && typeof value.agents.grammar === "object"
        ? Object.values(value.agents.grammar)
        : [];
    const selectedCount = grammarAgents.reduce(
      (sum, item) => sum + (Array.isArray(item?.selected_examples) ? item.selected_examples.length : 0),
      0,
    );
    const droppedCount = grammarAgents.reduce(
      (sum, item) => sum + (Array.isArray(item?.dropped_examples) ? item.dropped_examples.length : 0),
      0,
    );
    if (selectedCount > 0 || droppedCount > 0) {
      return `命中 ${selectedCount} 条 · 淘汰 ${droppedCount} 条`;
    }
    return summarizeJsonBlock(value);
  }

  if (kind === "trace" && value && typeof value === "object") {
    const requestId = firstDefined(value, ["request_id"]);
    return requestId ? `request ${previewText(requestId, 20)}` : `trace 引用 ${Object.keys(value).length} 项`;
  }

  return summarizeJsonBlock(value);
}

function getByPath(source, path) {
  if (!source || typeof source !== "object") return undefined;
  const keys = Array.isArray(path) ? path : String(path).split(".");
  let current = source;
  for (const key of keys) {
    if (!current || typeof current !== "object" || !(key in current)) return undefined;
    current = current[key];
  }
  return current;
}

function firstDefined(source, paths) {
  for (const path of paths) {
    const value = getByPath(source, path);
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function countCollection(value) {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") return Object.keys(value).length;
  return undefined;
}

function presentValue(value) {
  if (value == null || value === "") return "未记录";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "未记录";
  if (Array.isArray(value)) return `${value.length} 项`;
  if (value && typeof value === "object") return `${Object.keys(value).length} 个键`;
  return previewText(String(value), 96);
}

function createFact(label, value, detail = "") {
  if (value === undefined || value === null || value === "") return null;
  return {
    label,
    value: presentValue(value),
    detail: detail ? previewText(detail, 120) : "",
  };
}

function createRow(label, value, detail = "", options = {}) {
  if (value === undefined || value === null || value === "") return null;
  return {
    label,
    value: options.raw ? String(value) : presentValue(value),
    detail: detail ? previewText(detail, 160) : "",
    code: Boolean(options.code),
  };
}

function summarizeTopEntries(value, limit = 5) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value)
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .slice(0, limit)
    .map(([key, count]) => ({
      label: previewText(key, 36),
      value: presentValue(count),
    }));
}

function buildAgentUsageRows(perAgent) {
  if (!perAgent || typeof perAgent !== "object") return [];
  return Object.entries(perAgent)
    .map(([agentId, usage]) => {
      const totalTokens = firstDefined(usage, ["total_tokens", "tokens_total", "token_total"]);
      const latency = firstDefined(usage, ["latency_ms", "elapsed_ms", "duration_ms"]);
      const calls = firstDefined(usage, ["call_count", "request_count", "invocation_count"]);
      return {
        label: translateAgentId(agentId),
        value:
          totalTokens != null
            ? `${formatInteger(totalTokens)} tokens`
            : latency != null
              ? formatMilliseconds(latency)
              : calls != null
                ? `${formatInteger(calls)} 次`
                : "有调用",
        detail:
          [
            calls != null ? `${formatInteger(calls)} 次调用` : "",
            latency != null ? formatMilliseconds(latency) : "",
          ]
            .filter(Boolean)
            .join(" / "),
      };
    })
    .sort((left, right) => extractLeadingNumber(right.value) - extractLeadingNumber(left.value));
}

function buildCountRows(value, limit = 8) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value)
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .slice(0, limit)
    .map(([label, count]) => ({
      label,
      value: presentValue(count),
      detail: "",
      code: false,
    }));
}

function extractLeadingNumber(value) {
  const match = String(value || "").match(/-?\d+(\.\d+)?/);
  return match ? Number(match[0]) : 0;
}

function buildGenericSnapshotGroups(value) {
  if (!value || typeof value !== "object") return [];

  const scalarRows = [];
  const arrayRows = [];
  const objectRows = [];

  for (const [key, item] of Object.entries(value)) {
    if (item == null || item === "") continue;
    if (Array.isArray(item)) {
      arrayRows.push({ label: key, value: `${item.length} 项` });
      continue;
    }
    if (typeof item === "object") {
      objectRows.push({ label: key, value: `${Object.keys(item).length} 个键` });
      continue;
    }
    scalarRows.push({ label: key, value: presentValue(item) });
  }

  const groups = [];
  if (scalarRows.length) groups.push({ title: "关键字段", rows: scalarRows.slice(0, 8) });
  if (arrayRows.length) groups.push({ title: "数组结构", rows: arrayRows.slice(0, 8) });
  if (objectRows.length) groups.push({ title: "对象结构", rows: objectRows.slice(0, 8) });
  return groups;
}

function buildSnapshotStructuredSection(kind, value) {
  if (value == null) {
    return {
      facts: [],
      groups: [],
      note: "",
    };
  }

  const facts = [];
  const groups = [];
  let note = "";

  if (kind === "runtime_summary_json") {
    const perAgent = firstDefined(value, ["per_agent"]);
    const activeAgentCount = countCollection(perAgent);
    const totalTokens = firstDefined(value, ["total_tokens", "tokens_total", "token_total", "aggregate.total_tokens"]);
    const inputTokens = firstDefined(value, ["input_tokens", "prompt_tokens", "total_input_tokens", "aggregate.input_tokens"]);
    const outputTokens = firstDefined(value, ["output_tokens", "completion_tokens", "total_output_tokens", "aggregate.output_tokens"]);
    const latency = firstDefined(value, ["latency_ms", "elapsed_ms", "duration_ms"]);
    const billedPoints = firstDefined(value, ["billed_points", "points"]);
    [createFact("调用统计", firstDefined(value, ["usage_available"])), createFact("活跃代理", activeAgentCount), createFact("总 Tokens", totalTokens), createFact("输入 Tokens", inputTokens), createFact("输出 Tokens", outputTokens), createFact("积分", billedPoints), createFact("总耗时", latency != null ? formatMilliseconds(latency) : undefined)]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const agentRows = buildAgentUsageRows(perAgent);
    if (agentRows.length) groups.push({ title: "按代理查看", rows: agentRows });
  } else if (kind === "preprocess_summary_json") {
    [
      createFact("文本类型", firstDefined(value, ["text_type"])),
      createFact("快速路径", firstDefined(value, ["fast_path"])),
      createFact("识别语言", firstDefined(value, ["language_detected"])),
      createFact("英文占比", firstDefined(value, ["english_ratio"]) != null ? formatRatio(firstDefined(value, ["english_ratio"])) : undefined),
      createFact("噪音占比", firstDefined(value, ["noise_ratio"]) != null ? formatRatio(firstDefined(value, ["noise_ratio"])) : undefined),
      createFact("清洗动作", firstDefined(value, ["sanitize.action_count"])),
    ]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const sanitizeActions = asArray(firstDefined(value, ["sanitize.actions"]));
    const sanitizeRows = [];
    if (sanitizeActions.length) {
      sanitizeRows.push(
        ...sanitizeActions.map((item, index) => ({
          label: `动作 ${index + 1}`,
          value: String(item),
          detail: "",
          code: false,
        })),
      );
    }
    const removedCount = firstDefined(value, ["sanitize.removed_segment_count"]);
    if (removedCount !== undefined) {
      sanitizeRows.push({
        label: "移除片段",
        value: presentValue(removedCount),
        detail: "",
        code: false,
      });
    }
    if (sanitizeRows.length) groups.push({ title: "清洗动作", rows: sanitizeRows });

    const sourceHash = firstDefined(value, ["source_text_hash"]);
    if (sourceHash) {
      groups.push({
        title: "输入指纹",
        rows: [createRow("source_text_hash", sourceHash, "", { raw: true, code: true })].filter(Boolean),
      });
    }
  } else if (kind === "normalize_summary_json") {
    const mode = firstDefined(value, ["mode"]);
    const modeLabel = mode === "academic" ? "学术解析" : mode === "learning" ? "学习解析" : mode;
    [createFact("解析模式", modeLabel)].filter(Boolean).forEach((item) => facts.push(item));

    if (mode === "learning") {
      [
        createFact("最终标注", firstDefined(value, ["annotation_count"])),
        createFact("翻译句数", firstDefined(value, ["translation_count"])),
        createFact("质量筛除", firstDefined(value, ["quality_drop_count"])),
        createFact("总丢弃", firstDefined(value, ["total_drop_count"])),
        createFact("修复尝试", firstDefined(value, ["repair_attempted"])),
        createFact("修复成功", firstDefined(value, ["repair_succeeded"])),
      ]
        .filter(Boolean)
        .forEach((item) => facts.push(item));

      const warningCodes = asArray(firstDefined(value, ["warning_codes"]));
      groups.push({
        title: "告警与修复",
        rows: [
          createRow("全局告警", warningCodes.length > 0 ? warningCodes.map((code) => translateWarningCode(code)).join("、") : "无"),
          createRow("修复尝试", firstDefined(value, ["repair_attempted"])),
          createRow("修复成功", firstDefined(value, ["repair_succeeded"])),
        ].filter(Boolean),
      });
    } else if (mode === "academic") {
      [
        createFact("术语标注", firstDefined(value, ["term_annotation_count"])),
        createFact("翻译句数", firstDefined(value, ["translation_count"])),
        createFact("逻辑说明", firstDefined(value, ["logic_note_count"])),
        createFact("解读入口", firstDefined(value, ["interpretation_note_count"])),
        createFact("段落角色", firstDefined(value, ["paragraph_role_count"])),
        createFact("内容概要", firstDefined(value, ["content_summary_present"])),
      ]
        .filter(Boolean)
        .forEach((item) => facts.push(item));

      groups.push({
        title: "学术派生",
        rows: [
          createRow("逻辑说明", firstDefined(value, ["logic_note_count"])),
          createRow("解读入口", firstDefined(value, ["interpretation_note_count"])),
          createRow("段落角色", firstDefined(value, ["paragraph_role_count"])),
          createRow("内容概要", firstDefined(value, ["content_summary_present"])),
        ].filter(Boolean),
      });
    }
  } else if (kind === "rag_debug_json") {
    const grammarAgents = value.agents?.grammar && typeof value.agents.grammar === "object" ? value.agents.grammar : {};
    const outputKeys = Object.keys(grammarAgents);
    const selectedCount = outputKeys.reduce((sum, key) => sum + asArray(grammarAgents[key]?.selected_examples).length, 0);
    const droppedCount = outputKeys.reduce((sum, key) => sum + asArray(grammarAgents[key]?.dropped_examples).length, 0);

    [
      createFact("输出类型", outputKeys.length),
      createFact("命中样例", selectedCount),
      createFact("淘汰项", droppedCount),
    ]
      .filter(Boolean)
      .forEach((item) => facts.push(item));
    note = "RAG 检索已拆成专用视图，先看查询句、命中样例和 ANN / rerank / 淘汰链路；原始 JSON 只作为回查层。";
  } else if (kind === "drop_log_summary_json") {
    const totalDrops = firstDefined(value, ["total_drop_count"]);
    const qualityDrops = firstDefined(value, ["quality_drop_count"]);
    const densityDrops =
      typeof totalDrops === "number" && typeof qualityDrops === "number"
        ? Math.max(0, totalDrops - qualityDrops)
        : undefined;
    [createFact("总丢弃", totalDrops), createFact("质量筛除", qualityDrops), createFact("密度裁剪", densityDrops)]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const stageRows = buildCountRows(firstDefined(value, ["by_stage"]));
    if (stageRows.length) groups.push({ title: "按阶段", rows: stageRows });
    const agentRows = buildCountRows(firstDefined(value, ["by_source_agent"])).map((row) => ({
      ...row,
      label: translateAgentId(row.label),
    }));
    if (agentRows.length) groups.push({ title: "按来源代理", rows: agentRows });
    const typeRows = buildCountRows(firstDefined(value, ["by_annotation_type"])).map((row) => ({
      ...row,
      label: translateMarkType(row.label) !== row.label ? translateMarkType(row.label) : translateEntryType(row.label),
      detail:
        translateMarkType(row.label) !== row.label || translateEntryType(row.label) !== row.label
          ? row.label
          : "",
    }));
    if (typeRows.length) groups.push({ title: "按标注类型", rows: typeRows });

    const topReasons = asArray(firstDefined(value, ["top_reasons"]));
    if (topReasons.length) {
      groups.push({
        title: "主要原因",
        rows: topReasons.map((item, index) => ({
          label: item?.reason || `原因 ${index + 1}`,
          value: presentValue(item?.count),
          detail: "",
          code: false,
        })),
      });
    }
  } else if (kind === "academic_quality_json") {
    [
      createFact("质量状态", firstDefined(value, ["quality_state", "status", "verdict", "quality_status"])),
      createFact("问题数", asArray(firstDefined(value, ["quality_issues"])).length),
      createFact("段落角色", asArray(firstDefined(value, ["paragraph_roles"])).length),
    ]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const issues = asArray(firstDefined(value, ["quality_issues"]));
    if (issues.length) {
      groups.push({
        title: "质量问题",
        rows: issues.map((item, index) => ({
          label: `问题 ${index + 1}`,
          value: String(item),
          detail: "",
          code: false,
        })),
      });
    }

    const paragraphRoles = asArray(firstDefined(value, ["paragraph_roles"]));
    if (paragraphRoles.length) {
      groups.push({
        title: "段落角色",
        rows: paragraphRoles.slice(0, 8).map((item, index) => ({
          label: item?.paragraph_id || item?.paragraph_index || `段落 ${index + 1}`,
          value: item?.role || item?.paragraph_role || previewText(prettyJson(item), 72),
          detail: item?.reason || item?.description || "",
          code: false,
        })),
      });
    }
  } else if (kind === "trace_refs_json") {
    [
      createFact("Request ID", firstDefined(value, ["request_id"])),
      createFact("LangSmith", firstDefined(value, ["langsmith_enabled"])),
      createFact("Project", firstDefined(value, ["langsmith_project"])),
      createFact("Workflow Run ID", firstDefined(value, ["workflow_run_id"])),
      createFact("Trace URL", firstDefined(value, ["trace_url"])),
    ]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const traceRows = [
      createRow("Request ID", firstDefined(value, ["request_id"]), "", { raw: true, code: true }),
      createRow("Workflow Run ID", firstDefined(value, ["workflow_run_id"]), "", { raw: true, code: true }),
      createRow("Trace URL", firstDefined(value, ["trace_url"]), "", { raw: true }),
    ].filter(Boolean);
    if (traceRows.length) groups.push({ title: "追踪引用", rows: traceRows });
  }

  if (!groups.length) {
    groups.push(...buildGenericSnapshotGroups(value));
  }

  return {
    facts,
    groups,
    note,
  };
}

function scrollToSection(sectionId) {
  activeSectionId.value = sectionId;
  document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function updateActiveSectionFromScroll() {
  if (typeof window === "undefined") return;
  const threshold = 140;
  let current = sectionLinks[0]?.id || "section-summary";

  for (const section of sectionLinks) {
    const element = document.getElementById(section.id);
    if (!element) continue;
    const top = element.getBoundingClientRect().top;
    if (top <= threshold) current = section.id;
  }

  activeSectionId.value = current;
}

async function refresh() {
  loading.value = true;
  error.value = "";

  try {
    bundle.value = await loadInspectorBundle({
      recordId: recordId.value,
      resultId: resultId.value,
      taskId: taskId.value,
    });
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Inspector 数据加载失败";
    bundle.value = null;
  } finally {
    loading.value = false;
  }
}

function selectSentence(sentenceId) {
  selectedSentenceId.value = sentenceId;
}

function onTaskChange(event) {
  const nextTaskId = event.target.value;
  router.replace({
    query: {
      ...route.query,
      task: nextTaskId || undefined,
    },
  });
}

watch(
  () => [recordId.value, resultId.value, taskId.value],
  refresh,
  { immediate: true },
);

watch(
  sentenceRows,
  (rows) => {
    if (!rows.length) {
      selectedSentenceId.value = "";
      return;
    }

    const preferred =
      rows.find((item) => item.warningCount > 0 || item.missingTranslation || item.emptyEntryCount > 0 || item.grammarSupportGap)?.sentence_id ||
      rows.find((item) => item.issueLevel === "observation")?.sentence_id ||
      rows[0].sentence_id;

    if (!rows.some((item) => item.sentence_id === selectedSentenceId.value)) {
      selectedSentenceId.value = preferred;
    }
  },
  { immediate: true },
);

watch(
  () => bundle.value?.record?.id,
  () => {
    activeSectionId.value = "section-summary";
    requestAnimationFrame(() => updateActiveSectionFromScroll());
  },
);

onMounted(() => {
  window.addEventListener("scroll", updateActiveSectionFromScroll, { passive: true });
  requestAnimationFrame(() => updateActiveSectionFromScroll());
});

onBeforeUnmount(() => {
  window.removeEventListener("scroll", updateActiveSectionFromScroll);
});
</script>

<template>
  <private-view :title="pageTitle">
    <template v-if="breadcrumbItems.length" #headline>
      <v-breadcrumb :items="breadcrumbItems" />
    </template>

    <template #title-outer:prepend>
      <v-button rounded disabled icon secondary>
        <v-icon name="visibility" />
      </v-button>
    </template>

    <template v-if="bundle?.record" #navigation>
      <div class="module-navigation">
        <section class="module-navigation-group">
          <div class="module-navigation-label">页面导航</div>
          <button
            v-for="section in sectionLinks"
            :key="section.id"
            type="button"
            class="module-navigation-link"
            :class="{ 'is-active': activeSectionId === section.id }"
            :aria-current="activeSectionId === section.id ? 'location' : undefined"
            @click="scrollToSection(section.id)"
          >
            <span class="module-navigation-dot" :class="{ 'is-active': activeSectionId === section.id }" aria-hidden="true"></span>
            <span>{{ section.label }}</span>
          </button>
        </section>
      </div>
    </template>

    <template v-if="bundle?.record" #actions:prepend>
      <div class="header-inline">
        <span class="header-pill" :class="`tone-${statusTone(bundle.record.analysis_status)}`">
          {{ translateStatus(bundle.record.analysis_status) }}
        </span>
        <span class="header-copy">
          {{ adapter?.title || "解析结果" }} / {{ inspector?.schemaVersion || "未记录" }}
        </span>
      </div>
    </template>

    <template v-if="bundle?.record" #actions>
      <v-input
        class="module-search"
        :model-value="sentenceQuery"
        placeholder="搜索句子、译文、告警"
        @update:model-value="sentenceQuery = $event"
      >
        <template #prepend>
          <v-icon name="search" />
        </template>
      </v-input>
      <v-button secondary @click="refresh">刷新</v-button>
    </template>

    <div class="module-body">
      <v-info v-if="loading" icon="hourglass_top" title="正在装配 Inspector 数据">
        正在读取记录、结果、任务、事件、调用消耗和调试快照。
      </v-info>

      <v-info v-else-if="error" icon="error" title="Inspector 加载失败" type="danger">
        {{ error }}
      </v-info>

      <v-info v-else-if="!bundle?.record" icon="info" title="缺少 record 参数">
        请从 `analysis_records` 或 `analysis_results` 详情页进入 Inspector。
      </v-info>

      <template v-else>
        <section id="section-summary" class="section-card">
          <div class="section-head">
            <div>
              <div class="section-kicker">结果概览</div>
              <h2>{{ bundle.record.title || "未命名记录" }}</h2>
            </div>
            <div class="section-note">
              <span>{{ sentenceStats.total }} 句</span>
              <span>{{ attentionCount }} 个待关注项</span>
            </div>
          </div>

          <div class="summary-panel">
            <div class="summary-main">
              <div class="summary-block">
                <div class="summary-title">范围与版本</div>
                <dl class="summary-facts">
                  <div v-for="[label, value] in summaryConfigRows" :key="label">
                    <dt>{{ label }}</dt>
                    <dd>
                      <span>{{ value }}</span>
                    </dd>
                  </div>
                </dl>
                <dl class="summary-meta-list">
                  <div v-for="[label, value] in summaryMetaRows" :key="label">
                    <dt>{{ label }}</dt>
                    <dd><code class="copyable-code">{{ value }}</code></dd>
                  </div>
                </dl>
              </div>

              <div class="summary-block">
                <div class="summary-title-row">
                  <div class="help-inline">
                    <div class="summary-title">规模与成本</div>
                    <button type="button" class="help-trigger" aria-label="规模与成本说明">
                      ?
                      <span class="help-tooltip">{{ summaryHelpText.scaleCost }}</span>
                    </button>
                  </div>
                </div>
                <div class="summary-reference-strip">
                  <div
                    v-for="(item, index) in overviewReferenceStats"
                    :key="item.label"
                    class="summary-reference-item"
                  >
                    <div class="metric-label">
                      <span>{{ item.label }}</span>
                      <button
                        type="button"
                        class="help-trigger metric-help"
                        :aria-label="`${item.label} 说明`"
                      >
                        ?
                        <span class="help-tooltip" :class="{ 'align-right': index >= 3 }">
                          {{ metricHelpText[item.label] || "未提供说明" }}
                        </span>
                      </button>
                    </div>
                    <strong class="metric-value">{{ item.value }}</strong>
                    <small class="metric-detail">{{ item.detail }}</small>
                  </div>
                </div>
              </div>

              <div class="summary-block">
                <div class="summary-title-row">
                  <div class="help-inline">
                    <div class="summary-title">标注分布</div>
                    <button type="button" class="help-trigger" aria-label="标注分布说明">
                      ?
                      <span class="help-tooltip">{{ summaryHelpText.annotationDistribution }}</span>
                    </button>
                  </div>
                </div>
                <table class="compact-table summary-metric-table">
                  <thead>
                    <tr>
                      <th>类别</th>
                      <th>数量</th>
                      <th>对比基数</th>
                      <th>占比</th>
                      <th>细分</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in annotationBreakdownRows" :key="`${item.group}-${item.label}`">
                      <td>
                        <div class="table-label">
                          <span>{{ item.group }}</span>
                          <button
                            type="button"
                            class="help-trigger table-help"
                            :aria-label="`${item.group} 说明`"
                          >
                            ?
                            <span class="help-tooltip">{{ annotationRowHelp(item) }}</span>
                          </button>
                        </div>
                      </td>
                      <td class="table-number">{{ item.count }}</td>
                      <td class="table-base">
                        <span>{{ item.baseLabel }}</span>
                        <strong>{{ formatInteger(item.baseValue) }}</strong>
                      </td>
                      <td class="table-percent">{{ item.percent }}</td>
                      <td class="table-detail">{{ item.detail }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div class="summary-aside">
              <div class="summary-block">
                <div class="summary-title">解析路径</div>
                <div class="summary-state-head">
                  <span class="badge" :class="`tone-${summaryPathState.tone}`">{{ summaryPathState.value }}</span>
                  <code v-if="summaryPathState.code" class="copyable-code">{{ summaryPathState.code }}</code>
                </div>
                <p class="summary-copy">{{ summaryPathState.detail }}</p>
                <dl class="summary-path-facts">
                  <div v-for="[label, value] in summaryPathFacts" :key="label">
                    <dt>{{ label }}</dt>
                    <dd>{{ value }}</dd>
                  </div>
                </dl>
              </div>

              <div class="summary-block">
                <div class="summary-title">证据状态</div>
                <dl class="summary-evidence-list">
                  <div v-for="item in evidenceItems" :key="item.label" class="summary-evidence-row">
                    <dt>{{ item.label }}</dt>
                    <dd>
                      <span class="badge" :class="`tone-${item.tone}`">{{ item.value }}</span>
                    </dd>
                  </div>
                </dl>
              </div>
            </div>
          </div>
        </section>

        <section id="section-usage" class="section-card">
          <div class="section-head">
            <div>
              <div class="help-inline">
                <h2>调用消耗</h2>
                <button type="button" class="help-trigger" aria-label="调用消耗说明">
                  ?
                  <span class="help-tooltip">{{ summaryHelpText.usage }}</span>
                </button>
              </div>
            </div>
          </div>

          <div class="summary-reference-strip usage-overview-strip">
            <div v-for="item in usageOverviewStats" :key="item.label" class="summary-reference-item usage-overview-item">
              <span class="usage-overview-label">{{ item.label }}</span>
              <strong class="metric-value">{{ item.value }}</strong>
              <small class="metric-detail">{{ item.detail }}</small>
            </div>
          </div>

          <div class="usage-grid">
            <article v-for="group in usageGroups" :key="group.key" class="sub-card usage-card">
              <div class="usage-card-head">
                <div class="usage-card-copy">
                  <h3>{{ group.title }}</h3>
                  <p class="muted">{{ group.detail }}</p>
                </div>
                <div class="split-stats">
                  <span class="badge" :class="`tone-${group.tone}`">{{ group.statusLabel }}</span>
                  <span class="split-stat-chip tone-neutral">{{ group.count }} 次调用</span>
                </div>
              </div>

              <div class="usage-card-facts">
                <div v-for="fact in group.facts" :key="`${group.key}-${fact.label}`" class="usage-fact-item">
                  <span>{{ fact.label }}</span>
                  <strong>{{ fact.value }}</strong>
                  <small v-if="fact.detail" class="metric-detail">{{ fact.detail }}</small>
                </div>
              </div>

              <dl class="usage-meta-list">
                <div v-for="[label, value] in group.metaRows" :key="`${group.key}-${label}`">
                  <dt>{{ label }}</dt>
                  <dd>{{ value }}</dd>
                </div>
              </dl>

              <table class="compact-table usage-call-table" v-if="group.rows.length">
                <thead>
                  <tr>
                    <th>状态</th>
                    <th>模型</th>
                    <th>输入</th>
                    <th>输出</th>
                    <th>总 Tokens</th>
                    <th>积分</th>
                    <th>耗时</th>
                    <th>Prompt</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in group.rows" :key="item.id">
                    <td>
                      <span class="badge" :class="`tone-${item.tone}`">{{ item.status }}</span>
                    </td>
                    <td>{{ item.model }}</td>
                    <td>{{ item.inputTokens }}</td>
                    <td>{{ item.outputTokens }}</td>
                    <td>{{ item.totalTokens }}</td>
                    <td>{{ item.billedPoints }}</td>
                    <td>{{ item.latency }}</td>
                    <td>{{ item.promptVersion }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-else class="empty-copy">当前没有调用记录</div>
            </article>
          </div>
        </section>

        <section id="section-highlights" class="section-card">
          <div class="section-head">
            <div>
              <div class="help-inline">
                <h2>重点异常</h2>
                <button type="button" class="help-trigger" aria-label="重点异常说明">
                  ?
                  <span class="help-tooltip">{{ summaryHelpText.highlights }}</span>
                </button>
              </div>
            </div>
          </div>

          <table class="compact-table">
            <thead>
              <tr>
                <th>异常项</th>
                <th>状态</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in highlightItems" :key="`${item.label}-${item.value}`">
                <td>{{ item.label }}</td>
                <td>
                  <span class="badge" :class="`tone-${item.tone}`">{{ item.value }}</span>
                </td>
                <td>{{ item.detail }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section id="section-triage" class="section-card">
          <div class="section-head">
            <div>
              <div class="help-inline">
                <h2>句级排查</h2>
                <button type="button" class="help-trigger" aria-label="句级排查说明">
                  ?
                  <span class="help-tooltip">{{ summaryHelpText.triage }}</span>
                </button>
              </div>
            </div>
          </div>

          <v-info v-if="!scene" icon="article" title="当前记录没有 render scene">
            可以先查看运行证据和调试快照，定位为什么没有结果。
          </v-info>

          <template v-else>
            <div class="triage-layout">
              <div class="triage-table-shell">
                <table class="compact-table triage-table">
                  <thead>
                    <tr>
                      <th>句子</th>
                      <th>状态</th>
                      <th>关注点</th>
                      <th>译文</th>
                      <th>词汇标注</th>
                      <th>语法标注</th>
                      <th>告警</th>
                      <th>原句预览</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in sentenceRows"
                      :key="row.sentence_id"
                      class="clickable-row"
                      :class="{ selected: selectedSentence?.sentence_id === row.sentence_id }"
                      @click="selectSentence(row.sentence_id)"
                    >
                      <td class="sentence-id-cell">{{ row.sentence_id }}</td>
                      <td>
                        <span class="badge" :class="`tone-${issueLevelTone(row.issueLevel)}`">
                          {{ translateIssueLevel(row.issueLevel) }}
                        </span>
                      </td>
                      <td>
                        <span class="badge" :class="`tone-${row.primaryIssue.tone || 'neutral'}`">
                          {{ row.primaryIssue.label }}
                        </span>
                      </td>
                      <td>
                        <span class="badge" :class="row.missingTranslation ? 'tone-danger' : 'tone-success'">
                          {{ row.missingTranslation ? "缺翻译" : "已生成" }}
                        </span>
                      </td>
                      <td>
                        <div
                          class="evidence-count-cell"
                          :class="[`tone-${evidenceTone('vocabulary', row.vocabularyMarkCount)}`, { 'is-active': row.vocabularyMarkCount > 0 }]"
                        >
                          <strong>{{ row.vocabularyMarkCount }}</strong>
                          <span>{{ summarizeVocabularyMarks(row.vocabularyMarks) }}</span>
                        </div>
                      </td>
                      <td>
                        <div
                          class="evidence-count-cell"
                          :class="[`tone-${evidenceTone('grammar', row.grammarEvidenceCount)}`, { 'is-active': row.grammarEvidenceCount > 0 }]"
                        >
                          <strong>{{ row.grammarEvidenceCount }}</strong>
                          <span>{{ summarizeGrammarEvidence(row.grammarEvidenceRows) }}</span>
                        </div>
                      </td>
                      <td>
                        <div
                          class="evidence-count-cell"
                          :class="[`tone-${evidenceTone('warning', row.warningCount)}`, { 'is-active': row.warningCount > 0 }]"
                        >
                          <strong>{{ row.warningCount }}</strong>
                          <span>{{ summarizeWarningEvidence(row) }}</span>
                        </div>
                      </td>
                      <td>{{ previewText(row.text, 90) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <aside class="triage-detail-panel">
                <div class="split-head">
                  <div>
                    <div class="section-kicker">句子详情</div>
                    <h2>{{ selectedSentence?.sentence_id || "未选择句子" }}</h2>
                  </div>
                  <div class="split-stats" v-if="selectedSentence">
                    <span class="badge" :class="`tone-${issueLevelTone(selectedSentence.issueLevel)}`">
                      {{ translateIssueLevel(selectedSentence.issueLevel) }}
                    </span>
                    <span class="split-stat-chip" :class="`tone-${evidenceTone('vocabulary', selectedSentence.vocabularyMarkCount)}`">
                      {{ selectedSentence.vocabularyMarkCount }} 词汇标注
                    </span>
                    <span class="split-stat-chip" :class="`tone-${evidenceTone('grammar', selectedSentence.grammarEvidenceCount)}`">
                      {{ selectedSentence.grammarEvidenceCount }} 语法标注
                    </span>
                    <span class="split-stat-chip" :class="`tone-${evidenceTone('warning', selectedSentence.warningCount)}`">
                      {{ selectedSentence.warningCount }} 告警
                    </span>
                  </div>
                </div>

                <v-info v-if="!selectedSentence" icon="article" title="未选择句子">
                  在左侧表格中选择一行，这里会显示原句、译文、标注、讲解和告警。
                </v-info>

                <template v-else>
                  <section class="split-section">
                    <h3>原句</h3>
                    <p>{{ selectedSentence.text || "未记录" }}</p>
                  </section>

                  <section class="split-section">
                    <h3>译文</h3>
                    <p>{{ selectedSentence.translation?.translation_zh || "未生成翻译" }}</p>
                  </section>

                  <section class="split-section">
                    <div class="detail-section-head">
                      <h3>词汇标注</h3>
                      <span class="split-stat-chip" :class="`tone-${evidenceTone('vocabulary', selectedSentence.vocabularyMarkCount)}`">
                        {{ selectedSentence.vocabularyMarkCount }} 项
                      </span>
                    </div>
                    <ul class="detail-list" v-if="selectedSentence.vocabularyMarks.length">
                      <li v-for="item in selectedSentence.vocabularyMarks" :key="item.id" class="detail-list-item">
                        <div class="detail-item-top">
                          <span class="badge tone-success">{{ translateMarkType(item.annotation_type || item.visual_tone) }}</span>
                          <strong class="detail-anchor">{{ describeAnchor(item.anchor) }}</strong>
                        </div>
                        <div v-if="describeMarkContent(item)" class="detail-content detail-item-copy">{{ describeMarkContent(item) }}</div>
                      </li>
                    </ul>
                    <div v-else class="empty-copy">该句当前没有词汇标注</div>
                  </section>

                  <section class="split-section">
                    <div class="detail-section-head">
                      <h3>语法标注</h3>
                      <span class="split-stat-chip" :class="`tone-${evidenceTone('grammar', selectedSentence.grammarEvidenceCount)}`">
                        {{ selectedSentence.grammarEvidenceCount }} 项
                      </span>
                    </div>
                    <ul class="detail-list" v-if="selectedSentence.grammarEvidenceRows.length">
                      <li v-for="item in selectedSentence.grammarEvidenceRows" :key="item.id" class="detail-list-item">
                        <div class="detail-item-top">
                          <span class="badge tone-info">{{ item.type }}</span>
                          <strong class="detail-anchor">{{ item.anchor }}</strong>
                        </div>
                        <div class="detail-content detail-item-copy preserve-line">
                          <strong v-if="item.label && item.label !== '未命名'" class="detail-inline-label">{{ item.label }}</strong>
                          <span>{{ item.content }}</span>
                        </div>
                      </li>
                    </ul>
                    <div v-else class="empty-copy">该句当前没有语法标注</div>
                  </section>

                  <section class="split-section">
                    <div class="detail-section-head">
                      <h3>告警</h3>
                      <span class="split-stat-chip" :class="`tone-${evidenceTone('warning', selectedSentence.warningCount)}`">
                        {{ selectedSentence.warningCount }} 条
                      </span>
                    </div>
                    <ul class="detail-list" v-if="selectedSentence.warnings.length">
                      <li
                        v-for="item in selectedSentence.warnings"
                        :key="`${selectedSentence.sentence_id}-${item.code || item.level}-${item.message}`"
                        class="detail-list-item detail-list-item-warning"
                      >
                        <div class="detail-item-top">
                          <span class="badge tone-danger">{{ translateWarningCode(item.code || item.level) }}</span>
                          <span class="detail-anchor">{{ translateStatus(item.level) }}</span>
                        </div>
                        <div class="detail-content detail-item-copy">{{ item.message || "未记录" }}</div>
                      </li>
                    </ul>
                    <div v-else class="empty-copy">该句未绑定告警</div>
                  </section>
                </template>
              </aside>
            </div>
          </template>
        </section>

        <section id="section-runtime" class="section-card">
          <div class="section-head">
            <div>
              <div class="help-inline">
                <h2>运行证据</h2>
                <button type="button" class="help-trigger" aria-label="运行证据说明">
                  ?
                  <span class="help-tooltip">{{ summaryHelpText.runtime }}</span>
                </button>
              </div>
            </div>
            <label v-if="showTaskSwitcher" class="task-switcher">
              <span>历史任务</span>
              <select :value="taskId" @change="onTaskChange">
                <option value="">自动跟随最新任务</option>
                <option v-for="item in taskSwitcherOptions" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </label>
          </div>

          <div class="evidence-grid">
            <div class="sub-card runtime-summary-card">
              <div class="runtime-summary-head">
                <div>
                  <h3>{{ runtimeSummaryCard.title }}</h3>
                  <div class="runtime-summary-state">
                    <span class="badge" :class="`tone-${runtimeSummaryCard.tone}`">{{ runtimeSummaryCard.value }}</span>
                    <code v-if="runtimeSummaryCard.code" class="copyable-code">{{ runtimeSummaryCard.code }}</code>
                  </div>
                </div>
              </div>
              <p class="summary-copy runtime-summary-copy">{{ runtimeSummaryCard.detail }}</p>
              <dl class="runtime-summary-facts">
                <div v-for="[label, value] in runtimeSummaryCard.facts" :key="label">
                  <dt>{{ label }}</dt>
                  <dd :class="{ 'copyable-code': label === '任务 ID' }">{{ value }}</dd>
                </div>
              </dl>
            </div>

            <div class="sub-card runtime-summary-card">
              <div class="runtime-summary-head">
                <div>
                  <h3>{{ overviewSummaryCard.title }}</h3>
                  <div class="runtime-summary-state">
                    <span class="badge" :class="`tone-${overviewSummaryCard.tone}`">{{ overviewSummaryCard.value }}</span>
                    <code v-if="overviewSummaryCard.code" class="copyable-code">{{ overviewSummaryCard.code }}</code>
                  </div>
                </div>
              </div>
              <p class="summary-copy runtime-summary-copy">{{ overviewSummaryCard.detail }}</p>
              <dl class="runtime-summary-facts">
                <div v-for="[label, value] in overviewSummaryCard.facts" :key="label">
                  <dt>{{ label }}</dt>
                  <dd :class="{ 'copyable-code': label === '任务 ID' }">{{ value }}</dd>
                </div>
              </dl>
            </div>
          </div>

          <div class="event-grid">
            <div class="sub-card">
              <h3>任务事件</h3>
              <div v-if="runtimeEventItems.length" class="event-timeline">
                <article v-for="event in runtimeEventItems" :key="event.id" class="event-item">
                  <div class="event-item-head">
                    <time class="event-time">{{ event.time }}</time>
                    <span class="badge" :class="`tone-${event.tone}`">{{ event.label }}</span>
                  </div>
                  <div v-if="event.facts.length" class="event-facts">
                    <span v-for="fact in event.facts" :key="`${event.id}-${fact}`" class="event-fact-chip">{{ fact }}</span>
                  </div>
                  <dl v-if="event.identifiers.length" class="event-identifiers">
                    <div v-for="identifier in event.identifiers" :key="`${event.id}-${identifier.label}`">
                      <dt>{{ identifier.label }}</dt>
                      <dd><code class="copyable-code">{{ identifier.value }}</code></dd>
                    </div>
                  </dl>
                  <p v-if="event.detail" class="event-detail">{{ event.detail }}</p>
                </article>
              </div>
              <div v-else class="empty-copy">当前任务没有事件</div>
            </div>

            <div class="sub-card">
              <h3>概览任务事件</h3>
              <div v-if="overviewEventItems.length" class="event-timeline">
                <article v-for="event in overviewEventItems" :key="event.id" class="event-item">
                  <div class="event-item-head">
                    <time class="event-time">{{ event.time }}</time>
                    <span class="badge" :class="`tone-${event.tone}`">{{ event.label }}</span>
                  </div>
                  <div v-if="event.facts.length" class="event-facts">
                    <span v-for="fact in event.facts" :key="`${event.id}-${fact}`" class="event-fact-chip">{{ fact }}</span>
                  </div>
                  <dl v-if="event.identifiers.length" class="event-identifiers">
                    <div v-for="identifier in event.identifiers" :key="`${event.id}-${identifier.label}`">
                      <dt>{{ identifier.label }}</dt>
                      <dd><code class="copyable-code">{{ identifier.value }}</code></dd>
                    </div>
                  </dl>
                  <p v-if="event.detail" class="event-detail">{{ event.detail }}</p>
                </article>
              </div>
              <div v-else class="empty-copy">当前没有概览任务事件</div>
            </div>
          </div>
        </section>

        <section id="section-snapshot" class="section-card">
          <div class="section-head">
            <div>
              <div class="help-inline">
                <h2>调试快照</h2>
                <button type="button" class="help-trigger" aria-label="调试快照说明">
                  ?
                  <span class="help-tooltip">{{ summaryHelpText.snapshot }}</span>
                </button>
              </div>
            </div>
          </div>

          <div v-if="snapshotSections.length" class="details-stack">
            <details
              v-for="section in snapshotSections"
              :key="section.key"
              class="details-card"
              :open="section.key === 'normalize_summary_json' || section.key === 'runtime_summary_json' || section.key === 'rag_debug_json'"
            >
              <summary>
                <strong>{{ section.label }}</strong>
                <span>{{ section.summary }}</span>
              </summary>
              <div class="snapshot-body">
                <RagDebugSnapshotPanel v-if="section.key === 'rag_debug_json'" :value="section.value" />

                <template v-else>
                  <div v-if="section.structured.facts.length" class="snapshot-facts-grid">
                    <div v-for="fact in section.structured.facts" :key="`${section.key}-${fact.label}`" class="fact-card">
                      <span>{{ fact.label }}</span>
                      <strong>{{ fact.value }}</strong>
                      <span v-if="fact.detail" class="muted wrap-copy">{{ fact.detail }}</span>
                    </div>
                  </div>

                  <div v-if="section.structured.groups.length" class="snapshot-groups">
                    <section v-for="group in section.structured.groups" :key="`${section.key}-${group.title}`" class="snapshot-group">
                      <h3>{{ group.title }}</h3>
                      <dl class="snapshot-group-list">
                        <div v-for="row in group.rows" :key="`${group.title}-${row.label}`" class="snapshot-group-row">
                          <dt>{{ row.label }}</dt>
                          <dd :class="{ 'copyable-code': row.code }">{{ row.value }}</dd>
                          <div v-if="row.detail" class="muted wrap-copy">{{ row.detail }}</div>
                        </div>
                      </dl>
                    </section>
                  </div>

                  <div v-if="section.structured.note" class="snapshot-note">
                    {{ section.structured.note }}
                  </div>
                </template>

                <details class="snapshot-raw-toggle">
                  <summary>查看原始 JSON</summary>
                  <pre>{{ prettyJson(section.value) }}</pre>
                </details>
              </div>
            </details>
          </div>
          <div v-else class="empty-copy">当前没有选中的调试快照</div>
        </section>

        <section id="section-raw" class="section-card">
          <div class="section-head">
            <div>
              <div class="section-kicker">原始数据</div>
              <h2>作为兜底回查，不作为主视图</h2>
            </div>
          </div>

          <div class="details-stack">
            <details v-for="panel in rawPanels" :key="panel.label" class="details-card">
              <summary>
                <strong>{{ panel.label }}</strong>
                <span>{{ summarizeJsonBlock(panel.value) }}</span>
              </summary>
              <pre>{{ prettyJson(panel.value) }}</pre>
            </details>
          </div>
        </section>
      </template>
    </div>
  </private-view>
</template>

<style scoped>
.module-body {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 20px 24px 32px;
  background: var(--theme--background-page, #f5f7fa);
  min-height: 100%;
  font-size: 0.9375rem;
  line-height: 1.5;
  font-kerning: normal;
}

.header-inline,
.section-note,
.split-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.header-inline,
.section-note,
.split-stats,
.compact-table,
.summary-facts dd,
.distribution-row,
.fact-card strong,
.verdict-card strong {
  font-variant-numeric: tabular-nums;
}

.header-copy,
.muted,
.empty-copy,
.fact-card span,
.distribution-row span,
.section-kicker {
  color: #4b5563;
}

.header-copy,
.muted,
.empty-copy,
.section-note,
.split-stats {
  font-size: 0.875rem;
  line-height: 1.5;
}

.header-pill,
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
  border: 1px solid var(--theme--border-color, #d9dee7);
  white-space: nowrap;
}

.section-card,
.sub-card,
.triage-detail-panel {
  background: var(--theme--background-normal, #ffffff);
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 12px;
}

.section-card,
.triage-detail-panel {
  padding: 20px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}

.sub-card {
  padding: 16px;
  background: var(--theme--background-subdued, #fafbfc);
}

.details-stack,
.subsection,
.usage-grid,
.split-section,
.summary-main,
.summary-aside,
.summary-block,
.snapshot-body,
.snapshot-groups,
.snapshot-group,
.module-navigation,
.module-navigation-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.split-section h3,
.sub-card h3 {
  margin: 0;
}

.split-section h3,
.sub-card h3 {
  font-size: 0.9375rem;
  line-height: 1.45;
  font-weight: 700;
}

.section-head,
.split-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.section-head h2,
.split-head h2 {
  margin: 0;
  font-size: 1.625rem;
  line-height: 1.25;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.section-kicker {
  font-size: 0.75rem;
  line-height: 1.5;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-bottom: 6px;
  font-weight: 700;
}

.summary-panel,
.evidence-grid,
.event-grid,
.usage-grid {
  display: grid;
  gap: 16px;
}

.usage-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.summary-panel {
  grid-template-columns: minmax(0, 1.7fr) minmax(320px, 0.9fr);
}

.summary-block {
  padding: 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-subdued, #fafbfc);
}

.summary-title {
  margin: 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.45;
  font-weight: 700;
}

.summary-title-row,
.help-inline,
.metric-label,
.table-label {
  display: flex;
  align-items: center;
  gap: 6px;
}

.summary-title-row {
  margin-bottom: 2px;
}

.help-trigger {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: 1px solid var(--theme--border-color, #d9dee7);
  border-radius: 999px;
  background: var(--theme--background-normal, #ffffff);
  color: #245cb8;
  font-size: 0.75rem;
  line-height: 1;
  font-weight: 700;
  cursor: help;
  flex: 0 0 auto;
}

.help-trigger:focus-visible {
  outline: 2px solid #245cb8;
  outline-offset: 2px;
}

.help-tooltip {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 24;
  width: 260px;
  max-width: min(260px, 60vw);
  padding: 10px 12px;
  border-radius: 8px;
  background: #172940;
  color: #eef5ff;
  font-size: 0.75rem;
  line-height: 1.55;
  font-weight: 500;
  text-align: left;
  white-space: normal;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.22);
  opacity: 0;
  pointer-events: none;
  transform: translateY(4px);
  transition: opacity 140ms ease, transform 140ms ease;
}

.help-tooltip.align-right {
  left: auto;
  right: 0;
}

.help-trigger:hover .help-tooltip,
.help-trigger:focus-visible .help-tooltip {
  opacity: 1;
  transform: translateY(0);
}

.metric-help,
.table-help {
  width: 16px;
  height: 16px;
}

.summary-copy {
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.6;
}

.detail-content {
  color: var(--theme--foreground, #172940);
  line-height: 1.65;
  white-space: normal;
  overflow-wrap: anywhere;
}

.detail-inline-label {
  display: block;
  margin-bottom: 6px;
  color: var(--theme--foreground, #172940);
  font-size: 0.8125rem;
  line-height: 1.45;
  font-weight: 700;
}

.preserve-line {
  white-space: pre-wrap;
}

.summary-reference-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}

.summary-reference-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
}

.usage-overview-strip {
  margin-bottom: 16px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.usage-overview-item {
  min-height: 96px;
}

.usage-overview-label {
  color: #4b5563;
  font-size: 0.78125rem;
  line-height: 1.5;
  font-weight: 700;
}

.metric-label span {
  color: #4b5563;
  font-size: 0.78125rem;
  line-height: 1.5;
  font-weight: 700;
}

.metric-value {
  color: var(--theme--foreground, #172940);
  font-size: 1.5rem;
  line-height: 1.15;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.metric-detail {
  color: #4b5563;
  font-size: 0.78125rem;
  line-height: 1.5;
  font-weight: 600;
}

.usage-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.usage-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.usage-card-copy {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.usage-card-copy p {
  margin: 0;
}

.usage-card-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}

.usage-fact-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
}

.usage-fact-item span {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
}

.usage-fact-item strong {
  color: var(--theme--foreground, #172940);
  font-size: 1rem;
  line-height: 1.35;
  font-weight: 700;
}

.usage-meta-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding-top: 12px;
  border-top: 1px solid var(--theme--border-color-subdued, #e3e7ee);
}

.usage-meta-list div {
  display: grid;
  grid-template-columns: 7rem minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.usage-meta-list dt {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
}

.usage-meta-list dd {
  margin: 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.55;
  word-break: break-word;
}

.usage-call-table {
  margin-top: 2px;
}

.summary-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0;
}

.summary-facts div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.summary-facts dt {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
}

.summary-facts dd {
  margin: 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.9375rem;
  line-height: 1.5;
  word-break: break-word;
}

.summary-meta-list {
  display: grid;
  gap: 10px;
  margin: 16px 0 0;
  padding-top: 14px;
  border-top: 1px solid var(--theme--border-color-subdued, #e3e7ee);
}

.summary-meta-list div {
  display: grid;
  grid-template-columns: 5.5rem minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.summary-meta-list dt,
.summary-path-facts dt,
.summary-evidence-row dt {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
}

.summary-meta-list dd {
  margin: 0;
}

.summary-path-facts,
.summary-evidence-list {
  display: grid;
  gap: 10px;
  margin: 0;
}

.summary-state-head,
.summary-evidence-row,
.summary-path-facts div {
  display: grid;
  grid-template-columns: 5.5rem minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.summary-state-head {
  grid-template-columns: auto 1fr;
  margin-top: 12px;
}

.summary-path-facts dd,
.summary-evidence-row dd {
  margin: 0;
  min-width: 0;
}

.summary-path-facts {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--theme--border-color-subdued, #e3e7ee);
}

.summary-evidence-list {
  margin-top: 12px;
}

.summary-evidence-row {
  padding-top: 10px;
  border-top: 1px solid var(--theme--border-color-subdued, #e3e7ee);
}

.summary-evidence-row:first-child {
  padding-top: 0;
  border-top: none;
}

.summary-metric-table th,
.summary-metric-table td {
  padding-block: 10px;
}

.table-number,
.table-percent {
  white-space: nowrap;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.table-base {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.table-base span,
.table-detail {
  color: #4b5563;
}

.table-base strong {
  color: var(--theme--foreground, #172940);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.table-detail {
  line-height: 1.65;
}

.compact-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
  line-height: 1.6;
}

.compact-table th,
.compact-table td {
  padding: 12px 14px;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  word-break: break-word;
}

.compact-table th {
  color: #4b5563;
  font-weight: 600;
  font-size: 0.75rem;
  line-height: 1.5;
  letter-spacing: 0.04em;
  background: var(--theme--background-subdued, #fafbfc);
}

.compact-table tbody tr:last-child td {
  border-bottom: none;
}

.clickable-row {
  cursor: pointer;
}

.clickable-row:hover td {
  background: #f8fbff;
}

.clickable-row.selected td {
  background: #eef5ff;
}

.evidence-grid,
.event-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 16px;
}

.details-card {
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-subdued, #fafbfc);
  overflow: hidden;
}

.details-card summary {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  cursor: pointer;
  list-style: none;
}

.details-card summary::-webkit-details-marker {
  display: none;
}

.details-card summary span {
  color: #4b5563;
  font-size: 0.8125rem;
  line-height: 1.5;
}

.snapshot-body {
  padding: 4px 16px 16px;
  gap: 16px;
}

.snapshot-facts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.fact-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 104px;
  padding: 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
}

.fact-card span {
  font-size: 0.75rem;
  line-height: 1.55;
  font-weight: 600;
}

.fact-card strong {
  color: var(--theme--foreground, #172940);
  font-size: 1rem;
  line-height: 1.35;
  font-weight: 700;
}

.snapshot-groups {
  gap: 12px;
}

.snapshot-group {
  padding: 16px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
}

.snapshot-group h3 {
  margin: 0;
  font-size: 0.875rem;
  line-height: 1.45;
  font-weight: 700;
}

.snapshot-group-list {
  display: grid;
  gap: 0;
  margin: 0;
}

.snapshot-group-row {
  display: grid;
  grid-template-columns: minmax(120px, 160px) minmax(0, 1fr);
  gap: 4px 14px;
  padding: 12px 0;
  border-top: 1px solid var(--theme--border-color-subdued, #e3e7ee);
}

.snapshot-group-row:first-child {
  padding-top: 0;
  border-top: none;
}

.snapshot-group-list dt {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
}

.snapshot-group-list dd {
  margin: 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.6;
  word-break: break-word;
}

.snapshot-group-row .muted {
  grid-column: 2;
  margin-top: 2px;
}

.snapshot-note {
  padding: 12px 14px;
  border: 1px solid #d7e6fb;
  border-radius: 10px;
  background: #f7fbff;
  color: #245cb8;
  font-size: 0.875rem;
  line-height: 1.7;
}

.snapshot-raw-toggle {
  border-top: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  padding-top: 12px;
}

.snapshot-raw-toggle summary {
  color: #4b5563;
  font-size: 0.8125rem;
  line-height: 1.5;
  font-weight: 700;
  cursor: pointer;
  list-style: none;
}

.snapshot-raw-toggle summary::-webkit-details-marker {
  display: none;
}

.details-card pre {
  margin: 0;
  padding: 16px;
  border-top: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  background: #0f172a;
  color: #e5edf7;
  font-size: 0.8125rem;
  line-height: 1.55;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.module-navigation {
  padding: 8px 8px 16px;
}

.module-navigation-group {
  position: relative;
  gap: 2px;
  padding-left: 8px;
}

.module-navigation-group::before {
  content: "";
  position: absolute;
  left: 0;
  top: 2px;
  bottom: 2px;
  width: 1px;
  background: var(--theme--border-color-subdued, #e3e7ee);
}

.module-navigation-label {
  color: #4b5563;
  font-size: 0.71875rem;
  line-height: 1.5;
  font-weight: 700;
  letter-spacing: 0.02em;
  padding: 0 12px 6px;
}

.module-navigation-link {
  appearance: none;
  box-shadow: none;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 9px;
  min-height: 32px;
  padding: 0 12px 0 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #30445f;
  font-size: 0.875rem;
  line-height: 1.45;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
  width: 100%;
  position: relative;
  transition: background-color 140ms ease, color 140ms ease;
}

.module-navigation-link:hover {
  background: #f2f5f9;
}

.module-navigation-link.is-active {
  background: #eef5ff;
  color: #245cb8;
  font-weight: 700;
}

.module-navigation-link.is-active::before {
  content: "";
  position: absolute;
  left: -8px;
  top: 6px;
  bottom: 6px;
  width: 2px;
  border-radius: 999px;
  background: #245cb8;
}

.module-navigation-dot {
  width: 4px;
  height: 4px;
  border-radius: 999px;
  background: #b8c3d1;
  flex: 0 0 auto;
}

.module-navigation-dot.is-active {
  background: #245cb8;
}

.triage-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(320px, 0.9fr);
  gap: 20px;
  align-items: start;
}

.triage-table-shell {
  min-width: 0;
}

.triage-table .badge {
  justify-content: center;
}

.triage-table td {
  vertical-align: top;
}

.sentence-id-cell {
  font-weight: 700;
  white-space: nowrap;
}

.evidence-count-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 6.5rem;
  padding: 8px 10px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-subdued, #fafbfc);
}

.evidence-count-cell strong {
  color: var(--theme--foreground, #172940);
  font-size: 1rem;
  line-height: 1.2;
  font-weight: 700;
}

.evidence-count-cell span {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
}

.evidence-count-cell.is-active {
  box-shadow: inset 0 0 0 1px currentColor;
}

.runtime-summary-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.runtime-summary-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.runtime-summary-state {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.runtime-summary-copy {
  margin: 0;
}

.runtime-summary-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0;
  padding-top: 14px;
  border-top: 1px solid var(--theme--border-color-subdued, #e3e7ee);
}

.runtime-summary-facts div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.runtime-summary-facts dt {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
}

.runtime-summary-facts dd {
  margin: 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.55;
  word-break: break-word;
}

.event-timeline {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.event-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
}

.event-item-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.event-time {
  color: #4b5563;
  font-size: 0.8125rem;
  line-height: 1.5;
  font-weight: 600;
}

.event-facts {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.event-identifiers {
  display: grid;
  gap: 6px;
  margin: 0;
}

.event-identifiers div {
  display: grid;
  gap: 2px;
}

.event-identifiers dt {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 700;
}

.event-identifiers dd {
  margin: 0;
}

.event-fact-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color, #d9dee7);
  border-radius: 999px;
  background: var(--theme--background-subdued, #fafbfc);
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.45;
  font-weight: 600;
}

.event-detail {
  margin: 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.65;
}

.split-stat-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--theme--border-color, #d9dee7);
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
}

.detail-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.detail-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.detail-list-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-subdued, #fafbfc);
}

.detail-list-item-warning {
  background: #fffaf8;
}

.detail-item-top {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  flex-wrap: wrap;
}

.detail-anchor {
  color: var(--theme--foreground, #172940);
  font-size: 0.9375rem;
  line-height: 1.45;
  font-weight: 700;
  word-break: break-word;
}

.detail-item-copy {
  font-size: 0.875rem;
  line-height: 1.7;
}

.triage-detail-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  position: sticky;
  top: 20px;
  min-width: 0;
}

.split-section p {
  margin: 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.9375rem;
  line-height: 1.7;
  white-space: pre-wrap;
  max-width: 66ch;
}

.task-switcher {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 280px;
}

.task-switcher span {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 700;
}

.task-switcher select {
  min-height: 40px;
  border: 1px solid var(--theme--border-color, #d9dee7);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
  padding: 0 10px;
  color: var(--theme--foreground, #172940);
}

.module-search {
  width: 13rem;
  min-width: 13rem;
  flex: 0 0 13rem;
}

.copyable-code {
  display: inline-block;
  padding: 2px 0;
  background: transparent;
  color: var(--theme--foreground, #172940);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 0.8125rem;
  line-height: 1.6;
  white-space: normal;
  word-break: break-all;
  user-select: text;
}

.wrap-copy {
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.6;
}

.tone-success {
  background: #ecfdf3;
  border-color: #b7ebcf;
  color: #11795b;
}

.tone-info {
  background: #eef5ff;
  border-color: #c8defc;
  color: #245cb8;
}

.tone-warning {
  background: #fff7e8;
  border-color: #ffd9a8;
  color: #9a5b00;
}

.tone-danger {
  background: #fff1f2;
  border-color: #fecdd3;
  color: #be123c;
}

.tone-neutral {
  background: var(--theme--background-subdued, #fafbfc);
  border-color: var(--theme--border-color, #d9dee7);
  color: var(--theme--foreground-subdued, #6b7280);
}

@media (max-width: 1280px) {
  .summary-panel,
  .summary-status-list,
  .evidence-grid,
  .event-grid,
  .usage-grid,
  .summary-reference-strip,
  .snapshot-facts-grid,
  .summary-facts,
  .runtime-summary-facts {
    grid-template-columns: 1fr;
  }

  .triage-layout {
    grid-template-columns: 1fr;
  }

  .snapshot-group-row {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .snapshot-group-row .muted {
    grid-column: auto;
  }

  .usage-meta-list div {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .triage-detail-panel {
    position: static;
  }
}
</style>
