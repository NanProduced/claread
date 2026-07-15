/**
 * T4.2a-PUX-R4-R2.1E — Layer Published Revision Incremental Apply E2E Gate.
 *
 * Browser E2E tests that exercise the REAL ReaderRecordPlateSurface's
 * R2.1E changed-block-only apply path for `layer_published` events.
 *
 * Evidence scope:
 * - Test 1: same-topology translation revision → targeted_apply preserves
 *   non-target paragraph DOM identity (blockquote content updated).
 * - Test 2: same-topology grammar_note revision → targeted_apply preserves
 *   grammar callout expanded state (same itemId).
 * - Test 3: structural change (new sentence_analysis block) → fallback_full_reload
 *   via setValue (non-target DOM identity NOT preserved).
 * - Test 4: env gate (route reachable when CLAREAD_ENABLE_E2E_SPIKE=1).
 *
 * The harness at /e2e-plate-spike/surface (server-side env-gated) mounts
 * a real ReaderRecordPlateSurface. Tests drive the reload pipeline via
 * `window.__spikeSurface` and assert FINAL DOM/state after reload completes.
 *
 * Boundary: does NOT change production Surface/polling/merger/backend/payload.
 */

import { expect, test, type Page } from "@playwright/test";

const HARNESS_URL = "/e2e-plate-spike/surface";

// Intercept API calls that the Surface may make (dictionary lookup,
// favorites, feedback) and return benign mock responses so the test
// is self-contained and does not depend on a running backend.
async function mockApiRoutes(page: Page) {
  await page.route("**/api/web/dict/lookup*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, entries: [] }),
    });
  });
  await page.route("**/api/web/favorites**", (route) => {
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
// 1. Translation revision (same topology): targeted_apply preserves
//    non-target paragraph DOM identity, blockquote content updated.
// ===========================================================================

test.describe("1. R2.1E translation revision targeted_apply", () => {
  test("same-topology translation revision replaces blockquote, preserves paragraph DOM", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);

    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // Capture the paragraph DOM element before reload (non-target block).
    const paragraphBefore = page.locator(
      '[data-reader-record-node="paragraph"]',
    );
    await expect(paragraphBefore.first()).toBeVisible();

    // Use Playwright's evaluate to capture the DOM node identity via
    // a custom data attribute that survives React reconciliation.
    // We tag the paragraph element with a unique marker.
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-reader-record-node="paragraph"]',
      );
      if (el) el.setAttribute("data-r21e-marker", "paragraph-before");
    });

    // Build a same-topology translation revision snapshot + valid event.
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const nextSnapshot = s.makeLayerRevisionSnapshot({
        translationText: "制度记忆会塑造政策选择。(修订版)",
      });
      const event = s.makeValidLayerPublishedEvent("translation", 9);
      s.reloadWith(nextSnapshot, [event], {
        generation: 1,
        baseId: "base_1",
      });
    });

    await page.waitForTimeout(1000);

    // Non-target paragraph DOM identity preserved (targeted_apply used
    // replaceNodes on the blockquote only, NOT setValue).
    const paragraphAfter = page.locator(
      '[data-reader-record-node="paragraph"][data-r21e-marker="paragraph-before"]',
    );
    await expect(paragraphAfter).toBeVisible();

    // Target blockquote content was updated to the revised translation.
    // Note: fixture has 2 blockquotes (one per translation group); both
    // get the same revised text via makeLayerRevisionSnapshot. Use first()
    // to avoid strict mode violation.
    const blockquoteAfter = page
      .locator('[data-reader-record-node="blockquote"]')
      .first();
    await expect(blockquoteAfter).toContainText("修订版");

    await page.screenshot({
      path: "test-results/r2-1e-translation-revision-targeted-apply.png",
    });
  });
});

// ===========================================================================
// 2. Grammar note revision (same topology): targeted_apply preserves
//    grammar callout expanded state when itemId is unchanged.
// ===========================================================================

test.describe("2. R2.1E grammar_note revision preserves expansion", () => {
  test("same-topology grammar_note revision keeps callout expanded", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);

    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // Expand the grammar callout before reload.
    const callout = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    await callout
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // Apply a grammar_note revision via R2.1E targeted_apply.
    // The callout's itemId ("grammar_item_1") is unchanged, so the
    // expansion state should be preserved by ReaderGrammarExpansionContext.
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const nextSnapshot = s.makeLayerRevisionSnapshot({
        grammarNote: "shapes is the predicate verb. (revised note)",
      });
      const event = s.makeValidLayerPublishedEvent("grammar_note", 9);
      s.reloadWith(nextSnapshot, [event], {
        generation: 1,
        baseId: "base_1",
      });
    });

    await page.waitForTimeout(1000);

    // Grammar callout (same itemId) should still be expanded after
    // targeted_apply — the expansion state is keyed by itemId.
    const calloutAfter = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    await expect(calloutAfter).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    await page.screenshot({
      path: "test-results/r2-1e-grammar-revision-expansion-preserved.png",
    });
  });
});

// ===========================================================================
// 2b. R2.2-P1 vocabulary revision (same topology): targeted_apply replaces
//     paragraph (mark data change), preserves non-target blockquote DOM.
// ===========================================================================

test.describe("2b. R2.2-P1 vocabulary revision targeted_apply", () => {
  test("same-topology vocabulary revision replaces paragraph, preserves blockquote DOM", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);

    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // Capture the blockquote DOM element before reload (non-target block).
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-reader-record-node="blockquote"]',
      );
      if (el) el.setAttribute("data-r22p1-marker", "blockquote-before");
    });

    // Build a same-topology vocabulary revision snapshot + valid event.
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const nextSnapshot = s.makeLayerRevisionSnapshot({
        vocabularyGloss: "记忆 (修订)",
      });
      const event = s.makeValidLayerPublishedEvent("vocabulary", 9);
      s.reloadWith(nextSnapshot, [event], {
        generation: 1,
        baseId: "base_1",
      });
    });

    await page.waitForTimeout(1000);

    // Non-target blockquote DOM identity preserved (targeted_apply used
    // replaceNodes on the paragraph only, NOT setValue).
    const blockquoteAfter = page.locator(
      '[data-reader-record-node="blockquote"][data-r22p1-marker="blockquote-before"]',
    );
    await expect(blockquoteAfter).toBeVisible();

    // Target paragraph vocabulary mark is still present (paragraph was
    // replaced, not removed).
    const vocabMarkAfter = page.locator(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    await expect(vocabMarkAfter).toBeVisible();

    await page.screenshot({
      path: "test-results/r2-2-p1-vocabulary-revision-targeted-apply.png",
    });
  });
});

// ===========================================================================
// 3. Structural change (new sentence_analysis block): fallback_full_reload
//    via setValue — non-target DOM identity NOT preserved.
// ===========================================================================

test.describe("3. R2.1E structural change fallback_full_reload", () => {
  test("new sentence_analysis block triggers setValue full reload", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);

    await page.waitForSelector(".reader-record-plate-document", {
      timeout: 15_000,
    });

    // Tag the blockquote DOM element with a unique marker before reload.
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-reader-record-node="blockquote"]',
      );
      if (el) el.setAttribute("data-r21e-marker", "blockquote-before");
    });

    // Apply a structural change (new sentence_analysis block) via
    // a valid layer_published event. The merger detects the topology
    // change (unit_block_set_changed) and returns fallback_full_reload.
    await page.evaluate(() => {
      const s = window.__spikeSurface!;
      const nextSnapshot = s.makeStructuralChangeSnapshot();
      const event = s.makeValidLayerPublishedEvent("sentence_analysis", 9);
      s.reloadWith(nextSnapshot, [event], {
        generation: 1,
        baseId: "base_1",
      });
    });

    await page.waitForTimeout(1000);

    // Structural change → fallback_full_reload → setValue rebuilds all DOM.
    // The old blockquote DOM node (with the marker) is gone — React
    // remounted the entire tree.
    const oldBlockquote = page.locator(
      '[data-reader-record-node="blockquote"][data-r21e-marker="blockquote-before"]',
    );
    await expect(oldBlockquote).toHaveCount(0);

    // A new blockquote exists (rebuilt by setValue). Fixture has 2
    // blockquotes; use first() to avoid strict mode violation.
    const newBlockquote = page
      .locator('[data-reader-record-node="blockquote"]')
      .first();
    await expect(newBlockquote).toBeVisible();

    // The new sentence_analysis block is visible.
    const sentenceAnalysisBlocks = page.locator(
      '[data-reader-record-node="sentence-analysis"]',
    );
    await expect(sentenceAnalysisBlocks).toHaveCount(2);

    await page.screenshot({
      path: "test-results/r2-1e-structural-change-fallback-full-reload.png",
    });
  });
});

// ===========================================================================
// 4. Env gate: route is reachable when CLAREAD_ENABLE_E2E_SPIKE=1
// ===========================================================================

test.describe("4. R2.1E env gate", () => {
  test("surface route is reachable for R2.1E tests", async ({ page }) => {
    const response = await page.goto(HARNESS_URL);
    expect(response?.status()).toBe(200);
    await expect(
      page.locator('[data-testid="e2e-surface-harness-root"]'),
    ).toBeVisible();
  });
});
