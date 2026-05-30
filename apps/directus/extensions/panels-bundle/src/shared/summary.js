import { fetchJson } from "./query.js";

export const DEFAULT_SUMMARY_URL = "/parse-run-observability/summary?days=7";

export async function fetchSummary(endpointUrl = DEFAULT_SUMMARY_URL) {
  return await fetchJson(endpointUrl);
}

export function section(summary, key, fallback) {
  const value = summary?.[key];
  return value && typeof value === "object" ? value : fallback;
}

export function toneForState(state) {
  if (state === "normal" || state === "succeeded" || state === "ready") return "success";
  if (state === "degraded_light" || state === "partial") return "warning";
  if (state === "degraded_heavy" || state === "failed") return "danger";
  return "muted";
}

export function stateLabel(state) {
  const labels = {
    normal: "正常",
    degraded_light: "轻度降级",
    degraded_heavy: "重度降级",
    failed: "失败",
    unknown: "未记录",
  };
  return labels[state] ?? String(state || "未记录");
}

export function capabilityLabel(value) {
  const labels = {
    analysis_full: "主解析",
    analysis_overview_hint: "Overview",
    rag_embedding: "RAG Embedding",
    rag_rerank: "RAG Rerank",
  };
  return labels[value] ?? String(value || "unknown");
}

export function ragOutputTypeLabel(value) {
  const labels = {
    grammar_note: "语法讲解",
    sentence_analysis: "句子拆析",
  };
  return labels[value] ?? String(value || "未记录");
}

export function ragFallbackReasonLabel(value) {
  const raw = String(value || "");
  if (!raw) return "未记录";
  if (raw === "empty_candidates") return "未召回候选";
  if (raw === "low_confidence") return "低置信度";
  if (raw === "no_input_sentences") return "无检索句子";
  if (raw.startsWith("retrieval_error")) return raw.replace("retrieval_error:", "检索异常：").trim();
  return raw;
}

export function ragDropReasonLabel(value) {
  const raw = String(value || "");
  const labels = {
    "confidence_filter:below_confidence_threshold": "低于阈值",
    "diversity_dedup:duplicate_label": "标签重复",
    "diversity_dedup:duplicate_sentence": "原句重复",
    "diversity_dedup:duplicate_tag_set": "语法标签重复",
    "budget_trim:exceeds_injection_budget": "超出注入预算",
  };
  return labels[raw] ?? (raw.replace(":", " / ") || "未记录");
}
