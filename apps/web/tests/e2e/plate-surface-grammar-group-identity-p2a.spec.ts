/**
 * T4.2a-PUX-R4-R2.2-P2a — Grammar Callout-Group Identity Stabilization E2E.
 *
 * Browser E2E tests that exercise the REAL ReaderRecordPlateSurface with
 * real Plate kit to verify Method A2 group identity stability.
 *
 * Evidence scope:
 * - Test 1: two different anchors' grammar callouts form two independent
 *   callout-group blocks with stable IDs (callout-group:unit_1:seg_1 and
 *   callout-group:unit_1:seg_2).
 * - Test 2: expand anchor A (seg_1), then apply grammar_note revision on
 *   anchor B (seg_2) via R2.1E targeted_apply — anchor A's expansion state
 *   is preserved.
 *
 * The harness at /e2e-plate-spike/surface (server-side env-gated) mounts
 * a real ReaderRecordPlateSurface. Tests drive the reload pipeline via
 * `window.__spikeSurface`.
 *
 * Boundary: does NOT change production Surface/polling/merger/backend/payload.
 */

import { expect, test, type Page } from "@playwright/test";

const HARNESS_URL = "/e2e-plate-spike/surface";

test.beforeEach(() => {
  test.skip(
    true,
    "CUTOVER-WEB-LONG: grammar identity coverage is retained in ReaderRecordPlateSurface Vitest; this legacy harness suite awaits Physical deletion.",
  );
});

async function mockApiRoutes(page: Page) {
  await page.route("**/api/web/dict/lookup*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, entries: [] }),
    });
  });
  await page.route("**/api/web/reader/records/*/favorite**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, favorited: false }),
    });
  });
  await page.route("**/api/web/feedback**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
}

async function waitForHarnessReady(page: Page) {
  await page.goto(HARNESS_URL);
  await page.waitForFunction(
    () =>
      (window as unknown as { __spikeSurfaceReady?: boolean })
        .__spikeSurfaceReady === true,
    undefined,
    { timeout: 15_000 },
  );
}

// ===========================================================================
// 1. Cross-anchor grammar callouts form two independent groups.
// ===========================================================================

test.describe("1. R2.2-P2a cross-anchor group splitting", () => {
  test("two different anchors' grammar callouts form two independent groups", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);

    // Load a snapshot with grammar marks on BOTH seg_1 and seg_2.
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const multiAnchorSnapshot = s.makeMultiAnchorGrammarSnapshot();
      s.loadSnapshot(multiAnchorSnapshot);
    });

    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });
    await page.waitForTimeout(500);

    // Verify two independent callout-group blocks exist.
    const groupA = page.locator(
      '[data-reader-record-block-id="callout-group:unit_1:seg_1"]',
    );
    const groupB = page.locator(
      '[data-reader-record-block-id="callout-group:unit_1:seg_2"]',
    );

    await expect(groupA).toBeVisible();
    await expect(groupB).toBeVisible();

    // Verify each group has the correct grammar item.
    const itemA = groupA.locator(
      '[data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const itemB = groupB.locator(
      '[data-reader-record-grammar-item-id="grammar_item_2"]',
    );

    await expect(itemA).toBeVisible();
    await expect(itemB).toBeVisible();

    // Verify all block IDs are unique (no duplicate group IDs).
    const blockIds = await page.evaluate(() => {
      const els = document.querySelectorAll<HTMLElement>(
        "[data-reader-record-block-id]",
      );
      return Array.from(els).map(
        (el) => el.dataset.readerRecordBlockId ?? "",
      );
    });
    const uniqueIds = new Set(blockIds);
    expect(blockIds.length).toBe(uniqueIds.size);

    await page.screenshot({
      path: "test-results/p2a-cross-anchor-group-splitting.png",
    });
  });
});

// ===========================================================================
// 2. Expand anchor A, revise anchor B's grammar note via targeted_apply —
//    anchor A's expansion state is preserved.
// ===========================================================================

test.describe("2. R2.2-P2a independent expansion state across anchors", () => {
  test("expanding anchor A then revising anchor B preserves A's expansion", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);

    // Load a snapshot with grammar marks on BOTH seg_1 and seg_2.
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const multiAnchorSnapshot = s.makeMultiAnchorGrammarSnapshot();
      s.loadSnapshot(multiAnchorSnapshot);
    });

    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });
    await page.waitForTimeout(500);

    // Expand anchor A (seg_1's grammar_item_1).
    const calloutA = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    await expect(calloutA).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    await calloutA
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });
    await expect(calloutA).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // Anchor B (seg_2's grammar_item_2) should still be collapsed.
    const calloutB = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_2"]',
    );
    await expect(calloutB).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    // Apply a grammar_note revision on seg_2 via R2.1E targeted_apply.
    // The merger should detect only seg_2's callout-group block as changed
    // and replace it, leaving seg_1's callout-group block untouched.
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const nextSnapshot = s.makeSeg2GrammarRevisionSnapshot({
        grammarNote: "second modifies test sentence. (revised note)",
      });
      const event = s.makeValidLayerPublishedEvent("grammar_note", 10);
      s.reloadWith(nextSnapshot, [event], {
        generation: 1,
        baseId: "base_1",
      });
    });

    await page.waitForTimeout(1000);

    // Anchor A (seg_1) expansion must be preserved after targeted_apply
    // on anchor B (seg_2).
    const calloutAAfter = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    await expect(calloutAAfter).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // Group IDs remain stable.
    const groupA = page.locator(
      '[data-reader-record-block-id="callout-group:unit_1:seg_1"]',
    );
    const groupB = page.locator(
      '[data-reader-record-block-id="callout-group:unit_1:seg_2"]',
    );
    await expect(groupA).toBeVisible();
    await expect(groupB).toBeVisible();

    await page.screenshot({
      path: "test-results/p2a-independent-expansion-preserved.png",
    });
  });
});
