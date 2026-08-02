/**
 * T5.5a — Semantic Outline (L2) Browser Contract Gate.
 *
 * Chromium E2E against ReaderRecordPlateSurface on `/e2e-plate-spike/surface`.
 * Does not depend on real user records or backend outline workers.
 */

import { expect, test, type Page } from "@playwright/test";

import {
  makeL1PlusOutlineSnapshot,
  makeSemanticOutlineSnapshot,
} from "./fixtures/semantic-outline-navigation-snapshot";
import {
  makeL0SingleHeadingLongSnapshot,
  makeL1HeadingRichSnapshot,
} from "./fixtures/l1-heading-navigation-snapshot";

const HARNESS_URL = "/e2e-plate-spike/surface";

test.beforeEach(() => {
  test.skip(
    true,
    "CUTOVER-WEB-LONG: outline coverage is retained in ReaderRecordPlateSurface Vitest; this legacy harness suite awaits Physical deletion.",
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
  await page.addStyleTag({
    content: `
      .reader-record-plate-document [data-reader-record-node="paragraph"][data-unit-id] {
        min-height: 720px !important;
        margin-bottom: 64px !important;
        padding-top: 24px !important;
        padding-bottom: 24px !important;
        box-sizing: border-box !important;
        display: block !important;
      }
    `,
  });
}

async function injectSnapshotOnce(
  page: Page,
  snapshot: Record<string, unknown>,
  expectedUnitIds: string[],
) {
  await page.evaluate((next) => {
    const surface = (
      window as unknown as {
        __spikeSurface?: {
          loadSnapshot: (s: Record<string, unknown>) => void;
        };
      }
    ).__spikeSurface;
    if (!surface) {
      throw new Error("__spikeSurface not ready");
    }
    surface.loadSnapshot(next);
  }, snapshot);

  await page.waitForSelector(".reader-record-plate-document", {
    timeout: 15_000,
  });
  await page.waitForSelector('[data-testid="reader-record-navigation-rail"]', {
    timeout: 15_000,
  });
  await page.waitForFunction(
    (ids) => {
      const body = document.querySelector(".reader-record-plate-document");
      if (!body) return false;
      const found = new Set(
        Array.from(
          body.querySelectorAll(
            '[data-reader-record-node="paragraph"][data-unit-id]',
          ),
        ).map((n) => n.getAttribute("data-unit-id")),
      );
      return ids.every((id) => found.has(id));
    },
    expectedUnitIds,
    { timeout: 15_000 },
  );
}

async function loadSnapshot(
  page: Page,
  snapshot: Record<string, unknown>,
  expectedUnitIds: string[],
) {
  await injectSnapshotOnce(page, snapshot, expectedUnitIds);
  await page.waitForTimeout(200);
  const settled = {
    ...snapshot,
    snapshot_id: `${String(snapshot.snapshot_id ?? "snap")}_settled`,
  };
  await injectSnapshotOnce(page, settled, expectedUnitIds);
}

async function openPanel(page: Page) {
  const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
  await trigger.click();
  const panel = page.locator('[data-testid="reader-record-navigation-panel"]');
  await expect(panel).not.toHaveClass(/pointer-events-none/, { timeout: 5_000 });
  return panel;
}

test.describe("T5.5a semantic outline L2", () => {
  test.beforeEach(async ({ page }) => {
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
  });

  test("ready outline: switch to semantic, click locates unit, ticks depth=1 only", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-has-semantic-outline", "true");
    await expect(rail).toHaveAttribute("data-outline-surface", "deterministic");
    await expect(rail).toHaveAttribute("data-navigation-mode", "L0");

    await openPanel(page);
    await page
      .locator('[data-testid="reader-record-outline-mode-semantic"]')
      .click();
    await expect(rail).toHaveAttribute("data-outline-surface", "semantic");
    await expect(rail).toHaveAttribute("data-navigation-mode", "L2");
    await expect(rail).toHaveAttribute("aria-label", "内容大纲");

    const ticks = page.locator(
      '[data-testid="reader-record-mini-rail"] [data-navigation-tick-key]',
    );
    await expect(ticks).toHaveCount(2);
    await expect(ticks.nth(0)).toHaveAttribute("data-navigation-tick-key", "n1");
    await expect(ticks.nth(0)).toHaveAttribute("data-outline-node-id", "n1");
    await expect(ticks.nth(0)).not.toHaveAttribute("data-navigation-unit-id");
    await expect(ticks.nth(1)).toHaveAttribute("data-navigation-tick-key", "n3");
    await expect(ticks.nth(1)).toHaveAttribute("data-outline-node-id", "n3");
    await expect(ticks.nth(1)).not.toHaveAttribute("data-navigation-unit-id");

    await expect(
      page.locator('[data-testid="reader-record-outline-node-n2"]'),
    ).toBeVisible();

    const before = await page.evaluate(() => {
      const body = document.querySelector(".reader-record-plate-document");
      let scroller: Window | HTMLElement = window;
      let walk: HTMLElement | null = body?.parentElement ?? null;
      while (walk && walk !== document.body) {
        const style = window.getComputedStyle(walk);
        if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
          scroller = walk;
          break;
        }
        walk = walk.parentElement;
      }
      return scroller === window
        ? window.scrollY
        : (scroller as HTMLElement).scrollTop;
    });

    await page.locator('[data-testid="reader-record-outline-node-n3"]').click();
    await page.waitForTimeout(400);

    const after = await page.evaluate(() => {
      const body = document.querySelector(".reader-record-plate-document");
      let scroller: Window | HTMLElement = window;
      let walk: HTMLElement | null = body?.parentElement ?? null;
      while (walk && walk !== document.body) {
        const style = window.getComputedStyle(walk);
        if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
          scroller = walk;
          break;
        }
        walk = walk.parentElement;
      }
      return scroller === window
        ? window.scrollY
        : (scroller as HTMLElement).scrollTop;
    });
    expect(after).toBeGreaterThan(before);

    const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
    await expect(trigger).not.toHaveAttribute("aria-haspopup", "menu");
    const controls = await trigger.getAttribute("aria-controls");
    expect(controls).toBeTruthy();
  });

  test("no outline: L0/L1 regression and no mode switch", async ({ page }) => {
    const l0 = makeL0SingleHeadingLongSnapshot();
    await loadSnapshot(
      page,
      l0,
      (l0.navigation as { units: Array<{ unit_id: string }> }).units.map(
        (u) => u.unit_id,
      ),
    );
    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-has-semantic-outline", "false");
    await expect(rail).toHaveAttribute("data-navigation-mode", "L0");
    await openPanel(page);
    await expect(
      page.locator('[data-testid="reader-record-outline-mode-switch"]'),
    ).toHaveCount(0);

    const l1 = makeL1HeadingRichSnapshot();
    await loadSnapshot(
      page,
      l1,
      (l1.navigation as { units: Array<{ unit_id: string }> }).units.map(
        (u) => u.unit_id,
      ),
    );
    await expect(rail).toHaveAttribute("data-navigation-mode", "L1");
    await expect(rail).toHaveAttribute("data-has-semantic-outline", "false");
    await expect(rail).toHaveAttribute("aria-label", "阅读定位");
  });

  test("L1 + L2: switch does not lose L1 after return", async ({ page }) => {
    const snapshot = makeL1PlusOutlineSnapshot();
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4", "u5", "u6"]);
    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-navigation-mode", "L1");
    await expect(rail).toHaveAttribute("data-has-semantic-outline", "true");

    await openPanel(page);
    await expect(page.getByText("Chapter One").first()).toBeVisible();
    await page
      .locator('[data-testid="reader-record-outline-mode-semantic"]')
      .click();
    await expect(page.getByText("Semantic Whole")).toBeVisible();
    await page
      .locator('[data-testid="reader-record-outline-mode-deterministic"]')
      .click();
    await expect(page.getByText("Chapter One").first()).toBeVisible();
    await expect(rail).toHaveAttribute("data-navigation-mode", "L1");
  });

  test("hover does not change semantic panel vertical position", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);
    await openPanel(page);
    await page
      .locator('[data-testid="reader-record-outline-mode-semantic"]')
      .click();

    const panel = page.locator('[data-testid="reader-record-navigation-panel"]');
    const top1 = await panel.evaluate((el) => el.getBoundingClientRect().top);
    const ticks = page.locator(
      '[data-testid="reader-record-mini-rail"] [data-navigation-tick-key]',
    );
    await ticks.nth(0).hover();
    await ticks.nth(1).hover();
    const top2 = await panel.evaluate((el) => el.getBoundingClientRect().top);
    expect(Math.abs(top2 - top1)).toBeLessThan(1);
  });

  test("1280x800 Ask docked: semantic panel stays left of ask with gap", async ({
    page,
  }) => {
    // T5.1e contract: real Ask launcher + docked aside — no fake DOM.
    await page.setViewportSize({ width: 1280, height: 800 });
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    const askLauncher = page.locator('button[aria-label="打开 Ask Claread"]');
    await expect(askLauncher).toBeVisible({ timeout: 5_000 });
    await askLauncher.click();

    const askPanel = page.locator("aside.ai-workspace-panel");
    await expect(askPanel).toBeVisible({ timeout: 10_000 });
    await expect(askPanel).toHaveClass(/ai-workspace-panel--layout-docked/, {
      timeout: 5_000,
    });

    await openPanel(page);
    await page
      .locator('[data-testid="reader-record-outline-mode-semantic"]')
      .click();

    const navPanel = page.locator(
      '[data-testid="reader-record-navigation-panel"]',
    );
    await expect(navPanel).not.toHaveClass(/pointer-events-none/);

    const geometry = await page.evaluate(() => {
      const p = document.querySelector<HTMLElement>(
        '[data-testid="reader-record-navigation-panel"]',
      );
      const ask = document.querySelector<HTMLElement>(
        "aside.ai-workspace-panel",
      );
      if (!p) {
        throw new Error("Navigation panel is not rendered");
      }
      if (!ask) {
        throw new Error(
          "Ask sidecar (aside.ai-workspace-panel) is not rendered",
        );
      }
      const pr = p.getBoundingClientRect();
      const ar = ask.getBoundingClientRect();
      return { panelRight: pr.right, askLeft: ar.left };
    });

    expect(geometry.panelRight).toBeGreaterThan(0);
    expect(geometry.askLeft).toBeGreaterThan(0);
    expect(
      geometry.panelRight,
      `panel.right (${geometry.panelRight}) must be at least 12px left of ask.left (${geometry.askLeft})`,
    ).toBeLessThanOrEqual(geometry.askLeft - 12);
  });
});
