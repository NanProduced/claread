/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  READER_DICTIONARY_AI_STORAGE_KEY_PREFIX,
  createDictionaryAICacheEntry,
  dictionaryAIViewStateFromCacheEntry,
  persistDictionaryAIArticleCache,
  readStoredDictionaryAIArticleCache,
} from "./storage";

type StorageMock = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
  clear: () => void;
};

let storageMock: StorageMock;

beforeEach(() => {
  const store = new Map<string, string>();
  storageMock = {
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value);
    }),
    clear: vi.fn(() => {
      store.clear();
    }),
  };

  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storageMock,
  });
});

afterEach(() => {
  storageMock.clear();
});

describe("dictionary AI storage", () => {
  it("persists and restores article-scoped AI cache entries", () => {
    persistDictionaryAIArticleCache("record-1", {
      "entry-1": createDictionaryAICacheEntry(
        {
          kind: "ready",
          mode: "context_explain",
          requestKey: "context::record-1::s1",
          result: {
            kind: "context_explain",
            mode: "context_explain",
            query: "memory",
            summary: "这里强调制度与经验的累积。",
            bestFitSense: "长期积累的经验",
          },
        },
        false,
      ),
    });

    expect(storageMock.getItem(`${READER_DICTIONARY_AI_STORAGE_KEY_PREFIX}.record-1`)).toContain("\"expanded\":false");

    const restored = readStoredDictionaryAIArticleCache("record-1");
    expect(Object.keys(restored)).toEqual(["entry-1"]);
    expect(dictionaryAIViewStateFromCacheEntry(restored["entry-1"])).toMatchObject({
      kind: "ready",
      mode: "context_explain",
      requestKey: "context::record-1::s1",
    });
    expect(restored["entry-1"]?.expanded).toBe(false);
  });

  it("drops malformed entries and keeps only the newest 40 items", () => {
    const key = `${READER_DICTIONARY_AI_STORAGE_KEY_PREFIX}.record-1`;
    const payload: Record<string, unknown> = Object.fromEntries(
      Array.from({ length: 42 }, (_, index) => [
        `entry-${index}`,
        {
          expanded: true,
          updatedAt: new Date(2025, 0, index + 1).toISOString(),
          state: {
            kind: "ready",
            mode: "missing_fallback",
            requestKey: `missing-${index}`,
            result: {
              kind: "ai_unresolved",
              mode: "missing_fallback",
              query: `token-${index}`,
              classification: "unrecognized_noise",
              summary: `summary-${index}`,
              verified: false,
              source: "ai_generated",
              suggestedQuery: [],
              resultKind: "ai_unresolved",
            },
          },
        },
      ]),
    );

    payload["broken-entry"] = { expanded: true };
    storageMock.setItem(key, JSON.stringify(payload));

    const restored = readStoredDictionaryAIArticleCache("record-1");
    expect(Object.keys(restored)).toHaveLength(40);
    expect(restored["broken-entry"]).toBeUndefined();
    expect(restored["entry-0"]).toBeUndefined();
    expect(restored["entry-41"]).toBeTruthy();
  });
});
