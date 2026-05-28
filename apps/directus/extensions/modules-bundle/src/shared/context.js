import { inferClientSource } from "./enum-display.js";

const CACHE = new Map();

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

function normalizeTask(item, type) {
  if (!item || typeof item !== "object") return null;

  const record =
    item.analysis_record_id && typeof item.analysis_record_id === "object"
      ? normalizeRecord(item.analysis_record_id)
      : null;

  return {
    type,
    id: item.id ? String(item.id) : "",
    status: item.status ? String(item.status) : "",
    analysis_record_id:
      typeof item.analysis_record_id === "string" ? item.analysis_record_id : record?.id ?? "",
    record,
  };
}

function buildConfig(target) {
  if (target === "analysis_task") {
    return {
      path: (id) =>
        `/items/analysis_tasks/${encodeURIComponent(id)}?fields=id,status,analysis_record_id.id,analysis_record_id.title,analysis_record_id.client_record_id`,
      normalize: (item) => normalizeTask(item, "analysis_task"),
    };
  }

  if (target === "analysis_overview_task") {
    return {
      path: (id) =>
        `/items/analysis_overview_tasks/${encodeURIComponent(id)}?fields=id,status,analysis_record_id.id,analysis_record_id.title,analysis_record_id.client_record_id`,
      normalize: (item) => normalizeTask(item, "analysis_overview_task"),
    };
  }

  return {
    path: (id) =>
      `/items/analysis_records/${encodeURIComponent(id)}?fields=id,title,client_record_id,last_opened_at,source_text`,
    normalize: normalizeRecord,
  };
}

function getKey(target, id) {
  return `${target}:${id}`;
}

async function fetchItem(path) {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.status}`);
  }

  const payload = await response.json();
  return payload?.data ?? null;
}

export function normalizeContext(value, target = "record") {
  if (value == null || value === "") return null;

  if (typeof value === "string") {
    return {
      type: target,
      id: value,
    };
  }

  if (typeof value === "object") {
    if (target === "analysis_task" || target === "analysis_overview_task") {
      return normalizeTask(value, target);
    }
    return normalizeRecord(value);
  }

  return null;
}

export async function resolveContext(value, target = "record") {
  const normalized = normalizeContext(value, target);
  if (!normalized?.id) return normalized;

  if (
    target === "record" &&
    normalized.title &&
    normalized.client_record_id !== undefined &&
    normalized.source_text !== undefined
  ) {
    return normalized;
  }

  if ((target === "analysis_task" || target === "analysis_overview_task") && normalized.record) {
    return normalized;
  }

  const key = getKey(target, normalized.id);
  if (!CACHE.has(key)) {
    const config = buildConfig(target);
    CACHE.set(
      key,
      fetchItem(config.path(normalized.id))
        .then(config.normalize)
        .catch(() => normalized),
    );
  }

  return CACHE.get(key);
}

