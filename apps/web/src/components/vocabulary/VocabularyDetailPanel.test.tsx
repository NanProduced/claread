/** @vitest-environment jsdom */

"use client";

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";
import type { VocabularySourceRefDto } from "@/types/api/vocabulary";

import { VocabularyDetailPanel } from "./VocabularyDetailPanel";

// "在原文中定位" 按钮（VocabularyDetailPanel 内部）使用 text-lens-blue 类名
// 作为可识别标记。Radix Tooltip 把按钮渲染为常规 <button>，jsdom 下点击
// 不依赖 hover，可以直接通过 className 选择器精确定位。
//
// 必须用 [class~="text-lens-blue"] word-match 选择器，避免子串匹配
// Button primitive 的 `focus-visible:ring-lens-blue/20` 类名（后者是
// `ring-lens-blue/20` token，与 `text-lens-blue` 是不同 token）。
function locateSourceRefButton(container: HTMLElement): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>(
    'button[class~="text-lens-blue"]',
  );
}

function makeSourceRef(
  overrides: Partial<VocabularySourceRefDto> = {},
): VocabularySourceRefDto {
  return {
    source_sentence: "Institutional memory shapes choices.",
    ...overrides,
  };
}

function makeItem(
  sourceRefs: VocabularySourceRefDto[],
  overrides: Partial<VocabularyItemVm> = {},
): VocabularyItemVm {
  return {
    id: "vocab_1",
    word: "memory",
    lemma: "memory",
    phonetic: "/mem/",
    partOfSpeech: "noun",
    shortMeaning: "记忆",
    contextSentence: "Institutional memory shapes choices.",
    contextTranslation: "制度记忆会塑造选择。",
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
    sourceRefs,
    collectedForms: [],
    dictEntryId: null,
    audioUrl: undefined,
    detailMeanings: undefined,
    detailPhrases: undefined,
    detailExamples: undefined,
    totalSourceCount: sourceRefs.length,
    totalSourceArticleCount: sourceRefs.length,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  // jsdom 默认 matchMedia 不存在，VocabularyDetailPanel 不直接调用 matchMedia，
  // 但保留 polyfill 防御未来扩展。
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
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
});

describe("VocabularyDetailPanel - source ref reader entry priority", () => {
  it("calls onGoToSource with the Reading Record id and ignores legacy ids", () => {
    const onGoToSource = vi.fn();
    const item = makeItem([
      makeSourceRef({
        reading_record_id: "reading_record_1",
        cloud_record_id: "legacy_record_1",
      }),
    ]);

    const { container } = render(
      <VocabularyDetailPanel item={item} onGoToSource={onGoToSource} />,
    );

    const button = locateSourceRefButton(container);
    expect(button).not.toBeNull();

    fireEvent.click(button!);

    expect(onGoToSource).toHaveBeenCalledTimes(1);
    expect(onGoToSource).toHaveBeenCalledWith({
      readingRecordId: "reading_record_1",
      sentenceId: undefined,
    });
  });

  it("does not render a locate button when only cloud_record_id is present", () => {
    const onGoToSource = vi.fn();
    const item = makeItem([
      makeSourceRef({
        cloud_record_id: "legacy_record_1",
      }),
    ]);

    const { container } = render(
      <VocabularyDetailPanel item={item} onGoToSource={onGoToSource} />,
    );

    expect(locateSourceRefButton(container)).toBeNull();
    expect(onGoToSource).not.toHaveBeenCalled();
  });

  it("does not render a locate button when only client_record_id is present", () => {
    const onGoToSource = vi.fn();
    const item = makeItem([
      makeSourceRef({
        client_record_id: "client_record_1",
      }),
    ]);

    const { container } = render(
      <VocabularyDetailPanel item={item} onGoToSource={onGoToSource} />,
    );

    expect(locateSourceRefButton(container)).toBeNull();
    expect(onGoToSource).not.toHaveBeenCalled();
  });

  it("calls onGoToSource with readingRecordId only when no legacy id is present", () => {
    const onGoToSource = vi.fn();
    const item = makeItem([
      makeSourceRef({
        reading_record_id: "reading_record_1",
      }),
    ]);

    const { container } = render(
      <VocabularyDetailPanel item={item} onGoToSource={onGoToSource} />,
    );

    const button = locateSourceRefButton(container);
    expect(button).not.toBeNull();

    fireEvent.click(button!);

    expect(onGoToSource).toHaveBeenCalledTimes(1);
    expect(onGoToSource).toHaveBeenCalledWith({
      readingRecordId: "reading_record_1",
      sentenceId: undefined,
    });
  });

  it("forwards source_sentence_id as sentenceId when present", () => {
    const onGoToSource = vi.fn();
    const item = makeItem([
      makeSourceRef({
        reading_record_id: "reading_record_1",
        source_sentence_id: "sentence_42",
      }),
    ]);

    const { container } = render(
      <VocabularyDetailPanel item={item} onGoToSource={onGoToSource} />,
    );

    const button = locateSourceRefButton(container);
    expect(button).not.toBeNull();

    fireEvent.click(button!);

    expect(onGoToSource).toHaveBeenCalledWith({
      readingRecordId: "reading_record_1",
      sentenceId: "sentence_42",
    });
  });

  it("does NOT render the locate button and does NOT call onGoToSource when ref has neither reading_record_id nor cloud/client id", () => {
    const onGoToSource = vi.fn();
    const item = makeItem([
      makeSourceRef({
        // 仅含 source_sentence，无任何 reader id
        source_sentence_id: "orphan_sentence",
      }),
    ]);

    const { container } = render(
      <VocabularyDetailPanel item={item} onGoToSource={onGoToSource} />,
    );

    // 没有 Reading Record id 时不渲染按钮。
    expect(locateSourceRefButton(container)).toBeNull();
    expect(onGoToSource).not.toHaveBeenCalled();
  });
});
