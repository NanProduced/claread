export function dash(value, fallback = "-") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

export function artifactTopologyMode(artifact) {
  return artifact?.workflow_identity?.topology_mode
    || artifact?.schema_identity?.topology_mode
    || null;
}

export function isLearningArtifact(artifact) {
  return artifactTopologyMode(artifact) === "learning";
}

export function groupCandidatesByStatus(candidates) {
  const items = Array.isArray(candidates) ? candidates : [];
  return {
    published: items.filter((candidate) => candidate?.status === "ready_for_eval"),
    drafts: items.filter((candidate) => candidate?.status !== "ready_for_eval"),
  };
}

export function normalizeWorkflowScene(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  if (
    payload.render_scene
    && typeof payload.render_scene === "object"
    && !Array.isArray(payload.render_scene)
  ) {
    return payload.render_scene;
  }
  if (
    Array.isArray(payload.translations)
    || Array.isArray(payload.inline_marks)
    || Array.isArray(payload.sentence_entries)
  ) {
    return payload;
  }
  if (
    payload.output
    && typeof payload.output === "object"
    && !Array.isArray(payload.output)
    && (
      Array.isArray(payload.output.translations)
      || Array.isArray(payload.output.inline_marks)
      || Array.isArray(payload.output.sentence_entries)
    )
  ) {
    return payload.output;
  }
  return null;
}

export function normalizeSingleRunPayload(payload) {
  const scene = normalizeWorkflowScene(payload);
  const status = payload?.status || (scene ? "succeeded" : "unknown");
  return {
    status,
    scene,
    promptIdentity: payload?.prompt_identity || null,
    modelIdentity: payload?.model_identity || null,
    runtimeSummary: payload?.runtime_summary || null,
    warnings: Array.isArray(payload?.warnings)
      ? payload.warnings
      : Array.isArray(scene?.warnings)
        ? scene.warnings
        : [],
    error: payload?.error || null,
    savedHistoryRunId: payload?.saved_history_run_id || null,
    raw: payload,
  };
}

export function sceneTranslations(scene) {
  return Array.isArray(scene?.translations) ? scene.translations : [];
}

export function sceneInlineMarks(scene) {
  return Array.isArray(scene?.inline_marks) ? scene.inline_marks : [];
}

export function sceneSentenceEntries(scene) {
  return Array.isArray(scene?.sentence_entries) ? scene.sentence_entries : [];
}

export function sceneWarnings(scene) {
  return Array.isArray(scene?.warnings) ? scene.warnings : [];
}

function normalizeDisplayText(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .replace(/[“”]/g, "\"")
    .replace(/[‘’]/g, "'")
    .trim()
    .toLowerCase();
}

export function inlineMarkAnchorParts(mark) {
  const anchor = mark?.anchor;
  if (!anchor || typeof anchor !== "object") return [];
  if (anchor.kind === "multi_text" && Array.isArray(anchor.parts)) {
    return anchor.parts
      .map((part) => ({
        text: String(part?.anchor_text ?? part?.anchorText ?? "").trim(),
        occurrence: Number(part?.occurrence) || 1,
        role: String(part?.role || "").trim(),
      }))
      .filter((part) => part.text);
  }
  const text = String(anchor.anchor_text ?? anchor.anchorText ?? anchor.text ?? "").trim();
  if (!text) return [];
  return [{
    text,
    occurrence: Number(anchor.occurrence) || 1,
    role: String(anchor.role || "").trim(),
  }];
}

export function inlineMarkAnchorText(mark, separator = " / ") {
  const parts = inlineMarkAnchorParts(mark);
  return parts.map((part) => part.text).join(separator).trim();
}

export function inlineMarkLookupText(mark) {
  return String(mark?.lookup_text ?? mark?.lookupText ?? "").trim();
}

export function inlineMarkDisplayTitle(mark) {
  const lookup = inlineMarkLookupText(mark);
  const anchor = inlineMarkAnchorText(mark);
  const fallback = String(mark?.label || mark?.title || "").trim();
  return lookup || anchor || fallback || "—";
}

export function inlineMarkShowsDistinctAnchor(mark) {
  const title = inlineMarkDisplayTitle(mark);
  const anchor = inlineMarkAnchorText(mark);
  if (!anchor) return false;
  return normalizeDisplayText(title) !== normalizeDisplayText(anchor);
}

export function inlineMarkPrimarySummary(mark) {
  return String(mark?.glossary?.zh || mark?.glossary?.gloss || "").trim();
}

export function inlineMarkSecondarySummary(mark) {
  return String(mark?.glossary?.reason || mark?.glossary?.phrase_type || "").trim();
}

export function formatRunIdentity(run) {
  const variant = run?.prompt_variant_id || "baseline";
  const totalCases = run?.learning_case_count ?? run?.total_cases ?? 0;
  return `${variant} / ${totalCases} learning cases`;
}

/**
 * 用户可感知的 workflow 健康状态归一化。
 * 把 artifact 中分散的 user_facing_state / warnings / drop_log / usage_summary / runtime_summary
 * 聚合成单页 compare 视图可以直接消费的稳定 shape，不依赖后端再吐出额外字段。
 */
export const USER_FACING_STATE_TONE = {
  normal: { tone: "success", label: "正常" },
  success: { tone: "success", label: "正常" },
  succeeded: { tone: "success", label: "正常" },
  ok: { tone: "success", label: "正常" },
  running: { tone: "neutral", label: "运行中" },
  in_progress: { tone: "neutral", label: "运行中" },
  degraded_light: { tone: "warning", label: "轻度降级" },
  degraded_heavy: { tone: "danger", label: "严重降级" },
  partial_failure: { tone: "danger", label: "部分失败" },
  failed: { tone: "danger", label: "失败" },
  error: { tone: "danger", label: "失败" },
  cancelled: { tone: "neutral", label: "已取消" },
  canceled: { tone: "neutral", label: "已取消" },
  unknown: { tone: "neutral", label: "未知" },
};

export function userFacingStateTone(state) {
  const key = String(state || "unknown").toLowerCase();
  return USER_FACING_STATE_TONE[key] || USER_FACING_STATE_TONE.unknown;
}

/**
 * workflow-level warning code 的业务语义映射。
 * 加新 code 只需要在这个 map 里加一行；不在 map 里的 code 会回退到通用解释。
 */
export const WARNING_CODE_META = {
  anchor_resolve_failed: {
    label: "锚点解析失败",
    explanation: "锚点文本无法在原句中定位，说明上游 prompt 给出的 anchor_text 与原文不一致，标注无法挂接到具体位置。",
    severity: "warning",
    chipText: "锚点失败",
    chipTone: "anchor",
    category: "postprocess",
  },
  chunks_validation_failed: {
    label: "句法拆解校验失败",
    explanation: "句法拆解 chunk 与原句未对齐，已降级为普通讲解（不强保证结构化拆解）。",
    severity: "warning",
    chipText: "句法拆解降级",
    chipTone: "chunks",
    category: "postprocess",
  },
  DRAFT_VALIDATION: {
    label: "草稿校验未通过",
    explanation: "草稿阶段的 schema/文本一致性校验失败，说明上游 prompt 没让模型严格遵守 schema。",
    severity: "warning",
    chipText: "draft 校验问题",
    chipTone: "draft",
    category: "postprocess",
  },
  repair_triggered: {
    label: "修复节点被触发",
    explanation: "后置校验发现严重不一致，已转入 repair 节点救场；repair 开销会在下方 per-agent 中可见。",
    severity: "danger",
    chipText: "repair 介入",
    chipTone: "repair",
    category: "repair",
  },
  repair_failed: {
    label: "修复节点未恢复",
    explanation: "repair 节点无法把草稿修正到合格状态，整条 sentence 仍处于降级输出。",
    severity: "danger",
    chipText: "repair 失败",
    chipTone: "repair-fail",
    category: "repair",
  },
  schema_mismatch: {
    label: "schema 对齐失败",
    explanation: "模型输出与 schema 字段不一致，提示需要收窄 prompt 或调整 model profile。",
    severity: "warning",
    chipText: "schema 偏离",
    chipTone: "schema",
    category: "postprocess",
  },
  fallback_invoked: {
    label: "已降级到 fallback 模型",
    explanation: "主模型失败，已自动切换到 fallback profile；本侧结果不再代表主模型能力。",
    severity: "warning",
    chipText: "fallback",
    chipTone: "fallback",
    category: "postprocess",
  },
  unknown_warning: {
    label: "其他未识别 warning",
    explanation: "未在已知 code 列表中命中，建议查看 raw JSON 获取 detail。",
    severity: "warning",
    chipText: "未识别",
    chipTone: "unknown",
    category: "postprocess",
  },
};

export function warningCodeMeta(code) {
  if (code && Object.prototype.hasOwnProperty.call(WARNING_CODE_META, code)) {
    return WARNING_CODE_META[code];
  }
  return WARNING_CODE_META.unknown_warning;
}

function asWarningArray(maybe) {
  if (Array.isArray(maybe)) return maybe;
  if (Array.isArray(maybe?.warnings)) return maybe.warnings;
  return [];
}

function asDropArray(maybe) {
  if (Array.isArray(maybe?.drop_log)) return maybe.drop_log;
  if (Array.isArray(maybe?.dropLog)) return maybe.dropLog;
  return [];
}

/**
 * 把 warnings 数组按 code 分组，并按业务语义归一化。
 * 输出对 UI 友好：
 *   groups: 按 code 聚合，包含 count / level / sentenceIds / sampleMessages / meta
 *   total: 总条数
 *   bySid: Map<sentenceId, {code, meta, message}[]>  — 方便 sentence-level chip 消费
 *   byCode: Map<code, group>
 *   postprocessCount / repairCount: 后置校验 / 修复介入的快速分桶
 */
export function groupWorkflowWarnings(warningsOrPayload) {
  const warnings = asWarningArray(warningsOrPayload);
  const groupsMap = new Map();
  const bySid = new Map();
  let postprocessCount = 0;
  let repairCount = 0;

  for (const item of warnings) {
    if (!item || typeof item !== "object") continue;
    const code = String(item.code || "unknown_warning");
    const meta = warningCodeMeta(code);
    const sid = item.sentence_id != null ? String(item.sentence_id) : null;
    const key = code;

    if (!groupsMap.has(key)) {
      groupsMap.set(key, {
        code,
        meta,
        count: 0,
        level: item.level || meta.severity,
        sentenceIds: new Set(),
        sampleMessages: [],
      });
    }
    const group = groupsMap.get(key);
    group.count += 1;
    if (sid) group.sentenceIds.add(sid);
    if (group.sampleMessages.length < 3) {
      group.sampleMessages.push({
        message: item.message || "",
        sentenceId: sid,
      });
    }
    if (meta.category === "repair") repairCount += 1;
    else postprocessCount += 1;

    if (sid) {
      if (!bySid.has(sid)) bySid.set(sid, []);
      bySid.get(sid).push({ code, meta, message: item.message || "", level: item.level || meta.severity });
    }
  }

  const groups = Array.from(groupsMap.values()).map((group) => ({
    ...group,
    sentenceIds: Array.from(group.sentenceIds).sort((a, b) => {
      const an = Number((a.match(/\d+/) || [0])[0]);
      const bn = Number((b.match(/\d+/) || [0])[0]);
      return an - bn;
    }),
  }));

  // 排序：repair 类靠前，其他按 count 降序
  groups.sort((a, b) => {
    if (a.meta.category !== b.meta.category) {
      return a.meta.category === "repair" ? -1 : 1;
    }
    return b.count - a.count;
  });

  return {
    groups,
    total: warnings.length,
    bySid,
    byCode: groupsMap,
    postprocessCount,
    repairCount,
  };
}

/**
 * 把 artifact 中 workflow 健康相关字段归一化为单页可消费的 shape。
 * 字段名刻意保持稳定，便于 UI 直接绑定，不依赖 Vuex / props 链路。
 *
 * 与 counter / 桶字段相关的真实值统一从 `warningsGrouped` 与 `perAgent` 派生，
 * 不在顶层保留 fake 字段以免误用。
 */
export function extractHealthSignals(artifact) {
  if (!artifact || typeof artifact !== "object") {
    const grouped = groupWorkflowWarnings([]);
    return {
      userFacingState: "unknown",
      userFacingTone: userFacingStateTone("unknown"),
      warningCount: 0,
      dropCount: 0,
      latencySeconds: null,
      totalTokens: null,
      inputTokens: null,
      outputTokens: null,
      perAgent: [],
      repairTokens: null,
      repairShare: null,
      warnings: [],
      warningsGrouped: grouped,
      drops: [],
      warningCodeSet: new Set(),
    };
  }
  const userFacingState = artifact.user_facing_state || artifact.adapter_status || "unknown";
  const usage = artifact.usage_summary || {};
  const runtime = artifact.runtime_summary || {};
  const perAgentSource = (runtime.per_agent && typeof runtime.per_agent === "object")
    ? runtime.per_agent
    : {};
  const totalTokens = usage.total_tokens ?? runtime.aggregate?.total_tokens ?? null;
  const perAgent = ["vocabulary", "grammar", "translation", "repair"]
    .filter((name) => Object.prototype.hasOwnProperty.call(perAgentSource, name))
    .map((name) => {
      const entry = perAgentSource[name] || {};
      return {
        name,
        input: entry.input_tokens ?? null,
        output: entry.output_tokens ?? null,
        total: entry.total_tokens ?? null,
      };
    });
  // 兼容旧 artifact 之外有其他 agent 名时，仍然全部并入
  for (const [name, entry] of Object.entries(perAgentSource)) {
    if (perAgent.find((row) => row.name === name)) continue;
    perAgent.push({
      name,
      input: entry?.input_tokens ?? null,
      output: entry?.output_tokens ?? null,
      total: entry?.total_tokens ?? null,
    });
  }
  const repairEntry = perAgentSource.repair || null;
  const repairTokens = repairEntry?.total_tokens ?? null;
  const repairShare = (totalTokens && repairTokens)
    ? repairTokens / totalTokens
    : null;
  const warnings = asWarningArray(artifact);
  const drops = asDropArray(artifact);
  const grouped = groupWorkflowWarnings(warnings);
  return {
    userFacingState,
    userFacingTone: userFacingStateTone(userFacingState),
    warningCount: warnings.length,
    dropCount: drops.length,
    latencySeconds: Number.isFinite(Number(artifact.latency_seconds))
      ? Number(artifact.latency_seconds)
      : (Number.isFinite(Number(runtime.latency_ms)) ? Number(runtime.latency_ms) / 1000 : null),
    totalTokens,
    inputTokens: usage.input_tokens ?? runtime.aggregate?.input_tokens ?? null,
    outputTokens: usage.output_tokens ?? runtime.aggregate?.output_tokens ?? null,
    perAgent,
    repairTokens,
    repairShare,
    warnings,
    warningsGrouped: grouped,
    drops,
    // 派生：所有出现过的 warning code 集合
    warningCodeSet: new Set(grouped.byCode.keys()),
  };
}

/**
 * 把后置校验结果与修复节点消耗区分为两个独立维度，避免把 repair 伪装成普通节点。
 * 返回值既给上层 health panel 用，也给 sentence-level chip 用。
 */
export function splitPostprocessAndRepair(health) {
  if (!health) return { postprocess: [], repair: [] };
  const groups = health.warningsGrouped?.groups || [];
  const postprocess = groups.filter((g) => g.meta.category !== "repair");
  const repair = groups.filter((g) => g.meta.category === "repair");
  return { postprocess, repair };
}

/**
 * sentence-level chip helper：
 * 给定 warningsGrouped + sentenceId，返回该 sentence 应该挂的 chip 列表。
 * 同一 code 出现多次会合并成单 chip，并按严重度升序展示。
 */
export function sentenceWarningChips(warningsGrouped, sentenceId) {
  if (!warningsGrouped || !sentenceId) return [];
  const sid = String(sentenceId);
  const items = warningsGrouped.bySid?.get(sid) || [];
  if (!items.length) return [];
  const deduped = new Map();
  for (const item of items) {
    if (!deduped.has(item.code)) {
      deduped.set(item.code, {
        code: item.code,
        text: item.meta.chipText,
        tone: item.meta.chipTone,
        category: item.meta.category,
        severity: item.meta.severity,
        message: item.message,
      });
    }
  }
  const result = Array.from(deduped.values());
  result.sort((a, b) => {
    if (a.category !== b.category) return a.category === "repair" ? -1 : 1;
    return a.text.localeCompare(b.text);
  });
  return result;
}

/**
 * 合并 baseline + candidate 两侧 warnings，让 sentence-level chip 能跨侧显示问题。
 *
 * 关键设计：
 *  - 同 code 出现于两侧时，合并成单 chip 并把 sides 标为 ['baseline', 'candidate']。
 *  - chip 同时保留两侧原始 message，存到 `messages: { baseline?, candidate? }`，
 *    避免 UI 用单条 message 当 tooltip 误读为"两侧问题细节相同"。
 *  - 单侧时只填对应侧的 message，sides 数组也只含一个元素。
 *
 * 排序：repair category 优先 → category 相同按 chipText。
 */
export function mergeSentenceWarningChips(baselineGrouped, candidateGrouped, sentenceId) {
  const base = sentenceWarningChips(baselineGrouped, sentenceId);
  const cand = sentenceWarningChips(candidateGrouped, sentenceId);
  const byCode = new Map();
  const pushSide = (item, side) => {
    if (!byCode.has(item.code)) {
      byCode.set(item.code, {
        code: item.code,
        text: item.text,
        tone: item.tone,
        category: item.category,
        severity: item.severity,
        sides: [],
        messages: {},
        level: item.level,
      });
    }
    const entry = byCode.get(item.code);
    entry.sides.push(side);
    // 同一 code + 同一侧可能有多条 warning（如 chunks_validation_failed 命中多次），
    // 拼接为单行 summary，UI 端在 tooltip 里全部展开。
    if (item.message) {
      const slot = entry.messages[side] || [];
      slot.push(item.message);
      entry.messages[side] = slot;
    }
  };
  for (const item of base) pushSide(item, "baseline");
  for (const item of cand) pushSide(item, "candidate");
  const out = [];
  for (const entry of byCode.values()) {
    const sides = Array.from(new Set(entry.sides));
    sides.sort();
    out.push({ ...entry, sides });
  }
  out.sort((a, b) => {
    if (a.category !== b.category) return a.category === "repair" ? -1 : 1;
    return a.text.localeCompare(b.text);
  });
  return out;
}

/**
 * chip 的 tooltip 文本。
 *  - 单侧：使用该侧 message
 *  - 双侧：拼成 "B: ...\nC: ..."，让用户看到两侧可能不同的 failure detail
 *  - 无 message：fallback 到 chipText
 */
export function chipTooltip(chip) {
  if (!chip) return "";
  const messages = chip.messages || {};
  const sides = Array.isArray(chip.sides) ? chip.sides : [];
  if (sides.length >= 2) {
    const lines = [];
    if (messages.baseline?.length) lines.push(`B: ${messages.baseline.join(" | ")}`);
    if (messages.candidate?.length) lines.push(`C: ${messages.candidate.join(" | ")}`);
    return lines.length ? lines.join("\n") : chip.text;
  }
  const singleSide = sides[0];
  const slot = singleSide ? messages[singleSide] : null;
  if (slot?.length) return `${singleSide === "baseline" ? "B" : "C"}: ${slot.join(" | ")}`;
  return chip.text;
}

/**
 * chip 上显示的 side 简短标签。
 *  - 单侧："B" / "C"
 *  - 双侧："B+C"
 *  - 单侧 notebook（无 side 概念）：""
 */
export function chipSideLabel(sides) {
  if (!Array.isArray(sides) || !sides.length) return "";
  const set = new Set(sides);
  if (set.has("baseline") && set.has("candidate")) return "B+C";
  if (set.has("baseline")) return "B";
  if (set.has("candidate")) return "C";
  return "";
}

/**
 * repair 节点状态机。
 *
 * 综合三条信号：
 *  1. 是否有 `repair_failed` warning（明确的修复失败信号）
 *  2. 是否有 `repair_triggered` warning 或 repair token > 0（已介入）
 *  3. user_facing_state 是否已经是 danger tone（说明整体已经塌方）
 *
 * 输出：
 *  - { state, label, tone, hint }
 *  - state ∈ { 'not_triggered', 'triggered_clean', 'triggered_with_failures', 'failed_recovery', 'overrun_by_tokens' }
 */
export const REPAIR_STATE_META = {
  not_triggered: {
    label: "未触发 repair",
    tone: "neutral",
    hint: "本次 workflow 没有触发 repair 节点，结果由主内容节点直接产出。",
  },
  triggered_clean: {
    label: "已触发 repair，未见失败",
    tone: "warning",
    hint: "repair 节点介入过，但没有 capture 到 repair_failed 信号；建议关注 token 占比。",
  },
  triggered_with_failures: {
    label: "已触发 repair 且仍有失败信号",
    tone: "danger",
    hint: "repair 节点介入后仍未解决 schema / 文本一致性问题，需回看 prompt。",
  },
  failed_recovery: {
    label: "repair 节点未恢复",
    tone: "danger",
    hint: "捕获到 repair_failed 信号，repair 节点没有把草稿恢复到合格状态。",
  },
  overrun_by_tokens: {
    label: "repair token 占比异常",
    tone: "danger",
    hint: "repair 节点消耗的 token 已超过总 token 的 50%，上游可能存在严重 schema 不稳。",
  },
};

export function computeRepairStatus(health) {
  if (!health) {
    return { state: "not_triggered", ...REPAIR_STATE_META.not_triggered };
  }
  const grouped = health.warningsGrouped;
  const codes = new Set(grouped?.byCode?.keys() || []);
  const hasFailed = codes.has("repair_failed");
  const hasTriggered = codes.has("repair_triggered");
  const repairTokens = health.repairTokens;
  const repairShare = health.repairShare;
  const tone = health.userFacingTone?.tone;

  if (hasFailed) {
    return { state: "failed_recovery", ...REPAIR_STATE_META.failed_recovery };
  }
  if (repairShare != null && repairShare >= 0.5) {
    return { state: "overrun_by_tokens", ...REPAIR_STATE_META.overrun_by_tokens };
  }
  if (hasTriggered || (repairTokens != null && repairTokens > 0)) {
    if (tone === "danger" || health.userFacingState === "degraded_heavy" || health.userFacingState === "partial_failure" || health.userFacingState === "failed") {
      return { state: "triggered_with_failures", ...REPAIR_STATE_META.triggered_with_failures };
    }
    return { state: "triggered_clean", ...REPAIR_STATE_META.triggered_clean };
  }
  return { state: "not_triggered", ...REPAIR_STATE_META.not_triggered };
}

/**
 * compare insight：candidate 相对 baseline 的健康变化。
 *
 * 输出字段：
 *  - warningDelta: number  (candidate - baseline)
 *  - warningDeltaPct: number | null
 *  - repairShareDeltaPp: number | null  (candidate - baseline, 单位 pp)
 *  - newCodes: string[]  (只在 candidate 出现的 code)
 *  - removedCodes: string[]  (只在 baseline 出现的 code)
 *  - newCodesMeta / removedCodesMeta: 带 label / category 的对象
 *  - stateDirection: 'better' | 'same' | 'worse' | 'unknown'
 *  - overallDirectionLabel: 中文短标签
 */
export function compareHealthInsights(baselineHealth, candidateHealth) {
  const base = baselineHealth || null;
  const cand = candidateHealth || null;
  const baseWarnings = base?.warningCount ?? 0;
  const candWarnings = cand?.warningCount ?? 0;
  const warningDelta = candWarnings - baseWarnings;
  const warningDeltaPct = baseWarnings > 0
    ? (warningDelta / baseWarnings)
    : (candWarnings > 0 ? null : 0);

  const baseShare = base?.repairShare;
  const candShare = cand?.repairShare;
  const repairShareDeltaPp = (baseShare != null && candShare != null)
    ? (candShare - baseShare) * 100
    : null;

  const baseCodes = base?.warningCodeSet || new Set();
  const candCodes = cand?.warningCodeSet || new Set();
  const newCodes = [];
  for (const code of candCodes) if (!baseCodes.has(code)) newCodes.push(code);
  const removedCodes = [];
  for (const code of baseCodes) if (!candCodes.has(code)) removedCodes.push(code);
  newCodes.sort();
  removedCodes.sort();

  const decorate = (codes) => codes.map((code) => ({
    code,
    label: warningCodeMeta(code).label,
    chipText: warningCodeMeta(code).chipText,
    category: warningCodeMeta(code).category,
  }));

  const newCodesMeta = decorate(newCodes);
  const removedCodesMeta = decorate(removedCodes);

  // 整体方向：基于加权评分，冲突信号不会因任一 OR 命中就强行归到单侧。
  // 权重解释：
  //   - repair share：每 1pp 计 0.06 分（≈ 17pp = 1 分），20pp 就能推动判定。
  //   - repair 类别 code 变化：每条 2 分（强信号，schema/一致性塌方）。
  //   - postprocess 类别 code 变化：每条 0.8 分（次强，单纯警告）。
  //   - warning 总数：每条 0.4 分。
  //   - user_facing_state 升降：1 分（候选 danger / 消除 degraded 各 ±1）。
  const COMPARE_SCORE_THRESHOLD = 1.0;
  let stateDirection = "unknown";
  if (!base || !cand) {
    stateDirection = "unknown";
  } else {
    let score = 0;
    if (repairShareDeltaPp != null) score += repairShareDeltaPp * 0.06;
    score += warningDelta * 0.4;
    for (const c of newCodesMeta) {
      score += c.category === "repair" ? 2.0 : 0.8;
    }
    for (const c of removedCodesMeta) {
      score -= c.category === "repair" ? 2.0 : 0.8;
    }
    // user_facing_state 升降作为 1 分的硬信号
    const baseToneRank = userFacingToneRank(base.userFacingTone?.tone);
    const candToneRank = userFacingToneRank(cand.userFacingTone?.tone);
    if (candToneRank > baseToneRank) score += 1;
    else if (candToneRank < baseToneRank) score -= 1;

    if (score > COMPARE_SCORE_THRESHOLD) stateDirection = "worse";
    else if (score < -COMPARE_SCORE_THRESHOLD) stateDirection = "better";
    else stateDirection = "same";
  }

  const overallDirectionLabel = {
    better: "Candidate 总体更健康",
    same: "Candidate 与 baseline 健康度相当",
    worse: "Candidate 总体更不健康",
    unknown: "Candidate 总体健康度未知",
  }[stateDirection];

  return {
    warningDelta,
    warningDeltaPct,
    repairShareDeltaPp,
    newCodes,
    removedCodes,
    newCodesMeta,
    removedCodesMeta,
    stateDirection,
    overallDirectionLabel,
  };
}

/**
 * 把 user_facing_state tone 映射到一个可比较的 rank（越大越差）。
 * 用来在 compareHealthInsights 中作为 +1 / -1 的硬信号。
 */
function userFacingToneRank(tone) {
  switch (tone) {
    case "success": return 0;
    case "neutral": return 1;
    case "warning": return 2;
    case "danger": return 3;
    default: return 1;
  }
}
