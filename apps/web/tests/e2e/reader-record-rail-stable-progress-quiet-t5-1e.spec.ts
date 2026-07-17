/**
 * T5.1e-PUX-Rail-R1 — Stable Reader Navigation & Quiet Progress.
 *
 * Portable Chromium E2E against the REAL ReaderRecordPlateSurface mounted on
 * `/e2e-plate-spike/surface` (env-gated spike harness). Fixtures inject
 * multi-unit navigation snapshots via `window.__spikeSurface.loadSnapshot`.
 *
 * No real API calls, no real phone session, no fixed record UUID, no waiting
 * for real pipeline completion. If the harness is unreachable, the test fails
 * with the actual status/code/reason.
 *
 * Scope: browser contract only. Does NOT change production business logic,
 * projection, polling, event transport, Ask, Surface, or AppearanceProvider.
 */

import { expect, test, type Page } from "@playwright/test";

import { makeL1HeadingRichSnapshot } from "./fixtures/l1-heading-navigation-snapshot";

const HARNESS_URL = "/e2e-plate-spike/surface";
const RECORD_ID = "record_t5_1e_ask_geometry";

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
  await mockReaderAskRoutes(page);
}

async function mockReaderAskRoutes(page: Page) {
  const thread = {
    id: "thread_t5_1e_ask",
    record_id: RECORD_ID,
    title: "Ask Claread",
    is_default: true,
    selected_model: { key: "default", label: "Default" },
    created_at: "2026-07-17T00:00:00Z",
    updated_at: "2026-07-17T00:00:00Z",
  };

  await page.route("**/api/web/reader-ask/**", (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const pathname = url.pathname;

    if (pathname === "/api/web/reader-ask/model-options") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          default_key: "default",
          items: [{ key: "default", label: "Default", is_default: true }],
        }),
      });
      return;
    }

    if (pathname === "/api/web/reader-ask/threads") {
      if (method === "POST") {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(thread),
        });
        return;
      }
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
      return;
    }

    if (pathname.startsWith("/api/web/reader-ask/threads/")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...thread, messages: [] }),
      });
      return;
    }

    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
}

async function waitForHarnessReady(page: Page) {
  const response = await page.goto(HARNESS_URL, {
    waitUntil: "domcontentloaded",
  });
  const status = response?.status() ?? null;
  const statusText = response?.statusText() ?? "no-response";

  if (status === null || status >= 400) {
    const bodyText = await page
      .locator("body")
      .textContent()
      .catch(() => "(body unreadable)");
    throw new Error(
      `Spike harness failed to load: status=${status}, statusText=${statusText}, url=${page.url()}, bodyPreview=${bodyText?.slice(0, 300) ?? ""}`,
    );
  }

  await page.waitForFunction(
    () =>
      (window as unknown as { __spikeSurfaceReady?: boolean })
        .__spikeSurfaceReady === true,
    undefined,
    { timeout: 15_000 },
  );
}

async function injectSnapshot(
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

async function openPanelViaTick(page: Page, tickIndex = 0) {
  const ticks = page.locator(
    '[data-testid="reader-record-mini-rail"] [data-navigation-unit-id]',
  );
  const count = await ticks.count();
  if (count === 0) {
    throw new Error("No navigation ticks rendered");
  }
  await ticks.nth(tickIndex).hover();
  const panel = page.locator('[data-testid="reader-record-navigation-panel"]');
  await expect(panel).not.toHaveClass(/pointer-events-none/, {
    timeout: 5_000,
  });
  return panel;
}

async function openAskPanel(page: Page) {
  const launcher = page.locator('button[aria-label="打开 Ask Claread"]');
  await expect(launcher).toBeVisible({ timeout: 5_000 });
  await launcher.click();

  const askPanel = page.locator("aside.ai-workspace-panel");
  await expect(askPanel).toBeVisible({ timeout: 10_000 });
}

type PanelMeasure = {
  panelY: number;
  panelRight: number;
  panelClassName: string;
  tickCount: number;
};

async function measurePanel(page: Page): Promise<PanelMeasure> {
  return page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>(
      '[data-testid="reader-record-navigation-panel"]',
    );
    const ticks = document.querySelectorAll(
      '[data-testid="reader-record-mini-rail"] [data-navigation-unit-id]',
    );
    const rect = panel?.getBoundingClientRect();
    return {
      panelY: rect?.y ?? NaN,
      panelRight: rect?.right ?? NaN,
      panelClassName: panel?.className ?? "",
      tickCount: ticks.length,
    };
  });
}

type AskGeometry = {
  panelRight: number;
  askLeft: number;
};

async function measureAskGeometry(page: Page): Promise<AskGeometry> {
  return page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>(
      '[data-testid="reader-record-navigation-panel"]',
    );
    const ask = document.querySelector<HTMLElement>("aside.ai-workspace-panel");
    if (!panel) {
      throw new Error("Navigation panel is not rendered");
    }
    if (!ask) {
      throw new Error("Ask sidecar (aside.ai-workspace-panel) is not rendered");
    }
    const panelRect = panel.getBoundingClientRect();
    const askRect = ask.getBoundingClientRect();
    return {
      panelRight: panelRect.right,
      askLeft: askRect.left,
    };
  });
}

test.describe("T5.1e stable rail & quiet progress", () => {
  test.use({
    viewport: { width: 1280, height: 800 },
  });

  test.beforeEach(async ({ page }) => {
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
  });

  test("panel stays at a stable vertical position when hovering top vs bottom tick", async ({
    page,
  }) => {
    const snapshot = makeL1HeadingRichSnapshot({
      baseId: "base_t5_1e_stable",
      generation: 1,
      snapshotId: "snap_t5_1e_stable",
      recordId: "record_t5_1e_stable",
    });

    await injectSnapshot(page, snapshot, [
      "u1",
      "u2",
      "u3",
      "u4",
      "u5",
      "u6",
      "u7",
    ]);

    const ticks = page.locator(
      '[data-testid="reader-record-mini-rail"] [data-navigation-unit-id]',
    );
    const tickCount = await ticks.count();
    expect(
      tickCount,
      "need at least two ticks to compare top vs bottom",
    ).toBeGreaterThanOrEqual(2);

    await openPanelViaTick(page, 0);
    const top = await measurePanel(page);
    expect(top.tickCount).toBe(tickCount);
    expect(
      Number.isFinite(top.panelY),
      "panel must have a measurable bounding box",
    ).toBe(true);

    await openPanelViaTick(page, tickCount - 1);
    const bottom = await measurePanel(page);
    expect(
      Number.isFinite(bottom.panelY),
      "panel must have a measurable bounding box",
    ).toBe(true);

    expect(
      Math.abs(top.panelY - bottom.panelY),
      `panel y changed from ${top.panelY} to ${bottom.panelY} when hovering top vs bottom tick`,
    ).toBeLessThanOrEqual(1);

    expect(bottom.panelClassName).toContain("top-1/2");
    expect(bottom.panelClassName).toContain("-translate-y-1/2");
  });

  test("navigation panel keeps at least a 12px gap to the docked Ask sidecar", async ({
    page,
  }) => {
    const snapshot = makeL1HeadingRichSnapshot({
      baseId: "base_t5_1e_ask",
      generation: 1,
      snapshotId: "snap_t5_1e_ask",
      recordId: RECORD_ID,
    });

    await injectSnapshot(page, snapshot, [
      "u1",
      "u2",
      "u3",
      "u4",
      "u5",
      "u6",
      "u7",
    ]);

    await openAskPanel(page);

    const askPanel = page.locator("aside.ai-workspace-panel");
    await expect(askPanel).toHaveClass(/ai-workspace-panel--layout-docked/, {
      timeout: 5_000,
    });

    await openPanelViaTick(page, 0);
    const geometry = await measureAskGeometry(page);

    expect(
      geometry.panelRight,
      "navigation panel must have a measurable right edge",
    ).toBeGreaterThan(0);
    expect(
      geometry.askLeft,
      "Ask sidecar must have a measurable left edge",
    ).toBeGreaterThan(0);
    expect(
      geometry.panelRight,
      `panel.right (${geometry.panelRight}) must be at least 12px left of ask.left (${geometry.askLeft})`,
    ).toBeLessThanOrEqual(geometry.askLeft - 12);
  });
});
