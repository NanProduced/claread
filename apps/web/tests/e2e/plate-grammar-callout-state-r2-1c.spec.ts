/**
 * T4.2a-PUX-R4-R2.1C — Grammar Callout State Preservation Across Targeted Replace.
 *
 * Browser E2E evidence that when a standalone grammar callout is expanded
 * and then the SAME callout is targeted by `editor.tf.replaceNodes` (same
 * itemId), the expanded state survives — no reset, no flicker.
 *
 * Uses REAL Chromium/Playwright, REAL ReaderRecordPlateKit, REAL projection
 * fixture, REAL reader_callout node. The editor is the browser-mounted
 * Plate editor exposed on `window.__spikeEditor` by the /e2e-plate-spike
 * harness page (server-side env-gated, never exposed in production).
 *
 * Path layout in harness:
 *   [0]=content_summary, [1]=paragraph, [2]=callout, [3]=blockquote
 *
 * Boundary: does NOT implement the full R2 applier, does NOT change the
 * default snapshot reload path, does NOT wire polling/page/reloadSnapshot,
 * does NOT change backend/API/event contracts.
 */

import { expect, test, type Page } from "@playwright/test";

const HARNESS_URL = "/e2e-plate-spike";

async function waitForHarnessReady(page: Page) {
  await page.goto(HARNESS_URL);
  await page.waitForFunction(
    () => (window as unknown as { __spikeReady?: boolean }).__spikeReady === true,
    undefined,
    { timeout: 15_000 },
  );
}

// ===========================================================================
// 1. Target callout replace preserves expanded state (no reset/flicker)
// ===========================================================================

test.describe("1. Grammar callout expanded state survives targeted replaceNodes", () => {
  test("expanded callout remains expanded after replaceNodes on same itemId", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await waitForHarnessReady(page);

    const callout = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );

    // Initially collapsed (grammar default).
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    // Expand the callout.
    await callout
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });

    // Verify expanded.
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // Verify expanded content is visible (original note text).
    await expect(
      page.getByText("shapes acts as the predicate verb."),
    ).toBeVisible();

    // Capture pre-replace DOM identity for flicker check.
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
      );
      if (el) {
        el.setAttribute("data-spike-identity-tag", "callout-before");
      }
    });

    // Targeted replace on the callout at path [2] (same itemId).
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

    // Wait for DOM sync (2 rAF).
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() =>
            requestAnimationFrame(() => resolve()),
          );
        }),
    );

    // CRITICAL: The callout should STILL be expanded (no reset).
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // Content should be updated to the replacement text.
    await expect(
      page.getByText("UPDATED: shapes acts as the main predicate."),
    ).toBeVisible();

    // Original text should be gone.
    await expect(
      page.locator('[data-reader-record-node="callout"]'),
    ).not.toContainText("shapes acts as the predicate verb.");

    await page.screenshot({
      path: "test-results/r2-1c-callout-expanded-after-replace.png",
    });
  });
});

// ===========================================================================
// 2. Collapse → replace → still collapsed → re-expand works
// ===========================================================================

test.describe("2. Collapsed callout remains collapsed after targeted replace", () => {
  test("collapsed callout stays collapsed after replaceNodes, then can be expanded", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await waitForHarnessReady(page);

    const callout = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );

    // Initially collapsed (grammar default) — do NOT expand.
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    // Targeted replace on the callout at path [2] (same itemId).
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

    // Should still be collapsed (user's choice preserved).
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    // Content was updated (visible in collapsed preview or DOM).
    const calloutText = await callout.textContent();
    expect(calloutText).toContain(
      "UPDATED: shapes acts as the main predicate.",
    );

    // Now expand — should work (state is itemId-keyed, not instance-based).
    await callout
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });

    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    await expect(
      page.getByText("UPDATED: shapes acts as the main predicate."),
    ).toBeVisible();

    await page.screenshot({
      path: "test-results/r2-1c-callout-collapsed-then-expanded.png",
    });
  });
});

// ===========================================================================
// 3. Full reload (setValue) clears expansion state via clearRef
// ===========================================================================

test.describe("3. Full reload clears expansion state", () => {
  test("expanded callout collapses after clearRef + setValue", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await waitForHarnessReady(page);

    const callout = page.locator(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );

    // Expand.
    await callout
      .locator('[data-reader-record-callout-toggle="grammar"]')
      .click({ force: true });
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "false",
    );

    // Simulate full reload: clear expansion state, then setValue.
    await page.evaluate(() => {
      const w = window as unknown as {
        __spikeEditor?: {
          tf: { setValue: (v: unknown) => void };
        };
        __spikeGrammarExpansionClear?: (() => void) | null;
        __spikeHelpers?: { makeFreshPlateValue: () => unknown };
      };
      // Clear BEFORE setValue (matching ReaderRecordPlateSurface).
      w.__spikeGrammarExpansionClear?.();
      // setValue with fresh projected value (same content).
      w.__spikeEditor!.tf.setValue(w.__spikeHelpers!.makeFreshPlateValue());
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

    // After full reload, callout should be collapsed (state was cleared).
    await expect(callout).toHaveAttribute(
      "data-reader-record-callout-collapsed",
      "true",
    );

    await page.screenshot({
      path: "test-results/r2-1c-callout-collapsed-after-full-reload.png",
    });
  });
});
