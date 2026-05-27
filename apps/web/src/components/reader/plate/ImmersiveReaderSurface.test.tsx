/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReaderAssetProjection, ReaderJumpRangeSegment } from "@/lib/reader-plate";
import { renderSceneToPlateDocument } from "@/lib/reader-plate";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebReaderNoteVm } from "@/types/api/reader-notes";
import type { ReaderMockVm } from "@/types/view/ReaderMockVm";
import { ImmersiveReaderSurface } from "./ImmersiveReaderSurface";

function createScene(): ReaderMockVm {
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
    userFacingState: "normal",
    translations: [
      {
        sentenceId: "s1",
        translationZh: "制度记忆会塑造政策选择。",
      },
    ],
    inlineMarks: [
      {
        id: "mark-1",
        annotationType: "phrase_gloss",
        anchor: {
          kind: "text",
          sentenceId: "s1",
          anchorText: "memory",
          occurrence: 1,
        },
        renderType: "background",
        visualTone: "vocab",
        clickable: true,
      },
    ],
    sentenceEntries: [
      {
        id: "entry-grammar",
        sentenceId: "s1",
        entryType: "grammar_note",
        label: "语法旁注",
        title: "语法",
        content: "memory 在这里是名词性短语的一部分。",
      },
    ],
    warnings: [],
  };
}

function createHighlight(): WebAnnotationVm {
  return {
    id: "ann-1",
    recordId: "record-1",
    type: "highlight",
    anchorType: "text_range",
    targetKey: "record:record-1:range:s1:14:20:hash-1",
    paragraphId: "p1",
    sentenceId: "s1",
    selectedText: "memory",
    startOffset: 14,
    endOffset: 20,
    textHash: "hash-1",
    segments: [],
    color: "warm_yellow",
    createdAt: "2026-05-24T00:00:00Z",
    updatedAt: "2026-05-24T00:00:00Z",
  };
}

function createNote(): WebReaderNoteVm {
  return {
    id: "note-1",
    recordId: "record-1",
    anchorSentenceId: "s1",
    quoteMode: "sentence",
    targetKey: "record:record-1:sentence:s1",
    paragraphId: "p1",
    sentenceId: "s1",
    selectedText: "Institutional memory shapes policy choices.",
    startOffset: null,
    endOffset: null,
    textHash: null,
    segments: [],
    noteText: "Keep this line in mind.",
    createdAt: "2026-05-24T00:00:00Z",
    updatedAt: "2026-05-24T00:00:00Z",
  };
}

function createAssetProjection(annotation: WebAnnotationVm): ReaderAssetProjection {
  return {
    sentenceAssetProjectionBySentence: new Map([
      [
        "s1",
        {
          sentenceId: "s1",
          annotations: [annotation],
          annotationRanges: [],
          hasHighlight: true,
          primaryHighlightAnnotation: annotation,
        },
      ],
    ]),
    annotationRangesBySentence: new Map(),
    sentenceAssetSummaryBySentence: new Map([
      [
        "s1",
        {
          annotations: [annotation],
          hasHighlight: true,
        },
      ],
    ]),
  };
}

describe("ImmersiveReaderSurface", () => {
  it("hides translation and analysis while preserving lexical marks and note cue", () => {
    const scene = createScene();
    const note = createNote();
    const annotation = createHighlight();
    const onOpenSentenceNotes = vi.fn();

    const { container } = render(
      <ImmersiveReaderSurface
        document={renderSceneToPlateDocument(scene)}
        readingClassName="reader-serif text-ink"
        assetProjection={createAssetProjection(annotation)}
        readerNotesBySentence={new Map([["s1", [note]]])}
        onOpenSentenceNotes={onOpenSentenceNotes}
      />,
    );

    expect(screen.queryByText("制度记忆会塑造政策选择。")).toBeNull();
    expect(screen.queryByText("语法旁注")).toBeNull();
    expect(container.querySelector(".reader-mark")).toBeTruthy();
    expect(container.querySelector(".reader-immersive-paragraph-copy--lead")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("打开本段相关笔记"));
    expect(onOpenSentenceNotes).toHaveBeenCalledWith("s1", expect.any(HTMLButtonElement));
  });

  it("uses a paragraph highlight cue when no notes exist", () => {
    const scene = createScene();
    const annotation = createHighlight();
    const onAnnotationJump = vi.fn();

    render(
      <ImmersiveReaderSurface
        document={renderSceneToPlateDocument(scene)}
        readingClassName="reader-serif text-ink"
        assetProjection={createAssetProjection(annotation)}
        onAnnotationJump={onAnnotationJump}
      />,
    );

    fireEvent.click(screen.getByLabelText("查看本段相关高亮"));
    expect(onAnnotationJump).toHaveBeenCalledWith(annotation, expect.any(HTMLButtonElement), "s1");
  });

  it("renders retained Ask context with muted reader marks", () => {
    const scene = createScene();
    const annotation = createHighlight();
    const contextFocusRangesBySentence = new Map<string, ReaderJumpRangeSegment[]>([
      [
        "s1",
        [
          {
            paragraphId: "p1",
            sentenceId: "s1",
            selectedText: "memory",
            startOffset: 14,
            endOffset: 20,
            textHash: "hash-1",
          },
        ],
      ],
    ]);

    const { container } = render(
      <ImmersiveReaderSurface
        document={renderSceneToPlateDocument(scene)}
        readingClassName="reader-serif text-ink"
        assetProjection={createAssetProjection(annotation)}
        contextFocusRangesBySentence={contextFocusRangesBySentence}
      />,
    );

    expect(container.querySelector(".reader-context-focus-range")).toBeTruthy();
    expect(container.querySelector(".reader-mark--context-muted")).toBeTruthy();
  });
});
