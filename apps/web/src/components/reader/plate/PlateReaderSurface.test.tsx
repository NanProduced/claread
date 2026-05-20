/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReaderMockVm } from "@/types/view/ReaderMockVm";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import {
  hashAnchorText,
  projectReaderAssets,
  renderSceneToPlateDocument,
  type ReaderJumpTarget,
} from "../../../lib/reader-plate";
import { defaultReaderSettings } from "../settings";
import { PlateReaderSurface } from "./PlateReaderSurface";

afterEach(() => {
  document.body.innerHTML = "";
});

function createBaseScene(): ReaderMockVm {
  return {
    schemaVersion: "3.0.0",
    request: {
      requestId: "req-1",
      sourceType: "user_input",
      readingGoal: "daily_reading",
      readingVariant: "intermediate_reading",
      profileId: "upstream",
    },
    article: {
      paragraphs: [
        {
          paragraphId: "p1",
          sentenceIds: ["s1"],
        },
      ],
      sentences: [
        {
          sentenceId: "s1",
          paragraphId: "p1",
          text: "Institutional memory shapes policy choices.",
        },
      ],
    },
    userFacingState: "normal",
    translations: [],
    inlineMarks: [],
    sentenceEntries: [],
    warnings: [],
  };
}

function createAnnotation(overrides: Partial<WebAnnotationVm> = {}): WebAnnotationVm {
  return {
    id: overrides.id ?? "ann-1",
    recordId: overrides.recordId ?? "record-1",
    type: overrides.type ?? "highlight",
    anchorType: overrides.anchorType ?? "text_range",
    targetKey: overrides.targetKey ?? "record:record-1:range:s1:14:20:hash",
    paragraphId: overrides.paragraphId ?? "p1",
    sentenceId: overrides.sentenceId ?? "s1",
    selectedText: overrides.selectedText ?? "memory",
    startOffset: overrides.startOffset ?? 14,
    endOffset: overrides.endOffset ?? 20,
    textHash: overrides.textHash ?? hashAnchorText("memory"),
    segments: overrides.segments ?? [],
    color: overrides.color ?? "warm_yellow",
    note: overrides.note ?? null,
    createdAt: overrides.createdAt ?? "2026-05-19T00:00:00Z",
    updatedAt: overrides.updatedAt ?? "2026-05-19T00:00:00Z",
  };
}

function createFavorite(overrides: Partial<WebFavoriteTargetVm> = {}): WebFavoriteTargetVm {
  return {
    id: overrides.id ?? "fav-1",
    targetType: overrides.targetType ?? "text_range",
    targetKey: overrides.targetKey ?? "record:record-1:range:s1:28:43:hash",
    recordId: overrides.recordId ?? "record-1",
    anchorType: overrides.anchorType ?? "text_range",
    sentenceId: overrides.sentenceId ?? "s1",
    selectedText: overrides.selectedText ?? "policy choices.",
    startOffset: overrides.startOffset ?? 28,
    endOffset: overrides.endOffset ?? 43,
    textHash: overrides.textHash ?? hashAnchorText("policy choices."),
    segments: overrides.segments ?? [],
  };
}

describe("PlateReaderSurface", () => {
  it("renders standard reader content with translation and analysis blocks", () => {
    const document = renderSceneToPlateDocument({
      ...createBaseScene(),
      translations: [{ sentenceId: "s1", translationZh: "制度记忆会塑造政策选择。" }],
      inlineMarks: [
        {
          id: "mark-1",
          annotationType: "grammar_note",
          parentId: "entry-grammar",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "shapes",
            occurrence: 1,
          },
          renderType: "underline",
          visualTone: "grammar",
          clickable: false,
        },
      ],
      sentenceEntries: [
        {
          id: "entry-grammar",
          sentenceId: "s1",
          entryType: "grammar_note",
          label: "语法旁注",
          title: "语法",
          content: "shapes 在这里作谓语。",
        },
        {
          id: "entry-analysis",
          sentenceId: "s1",
          entryType: "sentence_analysis",
          label: "句子拆解",
          title: "句子拆解",
          content: "1) Institutional memory = 主语\n2) shapes policy choices = 谓语",
        },
      ],
    });

    const onSentenceActivate = vi.fn();
    const onAskTranslation = vi.fn();
    const onAskAnalysis = vi.fn();
    const { container, rerender } = render(
      <PlateReaderSurface
        document={document}
        showTranslation
        readingClassName="reader-serif text-ink"
        activeSentenceId="s1"
        jumpTarget={{
          targetType: "sentence",
          targetKey: "record:record-1:sentence:s1",
          sentenceIds: ["s1"],
          primarySentenceId: "s1",
          highlightMode: "sentence_frame",
          scrollStrategy: "center",
        }}
        onSentenceActivate={onSentenceActivate}
        onAskTranslation={onAskTranslation}
        onAskAnalysis={onAskAnalysis}
      />,
    );

    expect(screen.getByText("Institutional memory")).toBeTruthy();
    expect(screen.getByText("制度记忆会塑造政策选择。")).toBeTruthy();
    expect(screen.getByText("语法旁注")).toBeTruthy();
    expect(screen.getAllByText("句子拆解").length).toBeGreaterThan(0);

    const mark = container.querySelector("[data-reader-mark-id='mark-1']");
    expect(mark?.className).toContain("reader-mark--grammar");

    const sentence = container.querySelector("[data-sentence-id='s1']");
    const sentenceHandle = container.querySelector("[data-reader-sentence-handle='true']");
    expect(sentenceHandle).toBeTruthy();
    expect(sentence?.getAttribute("data-reader-anchor")).toBe("sentence");
    expect(sentence?.className).toContain("reader-route-focus-frame");
    expect(sentence?.className).toContain("bg-surface/42");
    expect(container.querySelector("[data-entry-id='entry-grammar']")?.getAttribute("data-entry-expanded")).toBe("false");

    if (!sentence || !sentenceHandle) {
      throw new Error("Expected sentence wrapper and handle");
    }
    fireEvent.click(sentence);
    expect(onSentenceActivate).not.toHaveBeenCalled();

    fireEvent.click(sentenceHandle);
    expect(onSentenceActivate).toHaveBeenCalledWith("s1", expect.any(HTMLElement));

    fireEvent.click(screen.getByLabelText("带这句译文进入 Ask"));
    expect(onAskTranslation).toHaveBeenCalledWith("s1", "制度记忆会塑造政策选择。");

    rerender(
      <PlateReaderSurface
        document={document}
        showTranslation
        readingClassName="reader-serif text-ink"
        activeSentenceId="s1"
        activeAnalysisEntryId="entry-grammar"
        jumpTarget={{
          targetType: "sentence",
          targetKey: "record:record-1:sentence:s1",
          sentenceIds: ["s1"],
          primarySentenceId: "s1",
          highlightMode: "sentence_frame",
          scrollStrategy: "center",
        }}
        onSentenceActivate={onSentenceActivate}
        onAskTranslation={onAskTranslation}
        onAskAnalysis={onAskAnalysis}
      />,
    );
    expect(mark?.className).toContain("reader-mark--entry-active");

    fireEvent.click(screen.getByLabelText("展开语法"));
    expect(container.querySelector("[data-entry-id='entry-grammar']")?.getAttribute("data-entry-expanded")).toBe("false");

    fireEvent.click(screen.getAllByLabelText("带解析进入 Ask")[0] as HTMLElement);
    expect(onAskAnalysis).toHaveBeenCalledWith("s1", "entry-grammar");
  });

  it("renders academic content summary and hides translation when disabled", () => {
    const document = renderSceneToPlateDocument({
      ...createBaseScene(),
      schemaVersion: "3.0.0-academic",
      request: {
        ...createBaseScene().request,
        readingGoal: "academic",
        readingVariant: "academic_general",
      },
      contentSummary: {
        completeness: "full",
        overview: "本文讨论制度记忆如何影响政策解释。",
        researchQuestion: "制度记忆如何收窄可接受解释范围？",
        methodology: "理论分析",
        keyFindings: ["制度记忆会约束解释空间"],
        limitations: ["缺少实证数据"],
      },
      translations: [{ sentenceId: "s1", translationZh: "制度记忆会塑造政策选择。" }],
      inlineMarks: [
        {
          id: "mark-term",
          annotationType: "term_note",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "Institutional memory",
            occurrence: 1,
          },
          renderType: "background",
          visualTone: "term",
          clickable: true,
        },
      ],
      sentenceEntries: [
        {
          id: "entry-term",
          sentenceId: "s1",
          entryType: "term_note",
          label: "术语说明",
          title: "Institutional memory",
          content: "指制度沉淀下来的组织记忆。",
        },
        {
          id: "entry-logic",
          sentenceId: "s1",
          entryType: "logic_note",
          label: "逻辑提示",
          title: "因果关系",
          content: "前半句给出原因，后半句给出结果。",
        },
        {
          id: "entry-interpretation",
          sentenceId: "s1",
          entryType: "interpretation_note",
          label: "理解提示",
          title: "核心观点",
          content: "作者强调解释框架的收窄效应。",
        },
      ],
    });

    const onAskContentSummary = vi.fn();
    const { container } = render(
      <PlateReaderSurface
        document={document}
        showTranslation={false}
        readingClassName="reader-serif text-ink"
        annotationVisibilityGroups={defaultReaderSettings.annotationVisibilityGroups}
        onAskContentSummary={onAskContentSummary}
      />,
    );

    expect(screen.getByText("Academic Summary")).toBeTruthy();
    expect(screen.getByText("本文讨论制度记忆如何影响政策解释。")).toBeTruthy();
    expect(screen.getByText("术语说明")).toBeTruthy();
    expect(screen.getByText("逻辑提示")).toBeTruthy();
    expect(screen.getByText("理解提示")).toBeTruthy();

    const translation = container.querySelector("[data-reader-node='translation']");
    expect(translation?.className).toContain("hidden");

    const mark = container.querySelector("[data-reader-mark-id='mark-term']");
    expect(mark?.className).toContain("reader-mark--term");
    expect(mark?.className).toContain("reader-mark--quiet");

    fireEvent.click(screen.getByLabelText("展开内容概要"));
    expect(screen.getByText("本文讨论制度记忆如何影响政策解释。")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("带内容概要进入 Ask"));
    expect(onAskContentSummary).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "reader_content_summary",
      }),
    );
  });

  it("renders precise range focus for text_range and multi_text jump targets", () => {
    const document = renderSceneToPlateDocument({
      ...createBaseScene(),
      article: {
        paragraphs: [
          {
            paragraphId: "p1",
            sentenceIds: ["s1", "s2"],
          },
        ],
        sentences: [
          {
            sentenceId: "s1",
            paragraphId: "p1",
            text: "Institutional memory shapes policy choices.",
          },
          {
            sentenceId: "s2",
            paragraphId: "p1",
            text: "These choices persist across administrations.",
          },
        ],
      },
    });

    const textRangeTarget: ReaderJumpTarget = {
      targetType: "text_range",
      targetKey: "record:record-1:range:s1:14:20:hash",
      sentenceIds: ["s1"],
      rangeSegments: [
        {
          sentenceId: "s1",
          startOffset: 14,
          endOffset: 20,
        },
      ],
      primarySentenceId: "s1",
      highlightMode: "range_segments",
      scrollStrategy: "center",
    };

    const { container, rerender } = render(
      <PlateReaderSurface
        document={document}
        showTranslation
        readingClassName="reader-serif text-ink"
        jumpTarget={textRangeTarget}
      />,
    );

    expect(container.querySelectorAll(".reader-route-focus-range").length).toBeGreaterThan(0);
    expect(container.querySelector("[data-sentence-id='s1']")?.className).toContain("reader-route-focus-frame");

    rerender(
      <PlateReaderSurface
        document={document}
        showTranslation
        readingClassName="reader-serif text-ink"
        jumpTarget={{
          targetType: "multi_text",
          targetKey: "record:record-1:multi_text:2:hash",
          sentenceIds: ["s1", "s2"],
          rangeSegments: [
            {
              sentenceId: "s1",
              startOffset: 28,
              endOffset: 43,
            },
            {
              sentenceId: "s2",
              startOffset: 0,
              endOffset: 13,
            },
          ],
          primarySentenceId: "s1",
          highlightMode: "range_segments",
          scrollStrategy: "center",
        }}
      />,
    );

    expect(container.querySelector("[data-sentence-id='s1']")?.className).toContain("reader-route-focus-frame");
    expect(container.querySelector("[data-sentence-id='s2']")?.className).toContain("reader-route-focus-frame");
    expect(container.querySelectorAll(".reader-route-focus-range").length).toBeGreaterThan(1);
  });

  it("renders asset overlays and routes annotation or favorite clicks through callbacks", () => {
    const document = renderSceneToPlateDocument(createBaseScene());
    const annotation = createAnnotation({
      note: "制度记忆是关键词。",
    });
    const favorite = createFavorite();
    const assetProjection = projectReaderAssets({
      annotations: [annotation],
      favoriteTargets: [favorite],
      recordId: "record-1",
    });
    const onAnnotationJump = vi.fn();
    const onFavoriteJump = vi.fn();

    const { container } = render(
      <PlateReaderSurface
        document={document}
        showTranslation
        readingClassName="reader-serif text-ink"
        assetProjection={assetProjection}
        onAnnotationJump={onAnnotationJump}
        onFavoriteJump={onFavoriteJump}
      />,
    );

    expect(container.querySelectorAll(".reader-user-range").length).toBeGreaterThan(0);
    expect(screen.getByText("制度记忆是关键词。")).toBeTruthy();

    const noteButton = container.querySelector(".reader-annotation-gutter-marker--note");
    const favoriteButton = container.querySelector(".reader-annotation-gutter-marker--favorite");

    if (!noteButton || !favoriteButton) {
      throw new Error("Expected asset gutter markers");
    }

    fireEvent.click(noteButton);
    fireEvent.click(favoriteButton);
    fireEvent.click(screen.getByText("制度记忆是关键词。"));

    expect(onAnnotationJump).toHaveBeenCalledWith(annotation);
    expect(onFavoriteJump).toHaveBeenCalledWith(favorite);
  });

  it("applies column width and annotation visibility groups to the surface", () => {
    const document = renderSceneToPlateDocument({
      ...createBaseScene(),
      inlineMarks: [
        {
          id: "mark-vocab",
          annotationType: "vocab_highlight",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "memory",
            occurrence: 1,
          },
          renderType: "underline",
          visualTone: "vocab",
          clickable: true,
        },
      ],
      sentenceEntries: [
        {
          id: "entry-logic",
          sentenceId: "s1",
          entryType: "logic_note",
          label: "逻辑提示",
          title: "逻辑提示",
          content: "前半句设定背景，后半句给出结果。",
        },
      ],
    });
    const assetProjection = projectReaderAssets({
      annotations: [createAnnotation()],
      favoriteTargets: [createFavorite()],
      recordId: "record-1",
    });
    const onLookupIntent = vi.fn();

    const { container } = render(
      <PlateReaderSurface
        document={document}
        showTranslation
        readingClassName="reader-serif text-ink"
        columnWidth="wide"
        annotationVisibilityGroups={{
          lexical: false,
          analysis: false,
          userAssets: false,
        }}
        assetProjection={assetProjection}
        onLookupIntent={onLookupIntent}
      />,
    );

    expect(container.querySelector("div[class*='max-w-[108ch]']")).toBeTruthy();
    expect(container.querySelector("[data-reader-mark-id='mark-vocab']")?.className ?? "").not.toContain("reader-mark--vocab");
    expect(container.querySelector("[data-entry-type='logic_note']")?.className).toContain("hidden");
    expect(container.querySelector(".reader-user-range")).toBeNull();
    expect(container.querySelector(".reader-annotation-gutter-marker--note")).toBeNull();
    expect(container.querySelector(".reader-annotation-gutter-marker--favorite")).toBeNull();

    const vocabMark = container.querySelector("[data-reader-mark-id='mark-vocab']");
    if (!vocabMark) {
      throw new Error("Expected vocab mark");
    }

    fireEvent.click(vocabMark);
    expect(onLookupIntent).not.toHaveBeenCalled();
  });

  it("emits lookup intents for word marks and inspect intents for structured marks", () => {
    const documentValue = renderSceneToPlateDocument({
      ...createBaseScene(),
      inlineMarks: [
        {
          id: "mark-word",
          annotationType: "vocab_highlight",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "memory",
            occurrence: 1,
          },
          renderType: "underline",
          visualTone: "vocab",
          clickable: true,
        },
        {
          id: "mark-phrase",
          annotationType: "phrase_gloss",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "policy choices",
            occurrence: 1,
          },
          renderType: "background",
          visualTone: "phrase",
          clickable: true,
          lookupKind: "phrase",
          lookupText: "policy choices",
          glossary: {
            zh: "政策选择",
            phraseType: "collocation",
          },
        },
      ],
    });
    const onLookupIntent = vi.fn();
    const onInspectIntent = vi.fn();

    const { container } = render(
      <PlateReaderSurface
        document={documentValue}
        showTranslation
        readingClassName="reader-serif text-ink"
        onLookupIntent={onLookupIntent}
        onInspectIntent={onInspectIntent}
      />,
    );

    fireEvent.click(container.querySelector("[data-reader-mark-id='mark-word']") as Element);
    expect(onLookupIntent).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "lexical_lookup",
        query: "memory",
        annotationType: "vocab_highlight",
      }),
      expect.any(Object),
    );

    fireEvent.click(container.querySelector("[data-reader-mark-id='mark-phrase']") as Element);
    expect(onInspectIntent).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "structured_annotation_inspect",
        markId: "mark-phrase",
        anchorText: "policy choices",
      }),
      expect.any(Object),
    );
  });

  it("keeps analysis cards collapsed by default and expands explicit entries only", () => {
    const document = renderSceneToPlateDocument({
      ...createBaseScene(),
      sentenceEntries: [
        {
          id: "entry-grammar",
          sentenceId: "s1",
          entryType: "grammar_note",
          label: "语法旁注",
          title: "语法",
          content: "这是一条较长的语法说明，用于测试折叠摘要。",
        },
      ],
    });

    const onToggle = vi.fn();

    const { container } = render(
      <PlateReaderSurface
        document={document}
        showTranslation
        readingClassName="reader-serif text-ink"
        onAnalysisToggle={onToggle}
      />,
    );

    const card = container.querySelector("[data-entry-id='entry-grammar']");
    expect(card?.getAttribute("data-entry-expanded")).toBe("false");

    fireEvent.click(screen.getByLabelText("展开语法"));
    expect(onToggle).toHaveBeenCalledWith("entry-grammar");
  });
});
