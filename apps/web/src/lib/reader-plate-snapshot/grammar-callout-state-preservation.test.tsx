/**
 * T4.2a-PUX-R4-R2.1C — Grammar Callout State Preservation Across Targeted Replace.
 *
 * Tests that the expanded/collapsed state of standalone grammar callouts
 * is preserved across `editor.tf.replaceNodes` on the SAME callout (target
 * replace), by lifting the state into a `ReaderGrammarExpansionProvider`
 * keyed by stable grammar itemId.
 *
 * Red-green TDD: these tests are written FIRST (red), then the production
 * implementation makes them pass (green).
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
  READER_CALLOUT_TYPE,
  type ReaderCalloutElement,
  projectReaderRecordPlateToPlateValue,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import type {
  ReaderRecordPlateCalloutBlock,
  ReaderRecordPlateDocument,
  ReaderRecordPlateParagraphBlock,
  ReaderRecordPlateTextLeaf,
  ReaderRecordPlateTranslationTextLeaf,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

// ---------------------------------------------------------------------------
// Fixture builders (same shape as plate-targeted-slate-ops-prod-kit.test.tsx)
// ---------------------------------------------------------------------------

const SOURCE_TEXT = "Institutional memory shapes policy choices.";
const TRANSLATION_TEXT = "制度记忆会塑造政策选择。";

function makeTextLeaf(
  overrides: Partial<ReaderRecordPlateTextLeaf> = {},
): ReaderRecordPlateTextLeaf {
  return {
    text: SOURCE_TEXT,
    owner: "stable",
    lockSource: true,
    sourceRole: "segment_text",
    baseRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
    anchorSegmentId: "seg_1",
    segmentRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
    marks: [],
    ...overrides,
  };
}

function makeTranslationLeaf(
  overrides: Partial<ReaderRecordPlateTranslationTextLeaf> = {},
): ReaderRecordPlateTranslationTextLeaf {
  return {
    text: TRANSLATION_TEXT,
    owner: "system_ai",
    sourceRole: "unit_translation_text",
    ...overrides,
  };
}

function makeParagraphBlock(
  overrides: Partial<ReaderRecordPlateParagraphBlock> = {},
): ReaderRecordPlateParagraphBlock {
  return {
    type: "paragraph",
    id: "paragraph:seg_1",
    children: [makeTextLeaf()],
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

function makeBlockquoteBlock(
  overrides: Partial<ReaderRecordPlateTranslationTextLeaf> = {},
): ReaderRecordPlateTranslationTextLeaf & {
  type: "blockquote";
  id: string;
  children: ReaderRecordPlateTranslationTextLeaf[];
  data: Record<string, unknown>;
} {
  return {
    type: "blockquote",
    id: "blockquote:layer_translation_1:group_translation_1",
    children: [makeTranslationLeaf()],
    data: {
      unitId: "unit_1",
      layerId: "layer_translation_1",
      layerVersion: 1,
      groupId: "group_translation_1",
      coveredAnchorSegmentIds: ["seg_1"],
      sourceTextHash: "unit_hash_1",
    },
    ...overrides,
  } as never;
}

function makeCalloutBlock(
  overrides: Partial<ReaderRecordPlateCalloutBlock> = {},
): ReaderRecordPlateCalloutBlock {
  return {
    type: "callout",
    id: "callout:grammar:grammar_item_1",
    variant: "grammar",
    icon: "📖",
    children: [{ type: "p", children: [{ text: "shapes acts as the predicate verb." }] }],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_grammar_1",
      itemId: "grammar_item_1",
      grammarPoint: "predicate verb",
      pattern: "subject + verb + object",
      note: "shapes acts as the predicate verb.",
    },
    ...overrides,
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
      title: "State preservation test",
      generation: 1,
      productState: "readable_enhancing",
      readinessState: "article_ready",
    },
    snapshot: {
      snapshotId: "snapshot_1",
      snapshotTakenAt: "2026-07-14T00:00:00Z",
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

function makeProjectedPlateValue(): Descendant[] {
  const doc = makeDocument([
    makeParagraphBlock(),
    makeCalloutBlock(),
    makeBlockquoteBlock() as never,
  ]);
  return projectReaderRecordPlateToPlateValue(doc);
}

// P2 fixture: grammar callout WITHOUT data.itemId — simulates legacy/edge
// data where itemId is missing. The callout must fall back to localExpanded.
function makeCalloutBlockWithoutItemId(): ReaderRecordPlateCalloutBlock {
  return {
    type: "callout",
    id: "callout:grammar:no_item_id",
    variant: "grammar",
    icon: "📖",
    children: [{ type: "p", children: [{ text: "Callout without itemId." }] }],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_grammar_1",
      // itemId intentionally missing
      grammarPoint: "predicate verb",
      pattern: "subject + verb + object",
      note: "Callout without itemId.",
    },
  } as ReaderRecordPlateCalloutBlock;
}

function makeProjectedPlateValueWithCalloutNoItemId(): Descendant[] {
  const doc = makeDocument([
    makeParagraphBlock(),
    makeCalloutBlockWithoutItemId(),
    makeBlockquoteBlock() as never,
  ]);
  return projectReaderRecordPlateToPlateValue(doc);
}

function makeReplacementCallout(): ReaderCalloutElement {
  return {
    type: READER_CALLOUT_TYPE,
    id: "callout:grammar:grammar_item_1",
    children: [{ type: "p", children: [{ text: "UPDATED: shapes acts as the main predicate." }] }] as Descendant[],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_grammar_1",
      itemId: "grammar_item_1",
      grammarPoint: "predicate verb (updated)",
      pattern: "subject + verb + object",
      note: "UPDATED: shapes acts as the main predicate.",
    },
    variant: "grammar",
    icon: "📖",
  };
}

// ---------------------------------------------------------------------------
// Mounted Plate harness WITH ReaderGrammarExpansionProvider
// ---------------------------------------------------------------------------

interface HarnessProps {
  initialValue: Descendant[];
  onEditorReady: (editor: NonNullable<ReturnType<typeof usePlateEditor>>) => void;
  controlRef?: ReaderGrammarExpansionControlRef;
}

function GrammarExpansionHarness({ initialValue, onEditorReady, controlRef }: HarnessProps) {
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
  const onEditorReady = (editor: NonNullable<ReturnType<typeof usePlateEditor>>) => {
    capturedEditor = editor;
  };

  const result = render(
    <GrammarExpansionHarness
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Grammar callout state preservation", () => {
  beforeEach(() => {
    if (!Range.prototype.getBoundingClientRect) {
      Range.prototype.getBoundingClientRect = vi.fn(() => ({
        x: 0, y: 0, top: 0, left: 0, bottom: 20, right: 100,
        width: 100, height: 20,
        toJSON() { return { x: 0, y: 0, top: 0, left: 0, bottom: 20, right: 100, width: 100, height: 20 }; },
      })) as unknown as Range["getBoundingClientRect"];
    }
    if (!HTMLElement.prototype.scrollIntoView) {
      HTMLElement.prototype.scrollIntoView = vi.fn();
    }
    if (!HTMLElement.prototype.scrollTo) {
      HTMLElement.prototype.scrollTo = vi.fn();
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

  // -------------------------------------------------------------------------
  // 1. Target callout replace preserves expanded state
  // -------------------------------------------------------------------------

  describe("1. Target callout replace preserves expanded state", () => {
    it("standalone grammar callout expanded state survives replaceNodes on same itemId", async () => {
      const { editor, container } = await renderHarness(makeProjectedPlateValue());

      // Path layout: [0]=paragraph, [1]=callout, [2]=blockquote
      const callout = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      expect(callout).not.toBeNull();

      // Initially collapsed (grammar default).
      expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("true");

      // Expand the callout.
      const toggle = callout!.querySelector<HTMLButtonElement>(
        '[data-reader-record-callout-toggle="grammar"]',
      );
      expect(toggle).not.toBeNull();

      await act(async () => {
        fireEvent.click(toggle!);
      });

      await waitFor(() => {
        expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("false");
      });

      // Targeted replace on the callout at path [1].
      await act(async () => {
        editor.tf.replaceNodes(makeReplacementCallout() as never, { at: [1] } as never);
      });

      // The callout content should be updated.
      const calloutAfter = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      expect(calloutAfter).not.toBeNull();
      expect(calloutAfter!.textContent).toContain("UPDATED: shapes acts as the main predicate.");

      // CRITICAL: The expanded state should be PRESERVED.
      expect(calloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("false");
    });
  });

  // -------------------------------------------------------------------------
  // 2. Sibling replace doesn't affect expanded callout
  // -------------------------------------------------------------------------

  describe("2. Sibling replace doesn't affect expanded callout", () => {
    it("replacing blockquote sibling preserves callout expanded state", async () => {
      const { editor, container } = await renderHarness(makeProjectedPlateValue());

      const callout = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      const toggle = callout!.querySelector<HTMLButtonElement>(
        '[data-reader-record-callout-toggle="grammar"]',
      );

      await act(async () => {
        fireEvent.click(toggle!);
      });

      await waitFor(() => {
        expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("false");
      });

      // Replace sibling blockquote at path [2].
      await act(async () => {
        editor.tf.replaceNodes(
          [{ type: "blockquote", id: "blockquote:layer_translation_1:group_translation_1", children: [{ text: "UPDATED: 制度记忆驱动政策选择。" }] as never }] as never,
          { at: [2] } as never,
        );
      });

      // Callout should still be expanded.
      const calloutAfter = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      expect(calloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("false");
    });
  });

  // -------------------------------------------------------------------------
  // 3. Full reload (setValue) clears expansion state via controlRef.clear()
  // -------------------------------------------------------------------------

  describe("3. Full reload clears expansion state", () => {
    it("controlRef.clear() clears expanded state so remounted callout defaults to collapsed", async () => {
      const controlRef: ReaderGrammarExpansionControlRef = { current: null };
      const { editor, container } = await renderHarness(
        makeProjectedPlateValue(),
        controlRef,
      );

      const callout = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      const toggle = callout!.querySelector<HTMLButtonElement>(
        '[data-reader-record-callout-toggle="grammar"]',
      );

      await act(async () => {
        fireEvent.click(toggle!);
      });

      await waitFor(() => {
        expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("false");
      });

      // Simulate full reload: request cleanup, then setValue.
      await act(async () => {
        controlRef.current?.clear();
        editor.tf.setValue(makeProjectedPlateValue() as never[]);
      });

      // After full reload, callout should be collapsed (state was cleared).
      const calloutAfter = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      expect(calloutAfter).not.toBeNull();
      expect(calloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("true");
    });
  });

  // -------------------------------------------------------------------------
  // 4. itemId disappears (callout removed) — forgetItem cleans state,
  //    reinsert of same itemId defaults to collapsed
  // -------------------------------------------------------------------------

  describe("4. itemId disappears — forgetItem cleans state", () => {
    it("forgetItem + removeNodes: reinsert of same itemId defaults to collapsed", async () => {
      const controlRef: ReaderGrammarExpansionControlRef = { current: null };
      const { editor, container } = await renderHarness(
        makeProjectedPlateValue(),
        controlRef,
      );

      const callout = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      const toggle = callout!.querySelector<HTMLButtonElement>(
        '[data-reader-record-callout-toggle="grammar"]',
      );

      // Expand the callout.
      await act(async () => {
        fireEvent.click(toggle!);
      });
      await waitFor(() => {
        expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("false");
      });

      // Simulate the Surface's targeted remove path: forgetItem THEN
      // removeNodes. This is what ReaderRecordPlateSurface does when
      // op.blockId matches callout:grammar:{itemId}.
      await act(async () => {
        controlRef.current?.forgetItem("grammar_item_1");
        editor.tf.removeNodes({ at: [1] } as never);
      });

      // Callout should be gone from DOM.
      expect(
        container.querySelector(
          '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
        ),
      ).toBeNull();

      // Reinsert a callout with the SAME itemId at [1].
      // Use projected Plate element format (not raw document block).
      await act(async () => {
        editor.tf.insertNodes(
          makeReplacementCallout() as never,
          { at: [1] } as never,
        );
      });

      // CRITICAL: the reinserted callout should be COLLAPSED — the
      // forgetItem call cleaned the stale expanded state, so the
      // same itemId reappearing does NOT inherit the old expansion.
      const calloutAfter = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      expect(calloutAfter).not.toBeNull();
      expect(calloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("true");
    });

    it("removeNodes without forgetItem retains stale expanded state (contrast test)", async () => {
      const { editor, container } = await renderHarness(makeProjectedPlateValue());

      const callout = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      const toggle = callout!.querySelector<HTMLButtonElement>(
        '[data-reader-record-callout-toggle="grammar"]',
      );

      await act(async () => {
        fireEvent.click(toggle!);
      });
      await waitFor(() => {
        expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("false");
      });

      // Remove WITHOUT forgetItem — stale state persists in context.
      await act(async () => {
        editor.tf.removeNodes({ at: [1] } as never);
      });

      // Reinsert same itemId (projected Plate element format).
      await act(async () => {
        editor.tf.insertNodes(
          makeReplacementCallout() as never,
          { at: [1] } as never,
        );
      });

      // Without forgetItem, the stale expanded state is inherited.
      // This test documents WHY forgetItem is necessary on the remove path.
      const calloutAfter = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      expect(calloutAfter).not.toBeNull();
      expect(calloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("false");
    });
  });

  // -------------------------------------------------------------------------
  // 5. Re-expand after target replace (state is keyed by itemId, not instance)
  // -------------------------------------------------------------------------

  describe("5. Re-expand after target replace", () => {
    it("callout can be expanded again after target replace (itemId-based state)", async () => {
      const { editor, container } = await renderHarness(makeProjectedPlateValue());

      const callout = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      const toggle = callout!.querySelector<HTMLButtonElement>(
        '[data-reader-record-callout-toggle="grammar"]',
      );

      // Expand.
      await act(async () => {
        fireEvent.click(toggle!);
      });
      await waitFor(() => {
        expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("false");
      });

      // Target replace.
      await act(async () => {
        editor.tf.replaceNodes(makeReplacementCallout() as never, { at: [1] } as never);
      });

      // Still expanded.
      const calloutAfter = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      expect(calloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("false");

      // Collapse.
      const toggleAfter = calloutAfter!.querySelector<HTMLButtonElement>(
        '[data-reader-record-callout-toggle="grammar"]',
      );
      await act(async () => {
        fireEvent.click(toggleAfter!);
      });
      await waitFor(() => {
        expect(calloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("true");
      });

      // Re-expand — should work (state is itemId-based, not instance-based).
      await act(async () => {
        fireEvent.click(toggleAfter!);
      });
      await waitFor(() => {
        expect(calloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("false");
      });
    });
  });

  // -------------------------------------------------------------------------
  // 6. P2: grammar callout WITHOUT itemId falls back to localExpanded
  // -------------------------------------------------------------------------

  describe("6. grammar callout without itemId uses localExpanded", () => {
    it("can be expanded and collapsed via local state when itemId is missing", async () => {
      const { container } = await renderHarness(
        makeProjectedPlateValueWithCalloutNoItemId(),
      );

      // Find the grammar callout that does NOT have data-reader-record-grammar-item-id.
      const callout = container.querySelector<HTMLElement>(
        '[data-callout-variant="grammar"]:not([data-reader-record-grammar-item-id])',
      );
      expect(callout).not.toBeNull();

      // Initially collapsed (grammar default: localExpanded = !isGrammar = false).
      expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("true");

      const toggle = callout!.querySelector<HTMLButtonElement>(
        '[data-reader-record-callout-toggle="grammar"]',
      );

      // Expand via localExpanded (NOT via itemId-keyed context).
      await act(async () => {
        fireEvent.click(toggle!);
      });
      await waitFor(() => {
        expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("false");
      });

      // Collapse.
      await act(async () => {
        fireEvent.click(toggle!);
      });
      await waitFor(() => {
        expect(callout!.dataset.readerRecordCalloutCollapsed).toBe("true");
      });
    });
  });
});
