/** @vitest-environment jsdom */

"use client";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { legacyAppReaderRoute } from "@/lib/routes";
import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";

import { VocabularyClient } from "./VocabularyClient";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

function installMatchMedia(matches = true) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function makeVocabularyItem(
  overrides: Partial<VocabularyItemVm> = {},
): VocabularyItemVm {
  return {
    id: "vocab_1",
    word: "memory",
    lookupKind: "word",
    lemma: "memory",
    phonetic: "/mem/",
    partOfSpeech: "noun",
    shortMeaning: "记忆",
    contextSentence: "Institutional memory shapes choices.",
    contextTranslation: "制度记忆会塑造选择。",
    sourceRecordId: "legacy record 1",
    sourceRecordTitle: "Legacy Article",
    createdAt: "2026-06-22T00:00:00.000Z",
    updatedAt: "2026-06-22T00:00:00.000Z",
    mastered: false,
    masteryStatus: "learning",
    reviewCount: 0,
    tags: [],
    nextReviewAt: undefined,
    reviewStage: undefined,
    lastReviewedAt: undefined,
    sourceRefs: [],
    collectedForms: [],
    dictEntryId: null,
    audioUrl: undefined,
    detailMeanings: undefined,
    detailPhrases: undefined,
    detailExamples: undefined,
    totalSourceCount: 1,
    totalSourceArticleCount: 1,
    ...overrides,
  };
}

beforeEach(() => {
  installMatchMedia(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("VocabularyClient source links", () => {
  it("navigates legacy sourceRecordId to the legacy reader route", () => {
    const item = makeVocabularyItem();

    render(
      <VocabularyClient
        items={[item]}
        status="ready"
        dueCount={0}
        learningCount={1}
        masteredCount={0}
        recentItems={[item]}
        multiContextItems={[]}
      />,
    );

    const sourceLink = screen.getByRole("link", { name: "查看来源语境" });

    expect(sourceLink.getAttribute("href")).toBe(
      legacyAppReaderRoute("legacy record 1"),
    );
  });
});
