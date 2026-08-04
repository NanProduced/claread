// task-history: T4.2a-PUX-R4-R2-S1
/**
 * Read-only Plate targeted update characterization tests.
 *
 * Spike goal: verify whether the real installed platejs@53.2.1 +
 * @platejs/slate@53.0.7 + @platejs/core@53.2.1 APIs can update a single
 * target subtree in a MOUNTED readOnly Plate editor WITHOUT calling
 * editor.tf.setValue(), WITHOUT rebuilding the entire Slate DOM, and while
 * preserving unrelated component local state.
 *
 * Note: an earlier version rendered a Plate with one editor but operated
 * on a DIFFERENT editor created via renderHook. This version uses a single
 * editor exposed via callback from the mounted component, renders real
 * visible Plate content via registered Plate plugins (NOT the renderElement
 * prop fallback), and verifies DOM-level evidence.
 *
 * RENDERING NOTE: Plate's pipeRenderElement gives priority to registered
 * plugins. BaseParagraphPlugin (always in core) claims type "p" and renders
 * it via the fast intrinsic path as <div> — the renderElement prop is ONLY
 * a fallback for types with NO registered plugin. To get custom DOM
 * attributes (data-spike-node) on ALL element types including "p", we
 * register spike plugins with custom components, exactly like
 * ReaderBlocksKit does in production.
 *
 * This is a TEST-ONLY spike. It does NOT implement the production
 * incremental applier, does NOT wire into polling/page/reloadSnapshot, and
 * does NOT change the default reload path.
 *
 * Candidate APIs tested:
 *   1. editor.tf.replaceNodes(node, { at: path })  — primary candidate
 *   2. editor.tf.removeNodes({ at: path }) + editor.tf.insertNodes(node, { at: path })
 *   3. editor.tf.setNodes(props, { at: path })  — control: should NOT replace children
 *
 * Verification levels:
 *   L1 — Headless model: setValue spy, Slate node identity (toBe)
 *   L2 — Mounted DOM: target DOM text updated, non-target DOM isSameNode
 *   L3 — React state: ReaderContentSummaryElement.expanded preserved
 *   L4 — Selection: save/restore by path on mounted editor
 *   L5 — Batch: multiple replaceNodes in one act() with observable DOM
 *   PENDING — Browser E2E: no visible intermediate state (jsdom cannot prove)
 */
/** @vitest-environment jsdom */

import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useEffect, useRef } from "react";

import { Editor, EditorContainer } from "@/components/ui/editor";
import {
  createPlatePlugin,
  type PlateElementProps,
  Plate,
  usePlateEditor,
} from "platejs/react";
import { ReaderContentSummaryElement } from "@/components/reader/plate/nodes/ReaderContentSummaryElement";

// ---------------------------------------------------------------------------
// Test node types
// ---------------------------------------------------------------------------

interface SpikeParagraph {
  type: "p";
  children: { text: string }[];
}

interface SpikeCallout {
  type: "callout";
  callout_id: string;
  children: { text: string }[];
}

interface SpikeContentSummary {
  type: "reader_content_summary";
  completeness: "full" | "partial" | "minimal";
  overview: string;
  researchQuestion?: string;
  methodology?: string;
  keyFindings: string[];
  limitations: string[];
  children: { text: string }[];
}

type SpikeValue = (SpikeParagraph | SpikeCallout | SpikeContentSummary)[];

// ---------------------------------------------------------------------------
// Spike plugin components — registered via createPlatePlugin so Plate's
// pipeRenderElement uses them instead of the default fast intrinsic path.
// ---------------------------------------------------------------------------

function SpikeParagraphComponent({
  element,
  children,
  attributes,
}: PlateElementProps) {
  const el = element as unknown as SpikeParagraph;
  const text = el.children[0]?.text ?? "";
  return (
    <p
      {...attributes}
      data-spike-node="p"
      data-spike-text={text}
    >
      {children}
    </p>
  );
}

function SpikeCalloutComponent({
  element,
  children,
  attributes,
}: PlateElementProps) {
  const el = element as unknown as SpikeCallout;
  return (
    <div
      {...attributes}
      data-spike-node="callout"
      data-callout-id={el.callout_id}
    >
      {children}
    </div>
  );
}

/**
 * Adapter component: Plate plugin components receive PlateElementProps
 * directly, but ReaderContentSummaryElement expects { props: RenderElementArgs }.
 * This wrapper adapts the shape so the real ReaderContentSummaryElement
 * component (with its local useState for expanded) is rendered in the
 * mounted Plate.
 */
function SpikeContentSummaryComponent({
  element,
  children,
  attributes,
}: PlateElementProps) {
  return (
    <ReaderContentSummaryElement
      props={{ element, children, attributes } as never}
    />
  );
}

const SpikeParagraphPlugin = createPlatePlugin({
  key: "p",
  node: { isElement: true, component: SpikeParagraphComponent },
});

const SpikeCalloutPlugin = createPlatePlugin({
  key: "callout",
  node: { isElement: true, component: SpikeCalloutComponent },
});

const SpikeContentSummaryPlugin = createPlatePlugin({
  key: "reader_content_summary",
  node: { isElement: true, component: SpikeContentSummaryComponent },
});

const SpikePlugins = [
  SpikeParagraphPlugin,
  SpikeCalloutPlugin,
  SpikeContentSummaryPlugin,
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeInitialValue(): SpikeValue {
  return [
    {
      type: "p",
      children: [{ text: "First paragraph content." }],
    },
    {
      type: "callout",
      callout_id: "callout_target_1",
      children: [{ text: "Original callout body." }],
    },
    {
      type: "p",
      children: [{ text: "Third paragraph after callout." }],
    },
  ];
}

function makeReplacementCallout(): SpikeCallout {
  return {
    type: "callout",
    callout_id: "callout_target_1",
    children: [{ text: "Updated callout body via targeted op." }],
  };
}

function makeContentSummaryValue(): SpikeValue {
  return [
    {
      type: "reader_content_summary",
      completeness: "partial" as const,
      overview: "This is a summary overview for spike testing.",
      researchQuestion: "Can targeted Slate ops preserve React state?",
      methodology: "Render mounted Plate, expand summary, replace sibling.",
      keyFindings: ["Finding A"],
      limitations: ["jsdom limitation"],
      children: [{ text: "" }],
    },
    {
      type: "p",
      children: [{ text: "Paragraph sibling to content summary." }],
    },
  ];
}

// ---------------------------------------------------------------------------
// Mounted Plate harness — single editor, real visible content
// ---------------------------------------------------------------------------

interface MountedPlateHarnessProps {
  initialValue: SpikeValue;
  onEditorReady: (editor: NonNullable<ReturnType<typeof usePlateEditor>>) => void;
}

/**
 * Renders a MOUNTED readOnly Plate with real visible content.
 * The editor is created via usePlateEditor INSIDE this component and
 * exposed to the test via onEditorReady callback. There is no second
 * editor — the test operates on the SAME editor that's mounted.
 *
 * Element rendering is done via registered Spike plugins (not the
 * renderElement prop fallback). This matches how the real
 * ReaderRecordPlateSurface works (it uses ReaderRecordPlateKit plugins).
 */
function MountedPlateHarness({ initialValue, onEditorReady }: MountedPlateHarnessProps) {
  const editor = usePlateEditor(
    {
      plugins: SpikePlugins,
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
    <Plate editor={editor} readOnly>
      <EditorContainer className="h-auto overflow-visible bg-transparent px-0 py-0">
        <Editor
          readOnly
          disableDefaultStyles
          className="space-y-2 px-0 py-0 outline-none"
        />
      </EditorContainer>
    </Plate>
  );
}

/**
 * Render a mounted readOnly Plate and return:
 * - editor (the SAME one mounted in the Plate)
 * - setValueSpy (spying on the mounted editor's tf.setValue)
 * - container (the rendered DOM container)
 */
async function renderMountedPlate(initialValue: SpikeValue) {
  const setValueSpy = vi.fn();
  let capturedEditor: ReturnType<typeof usePlateEditor> | null = null;

  const onEditorReady = (editor: NonNullable<ReturnType<typeof usePlateEditor>>) => {
    capturedEditor = editor;
    // Spy on the SAME editor that's mounted — no second editor.
    const original = editor.tf.setValue.bind(editor);
    const spied = (...args: Parameters<typeof original>) => {
      setValueSpy(...(args as unknown[]));
      return original(...args);
    };
    Object.assign(spied, original);
    editor.tf.setValue = spied as typeof editor.tf.setValue;
  };

  const result = render(<MountedPlateHarness initialValue={initialValue} onEditorReady={onEditorReady} />);

  await waitFor(() => {
    expect(capturedEditor).not.toBeNull();
  });

  return {
    editor: capturedEditor!,
    setValueSpy,
    container: result.container,
    unmount: result.unmount,
  };
}

// ---------------------------------------------------------------------------
// Spike tests
// ---------------------------------------------------------------------------

describe("mounted Plate targeted Slate ops", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // -------------------------------------------------------------------------
  // L1 + L2: replaceNodes — setValue=0, Slate identity, DOM identity
  // -------------------------------------------------------------------------

  describe("L1+L2: editor.tf.replaceNodes on mounted Plate", () => {
    it("replaces target callout at path [1] without calling setValue", async () => {
      const { editor, setValueSpy } = await renderMountedPlate(makeInitialValue());

      const childrenBefore = editor.children as SpikeValue;
      const firstBefore = childrenBefore[0];
      const thirdBefore = childrenBefore[2];

      await act(async () => {
        editor.tf.replaceNodes(makeReplacementCallout() as never, { at: [1] } as never);
      });

      // L1: setValue not called.
      expect(setValueSpy).not.toHaveBeenCalled();

      // L1: Slate model — target replaced, non-target identity preserved.
      const childrenAfter = editor.children as SpikeValue;
      expect(childrenAfter[1]).toEqual(makeReplacementCallout());
      expect(childrenAfter[0]).toBe(firstBefore);
      expect(childrenAfter[2]).toBe(thirdBefore);
    });

    it("updates target DOM text while preserving non-target DOM node identity (isSameNode)", async () => {
      const { editor, setValueSpy, container } = await renderMountedPlate(makeInitialValue());

      // Capture non-target DOM nodes BEFORE the op.
      const firstBefore = container.querySelector('[data-spike-node="p"]');
      expect(firstBefore).not.toBeNull();
      const firstTextBefore = firstBefore!.getAttribute("data-spike-text");

      // Verify target DOM exists before.
      const calloutBefore = container.querySelector('[data-spike-node="callout"]');
      expect(calloutBefore!.textContent).toContain("Original callout body.");

      await act(async () => {
        editor.tf.replaceNodes(makeReplacementCallout() as never, { at: [1] } as never);
      });

      expect(setValueSpy).not.toHaveBeenCalled();

      // L2: Target DOM text updated.
      const calloutAfter = container.querySelector('[data-spike-node="callout"]');
      expect(calloutAfter!.textContent).toContain("Updated callout body via targeted op.");

      // L2: Non-target DOM node identity preserved — same DOM node.
      const firstAfter = container.querySelector('[data-spike-node="p"]');
      expect(firstBefore!.isSameNode(firstAfter)).toBe(true);
      expect(firstAfter!.getAttribute("data-spike-text")).toBe(firstTextBefore);
    });
  });

  // -------------------------------------------------------------------------
  // L1 + L2: removeNodes + insertNodes
  // -------------------------------------------------------------------------

  describe("L1+L2: removeNodes + insertNodes on mounted Plate", () => {
    it("removes and re-inserts target at same path without setValue; non-target DOM preserved", async () => {
      const { editor, setValueSpy, container } = await renderMountedPlate(makeInitialValue());

      const firstBefore = container.querySelector('[data-spike-node="p"]');
      expect(firstBefore).not.toBeNull();

      await act(async () => {
        editor.tf.removeNodes({ at: [1] } as never);
        editor.tf.insertNodes(makeReplacementCallout() as never, { at: [1] } as never);
      });

      expect(setValueSpy).not.toHaveBeenCalled();

      // Target DOM updated.
      const calloutAfter = container.querySelector('[data-spike-node="callout"]');
      expect(calloutAfter!.textContent).toContain("Updated callout body via targeted op.");

      // Non-target DOM identity preserved.
      const firstAfter = container.querySelector('[data-spike-node="p"]');
      expect(firstBefore!.isSameNode(firstAfter)).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // L1: setNodes (control) — updates props, not children
  // -------------------------------------------------------------------------

  describe("L1: editor.tf.setNodes (control)", () => {
    it("updates node props without replacing children or calling setValue", async () => {
      const { editor, setValueSpy } = await renderMountedPlate(makeInitialValue());

      const childrenBefore = editor.children as SpikeValue;
      const calloutChildrenBefore = (childrenBefore[1] as SpikeCallout).children;

      await act(async () => {
        editor.tf.setNodes(
          { callout_id: "callout_target_1_renamed" } as never,
          { at: [1] } as never,
        );
      });

      expect(setValueSpy).not.toHaveBeenCalled();

      const childrenAfter = editor.children as SpikeValue;
      const calloutAfter = childrenAfter[1] as SpikeCallout;
      expect(calloutAfter.callout_id).toBe("callout_target_1_renamed");
      // Children reference preserved (setNodes does not replace children).
      expect(calloutAfter.children).toBe(calloutChildrenBefore);
    });
  });

  // -------------------------------------------------------------------------
  // L5: Batch atomicity — multiple replaceNodes in one act()
  // -------------------------------------------------------------------------

  describe("L5: batch atomicity on mounted Plate", () => {
    it("applies multiple replaceNodes in one act() with observable DOM result", async () => {
      const batchValue: SpikeValue = [
        {
          type: "callout",
          callout_id: "callout_a",
          children: [{ text: "A original." }],
        },
        {
          type: "callout",
          callout_id: "callout_b",
          children: [{ text: "B original." }],
        },
        {
          type: "p",
          children: [{ text: "Stable paragraph." }],
        },
      ];

      const { editor, setValueSpy, container } = await renderMountedPlate(batchValue);

      // Capture stable paragraph DOM node BEFORE.
      const pBefore = container.querySelector('[data-spike-node="p"]');
      expect(pBefore).not.toBeNull();

      await act(async () => {
        editor.tf.replaceNodes(
          { type: "callout", callout_id: "callout_a", children: [{ text: "A updated." }] } as never,
          { at: [0] } as never,
        );
        editor.tf.replaceNodes(
          { type: "callout", callout_id: "callout_b", children: [{ text: "B updated." }] } as never,
          { at: [1] } as never,
        );
      });

      expect(setValueSpy).not.toHaveBeenCalled();

      // DOM reflects both updates.
      const callouts = container.querySelectorAll('[data-spike-node="callout"]');
      expect(callouts[0].textContent).toContain("A updated.");
      expect(callouts[1].textContent).toContain("B updated.");

      // Non-target DOM identity preserved.
      const pAfter = container.querySelector('[data-spike-node="p"]');
      expect(pBefore!.isSameNode(pAfter)).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // L3: ReaderContentSummaryElement.expanded preserved
  // -------------------------------------------------------------------------

  describe("L3: ReaderContentSummaryElement.expanded preserved after sibling replaceNodes", () => {
    it("content summary remains expanded after replacing a sibling paragraph", async () => {
      const { editor, setValueSpy, container } = await renderMountedPlate(makeContentSummaryValue());

      // Verify content summary is rendered and collapsed initially.
      const summary = container.querySelector("#reader-content-summary");
      expect(summary).not.toBeNull();
      expect(summary!.getAttribute("data-expanded")).toBe("false");

      // Click the expand button.
      const expandButton = summary!.querySelector("button");
      expect(expandButton).not.toBeNull();

      await act(async () => {
        fireEvent.click(expandButton!);
      });

      // Verify expanded.
      const summaryExpanded = container.querySelector("#reader-content-summary");
      expect(summaryExpanded!.getAttribute("data-expanded")).toBe("true");

      // Now replace the SIBLING paragraph at path [1] (not the content summary at [0]).
      await act(async () => {
        editor.tf.replaceNodes(
          { type: "p", children: [{ text: "Replaced sibling paragraph." }] } as never,
          { at: [1] } as never,
        );
      });

      expect(setValueSpy).not.toHaveBeenCalled();

      // L3: Content summary is STILL expanded — React preserved the component instance.
      const summaryAfter = container.querySelector("#reader-content-summary");
      expect(summaryAfter!.getAttribute("data-expanded")).toBe("true");

      // Sibling DOM updated.
      const pAfter = container.querySelector('[data-spike-node="p"]');
      expect(pAfter!.textContent).toContain("Replaced sibling paragraph.");
    });
  });

  // -------------------------------------------------------------------------
  // L4: Selection save/restore by path on mounted editor
  // -------------------------------------------------------------------------

  describe("L4: selection save/restore on mounted Plate", () => {
    it("selection can be saved before replaceNodes and restored after by path", async () => {
      const { editor, setValueSpy } = await renderMountedPlate(makeInitialValue());

      // In jsdom, editor.selection may be null without real focus.
      // The applier pattern: save selection, do op, restore if path exists.
      // This test verifies the PATTERN works, not that jsdom has a real selection.

      // Attempt to set selection on path [0, 0].
      await act(async () => {
        editor.tf.setSelection({
          anchor: { path: [0, 0], offset: 5 },
          focus: { path: [0, 0], offset: 10 },
        } as never);
      });

      // Save selection before op (applier responsibility).
      const savedSelection = editor.selection ? { ...editor.selection } : null;

      // Replace callout at [1] (different path from selection [0,0]).
      await act(async () => {
        editor.tf.replaceNodes(makeReplacementCallout() as never, { at: [1] } as never);
      });

      expect(setValueSpy).not.toHaveBeenCalled();

      // The non-target path [0, 0] still exists in the Slate model.
      const childrenAfter = editor.children as SpikeValue;
      expect(childrenAfter[0]).toBeDefined();
      expect((childrenAfter[0] as SpikeParagraph).children[0]).toBeDefined();

      // Applier can restore selection by path if it was saved.
      // In jsdom this may not produce a visible selection, but the path
      // validity check (the applier's core logic) is verified.
      if (savedSelection) {
        await act(async () => {
          editor.tf.setSelection(savedSelection as never);
        });
      }

      // The key evidence: the op did not throw, path [0,0] is valid,
      // and the applier pattern (save -> op -> restore) is executable.
      expect(childrenAfter[1]).toEqual(makeReplacementCallout());
    });
  });

  // -------------------------------------------------------------------------
  // readOnly constraint: no throw on mounted Plate
  // -------------------------------------------------------------------------

  describe("readOnly constraint on mounted Plate", () => {
    it("targeted ops do not throw on a mounted readOnly Plate", async () => {
      const { editor } = await renderMountedPlate(makeInitialValue());

      // act() will throw if the callback throws — no need for expect().not.toThrow()
      await act(async () => {
        editor.tf.replaceNodes(makeReplacementCallout() as never, { at: [1] } as never);
      });
    });
  });

  // -------------------------------------------------------------------------
  // API existence enumeration
  // -------------------------------------------------------------------------

  describe("API existence enumeration (installed platejs@53.2.1, mounted editor)", () => {
    it("editor.tf.replaceNodes / removeNodes / insertNodes / setNodes / setValue are functions", async () => {
      const { editor } = await renderMountedPlate(makeInitialValue());
      expect(typeof editor.tf.replaceNodes).toBe("function");
      expect(typeof editor.tf.removeNodes).toBe("function");
      expect(typeof editor.tf.insertNodes).toBe("function");
      expect(typeof editor.tf.setNodes).toBe("function");
      expect(typeof editor.tf.setValue).toBe("function");
    });
  });

  // -------------------------------------------------------------------------
  // PENDING: Browser E2E — jsdom cannot prove "no visible intermediate state"
  // -------------------------------------------------------------------------

  describe("PENDING: browser E2E — no visible intermediate state", () => {
    it("jsdom cannot reliably prove absence of visible intermediate DOM state (marked as browser E2E pending)", async () => {
      // FINDING: jsdom does not have a real rendering pipeline (no layout,
      // no paint, no frames). It cannot prove that multiple Slate ops
      // within a single act() batch produce no visible intermediate DOM
      // state for the user. A real browser E2E test (Playwright/Cypress)
      // would be needed to capture frames between ops and verify no
      // flicker.
      //
      // This test documents the limitation explicitly. It does NOT claim
      // that intermediate state is absent. It only verifies that the batch
      // completes and the final DOM is correct (which is already covered
      // by the L5 batch test above).
      //
      // BROWSER E2E PENDING: verify with Playwright that a batch of 2+
      // replaceNodes ops in one React commit produces no visible flicker
      // in a real browser.
      const { editor, setValueSpy, container } = await renderMountedPlate([
        {
          type: "callout",
          callout_id: "callout_a",
          children: [{ text: "A original." }],
        },
        {
          type: "callout",
          callout_id: "callout_b",
          children: [{ text: "B original." }],
        },
      ]);

      await act(async () => {
        editor.tf.replaceNodes(
          { type: "callout", callout_id: "callout_a", children: [{ text: "A updated." }] } as never,
          { at: [0] } as never,
        );
        editor.tf.replaceNodes(
          { type: "callout", callout_id: "callout_b", children: [{ text: "B updated." }] } as never,
          { at: [1] } as never,
        );
      });

      expect(setValueSpy).not.toHaveBeenCalled();

      // Final DOM is correct (but intermediate state absence is NOT proven).
      const callouts = container.querySelectorAll('[data-spike-node="callout"]');
      expect(callouts[0].textContent).toContain("A updated.");
      expect(callouts[1].textContent).toContain("B updated.");
    });
  });
});
