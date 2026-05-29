<script setup>
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { buildInspectorVm } from "./inspector-adapters.js";
import { loadInspectorBundle } from "./inspector-data.js";

const route = useRoute();
const router = useRouter();

const loading = ref(false);
const error = ref("");
const bundle = ref(null);
const selectedSentenceId = ref("");
const sentenceQuery = ref("");

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
  { id: "section-highlights", label: "重点异常" },
  { id: "section-triage", label: "句级排查" },
  { id: "section-runtime", label: "运行证据" },
  { id: "section-snapshot", label: "调试快照" },
  { id: "section-usage", label: "调用消耗" },
  { id: "section-raw", label: "原始数据" },
];

const groupedUsageEvents = computed(() => {
  const events = Array.isArray(bundle.value?.usageEvents) ? bundle.value.usageEvents : [];
  return {
    analysis: events.filter((item) => item.capability_code === "analysis_full"),
    overview: events.filter((item) => item.capability_code === "analysis_overview_hint"),
  };
});

const allSentenceRows = computed(() => {
  if (!normalized.value) return [];

  return normalized.value.sentences.map((sentence, index) => {
    const translation = normalized.value.translationsBySentence[sentence.sentence_id] ?? null;
    const marks = normalized.value.marksBySentence[sentence.sentence_id] ?? [];
    const entries = normalized.value.entriesBySentence[sentence.sentence_id] ?? [];
    const warnings = normalized.value.warningsBySentence[sentence.sentence_id] ?? [];
    const missingTranslation = !translation?.translation_zh;
    const missingEntries = entries.length === 0;
    const topWarning = warnings[0]?.code || warnings[0]?.level || "";
    const warningCount = warnings.length;
    const markCount = marks.length;
    const entryCount = entries.length;
    const density = markCount + entryCount;
    const score =
      (missingTranslation ? 1000 : 0) +
      (warningCount * 100) +
      (missingEntries ? 30 : 0) +
      density;
    const primaryIssue = summarizeSentenceIssue({
      missingTranslation,
      missingEntries,
      warningCount,
      topWarning,
      density,
      markCount,
      entryCount,
    });

    return {
      sentence,
      sentence_id: sentence.sentence_id,
      sentenceIndex: index + 1,
      text: sentence.text || "",
      translation,
      marks,
      entries,
      warnings,
      missingTranslation,
      missingEntries,
      topWarning,
      warningCount,
      markCount,
      entryCount,
      density,
      score,
      primaryIssue,
      evidenceSummary: `${markCount} 标注 / ${entryCount} 入口 / ${warningCount} 告警`,
      issueLevel: score >= 1000 ? "warning" : warningCount > 0 || missingEntries ? "attention" : density >= 4 ? "dense" : "normal",
    };
  });
});

const sentenceRows = computed(() => {
  const query = sentenceQuery.value.trim().toLowerCase();
  const rows = [...allSentenceRows.value].sort((left, right) => {
    if (right.score !== left.score) return right.score - left.score;
    return left.sentenceIndex - right.sentenceIndex;
  });

  if (!query) return rows;

  return rows.filter((row) => {
    const haystacks = [
      row.sentence_id,
      row.text,
      row.translation?.translation_zh,
      row.primaryIssue.label,
      row.primaryIssue.detail,
      row.topWarning,
      row.warnings.map((item) => item.message).join(" "),
      row.entries.map((item) => item.content || item.label || "").join(" "),
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
  const missingEntries = rows.filter((item) => item.missingEntries).length;
  const warningSentences = rows.filter((item) => item.warningCount > 0).length;
  return {
    total: rows.length,
    missingTranslation,
    missingEntries,
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
  const currentSignals = inspector.value?.derived.signals || [];

  if (currentBundle?.selectedTask?.failure_code || currentBundle?.snapshot?.failure_code) {
    items.push({
      label: "任务失败",
      tone: "danger",
      value: currentBundle.snapshot?.failure_code || currentBundle.selectedTask?.failure_code,
      detail: currentBundle.snapshot?.failure_message || currentBundle.selectedTask?.failure_message || "任务执行失败。",
    });
  }

  for (const signal of currentSignals) {
    if (signal.value === 0 || signal.value === "已生成") continue;
    items.push({
      label: signal.label,
      tone: signal.tone || "neutral",
      value: String(signal.value),
      detail: compactSentenceList(signal.detail),
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
      detail: "当前记录未选中 debug snapshot，无法直接定位 preprocess / RAG / few-shot / runtime 层。",
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

const runtimeFactRows = computed(() => {
  const currentBundle = bundle.value;
  const currentSnapshot = currentBundle?.snapshot;
  const currentTask = currentBundle?.selectedTask;

  return [
    ["任务状态", translateStatus(currentSnapshot?.task_status || currentTask?.status)],
    ["失败码", currentSnapshot?.failure_code || currentTask?.failure_code || "未记录"],
    ["任务失败说明", currentSnapshot?.failure_message || currentTask?.failure_message || "未记录"],
    ["结果 workflow", currentBundle?.result?.workflow_version || "未记录"],
    ["快照 workflow", currentSnapshot?.workflow_version || "未记录"],
    ["Prompt 版本", currentSnapshot?.prompt_version || "未记录"],
  ];
});

const overviewFactRows = computed(() => {
  const currentBundle = bundle.value;
  const lane = inspector.value?.derived.overviewLane;

  return [
    ["概览状态", lane?.label || "未记录"],
    ["概览任务", shortId(currentBundle?.overviewTask?.id)],
    ["概览失败码", currentBundle?.overviewTask?.failure_code || "未记录"],
    ["概览更新时间", formatDateTime(currentBundle?.overviewHint?.updated_at)],
  ];
});

const usageGroups = computed(() => [
  { key: "analysis", title: "主解析调用", items: groupedUsageEvents.value.analysis },
  { key: "overview", title: "概览提示调用", items: groupedUsageEvents.value.overview },
]);

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
      key: "few_shot_debug_json",
      label: "Few-shot 证据",
      value: snapshot.few_shot_debug_json,
      summary: summarizeSnapshotSection("few_shot", snapshot.few_shot_debug_json),
    },
    {
      key: "rag_debug_json",
      label: "RAG 证据",
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

function formatPercent(count, base) {
  if (!base || typeof count !== "number" || !Number.isFinite(count)) return "0%";
  return `${((count / base) * 100).toFixed(base >= 100 ? 1 : 0)}%`;
}

function summarizeSentenceIssue({ missingTranslation, missingEntries, warningCount, topWarning, density, markCount, entryCount }) {
  if (missingTranslation) {
    return {
      label: "缺翻译",
      detail: "当前句没有生成译文，优先回查 agent 产出和投影结果。",
    };
  }

  if (warningCount > 0) {
    return {
      label: "告警句",
      detail: `${translateWarningCode(topWarning)}，共 ${warningCount} 条告警。`,
    };
  }

  if (missingEntries) {
    return {
      label: "缺句级入口",
      detail: `已有 ${markCount} 个标注，但没有句级解释入口。`,
    };
  }

  if (density >= 4) {
    return {
      label: "信息密集",
      detail: `当前句聚合了 ${markCount} 个标注和 ${entryCount} 个入口，适合抽查表达是否过载。`,
    };
  }

  return {
    label: "结构正常",
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

function translateIssueLevel(level) {
  const map = {
    warning: "高优先",
    attention: "需检查",
    dense: "信息密集",
    normal: "正常",
  };
  return map[level] || level || "正常";
}

function issueLevelTone(level) {
  const map = {
    warning: "warning",
    attention: "danger",
    dense: "info",
    normal: "neutral",
  };
  return map[level] || "neutral";
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

  if (kind === "runtime" && value && typeof value === "object") {
    const perAgent = value.per_agent && typeof value.per_agent === "object" ? Object.keys(value.per_agent) : [];
    const tokenPart = value.total_tokens ? `总 tokens ${value.total_tokens}` : "未记录总 tokens";
    return perAgent.length > 0 ? `${perAgent.length} 个代理有调用，${tokenPart}` : tokenPart;
  }

  if (kind === "rag" && value && typeof value === "object") {
    const hits = Array.isArray(value.retrievals) ? value.retrievals.length : Array.isArray(value.items) ? value.items.length : null;
    return hits != null ? `检索候选 ${hits} 项` : summarizeJsonBlock(value);
  }

  if (kind === "few_shot" && value && typeof value === "object") {
    const examples = Array.isArray(value.examples) ? value.examples.length : Array.isArray(value.selected_examples) ? value.selected_examples.length : null;
    return examples != null ? `few-shot 样例 ${examples} 条` : summarizeJsonBlock(value);
  }

  if (kind === "trace" && value && typeof value === "object") {
    const refs = Array.isArray(value.refs) ? value.refs.length : Object.keys(value).length;
    return `trace 引用 ${refs} 条`;
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
        value: totalTokens != null ? `${totalTokens} tokens` : latency != null ? `${latency} ms` : calls != null ? `${calls} 次` : "有调用",
        detail:
          [
            calls != null ? `${calls} 次调用` : "",
            latency != null ? `${latency} ms` : "",
          ]
            .filter(Boolean)
            .join(" / "),
      };
    })
    .sort((left, right) => extractLeadingNumber(right.value) - extractLeadingNumber(left.value));
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
    };
  }

  const facts = [];
  const groups = [];

  if (kind === "runtime_summary_json") {
    const perAgent = firstDefined(value, ["per_agent"]);
    const activeAgentCount = countCollection(perAgent);
    const totalTokens = firstDefined(value, ["total_tokens", "tokens_total", "token_total", "aggregate.total_tokens"]);
    const inputTokens = firstDefined(value, ["input_tokens", "prompt_tokens", "total_input_tokens", "aggregate.input_tokens"]);
    const outputTokens = firstDefined(value, ["output_tokens", "completion_tokens", "total_output_tokens", "aggregate.output_tokens"]);
    const latency = firstDefined(value, ["latency_ms", "elapsed_ms", "duration_ms"]);
    const billedPoints = firstDefined(value, ["billed_points", "points"]);
    [createFact("活跃代理", activeAgentCount), createFact("总 Tokens", totalTokens), createFact("输入 Tokens", inputTokens), createFact("输出 Tokens", outputTokens), createFact("积分", billedPoints), createFact("总耗时", latency != null ? `${latency} ms` : undefined)]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const agentRows = buildAgentUsageRows(perAgent);
    if (agentRows.length) groups.push({ title: "按代理查看", rows: agentRows });
  } else if (kind === "few_shot_debug_json") {
    const selected = firstDefined(value, ["selected_examples", "examples", "shots"]);
    const candidates = firstDefined(value, ["candidate_examples", "retrieved_examples", "pool_examples"]);
    const strategy = firstDefined(value, ["strategy", "selector", "selection_strategy"]);
    const query = firstDefined(value, ["query", "prompt_query", "selection_query"]);
    [createFact("已选样例", countCollection(selected)), createFact("候选样例", countCollection(candidates)), createFact("选择策略", strategy), createFact("检索问题", query)]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const selectedRows = asArray(selected).slice(0, 6).map((item, index) => ({
      label: `样例 ${index + 1}`,
      value: previewText(item?.id || item?.example_id || item?.title || item?.source || prettyJson(item), 72),
    }));
    if (selectedRows.length) groups.push({ title: "已选样例", rows: selectedRows });
  } else if (kind === "rag_debug_json") {
    const retrievals = firstDefined(value, ["retrievals", "items", "hits", "documents"]);
    const query = firstDefined(value, ["query", "search_query", "retrieval_query"]);
    const sourceCounts = firstDefined(value, ["source_counts", "sources"]);
    [createFact("检索候选", countCollection(retrievals)), createFact("检索问题", query), createFact("来源类型", countCollection(sourceCounts))]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const retrievalRows = asArray(retrievals).slice(0, 6).map((item, index) => ({
      label: item?.source || item?.doc_id || item?.chunk_id || `候选 ${index + 1}`,
      value: previewText(item?.title || item?.text || item?.content || item?.snippet || prettyJson(item), 84),
    }));
    if (retrievalRows.length) groups.push({ title: "命中候选", rows: retrievalRows });

    const sourceRows = summarizeTopEntries(sourceCounts);
    if (sourceRows.length) groups.push({ title: "来源分布", rows: sourceRows });
  } else if (kind === "normalize_summary_json") {
    const factsMap = [
      createFact("句子数", firstDefined(value, ["sentence_count", "sentences", "article_sentence_count"])),
      createFact("翻译数", firstDefined(value, ["translation_count", "translations"])),
      createFact("标注数", firstDefined(value, ["inline_mark_count", "mark_count", "inline_marks"])),
      createFact("入口数", firstDefined(value, ["entry_count", "sentence_entry_count", "entries"])),
      createFact("告警数", firstDefined(value, ["warning_count", "warnings"])),
    ].filter(Boolean);
    facts.push(...factsMap);
  } else if (kind === "preprocess_summary_json") {
    const factsMap = [
      createFact("段落数", firstDefined(value, ["paragraph_count", "paragraphs"])),
      createFact("句子数", firstDefined(value, ["sentence_count", "sentences"])),
      createFact("输入长度", firstDefined(value, ["source_length", "input_length", "character_count"])),
      createFact("语言", firstDefined(value, ["language", "source_language"])),
    ].filter(Boolean);
    facts.push(...factsMap);
  } else if (kind === "drop_log_summary_json") {
    const droppedItems = firstDefined(value, ["items", "drops", "drop_items", "records"]);
    const reasonCounts = firstDefined(value, ["reason_counts", "drop_reason_counts"]);
    [createFact("丢弃项", countCollection(droppedItems)), createFact("原因类型", countCollection(reasonCounts))]
      .filter(Boolean)
      .forEach((item) => facts.push(item));

    const reasonRows = summarizeTopEntries(reasonCounts);
    if (reasonRows.length) groups.push({ title: "主要原因", rows: reasonRows });
  } else if (kind === "academic_quality_json") {
    const factsMap = [
      createFact("质量结论", firstDefined(value, ["status", "verdict", "quality_status"])),
      createFact("质量分数", firstDefined(value, ["score", "quality_score"])),
      createFact("内容概要", firstDefined(value, ["content_summary_status", "content_summary"])),
    ].filter(Boolean);
    facts.push(...factsMap);
  } else if (kind === "trace_refs_json") {
    const refs = firstDefined(value, ["refs", "items", "traces"]);
    facts.push(...[createFact("引用条数", countCollection(refs))].filter(Boolean));
    const refRows = asArray(refs).slice(0, 8).map((item, index) => ({
      label: item?.agent || item?.node || item?.trace_id || `引用 ${index + 1}`,
      value: previewText(item?.ref || item?.id || item?.span_id || prettyJson(item), 72),
    }));
    if (refRows.length) groups.push({ title: "引用预览", rows: refRows });
  }

  if (!groups.length) {
    groups.push(...buildGenericSnapshotGroups(value));
  }

  return {
    facts,
    groups,
  };
}

function scrollToSection(sectionId) {
  document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
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
      rows.find((item) => item.warningCount > 0 || item.missingTranslation)?.sentence_id ||
      rows[0].sentence_id;

    if (!rows.some((item) => item.sentence_id === selectedSentenceId.value)) {
      selectedSentenceId.value = preferred;
    }
  },
  { immediate: true },
);
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
            @click="scrollToSection(section.id)"
          >
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
                <div class="summary-title">规模与成本</div>
                <div class="summary-reference-strip">
                  <div
                    v-for="item in overviewReferenceStats"
                    :key="item.label"
                    class="summary-reference-item"
                  >
                    <span>{{ item.label }}</span>
                    <strong>{{ item.value }}</strong>
                    <small>{{ item.detail }}</small>
                  </div>
                </div>
              </div>

              <div class="summary-block">
                <div class="summary-title">标注分布</div>
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
                      <td>{{ item.group }}</td>
                      <td>{{ item.count }}</td>
                      <td>{{ item.baseLabel }} {{ formatInteger(item.baseValue) }}</td>
                      <td>{{ item.percent }}</td>
                      <td>{{ item.detail }}</td>
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

        <section id="section-highlights" class="section-card">
          <div class="section-head">
            <div>
              <div class="section-kicker">重点异常</div>
              <h2>先看异常，再决定往哪层下钻</h2>
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
              <div class="section-kicker">句级排查</div>
              <h2>按风险排序的句级联动表</h2>
            </div>
            <div class="section-note">
              <span>点击行，右侧详情会同步更新；默认选中一条高优先句子</span>
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
                      <th>优先级</th>
                      <th>句子</th>
                      <th>问题类型</th>
                      <th>风险依据</th>
                      <th>证据概况</th>
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
                      <td>
                        <span class="badge" :class="`tone-${issueLevelTone(row.issueLevel)}`">
                          {{ translateIssueLevel(row.issueLevel) }}
                        </span>
                      </td>
                      <td>{{ row.sentence_id }}</td>
                      <td>{{ row.primaryIssue.label }}</td>
                      <td>{{ row.primaryIssue.detail }}</td>
                      <td>{{ row.evidenceSummary }}</td>
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
                    <span>{{ selectedSentence.markCount }} 标注</span>
                    <span>{{ selectedSentence.entryCount }} 入口</span>
                    <span>{{ selectedSentence.warningCount }} 告警</span>
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
                    <h3>行内标注</h3>
                    <table class="compact-table" v-if="selectedSentence.marks.length">
                      <thead>
                        <tr>
                          <th>类型</th>
                          <th>锚点</th>
                          <th>内容预览</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="item in selectedSentence.marks" :key="item.id">
                          <td>{{ translateMarkType(item.annotation_type || item.visual_tone) }}</td>
                          <td>{{ item.anchor?.sentence_id || "未记录" }}</td>
                          <td>{{ previewText(item.text || item.content || item.payload || "", 80) || "空内容" }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else class="empty-copy">该句暂无行内标注</div>
                  </section>

                  <section class="split-section">
                    <h3>句级入口</h3>
                    <table class="compact-table" v-if="selectedSentence.entries.length">
                      <thead>
                        <tr>
                          <th>类型</th>
                          <th>标签</th>
                          <th>内容预览</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="item in selectedSentence.entries" :key="item.id">
                          <td>{{ translateEntryType(item.entry_type) }}</td>
                          <td>{{ item.label || item.title || "未命名" }}</td>
                          <td>{{ previewText(item.content || "", 96) || "空内容" }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else class="empty-copy">该句没有句级解释入口</div>
                  </section>

                  <section class="split-section">
                    <h3>告警</h3>
                    <table class="compact-table" v-if="selectedSentence.warnings.length">
                      <thead>
                        <tr>
                          <th>类型</th>
                          <th>级别</th>
                          <th>说明</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="item in selectedSentence.warnings"
                          :key="`${selectedSentence.sentence_id}-${item.code || item.level}-${item.message}`"
                        >
                          <td>{{ translateWarningCode(item.code || item.level) }}</td>
                          <td>{{ translateStatus(item.level) }}</td>
                          <td>{{ item.message || "未记录" }}</td>
                        </tr>
                      </tbody>
                    </table>
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
              <div class="section-kicker">运行证据</div>
              <h2>任务、事件和 overview 联动</h2>
            </div>
            <label class="task-switcher">
              <span>切换任务</span>
              <select :value="taskId || bundle.selectedTask?.id || ''" @change="onTaskChange">
                <option value="">latest</option>
                <option v-for="item in bundle.tasks" :key="item.id" :value="item.id">
                  {{ translateStatus(item.status) }} · {{ shortId(item.id) }}
                </option>
              </select>
            </label>
          </div>

          <div class="evidence-grid">
            <div class="sub-card">
              <h3>主任务状态</h3>
              <div class="facts-grid">
                <div class="fact-card" v-for="[label, value] in runtimeFactRows" :key="label">
                  <span>{{ label }}</span>
                  <strong>{{ value }}</strong>
                </div>
              </div>
            </div>

            <div class="sub-card">
              <h3>概览联动</h3>
              <div class="facts-grid">
                <div class="fact-card" v-for="[label, value] in overviewFactRows" :key="label">
                  <span>{{ label }}</span>
                  <strong>{{ value }}</strong>
                </div>
              </div>
            </div>
          </div>

          <div class="event-grid">
            <div class="sub-card">
              <h3>任务事件</h3>
              <table class="compact-table" v-if="bundle.taskEvents.length">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>事件</th>
                    <th>载荷预览</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="event in bundle.taskEvents" :key="event.id">
                    <td>{{ formatDateTime(event.created_at) }}</td>
                    <td>{{ event.event_type }}</td>
                    <td>{{ previewText(prettyJson(event.event_payload_json), 96) }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-else class="empty-copy">当前任务没有事件</div>
            </div>

            <div class="sub-card">
              <h3>概览任务事件</h3>
              <table class="compact-table" v-if="bundle.overviewTaskEvents.length">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>事件</th>
                    <th>载荷预览</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="event in bundle.overviewTaskEvents" :key="event.id">
                    <td>{{ formatDateTime(event.created_at) }}</td>
                    <td>{{ event.event_type }}</td>
                    <td>{{ previewText(prettyJson(event.event_payload_json), 96) }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-else class="empty-copy">当前没有概览任务事件</div>
            </div>
          </div>
        </section>

        <section id="section-snapshot" class="section-card">
          <div class="section-head">
            <div>
              <div class="section-kicker">调试快照</div>
              <h2>按层查看 preprocess、normalize、RAG、few-shot 和 runtime 证据</h2>
            </div>
          </div>

          <div v-if="snapshotSections.length" class="details-stack">
            <details
              v-for="section in snapshotSections"
              :key="section.key"
              class="details-card"
              :open="section.key === 'normalize_summary_json' || section.key === 'runtime_summary_json'"
            >
              <summary>
                <strong>{{ section.label }}</strong>
                <span>{{ section.summary }}</span>
              </summary>
              <div class="snapshot-body">
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
                      <div v-for="row in group.rows" :key="`${group.title}-${row.label}`">
                        <dt>{{ row.label }}</dt>
                        <dd>{{ row.value }}</dd>
                        <div v-if="row.detail" class="muted wrap-copy">{{ row.detail }}</div>
                      </div>
                    </dl>
                  </section>
                </div>
              </div>
              <pre>{{ prettyJson(section.value) }}</pre>
            </details>
          </div>
          <div v-else class="empty-copy">当前没有选中的调试快照</div>
        </section>

        <section id="section-usage" class="section-card">
          <div class="section-head">
            <div>
              <div class="section-kicker">调用消耗</div>
              <h2>调用记录与消耗</h2>
            </div>
          </div>

          <div class="usage-grid">
            <div v-for="group in usageGroups" :key="group.key" class="sub-card">
              <h3>{{ group.title }}</h3>
              <table class="compact-table" v-if="group.items.length">
                <thead>
                  <tr>
                    <th>状态</th>
                    <th>模型</th>
                    <th>Tokens</th>
                    <th>积分</th>
                    <th>耗时</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in group.items" :key="item.id">
                    <td>
                      <span class="badge" :class="`tone-${statusTone(item.status)}`">{{ translateStatus(item.status) }}</span>
                    </td>
                    <td>{{ item.model_provider }}/{{ item.model_name }}</td>
                    <td>{{ item.total_tokens ?? "未记录" }}</td>
                    <td>{{ item.billed_points ?? "未记录" }}</td>
                    <td>{{ item.latency_ms ?? "未记录" }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-else class="empty-copy">当前没有调用记录</div>
            </div>
          </div>
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

.summary-copy {
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.6;
}

.summary-reference-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}

.summary-reference-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
}

.summary-reference-item span {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 600;
}

.summary-reference-item strong {
  color: var(--theme--foreground, #172940);
  font-size: 1rem;
  line-height: 1.4;
  font-weight: 700;
}

.summary-reference-item small {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
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
  padding: 0 16px 16px;
}

.snapshot-facts-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.snapshot-groups {
  gap: 10px;
}

.snapshot-group {
  padding: 14px;
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
  gap: 10px;
  margin: 0;
}

.snapshot-group-list div {
  display: grid;
  gap: 2px;
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
  padding: 14px 12px 16px;
}

.module-navigation-group {
  gap: 6px;
}

.module-navigation-label {
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.module-navigation-link,
.module-navigation-stat {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 9px;
  background: var(--theme--background-normal, #ffffff);
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.45;
}

.module-navigation-link {
  cursor: pointer;
  text-align: left;
  width: 100%;
}

.module-navigation-link:hover {
  background: #eef5ff;
}

.module-navigation-link strong,
.module-navigation-stat strong {
  color: var(--theme--foreground, #172940);
  font-size: 0.8125rem;
  line-height: 1.45;
  font-weight: 700;
}

.module-navigation-stats {
  display: flex;
  flex-direction: column;
  gap: 6px;
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
  min-width: 220px;
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
  .facts-grid,
  .summary-reference-strip,
  .snapshot-facts-grid,
  .summary-facts {
    grid-template-columns: 1fr;
  }

  .triage-layout {
    grid-template-columns: 1fr;
  }

  .triage-detail-panel {
    position: static;
  }
}
</style>
