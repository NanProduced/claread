/**
 * T4.2a-PUX-R4-R2-S2 — Browser E2E Gate for Targeted Plate Updates.
 *
 * Closes S1's two browser-level evidence gaps:
 *   A. Non-target text Selection truly restores after targeted Slate update.
 *   B. ReaderContentSummaryElement.expanded survives sibling replaceNodes.
 *   C. Batch multiple targeted updates: non-target DOM not removed, no
 *      observable intermediate blank/whole-page replacement.
 *
 * Uses REAL Chromium/Playwright (not jsdom), REAL ReaderRecordPlateKit,
 * REAL projection fixture, REAL reader_paragraph / reader_callout /
 * reader_blockquote nodes. The editor operated on is the browser-mounted
 * Plate editor exposed on `window.__spikeEditor` by the /e2e-plate-spike
 * harness page.
 *
 * Boundary: does NOT implement the full R2 applier, does NOT change the
 * default snapshot reload path, does NOT wire polling/page/reloadSnapshot,
 * does NOT change backend/API/event contracts.
 */

import { expect, test, type Page } from "@playwright/test";

const HARNESS_URL = "/e2e-plate-spike";

/**
 * Navigate to the E2E harness page and wait for the mounted Plate editor
 * to signal readiness via `window.__spikeReady === true`.
 */
async function waitForHarnessReady(page: Page) {
  await page.goto(HARNESS_URL);
  await page.waitForFunction(
    () => (window as unknown as { __spikeReady?: boolean }).__spikeReady === true,
    undefined,
    { timeout: 15_000 },
  );
}

// ===========================================================================
// A. Selection — non-target reader_paragraph Selection survives sibling
//    callout replaceNodes, verified via save → op → path validity → restore.
// ===========================================================================

test.describe("A. Selection — non-target Selection survives sibling replaceNodes", () => {
  test("real browser Selection in paragraph is preserved after sibling callout replaceNodes", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await waitForHarnessReady(page);

    // Path layout: [0]=content_summary, [1]=paragraph, [2]=callout, [3]=blockquote
    //
    // FINDING (consistent with S1-P2 jsdom): In readOnly mode,
    // editor.tf.setSelection does NOT persist editor.selection — it stays
    // null. This is a Plate readOnly behavior, not a bug. The L4 question
    // for the browser is therefore: does the real browser DOM Selection
    // survive a targeted replaceNodes on a sibling, without any Slate
    // selection restore? If yes, the applier does not need to save/restore
    // Slate selection in readOnly mode — the browser Selection is naturally
    // preserved because the target op does not touch the paragraph DOM.
    //
    // We still exercise the save→op→path validity→restore pattern to
    // document whether the Slate path works. But the PASS/FAIL gate for
    // L4 is the browser Selection survival, not the Slate selection state.

    // Step 1: Establish a real browser Selection in the paragraph text.
    const setup = await page.evaluate(() => {
      const w = window as unknown as {
        __spikeEditor?: {
          tf: {
            setSelection: (sel: unknown) => void;
            replaceNodes: (nodes: unknown, opts: unknown) => void;
          };
          selection: {
            anchor: { path: number[]; offset: number };
            focus: { path: number[]; offset: number };
          } | null;
          children: unknown[];
        };
        __spikeHelpers?: {
          makeReplacementCallout: () => unknown;
          makeReplacementParagraph: () => unknown;
        };
      };
      const editor = w.__spikeEditor;
      if (!editor) return { error: "editor not exposed" };

      const paragraphEl = document.querySelector(
        '[data-reader-record-node="paragraph"]',
      );
      if (!paragraphEl) return { error: "paragraph DOM not found" };

      // Find first text node inside the paragraph.
      const walker = document.createTreeWalker(
        paragraphEl,
        NodeFilter.SHOW_TEXT,
      );
      const textNode = walker.nextNode() as Text | null;
      if (!textNode) return { error: "text node not found in paragraph" };

      const textLength = textNode.textContent?.length ?? 0;
      const startOffset = Math.min(5, textLength);
      const endOffset = Math.min(15, textLength);

      // Set real browser Selection on the paragraph text.
      const range = document.createRange();
      range.setStart(textNode, startOffset);
      range.setEnd(textNode, endOffset);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);

      // Attempt Slate selection (applier save pattern). In readOnly this
      // may not persist — we record the result for diagnostic purposes.
      editor.tf.setSelection({
        anchor: { path: [1, 0], offset: startOffset },
        focus: { path: [1, 0], offset: endOffset },
      });

      const slateSelectionAfterSet = editor.selection ? "non-null" : "null";

      // Save Slate selection snapshot (may be null in readOnly).
      const savedSel = editor.selection
        ? {
            anchor: {
              path: [...editor.selection.anchor.path],
              offset: editor.selection.anchor.offset,
            },
            focus: {
              path: [...editor.selection.focus.path],
              offset: editor.selection.focus.offset,
            },
          }
        : null;

      const browserSel = window.getSelection();
      return {
        textLength,
        startOffset,
        endOffset,
        savedSel,
        slateSelectionAfterSet,
        browserRangeCount: browserSel?.rangeCount ?? 0,
        browserSelectedText: browserSel?.toString() ?? "",
        browserAnchorOffset: browserSel?.anchorOffset ?? 0,
        browserFocusOffset: browserSel?.focusOffset ?? 0,
      };
    });

    expect(setup.error).toBeUndefined();
    expect(
      setup.browserRangeCount,
      "Browser Selection should have at least one range after DOM setup",
    ).toBeGreaterThan(0);

    // Step 2: save → targeted op → path validity → restore (applier pattern).
    const opResult = await page.evaluate(() => {
      const w = window as unknown as {
        __spikeEditor?: {
          tf: {
            setSelection: (sel: unknown) => void;
            replaceNodes: (nodes: unknown, opts: unknown) => void;
          };
          selection: {
            anchor: { path: number[]; offset: number };
            focus: { path: number[]; offset: number };
          } | null;
          children: unknown[];
        };
        __spikeHelpers?: {
          makeReplacementCallout: () => unknown;
        };
      };
      const editor = w.__spikeEditor!;
      const helpers = w.__spikeHelpers!;

      // SAVE: snapshot the current Slate selection before the op.
      const saved = editor.selection
        ? {
            anchor: {
              path: [...editor.selection.anchor.path],
              offset: editor.selection.anchor.offset,
            },
            focus: {
              path: [...editor.selection.focus.path],
              offset: editor.selection.focus.offset,
            },
          }
        : null;

      // TARGETED OP: replaceNodes on sibling callout at [2] (not the
      // paragraph at [1] where the browser Selection lives).
      editor.tf.replaceNodes(helpers.makeReplacementCallout(), {
        at: [2],
      });

      // PATH VALIDITY: verify the paragraph path [1] and its first child
      // [1, 0] still exist (the selection target was not removed).
      const children = editor.children as unknown[];
      const paraNode = children[1] as
        | { children?: unknown[] }
        | undefined;
      const paraChildren = paraNode?.children;
      const pathValid =
        Array.isArray(paraChildren) && paraChildren.length > 0;

      // RESTORE: re-apply the saved Slate selection if we had one.
      // In readOnly this may be a no-op (saved was null), but we still
      // exercise the pattern for diagnostic purposes.
      let restoreAttempted = false;
      if (saved && pathValid) {
        restoreAttempted = true;
        editor.tf.setSelection({
          anchor: {
            path: saved.anchor.path,
            offset: saved.anchor.offset,
          },
          focus: {
            path: saved.focus.path,
            offset: saved.focus.offset,
          },
        });
      }

      return {
        savedWasNonNull: saved !== null,
        pathValid,
        restoreAttempted,
        selectionAfterOp: editor.selection ? "non-null" : "null",
        selectionAfterRestore: editor.selection ? "non-null" : "null",
      };
    });

    // Path validity is the key Slate-level guarantee: the paragraph was
    // not removed or shifted by the sibling replacement.
    expect(
      opResult.pathValid,
      "Saved selection path [1, 0] should still be valid after sibling replaceNodes",
    ).toBe(true);

    // Step 3: Wait for React + DOM sync (2 rAF).
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() =>
            requestAnimationFrame(() => resolve()),
          );
        }),
    );

    // Step 4 (L4 GATE): Assert the real browser Selection is non-empty
    // and still inside the original paragraph. This is the browser-level
    // evidence for L4. The Slate selection state is reported as
    // diagnostic but does not determine PASS/FAIL — the browser Selection
    // survival is what matters for the reader UX.
    const finalSel = await page.evaluate(() => {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) {
        return { hasSelection: false };
      }

      const anchorNode = sel.anchorNode;
      const focusNode = sel.focusNode;
      const paragraphEl = document.querySelector(
        '[data-reader-record-node="paragraph"]',
      );
      const isAnchorInsideParagraph =
        paragraphEl?.contains(anchorNode) ?? false;
      const isFocusInsideParagraph =
        paragraphEl?.contains(focusNode) ?? false;

      return {
        hasSelection: true,
        anchorOffset: sel.anchorOffset,
        focusOffset: sel.focusOffset,
        isAnchorInsideParagraph,
        isFocusInsideParagraph,
        selectedText: sel.toString(),
      };
    });

    expect(
      finalSel.hasSelection,
      "Browser Selection should be non-empty after targeted sibling replaceNodes",
    ).toBe(true);
    expect(
      finalSel.isAnchorInsideParagraph,
      "Selection anchor should still be inside the original reader_paragraph",
    ).toBe(true);
    expect(
      finalSel.isFocusInsideParagraph,
      "Selection focus should still be inside the original reader_paragraph",
    ).toBe(true);

    // Verify the sibling callout was actually updated.
    const calloutText = await page
      .locator('[data-reader-record-node="callout"]')
      .textContent();
    expect(calloutText).toContain(
      "UPDATED: shapes acts as the main predicate.",
    );

    // Attach diagnostic info as test annotations (visible in reports).
    console.log("[S2-L4 diagnostic]", {
      slateSelectionAfterSet: setup.slateSelectionAfterSet,
      savedWasNonNull: opResult.savedWasNonNull,
      pathValid: opResult.pathValid,
      restoreAttempted: opResult.restoreAttempted,
      selectionAfterOp: opResult.selectionAfterOp,
      selectionAfterRestore: opResult.selectionAfterRestore,
      browserSelectionSurvived: finalSel.hasSelection,
      browserSelectedText: finalSel.selectedText,
    });

    await page.screenshot({
      path: "test-results/s2-selection-after-restore.png",
    });
  });
});

// ===========================================================================
// B. Interaction preservation — ReaderContentSummaryElement.expanded
//    survives sibling replaceNodes.
// ===========================================================================

test.describe("B. Interaction — expanded state survives sibling replaceNodes", () => {
  test("ReaderContentSummaryElement.expanded survives sibling callout replaceNodes", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await waitForHarnessReady(page);

    const summary = page.locator("#reader-content-summary");

    // Verify collapsed initially.
    await expect(summary).toHaveAttribute("data-expanded", "false");

    // Click the expand button.
    await summary.locator("button").first().click();

    // Verify expanded.
    await expect(summary).toHaveAttribute("data-expanded", "true");

    // Verify expanded content is visible (research question text).
    await expect(
      page.getByText(
        "Does replaceNodes preserve React state in real browser?",
      ),
    ).toBeVisible();

    // Replace sibling callout at [2].
    await page.evaluate(() => {
      const w = window as unknown as {
        __spikeEditor?: {
          tf: { replaceNodes: (n: unknown, o: unknown) => void };
        };
        __spikeHelpers?: { makeReplacementCallout: () => unknown };
      };
      w.__spikeEditor!.tf.replaceNodes(
        w.__spikeHelpers!.makeReplacementCallout(),
        { at: [2] },
      );
    });

    // Wait for DOM sync.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() =>
            requestAnimationFrame(() => resolve()),
          );
        }),
    );

    // L3: Content summary is STILL expanded — React preserved the component.
    await expect(summary).toHaveAttribute("data-expanded", "true");

    // Expanded content is still visible.
    await expect(
      page.getByText(
        "Does replaceNodes preserve React state in real browser?",
      ),
    ).toBeVisible();

    // Sibling DOM was updated.
    const calloutText = await page
      .locator('[data-reader-record-node="callout"]')
      .textContent();
    expect(calloutText).toContain(
      "UPDATED: shapes acts as the main predicate.",
    );

    await page.screenshot({
      path: "test-results/s2-interaction-preserved.png",
    });
  });
});

// ===========================================================================
// C. Batch / flicker evidence — multiple replaceNodes in one tick: non-target
//    DOM not removed, final DOM has both updates, DOM identity preserved.
// ===========================================================================

test.describe("C. Batch / flicker — non-target DOM not removed during batch", () => {
  test("batch replaceNodes: non-target nodes not removed, final DOM has both updates", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await waitForHarnessReady(page);

    // Install MutationObserver, execute batch (callout [2] + paragraph [1]),
    // wait 2 rAF, then collect evidence.
    // Targets: callout [2] and paragraph [1].
    // Non-targets: content summary [0] and blockquote [3].
    const evidence = await page.evaluate(() => {
      const w = window as unknown as {
        __spikeEditor?: {
          tf: { replaceNodes: (n: unknown, o: unknown) => void };
        };
        __spikeHelpers?: {
          makeReplacementCallout: () => unknown;
          makeReplacementParagraph: () => unknown;
        };
      };
      const editor = w.__spikeEditor!;
      const helpers = w.__spikeHelpers!;

      // Find the editor root to observe.
      const editorRoot =
        document.querySelector("[data-slate-editor]") ||
        document.querySelector("[contenteditable]") ||
        document.querySelector("main");

      if (!editorRoot) {
        return { removedNodes: [], error: "editor root not found" };
      }

      const removedNodes: string[] = [];
      const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
          for (const removedNode of Array.from(mutation.removedNodes)) {
            const el = removedNode as HTMLElement;
            const recordType = el?.dataset?.readerRecordNode;
            const readerNodeType = el?.dataset?.readerNode;
            const id = el?.id;
            removedNodes.push(
              recordType ?? readerNodeType ?? id ?? `unknown:${removedNode.nodeName}`,
            );
          }
        }
      });

      observer.observe(editorRoot, {
        childList: true,
        subtree: true,
      });

      // Execute batch: two replaceNodes in the same synchronous tick.
      editor.tf.replaceNodes(helpers.makeReplacementCallout(), {
        at: [2],
      });
      editor.tf.replaceNodes(helpers.makeReplacementParagraph(), {
        at: [1],
      });

      return new Promise<{ removedNodes: string[]; error?: string }>(
        (resolve) => {
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              observer.disconnect();
              resolve({ removedNodes });
            }),
          );
        },
      );
    });

    expect(evidence.error).toBeUndefined();

    // Filter for non-target removals.
    // Non-targets: content-summary (id="reader-content-summary",
    // data-reader-node="content-summary") and blockquote
    // (data-reader-record-node="blockquote").
    const nonTargetRemoved = evidence.removedNodes.filter(
      (node) =>
        node === "reader-content-summary" ||
        node === "content-summary" ||
        node === "blockquote",
    );

    expect(
      nonTargetRemoved,
      `Non-target nodes were removed during batch: ${JSON.stringify(nonTargetRemoved)}`,
    ).toEqual([]);

    // Final DOM has both updates.
    const calloutText = await page
      .locator('[data-reader-record-node="callout"]')
      .textContent();
    expect(calloutText).toContain(
      "UPDATED: shapes acts as the main predicate.",
    );

    const paragraphText = await page
      .locator('[data-reader-record-node="paragraph"]')
      .textContent();
    expect(paragraphText).toContain(
      "UPDATED: Institutional memory drives policy choices.",
    );

    // Non-target nodes are still present.
    await expect(page.locator("#reader-content-summary")).toBeVisible();
    await expect(
      page.locator('[data-reader-record-node="blockquote"]'),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/s2-batch-final-dom.png",
    });
  });

  test("batch replaceNodes: DOM identity of non-target nodes preserved across batch", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await waitForHarnessReady(page);

    // Tag non-target nodes with identity markers BEFORE the batch.
    await page.evaluate(() => {
      const contentSummary = document.querySelector(
        "#reader-content-summary",
      );
      const blockquote = document.querySelector(
        '[data-reader-record-node="blockquote"]',
      );
      if (contentSummary) {
        contentSummary.setAttribute(
          "data-spike-identity-tag",
          "content-summary",
        );
      }
      if (blockquote) {
        blockquote.setAttribute(
          "data-spike-identity-tag",
          "blockquote",
        );
      }
    });

    // Execute batch.
    await page.evaluate(() => {
      const w = window as unknown as {
        __spikeEditor?: {
          tf: { replaceNodes: (n: unknown, o: unknown) => void };
        };
        __spikeHelpers?: {
          makeReplacementCallout: () => unknown;
          makeReplacementParagraph: () => unknown;
        };
      };
      const editor = w.__spikeEditor!;
      const helpers = w.__spikeHelpers!;

      editor.tf.replaceNodes(helpers.makeReplacementCallout(), {
        at: [2],
      });
      editor.tf.replaceNodes(helpers.makeReplacementParagraph(), {
        at: [1],
      });
    });

    // Wait for DOM sync.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() =>
            requestAnimationFrame(() => resolve()),
          );
        }),
    );

    // Verify identity tags are still present — same DOM nodes, not remounted.
    const contentSummaryTag = await page
      .locator("#reader-content-summary")
      .getAttribute("data-spike-identity-tag");
    expect(contentSummaryTag).toBe("content-summary");

    const blockquoteTag = await page
      .locator('[data-reader-record-node="blockquote"]')
      .getAttribute("data-spike-identity-tag");
    expect(blockquoteTag).toBe("blockquote");
  });
});
