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
