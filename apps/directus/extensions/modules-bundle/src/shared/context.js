import { inferClientSource } from "./enum-display.js";

function normalizeRecord(item) {
  if (!item || typeof item !== "object") return null;
  return {
    type: "record",
    id: item.id ? String(item.id) : "",
    title: item.title ? String(item.title) : "未命名记录",
    client_record_id: item.client_record_id ? String(item.client_record_id) : "",
    last_opened_at: item.last_opened_at ?? null,
    source_text: item.source_text ? String(item.source_text) : "",
    client_source: inferClientSource(item.client_record_id),
  };
}

// DATA-LEGACY-IDENTITY-EXIT: the analysis_task / analysis_overview_task
// deep-link branches and the /items fetch fallback are gone; context values
// are normalized locally only.

export function normalizeContext(value, target = "record") {
  if (value == null || value === "") return null;

  if (typeof value === "string") {
    return {
      type: target,
      id: value,
    };
  }

  if (typeof value === "object") {
    return normalizeRecord(value);
  }

  return null;
}

export async function resolveContext(value, target = "record") {
  return normalizeContext(value, target);
}

