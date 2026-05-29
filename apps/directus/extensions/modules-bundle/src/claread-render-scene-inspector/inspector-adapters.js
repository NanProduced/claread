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

function normalizeOverviewLane({ readingGoal, overviewTask, overviewHint }) {
  if (readingGoal === "academic") {
    return {
      code: "not_triggered",
      tone: "muted",
      label: "未触发任务",
      detail: "academic 当前不进入 overview hint 派生任务。",
    };
  }

  if (overviewHint) {
    if (overviewHint.status === "ready" && overviewHint.overview) {
      return {
        code: "ready",
        tone: "success",
        label: "已生成",
        detail: overviewHint.overview,
      };
    }

    if (overviewHint.status === "pending") {
      return {
        code: "pending",
        tone: "info",
        label: "进行中",
        detail: "overview hint 任务已触发，等待结果回写。",
      };
    }

    if (overviewHint.status === "unavailable") {
      return {
        code: "unavailable",
        tone: "warning",
        label: "无内容",
        detail: overviewHint.reason || "任务成功，但未生成 overview 文本。",
      };
    }

    if (overviewHint.status === "failed") {
      return {
        code: "failed",
        tone: "danger",
        label: "任务失败",
        detail: overviewHint.reason || "overview hint 任务失败。",
      };
    }

    if (overviewHint.status === "stale") {
      return {
        code: "stale",
        tone: "warning",
        label: "结果过期",
        detail: "overview hint 与当前 source text 或 workflow 版本不匹配。",
      };
    }
  }

  if (overviewTask) {
    if (overviewTask.status === "failed") {
      return {
        code: "failed",
        tone: "danger",
        label: "任务失败",
        detail: overviewTask.failure_message || overviewTask.failure_code || "overview hint 任务失败。",
      };
    }

    if (overviewTask.status === "succeeded") {
      return {
        code: "succeeded_no_writeback",
        tone: "warning",
        label: "成功未回写",
        detail: "任务成功，但 page_state_json 中没有 overview_hint。",
      };
    }

    if (overviewTask.status === "queued" || overviewTask.status === "running" || overviewTask.status === "finalizing") {
      return {
        code: "pending",
        tone: "info",
        label: "进行中",
        detail: "overview hint 任务已入队。",
      };
    }
  }

  return {
    code: "not_triggered",
    tone: "muted",
    label: "未触发任务",
    detail: "当前记录还没有 overview hint 派生任务。",
  };
}

function buildKpis(scene, adapter) {
  const article = scene?.article && typeof scene.article === "object" ? scene.article : {};
  const sentences = Array.isArray(article.sentences) ? article.sentences : [];
  const paragraphs = Array.isArray(article.paragraphs) ? article.paragraphs : [];
  const translations = Array.isArray(scene?.translations) ? scene.translations : [];
  const inlineMarks = Array.isArray(scene?.inline_marks) ? scene.inline_marks : [];
  const sentenceEntries = Array.isArray(scene?.sentence_entries) ? scene.sentence_entries : [];
  const warnings = Array.isArray(scene?.warnings) ? scene.warnings : [];

  const translationCoverage =
    sentences.length > 0 ? `${Math.round((translations.length / sentences.length) * 100)}%` : "0%";

  return [
    { label: "段落", value: paragraphs.length },
    { label: "句子", value: sentences.length },
    { label: "翻译覆盖", value: translationCoverage },
    { label: adapter.markLabel, value: inlineMarks.length },
    { label: adapter.entryLabel, value: sentenceEntries.length },
    { label: "告警", value: warnings.length },
  ];
}

function buildSignals(scene, normalized, adapter) {
  const sentenceIds = normalized.sentences.map((item) => item.sentence_id);
  const translationIds = new Set(normalized.translations.map((item) => item.sentence_id));
  const missingTranslations = sentenceIds.filter((sentenceId) => !translationIds.has(sentenceId));
  const warningSentences = normalized.sentences
    .filter((sentence) => (normalized.warningsBySentence[sentence.sentence_id]?.length ?? 0) > 0)
    .map((sentence) => sentence.sentence_id);

  const signals = [
    {
      label: "缺翻译句",
      tone: missingTranslations.length > 0 ? "warning" : "success",
      value: missingTranslations.length,
      detail: missingTranslations.length > 0 ? missingTranslations.join(", ") : "无",
    },
    {
      label: "句内告警",
      tone: warningSentences.length > 0 ? "warning" : "success",
      value: warningSentences.length,
      detail: warningSentences.length > 0 ? warningSentences.join(", ") : "无",
    },
  ];

  if (adapter.kind === "academic") {
    signals.push({
      label: "内容概要",
      tone: scene?.content_summary ? "success" : "muted",
      value: scene?.content_summary ? "已生成" : "可选增强缺失",
      detail: scene?.content_summary ? "academic content_summary 已生成。" : "当前 policy 下 content_summary 不是必需产物。",
    });
  }

  return signals;
}

function createAdapter(config) {
  return {
    ...config,
    buildVm({ scene, normalized, overviewLane, snapshot, selectedTask }) {
      return {
        kind: config.kind,
        title: config.title,
        kpis: buildKpis(scene, config),
        overviewLane,
        markCounts: countBy(scene?.inline_marks, "annotation_type"),
        entryCounts: countBy(scene?.sentence_entries, "entry_type"),
        warningCounts: countBy(scene?.warnings, "code"),
        signals: buildSignals(scene, normalized, config),
        agentCards: config.agentOrder.map((agentId) => {
          const perAgent = snapshot?.runtime_summary_json?.per_agent;
          const agentUsage = perAgent && typeof perAgent === "object" ? perAgent[agentId] : null;

          return {
            id: agentId,
            label: config.agentLabels[agentId] || agentId,
            usage: agentUsage && typeof agentUsage === "object" ? agentUsage : null,
            active: Boolean(agentUsage),
          };
        }),
        taskFocus: selectedTask?.status || snapshot?.task_status || "unknown",
      };
    },
  };
}

export const LEARNING_ADAPTER = createAdapter({
  kind: "learning",
  title: "学习解析",
  markLabel: "行内标注",
  entryLabel: "句尾入口",
  densityThreshold: 4,
  agentOrder: ["vocabulary", "grammar", "translation", "repair"],
  agentLabels: {
    vocabulary: "词汇代理",
    grammar: "语法代理",
    translation: "翻译代理",
    repair: "修复代理",
  },
});

export const ACADEMIC_ADAPTER = createAdapter({
  kind: "academic",
  title: "学术解析",
  markLabel: "学术标注",
  entryLabel: "句级解释",
  densityThreshold: 3,
  agentOrder: ["term", "translation", "understanding"],
  agentLabels: {
    term: "术语代理",
    translation: "学术翻译代理",
    understanding: "理解代理",
  },
});

export function resolveAdapter(schemaVersion) {
  return schemaVersion === "3.0.0-academic" ? ACADEMIC_ADAPTER : LEARNING_ADAPTER;
}

export function normalizeScene(scene) {
  const article = scene?.article && typeof scene.article === "object" ? scene.article : {};
  const sentences = Array.isArray(article.sentences) ? article.sentences : [];
  const paragraphs = Array.isArray(article.paragraphs) ? article.paragraphs : [];
  const translations = Array.isArray(scene?.translations) ? scene.translations : [];
  const inlineMarks = Array.isArray(scene?.inline_marks) ? scene.inline_marks : [];
  const sentenceEntries = Array.isArray(scene?.sentence_entries) ? scene.sentence_entries : [];
  const warnings = Array.isArray(scene?.warnings) ? scene.warnings : [];

  const translationsBySentence = {};
  for (const item of translations) {
    if (!item || typeof item !== "object" || !item.sentence_id) continue;
    translationsBySentence[item.sentence_id] = item;
  }

  const marksBySentence = {};
  for (const mark of inlineMarks) {
    const sentenceId = mark?.anchor?.sentence_id;
    if (!sentenceId) continue;
    marksBySentence[sentenceId] = marksBySentence[sentenceId] ?? [];
    marksBySentence[sentenceId].push(mark);
  }

  const entriesBySentence = {};
  const floatingEntries = [];
  for (const entry of sentenceEntries) {
    const sentenceId = entry?.sentence_id ? String(entry.sentence_id) : "";
    if (!sentenceId) {
      floatingEntries.push(entry);
      continue;
    }

    entriesBySentence[sentenceId] = entriesBySentence[sentenceId] ?? [];
    entriesBySentence[sentenceId].push(entry);
  }

  const warningsBySentence = {};
  const globalWarnings = [];
  for (const warning of warnings) {
    const sentenceId = warning?.sentence_id ? String(warning.sentence_id) : "";
    if (!sentenceId) {
      globalWarnings.push(warning);
      continue;
    }

    warningsBySentence[sentenceId] = warningsBySentence[sentenceId] ?? [];
    warningsBySentence[sentenceId].push(warning);
  }

  return {
    paragraphs,
    sentences,
    translations,
    inlineMarks,
    sentenceEntries,
    warnings,
    floatingEntries,
    globalWarnings,
    translationsBySentence,
    marksBySentence,
    entriesBySentence,
    warningsBySentence,
  };
}

export function buildInspectorVm({ record, result, snapshot, selectedTask, overviewTask, overviewHint }) {
  const scene = result?.render_scene_json && typeof result.render_scene_json === "object"
    ? result.render_scene_json
    : null;
  const schemaVersion = scene?.schema_version || result?.schema_version || record?.result?.schema_version || "3.0.0";
  const adapter = resolveAdapter(schemaVersion);
  const normalized = normalizeScene(scene);
  const readingGoal =
    record?.reading_goal ||
    record?.request_payload_json?.reading_goal ||
    scene?.request?.reading_goal ||
    "daily_reading";
  const overviewLane = normalizeOverviewLane({ readingGoal, overviewTask, overviewHint });

  return {
    schemaVersion,
    adapter,
    normalized,
    scene,
    overviewLane,
    derived: adapter.buildVm({
      scene,
      normalized,
      overviewLane,
      snapshot,
      selectedTask,
    }),
  };
}
