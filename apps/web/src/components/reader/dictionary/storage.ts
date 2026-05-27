"use client";

import type {
  DictionaryAIViewState,
  WebDictAIErrorResult,
  WebDictAIRequest,
  WebDictAIResult,
} from "@/types/api/dict-ai";

export const READER_DICTIONARY_AI_STORAGE_KEY_PREFIX = "claread.reader.dictionary-ai.v1";
const MAX_DICTIONARY_AI_CACHE_ENTRIES = 40;

type PersistedDictionaryAIReadyState = {
  kind: "ready";
  mode: WebDictAIRequest["mode"];
  requestKey: string;
  result: WebDictAIResult;
};

type PersistedDictionaryAIErrorState = {
  kind: "error";
  mode: WebDictAIRequest["mode"];
  requestKey: string;
  error: WebDictAIErrorResult;
};

export type PersistedDictionaryAIState =
  | PersistedDictionaryAIReadyState
  | PersistedDictionaryAIErrorState;

export interface PersistedDictionaryAIEntry {
  expanded: boolean;
  updatedAt: string;
  state: PersistedDictionaryAIState;
}

export type DictionaryAIArticleCache = Record<string, PersistedDictionaryAIEntry>;

function isDictionaryAIMode(value: unknown): value is WebDictAIRequest["mode"] {
  return value === "context_explain" || value === "missing_fallback";
}

function isDictionaryAIReadyState(value: unknown): value is PersistedDictionaryAIReadyState {
  if (!value || typeof value !== "object") {
    return false;
  }

  const payload = value as Record<string, unknown>;
  return (
    payload.kind === "ready" &&
    isDictionaryAIMode(payload.mode) &&
    typeof payload.requestKey === "string" &&
    Boolean(payload.result && typeof payload.result === "object")
  );
}

function isDictionaryAIErrorState(value: unknown): value is PersistedDictionaryAIErrorState {
  if (!value || typeof value !== "object") {
    return false;
  }

  const payload = value as Record<string, unknown>;
  return (
    payload.kind === "error" &&
    isDictionaryAIMode(payload.mode) &&
    typeof payload.requestKey === "string" &&
    Boolean(payload.error && typeof payload.error === "object")
  );
}

function normalizeDictionaryAIEntry(value: unknown): PersistedDictionaryAIEntry | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const payload = value as Record<string, unknown>;
  const state = isDictionaryAIReadyState(payload.state)
    ? payload.state
    : isDictionaryAIErrorState(payload.state)
      ? payload.state
      : null;
  if (!state) {
    return null;
  }

  return {
    expanded: typeof payload.expanded === "boolean" ? payload.expanded : true,
    updatedAt: typeof payload.updatedAt === "string" && payload.updatedAt.trim() ? payload.updatedAt : new Date(0).toISOString(),
    state,
  };
}

function pruneDictionaryAIArticleCache(cache: DictionaryAIArticleCache): DictionaryAIArticleCache {
  const nextEntries = Object.entries(cache)
    .sort((left, right) => Date.parse(right[1].updatedAt) - Date.parse(left[1].updatedAt))
    .slice(0, MAX_DICTIONARY_AI_CACHE_ENTRIES);

  return Object.fromEntries(nextEntries);
}

function storageKey(recordId: string) {
  return `${READER_DICTIONARY_AI_STORAGE_KEY_PREFIX}.${recordId}`;
}

export function dictionaryAIViewStateFromCacheEntry(entry: PersistedDictionaryAIEntry): DictionaryAIViewState {
  if (entry.state.kind === "ready") {
    return {
      kind: "ready",
      mode: entry.state.mode,
      requestKey: entry.state.requestKey,
      result: entry.state.result,
    };
  }

  return {
    kind: "error",
    mode: entry.state.mode,
    requestKey: entry.state.requestKey,
    error: entry.state.error,
  };
}

export function readStoredDictionaryAIArticleCache(recordId: string): DictionaryAIArticleCache {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const raw = window.localStorage.getItem(storageKey(recordId));
    if (!raw) {
      return {};
    }

    const payload = JSON.parse(raw) as Record<string, unknown>;
    const nextCache: DictionaryAIArticleCache = {};
    Object.entries(payload).forEach(([key, value]) => {
      const normalized = normalizeDictionaryAIEntry(value);
      if (normalized) {
        nextCache[key] = normalized;
      }
    });

    return pruneDictionaryAIArticleCache(nextCache);
  } catch {
    return {};
  }
}

export function persistDictionaryAIArticleCache(recordId: string, cache: DictionaryAIArticleCache) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(storageKey(recordId), JSON.stringify(pruneDictionaryAIArticleCache(cache)));
  } catch {
    // Ignore persistence failures. Dictionary AI cache is best-effort only.
  }
}

export function createDictionaryAICacheEntry(
  state: Extract<DictionaryAIViewState, { kind: "ready" | "error" }>,
  expanded: boolean,
): PersistedDictionaryAIEntry {
  return {
    expanded,
    updatedAt: new Date().toISOString(),
    state:
      state.kind === "ready"
        ? {
            kind: "ready",
            mode: state.mode,
            requestKey: state.requestKey,
            result: state.result,
          }
        : {
            kind: "error",
            mode: state.mode,
            requestKey: state.requestKey,
            error: state.error,
          },
  };
}
