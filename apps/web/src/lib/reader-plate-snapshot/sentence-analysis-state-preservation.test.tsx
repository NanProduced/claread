/**
 * Sentence-analysis card state preservation across targeted replace.
 *
 * Sentence-analysis expansion state lives in the shared keyed expansion
 * context (no second expansion context), keyed by `sentence_analysis:{analysisId}`.
 * These tests prove the keyed state survives editor.tf.replaceNodes remounts,
 * that remove + forget resets to collapsed, and that cards without an
 * analysisId keep their local-state fallback.
 */
/** @vitest-environment jsdom */

import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect, useRef } from "react";
import type { Descendant } from "platejs";

import { Editor, EditorContainer } from "@/components/ui/editor";
import {
  Plate,
  usePlateEditor,
} from "platejs/react";
import { ReaderRecordPlateKit } from "@/components/editor/plugins/reader-plate-kit";
import {
  ReaderGrammarExpansionProvider,
  type ReaderGrammarExpansionControlRef,
} from "@/components/editor/plugins/reader-blocks-kit";
import {
  READER_SENTENCE_ANALYSIS_TYPE,
  type ReaderSentenceAnalysisElement,
  projectReaderRecordPlateToPlateValue,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import type {
  ReaderRecordPlateDocument,
  ReaderRecordPlateParagraphBlock,
  ReaderRecordPlateSentenceAnalysisBlock,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

const SOURCE_TEXT = "Institutional memory shapes policy choices.";

function makeParagraphBlock(
  overrides: Partial<ReaderRecordPlateParagraphBlock> = {},
): ReaderRecordPlateParagraphBlock {
  return {
    type: "paragraph",
    id: "paragraph:seg_1",
    children: [
      {
        text: SOURCE_TEXT,
        owner: "stable",
        lockSource: true,
        sourceRole: "segment_text",
        baseRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
        anchorSegmentId: "seg_1",
        segmentRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
        marks: [],
      },
    ],
    data: {
      anchorSegmentId: "seg_1",
      coveredAnchorSegmentIds: ["seg_1"],
      sentenceId: "sent_1",
      unitId: "unit_1",
      baseId: "base_1",
      baseRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
      unitRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
      textHash: "seg_1_hash",
      hashAlgorithm: "fnv1a32-utf16",
      segmentType: "sentence",
      boundaryQuality: "normal",
    },
    ...overrides,
  };
}

function makeSentenceAnalysisBlock(
  overrides: Partial<ReaderRecordPlateSentenceAnalysisBlock> = {},
): ReaderRecordPlateSentenceAnalysisBlock {
  return {
    type: "sentence_analysis",
    id: "sentence_analysis:analysis_1",
    icon: "🔍",
    children: [
      { type: "p", children: [{ text: "主语驱动谓语的结构说明。" }] },
    ],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_sentence_analysis_1",
      analysisId: "analysis_1",
      label: "subject driving predicate",
      analysis: "主语驱动谓语的结构说明。",
      chunks: [],
    },
    ...overrides,
  };
}

function makeSentenceAnalysisBlockWithoutAnalysisId(): ReaderRecordPlateSentenceAnalysisBlock {
  const block = makeSentenceAnalysisBlock({
    id: "sentence_analysis:no_analysis_id",
  });
  return {
    ...block,
    data: {
      ...block.data,
      analysisId: undefined as unknown as string,
    },
  };
}

function makeDocument(
  children: ReaderRecordPlateDocument["children"],
): ReaderRecordPlateDocument {
  return {
    type: "reader_record_plate_document",
    schemaVersion: "reader-record-plate-document/v1",
    record: {
      recordId: "record_1",
      title: "Sentence analysis state preservation",
      generation: 1,
      productState: "readable_enhancing",
      readinessState: "article_ready",
    },
    snapshot: {
      snapshotId: "snapshot_1",
      snapshotTakenAt: "2026-08-08T00:00:00Z",
      lastEventSequence: 1,
    },
    base: {
      baseId: "base_1",
      contentSha256: "sha256_1",
      textLengthUtf16: SOURCE_TEXT.length,
      hashAlgorithm: "fnv1a32-utf16",
    },
    progress: {
      overallStatus: "ready",
      layers: [],
    },
    children,
  };
}

function makeProjectedPlateValue(
  analysisBlock: ReaderRecordPlateSentenceAnalysisBlock = makeSentenceAnalysisBlock(),
): Descendant[] {
  const doc = makeDocument([makeParagraphBlock(), analysisBlock]);
  return projectReaderRecordPlateToPlateValue(doc);
}

function makeReplacementAnalysisElement(): ReaderSentenceAnalysisElement {
  return {
    type: READER_SENTENCE_ANALYSIS_TYPE,
    id: "sentence_analysis:analysis_1",
    icon: "🔍",
    children: [
      { type: "p", children: [{ text: "UPDATED: 更新后的拆析说明。" }] },
    ] as Descendant[],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_sentence_analysis_1",
      analysisId: "analysis_1",
      label: "subject driving predicate (updated)",
      analysis: "UPDATED: 更新后的拆析说明。",
      chunks: [],
    },
  } as ReaderSentenceAnalysisElement;
}

// ---------------------------------------------------------------------------
// Mounted Plate harness WITH the shared expansion provider
// ---------------------------------------------------------------------------

interface HarnessProps {
  initialValue: Descendant[];
  onEditorReady: (editor: NonNullable<ReturnType<typeof usePlateEditor>>) => void;
  controlRef?: ReaderGrammarExpansionControlRef;
}

function SentenceAnalysisExpansionHarness({
  initialValue,
  onEditorReady,
  controlRef,
}: HarnessProps) {
  const editor = usePlateEditor(
    {
      plugins: [...ReaderRecordPlateKit],
      value: initialValue as never[],
    },
    [],
  );

  const readyRef = useRef(false);
  useEffect(() => {
    if (!readyRef.current && editor) {
      readyRef.current = true;
      onEditorReady(editor);
    }
  }, [editor, onEditorReady]);

  return (
    <ReaderGrammarExpansionProvider controlRef={controlRef}>
      <Plate editor={editor} readOnly>
        <EditorContainer className="h-auto overflow-visible bg-transparent px-0 py-0">
          <Editor
            readOnly
            disableDefaultStyles
            className="space-y-2 px-0 py-0 outline-none"
          />
        </EditorContainer>
      </Plate>
    </ReaderGrammarExpansionProvider>
  );
}

async function renderHarness(
  initialValue: Descendant[],
  controlRef?: ReaderGrammarExpansionControlRef,
) {
  let capturedEditor: ReturnType<typeof usePlateEditor> | null = null;
  const onEditorReady = (
    editor: NonNullable<ReturnType<typeof usePlateEditor>>,
  ) => {
    capturedEditor = editor;
  };

  const result = render(
    <SentenceAnalysisExpansionHarness
      initialValue={initialValue}
      onEditorReady={onEditorReady}
      controlRef={controlRef}
    />,
  );

  await waitFor(() => {
    expect(capturedEditor).not.toBeNull();
  });

  return {
    editor: capturedEditor!,
    container: result.container,
    unmount: result.unmount,
    rerender: result.rerender,
  };
}

function analysisCard(container: HTMLElement): HTMLElement | null {
  return container.querySelector<HTMLElement>(
    '[data-reader-record-sentence-analysis-block="true"]',
  );
}

function isExpanded(card: HTMLElement): boolean {
  return (
    card.getAttribute("data-reader-record-sentence-analysis-collapsed") ===
    "false"
  );
}

function toggle(card: HTMLElement): void {
  const button = card.querySelector<HTMLButtonElement>(
    '[data-reader-record-callout-toggle="sentence-analysis"]',
  );
  expect(button).not.toBeNull();
  fireEvent.click(button!);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Sentence analysis card state preservation", () => {
  beforeEach(() => {
    if (!HTMLElement.prototype.scrollIntoView) {
      HTMLElement.prototype.scrollIntoView = vi.fn();
    }
    vi.stubGlobal("ResizeObserver", class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("expanded state survives replaceNodes on the same analysisId", async () => {
    const { editor, container } = await renderHarness(makeProjectedPlateValue());

    const card = analysisCard(container);
    expect(card).not.toBeNull();
    expect(isExpanded(card!)).toBe(false);

    toggle(card!);
    await waitFor(() => {
      expect(isExpanded(analysisCard(container)!)).toBe(true);
    });

    act(() => {
      editor.tf.replaceNodes(makeReplacementAnalysisElement() as never, {
        at: [1],
      });
    });

    await waitFor(() => {
      const replaced = analysisCard(container);
      expect(replaced).not.toBeNull();
      expect(isExpanded(replaced!)).toBe(true);
      expect(replaced!.textContent).toContain("UPDATED");
    });
  });

  it("replacing a sibling block does not affect the expanded analysis card", async () => {
    const { editor, container } = await renderHarness(makeProjectedPlateValue());

    toggle(analysisCard(container)!);
    await waitFor(() => {
      expect(isExpanded(analysisCard(container)!)).toBe(true);
    });

    act(() => {
      editor.tf.replaceNodes(
        {
          ...makeReplacementAnalysisElement(),
          type: "reader_paragraph",
          id: "paragraph:seg_1",
        } as never,
        { at: [0] },
      );
    });

    await waitFor(() => {
      expect(isExpanded(analysisCard(container)!)).toBe(true);
    });
  });

  it("removeNodes + forgetItem: reinsert of the same analysisId defaults to collapsed", async () => {
    const controlRef: ReaderGrammarExpansionControlRef = { current: null };
    const { editor, container } = await renderHarness(
      makeProjectedPlateValue(),
      controlRef,
    );

    toggle(analysisCard(container)!);
    await waitFor(() => {
      expect(isExpanded(analysisCard(container)!)).toBe(true);
    });

    act(() => {
      // Mirrors the surface targeted-op path: forget before remove.
      controlRef.current?.forgetItem("sentence_analysis:analysis_1");
      editor.tf.removeNodes({ at: [1] });
    });
    await waitFor(() => {
      expect(analysisCard(container)).toBeNull();
    });

    act(() => {
      editor.tf.insertNodes(makeReplacementAnalysisElement() as never, {
        at: [1],
      });
    });
    await waitFor(() => {
      const reinserted = analysisCard(container);
      expect(reinserted).not.toBeNull();
      expect(isExpanded(reinserted!)).toBe(false);
    });
  });

  it("card can be expanded again after a targeted replace", async () => {
    const { editor, container } = await renderHarness(makeProjectedPlateValue());

    act(() => {
      editor.tf.replaceNodes(makeReplacementAnalysisElement() as never, {
        at: [1],
      });
    });
    await waitFor(() => {
      expect(isExpanded(analysisCard(container)!)).toBe(false);
    });

    toggle(analysisCard(container)!);
    await waitFor(() => {
      expect(isExpanded(analysisCard(container)!)).toBe(true);
    });
  });

  it("card without analysisId falls back to local expand/collapse", async () => {
    const { container } = await renderHarness(
      makeProjectedPlateValue(makeSentenceAnalysisBlockWithoutAnalysisId()),
    );

    const card = analysisCard(container);
    expect(card).not.toBeNull();
    expect(isExpanded(card!)).toBe(false);

    toggle(card!);
    await waitFor(() => {
      expect(isExpanded(analysisCard(container)!)).toBe(true);
    });

    toggle(analysisCard(container)!);
    await waitFor(() => {
      expect(isExpanded(analysisCard(container)!)).toBe(false);
    });
  });
});
