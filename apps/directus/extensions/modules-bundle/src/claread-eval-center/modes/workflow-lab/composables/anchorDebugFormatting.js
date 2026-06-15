/**
 * Anchor Debug composable: 数据提取、筛选和统计逻辑。
 *
 * 职责：
 *  - 从 render_scene payload 中提取 inline_marks、drop_log、canonical_drop_log、warnings、canonical_stats
 *  - 按 anchor kind / annotation type / drop reason 分类统计
 *  - 提供筛选逻辑
 *  - 不做任何 text-search fallback 或 anchor 修复
 */

// ── Node timings & repair stats extraction ──────────────────────

export function extractNodeTimings(artifact) {
  const timings = artifact?.node_timings;
  if (!timings || typeof timings !== "object") return null;
  return timings;
}

export function extractRepairStats(artifact) {
  const stats = artifact?.repair_stats;
  if (!stats || typeof stats !== "object") return null;
  return stats;
}

// ── LLM Config Snapshot ──────────────────────────────────────

export function extractLLMConfigSnapshot(artifact) {
  const snapshot = artifact?.llm_config_snapshot;
  if (!snapshot || typeof snapshot !== "object") return null;

  const so = snapshot.structured_output || {};
  const ms = snapshot.model_settings || {};
  const eb = ms.extra_body || {};

  let thinkingEnabled = false;
  if (eb.enable_thinking === true) thinkingEnabled = true;
  if (typeof eb.thinking === "object" && eb.thinking?.type === "enabled") thinkingEnabled = true;
  if (snapshot.thinking_enabled === true) thinkingEnabled = true;

  const runtimeEntries = Array.isArray(snapshot.structured_output_runtime)
    ? snapshot.structured_output_runtime
    : [];

  return {
    profile: snapshot.profile_name || null,
    provider: snapshot.provider || null,
    adapter: snapshot.adapter || null,
    model: snapshot.model_name || null,
    openai_supports_tool_choice_required: so.openai_supports_tool_choice_required ?? null,
    expected_tool_choice: so.expected_tool_choice || null,
    supports_json_schema_output: so.supports_json_schema_output ?? null,
    supports_json_object_output: so.supports_json_object_output ?? null,
    default_structured_output_mode: so.default_structured_output_mode || null,
    expected_response_format: so.expected_response_format ?? null,
    thinking_enabled: thinkingEnabled,
    parallel_tool_calls: snapshot.parallel_tool_calls ?? null,
    structured_output_runtime: runtimeEntries,
  };
}

// ── Anchor kind 分类 ──────────────────────────────────────────

export function anchorKindCategory(anchor) {
  if (!anchor || typeof anchor !== "object") return "unknown";
  switch (anchor.kind) {
    case "range": return "range";
    case "multi_range": return "multi_range";
    case "text": return "text";
    case "multi_text": return "multi_text";
    default: return "unknown";
  }
}

// ── Anchor 详情提取 ──────────────────────────────────────────

export function extractAnchorDetail(mark) {
  const anchor = mark?.anchor;
  if (!anchor || typeof anchor !== "object") {
    return { kind: "unknown", sentenceId: null, ranges: [], offsetUnit: null };
  }

  const kind = anchorKindCategory(anchor);
  const sentenceId = String(anchor.sentence_id ?? anchor.sentenceId ?? "");
  const offsetUnit = String(anchor.offset_unit ?? anchor.offsetUnit ?? "");

  if (kind === "range") {
    const range = anchor.range && typeof anchor.range === "object" ? anchor.range : anchor;
    return {
      kind,
      sentenceId,
      offsetUnit: offsetUnit || "utf16",
      ranges: [{
        start: range.start ?? null,
        end: range.end ?? null,
        text: String(range.text ?? ""),
        sourceQuote: String(
          range.source_quote ??
          range.sourceQuote ??
          anchor.source_quote ??
          anchor.sourceQuote ??
          "",
        ),
        resolutionKind: String(
          range.resolution_kind ??
          range.resolutionKind ??
          anchor.resolution_kind ??
          anchor.resolutionKind ??
          "",
        ),
        role: String(range.role ?? ""),
      }],
    };
  }

  if (kind === "multi_range") {
    const parts = Array.isArray(anchor.ranges) ? anchor.ranges : [];
    return {
      kind,
      sentenceId,
      offsetUnit: offsetUnit || "utf16",
      ranges: parts.map((part) => ({
        start: part?.start ?? null,
        end: part?.end ?? null,
        text: String(part?.text ?? ""),
        sourceQuote: String(part?.source_quote ?? part?.sourceQuote ?? ""),
        resolutionKind: String(part?.resolution_kind ?? part?.resolutionKind ?? ""),
        role: String(part?.role ?? ""),
      })),
    };
  }

  if (kind === "multi_text") {
    const parts = Array.isArray(anchor.parts) ? anchor.parts : [];
    return {
      kind,
      sentenceId,
      offsetUnit: null,
      ranges: parts.map((part) => ({
        start: null,
        end: null,
        text: String(part?.anchor_text ?? part?.anchorText ?? ""),
        sourceQuote: "",
        resolutionKind: "",
        role: String(part?.role ?? ""),
        occurrence: part?.occurrence ?? null,
      })),
    };
  }

  // text
  return {
    kind,
    sentenceId,
    offsetUnit: null,
    ranges: [{
      start: null,
      end: null,
      text: String(anchor.anchor_text ?? anchor.anchorText ?? ""),
      sourceQuote: "",
      resolutionKind: "",
      role: "",
      occurrence: anchor.occurrence ?? null,
    }],
  };
}

// ── Anchor-related drop reasons ─────────────────────────────────

const ANCHOR_DROP_REASONS = new Set([
  "quote_not_found",
  "quote_ambiguous",
  "quote_out_of_order",
  "quote_too_short",
  "quote_boundary_violation",
  "sentence_id_invalid",
  "anchor_not_substring",
  "anchor_invalid",
  "resolve_failed",
  "sentence_id_not_found",
  "schematic_anchor_not_groundable",
]);

// ── 统计 ──────────────────────────────────────────────────────

export function computeAnchorStats(inlineMarks, dropLog, canonicalDropLog, warnings, canonicalStats) {
  const marks = Array.isArray(inlineMarks) ? inlineMarks : [];
  const drops = Array.isArray(dropLog) ? dropLog : [];
  const cDrops = Array.isArray(canonicalDropLog) ? canonicalDropLog : [];
  const warns = Array.isArray(warnings) ? warnings : [];

  let rangeCount = 0;
  let multiRangeCount = 0;
  let textCount = 0;
  let multiTextCount = 0;
  let unknownKindCount = 0;
  const byAnnotationType = {};

  for (const mark of marks) {
    const detail = extractAnchorDetail(mark);
    switch (detail.kind) {
      case "range": rangeCount++; break;
      case "multi_range": multiRangeCount++; break;
      case "text": textCount++; break;
      case "multi_text": multiTextCount++; break;
      default: unknownKindCount++; break;
    }

    const atype = mark?.annotation_type || "unknown";
    if (!byAnnotationType[atype]) byAnnotationType[atype] = 0;
    byAnnotationType[atype]++;
  }

  let canonicalAnchorDropCount = cDrops.filter((d) => ANCHOR_DROP_REASONS.has(dropReasonOf(d))).length;
  if (canonicalStats?.canonical_anchor_drop_summary?.total_anchor_drops != null) {
    canonicalAnchorDropCount = canonicalStats.canonical_anchor_drop_summary.total_anchor_drops;
  } else if (canonicalStats?.canonical_drop_counts_by_reason && typeof canonicalStats.canonical_drop_counts_by_reason === "object") {
    let sum = 0;
    for (const [reason, count] of Object.entries(canonicalStats.canonical_drop_counts_by_reason)) {
      if (ANCHOR_DROP_REASONS.has(reason)) sum += count;
    }
    canonicalAnchorDropCount = sum;
  }

  return {
    totalInlineMarks: marks.length,
    rangeCount,
    multiRangeCount,
    textCount,
    multiTextCount,
    unknownKindCount,
    byAnnotationType,
    dropCount: drops.length,
    canonicalDropCount: cDrops.length,
    canonicalAnchorDropCount,
    warningCount: warns.length,
  };
}

// ── 筛选 ──────────────────────────────────────────────────────

export const FILTER_GROUPS = [
  {
    label: "锚点类型",
    options: [
      { key: "all", label: "全部" },
      { key: "range", label: "范围锚点" },
      { key: "multi_range", label: "多范围锚点" },
      { key: "legacy", label: "旧版文本" },
    ],
  },
  {
    label: "标注类型",
    options: [
      { key: "vocab_highlight", label: "词汇" },
      { key: "phrase_gloss", label: "短语" },
      { key: "context_gloss", label: "语境" },
      { key: "grammar_note", label: "语法" },
    ],
  },
  {
    label: "状态",
    options: [
      { key: "dropped", label: "已丢弃" },
      { key: "warnings", label: "提醒" },
    ],
  },
];

export const FILTER_OPTIONS = FILTER_GROUPS.flatMap((g) => g.options);

export function filterInlineMarks(marks, filterKey, dropLog, canonicalDropLog, warnings) {
  const all = Array.isArray(marks) ? marks : [];
  const drops = Array.isArray(dropLog) ? dropLog : [];
  const cDrops = Array.isArray(canonicalDropLog) ? canonicalDropLog : [];
  const warns = Array.isArray(warnings) ? warnings : [];

  if (filterKey === "dropped") {
    return [...drops, ...cDrops];
  }
  if (filterKey === "warnings") {
    return warns;
  }
  if (filterKey === "all") return all;
  if (filterKey === "range") return all.filter((m) => m?.anchor?.kind === "range");
  if (filterKey === "multi_range") return all.filter((m) => m?.anchor?.kind === "multi_range");
  if (filterKey === "legacy") return all.filter((m) => m?.anchor?.kind === "text" || m?.anchor?.kind === "multi_text");
  // annotation type filter
  return all.filter((m) => m?.annotation_type === filterKey);
}

// ── Drop reason 严重度 ────────────────────────────────────────

const DROP_REASON_SEVERITY = {
  quote_boundary_violation: "danger",
  quote_not_found: "danger",
  quote_ambiguous: "warning",
  quote_out_of_order: "warning",
  quote_too_short: "warning",
  duplicate: "neutral",
  conflict_resolution: "neutral",
  density_exceeded: "neutral",
};

export function dropReasonSeverity(reason) {
  return DROP_REASON_SEVERITY[reason] || "neutral";
}

export function dropReasonOf(item) {
  if (!item || typeof item !== "object") return "";
  return item.drop_reason ?? item.reason ?? item.code ?? "";
}

export function dropStageOf(item) {
  if (!item || typeof item !== "object") return "";
  return item.drop_stage ?? item.stage ?? "";
}

export function dropReasonLabel(reason) {
  const map = {
    quote_boundary_violation: "词边界违规",
    quote_not_found: "原文未找到",
    quote_ambiguous: "原文歧义",
    quote_out_of_order: "原文乱序",
    quote_too_short: "原文过短",
    duplicate: "重复",
    conflict_resolution: "冲突消解",
    density_exceeded: "密度超限",
    sentence_id_invalid: "句子 ID 无效",
  };
  return map[reason] || reason || "未知";
}

// ── Annotation type 标签 ──────────────────────────────────────

export function annotationTypeLabel(type) {
  const map = {
    vocab_highlight: "词汇",
    phrase_gloss: "短语",
    context_gloss: "语境",
    grammar_note: "语法",
    sentence_analysis: "句分析",
  };
  return map[type] || type || "标注";
}

export function annotationTypeTone(type) {
  const map = {
    vocab_highlight: "vocab",
    phrase_gloss: "phrase",
    context_gloss: "context",
    grammar_note: "grammar",
    sentence_analysis: "analysis",
  };
  return map[type] || "default";
}

// ── Visual tone 标签 ──────────────────────────────────────────

export function visualToneLabel(tone) {
  const map = {
    vocab: "词汇",
    phrase: "短语",
    context: "语境",
    grammar: "语法",
    analysis: "分析",
  };
  return map[tone] || tone || "";
}
