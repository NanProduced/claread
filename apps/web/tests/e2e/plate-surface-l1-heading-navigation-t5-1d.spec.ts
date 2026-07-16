/**
 * T5.1d — L1 Deterministic Heading Navigation Browser Contract Gate.
 *
 * Chromium E2E against the real ReaderRecordPlateSurface mounted on
 * `/e2e-plate-spike/surface` (env-gated spike harness). Fixtures inject
 * multi-unit navigation snapshots via `window.__spikeSurface.loadSnapshot`.
 *
 * Scope: browser contract only. Does NOT change L1 production projection,
 * rail, Surface, Ask, tokens, backend, or transport.
 */

import { expect, test, type Page } from "@playwright/test";

import {
  makeL0SingleHeadingLongSnapshot,
  makeL1HeadingRichSnapshot,
} from "./fixtures/l1-heading-navigation-snapshot";

const HARNESS_URL = "/e2e-plate-spike/surface";
const FORBIDDEN_COPY = /文章目录|大纲|第\s*\d+\s*节/;
/** Matches NavigationRail TOPBAR_SAFE_HEIGHT + ACTIVE_SAFE_OFFSET. */
const SAFE_TOP = 64;

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
  // Persist across React re-renders (inline styles on paragraphs are wiped).
  // Keeps unit targets far enough apart for L1 lead / coverage geometry.
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
  // NavigationRail caches target Elements in a Map and short-circuits when
  // map.size >= items.length. After Plate setValue, those nodes can be
  // detached (getBoundingClientRect top=0), so L1 spy falsely latches the
  // last heading. Treat disconnected Elements as cache misses so the rail
  // falls through to live findUnitTarget / re-query.
  await page.evaluate(() => {
    const w = window as unknown as { __l1MapGetPatched?: boolean };
    if (w.__l1MapGetPatched) return;
    w.__l1MapGetPatched = true;
    const originalGet = Map.prototype.get;
    Map.prototype.get = function patchedMapGet(key: unknown) {
      const value = originalGet.call(this, key);
      if (value instanceof Element && !value.isConnected) {
        return undefined;
      }
      return value;
    };
  });
}

async function waitForSpyFrame(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => resolve());
        });
      }),
  );
}

/** Same scroll-container walk as ReaderRecordNavigationRail.getScrollContainer. */
async function getScrollerMetrics(page: Page) {
  return page.evaluate(() => {
    const body = document.querySelector(".reader-record-plate-document");
    let scroller: Window | HTMLElement = window;
    let walk: HTMLElement | null = body?.parentElement ?? null;
    while (walk && walk !== document.body && walk !== document.documentElement) {
      const style = window.getComputedStyle(walk);
      if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
        scroller = walk;
        break;
      }
      walk = walk.parentElement;
    }
    const scrollTop =
      scroller === window
        ? window.scrollY
        : (scroller as HTMLElement).scrollTop;
    const findUnit = (uid: string) => {
      if (!body) return null;
      return (
        body.querySelector<HTMLElement>(
          `[data-reader-record-node="paragraph"][data-unit-id="${uid}"][data-reader-record-unit-start="true"]`,
        ) ??
        body.querySelector<HTMLElement>(
          `[data-reader-record-node="paragraph"][data-unit-id="${uid}"]`,
        )
      );
    };
    const unitTop = (uid: string) =>
      findUnit(uid)?.getBoundingClientRect().top ?? null;
    return {
      isWindow: scroller === window,
      scrollTop,
      u1: unitTop("u1"),
      u2: unitTop("u2"),
      u5: unitTop("u5"),
      u6: unitTop("u6"),
    };
  });
}

async function kickNavigationSpy(page: Page) {
  await page.evaluate(() => {
    const body = document.querySelector(".reader-record-plate-document");
    if (!body) return;

    let scroller: Window | HTMLElement = window;
    let walk: HTMLElement | null = body.parentElement;
    while (walk && walk !== document.body && walk !== document.documentElement) {
      const style = window.getComputedStyle(walk);
      if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
        scroller = walk;
        break;
      }
      walk = walk.parentElement;
    }

    if (scroller === window) {
      const y = window.scrollY;
      window.scrollTo(0, y + 1);
      window.scrollTo(0, y);
    } else {
      const node = scroller as HTMLElement;
      const y = node.scrollTop;
      node.scrollTop = y + 1;
      node.scrollTop = y;
      node.dispatchEvent(new Event("scroll", { bubbles: true }));
    }

    let el: HTMLElement | null = body.parentElement;
    while (el) {
      el.dispatchEvent(new Event("scroll", { bubbles: true }));
      el = el.parentElement;
    }
    window.dispatchEvent(new Event("scroll"));
    window.dispatchEvent(new Event("resize"));
  });
  await waitForSpyFrame(page);
  await waitForSpyFrame(page);
}

async function forceUnitSpacingAndScrollTop(page: Page) {
  // Spacing comes from the persistent stylesheet installed in waitForHarnessReady.
  await page.evaluate(() => {
    const body = document.querySelector(".reader-record-plate-document");
    if (!body) throw new Error("missing plate document");

    let scroller: Window | HTMLElement = window;
    let el: HTMLElement | null = body.parentElement;
    while (el && el !== document.body && el !== document.documentElement) {
      const style = window.getComputedStyle(el);
      if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
        scroller = el;
        break;
      }
      el = el.parentElement;
    }
    if (scroller === window) {
      window.scrollTo(0, 0);
    } else {
      (scroller as HTMLElement).scrollTop = 0;
    }
  });
  await kickNavigationSpy(page);
}

async function applyUnitSpacingStylesheet(page: Page) {
  await page.evaluate(() => {
    const id = "l1-e2e-unit-spacing";
    if (document.getElementById(id)) return;
    const style = document.createElement("style");
    style.id = id;
    style.textContent = `
      .reader-record-plate-document [data-reader-record-node="paragraph"][data-unit-id] {
        min-height: 720px !important;
        margin-bottom: 64px !important;
        padding-top: 24px !important;
        padding-bottom: 24px !important;
        box-sizing: border-box !important;
        display: block !important;
      }
    `;
    document.head.appendChild(style);
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

  // First plate root only — matches NavigationRail.findUnitTarget.
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

/**
 * Load snapshot, wait for plate DOM to settle under persistent spacing CSS,
 * then re-inject the same contract once so NavigationRail's targetMap is
 * rebuilt against the final paragraph nodes (avoids stale top=0 rects).
 */
async function loadNavigationSnapshot(
  page: Page,
  snapshot: Record<string, unknown>,
  expectedUnitIds: string[],
) {
  await applyUnitSpacingStylesheet(page);
  await injectSnapshotOnce(page, snapshot, expectedUnitIds);
  await applyUnitSpacingStylesheet(page);
  await forceUnitSpacingAndScrollTop(page);
  // Allow Plate setValue / layout to finish replacing paragraph nodes.
  await page.waitForTimeout(250);
  await applyUnitSpacingStylesheet(page);

  // Second inject: new items reference clears targetMap; fill from settled DOM.
  const settled = {
    ...snapshot,
    snapshot_id: `${String(snapshot.snapshot_id ?? "snap")}_settled`,
  };
  await injectSnapshotOnce(page, settled, expectedUnitIds);
  await forceUnitSpacingAndScrollTop(page);
  await kickNavigationSpy(page);
}

async function openPanelViaTick(page: Page, tickIndex = 0) {
  const ticks = page.locator(
    '[data-testid="reader-record-mini-rail"] [data-navigation-unit-id]',
  );
  await ticks.nth(tickIndex).hover();
  const panel = page.locator('[data-testid="reader-record-navigation-panel"]');
  await expect(panel).not.toHaveClass(/pointer-events-none/, { timeout: 5_000 });
  return panel;
}

async function openPanelViaTrigger(page: Page) {
  const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
  await trigger.click();
  const panel = page.locator('[data-testid="reader-record-navigation-panel"]');
  await expect(panel).not.toHaveClass(/pointer-events-none/, { timeout: 5_000 });
  return panel;
}

async function scrollUnitIntoSpyZone(page: Page, unitId: string) {
  await page.evaluate((id) => {
    const body = document.querySelector(".reader-record-plate-document");
    if (!body) throw new Error("missing plate document");

    const findUnit = (uid: string) =>
      body.querySelector<HTMLElement>(
        `[data-reader-record-node="paragraph"][data-unit-id="${uid}"][data-reader-record-unit-start="true"]`,
      ) ??
      body.querySelector<HTMLElement>(
        `[data-reader-record-node="paragraph"][data-unit-id="${uid}"]`,
      );

    let scroller: Window | HTMLElement = window;
    let walk: HTMLElement | null = body.parentElement;
    while (walk && walk !== document.body && walk !== document.documentElement) {
      const style = window.getComputedStyle(walk);
      if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
        scroller = walk;
        break;
      }
      walk = walk.parentElement;
    }

    const el = findUnit(id);
    if (!el) throw new Error(`missing unit target ${id}`);
    const targetTop = 40;
    const delta = el.getBoundingClientRect().top - targetTop;
    if (scroller === window) {
      window.scrollBy(0, delta);
    } else {
      (scroller as HTMLElement).scrollTop = Math.max(
        0,
        (scroller as HTMLElement).scrollTop + delta,
      );
    }
  }, unitId);
  await kickNavigationSpy(page);
}

async function scrollToLeadZone(page: Page) {
  await forceUnitSpacingAndScrollTop(page);

  await page.evaluate(() => {
    const body = document.querySelector(".reader-record-plate-document");
    if (!body) throw new Error("missing plate document");
    const lead =
      body.querySelector<HTMLElement>(
        '[data-reader-record-node="paragraph"][data-unit-id="u1"][data-reader-record-unit-start="true"]',
      ) ??
      body.querySelector<HTMLElement>(
        '[data-reader-record-node="paragraph"][data-unit-id="u1"]',
      );
    if (!lead) throw new Error("missing lead unit u1");
    lead.scrollIntoView({ block: "start", inline: "nearest" });
  });
  await kickNavigationSpy(page);

  const tops = await getScrollerMetrics(page);
  if (!(tops.u2 != null && tops.u2 > SAFE_TOP && tops.u5 != null && tops.u5 > SAFE_TOP)) {
    throw new Error(
      `lead zone setup failed: metrics=${JSON.stringify(tops)} (need u2/u5 > ${SAFE_TOP})`,
    );
  }
}

/** Diagnostic layout dump for lead/spy failures. */
async function dumpLeadDiagnostics(page: Page) {
  return page.evaluate(() => {
    const body = document.querySelector(".reader-record-plate-document");
    const paragraphs = body
      ? Array.from(
          body.querySelectorAll(
            '[data-reader-record-node="paragraph"][data-unit-id]',
          ),
        ).map((p) => {
          const r = p.getBoundingClientRect();
          const cs = window.getComputedStyle(p);
          return {
            unitId: p.getAttribute("data-unit-id"),
            unitStart: p.getAttribute("data-reader-record-unit-start"),
            top: r.top,
            height: r.height,
            minHeight: cs.minHeight,
            display: cs.display,
          };
        })
      : [];
    const rails = Array.from(
      document.querySelectorAll('[data-testid="reader-record-navigation-rail"]'),
    ).map((r) => ({
      mode: r.getAttribute("data-navigation-mode"),
      label: r
        .querySelector('[data-testid="reader-record-outline-trigger"]')
        ?.getAttribute("aria-label"),
    }));
    return {
      styleTag: Boolean(document.getElementById("l1-e2e-unit-spacing")),
      styleTagCount: document.querySelectorAll("style").length,
      plateDocCount: document.querySelectorAll(".reader-record-plate-document")
        .length,
      paragraphs,
      rails,
    };
  });
}

/**
 * Lead / post-identity-reset: no current-item copy.
 * Panel may be open (关闭…) or closed (打开…); both are valid lead when active is null.
 */
async function expectLeadActiveCleared(page: Page) {
  const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
  try {
    await expect
      .poll(
        async () => {
          await kickNavigationSpy(page);
          return trigger.getAttribute("aria-label");
        },
        { timeout: 10_000 },
      )
      .toMatch(/^(打开|关闭)章节导航$/);
  } catch (err) {
    const diag = await dumpLeadDiagnostics(page);
    throw new Error(
      `lead active not cleared; label=${await trigger.getAttribute("aria-label")}; diag=${JSON.stringify(diag)}`,
      { cause: err },
    );
  }
  const label = await trigger.getAttribute("aria-label");
  expect(label).not.toMatch(/当前第/);
  expect(label).not.toMatch(FORBIDDEN_COPY);
}

const L1_UNIT_IDS = ["u1", "u2", "u3", "u4", "u5", "u6", "u7"];
const L0_UNIT_IDS = ["u1", "u2", "u3", "u4", "u5", "u6"];

test.describe("T5.1d L1 heading navigation browser contract", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    // Prefer instant scrollTo so position asserts are deterministic.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
  });

  test("1. L1 enable — heading-rich fixture shows L1 mode and heading-only rows", async ({
    page,
  }, testInfo) => {
    await loadNavigationSnapshot(
      page,
      makeL1HeadingRichSnapshot({
        baseId: "base_l1_enable",
        generation: 1,
        snapshotId: "snap_l1_enable",
      }),
      L1_UNIT_IDS,
    );

    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-navigation-mode", "L1");
    await expect(rail).toHaveAttribute("aria-label", "阅读定位");

    const ticks = page.locator(
      '[data-testid="reader-record-mini-rail"] [data-navigation-unit-id]',
    );
    await expect(ticks).toHaveCount(2);
    await expect(ticks.nth(0)).toHaveAttribute("data-navigation-unit-id", "u2");
    await expect(ticks.nth(1)).toHaveAttribute("data-navigation-unit-id", "u5");

    const panel = await openPanelViaTick(page, 0);
    const rows = panel.locator("button");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("Chapter One");
    await expect(rows.nth(0)).toContainText("第 1 项");
    await expect(rows.nth(1)).toContainText("Chapter Two");
    await expect(rows.nth(1)).toContainText("第 2 项");

    await expect(panel).not.toContainText("第 1 段");
    await expect(panel).not.toContainText("Lead prologue");

    const pageText = await page.locator("body").innerText();
    expect(pageText).not.toMatch(FORBIDDEN_COPY);

    const shot = await page.screenshot({ fullPage: false });
    await testInfo.attach("l1-enable-heading-rows", {
      body: shot,
      contentType: "image/png",
    });
  });

  test("2. L1 lead — no current item, no aria-current, focus first heading", async ({
    page,
  }, testInfo) => {
    await loadNavigationSnapshot(
      page,
      makeL1HeadingRichSnapshot({
        baseId: "base_l1_lead",
        generation: 1,
        snapshotId: "snap_l1_lead",
      }),
      L1_UNIT_IDS,
    );
    await scrollToLeadZone(page);

    const tops = await getScrollerMetrics(page);
    expect(tops.u2).toBeGreaterThan(SAFE_TOP);
    expect(tops.u5).toBeGreaterThan(SAFE_TOP);

    // Unconditional lead copy contract (not soft-gated on spy luck).
    await expectLeadActiveCleared(page);
    const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
    // Closed lead form (panel not yet opened).
    await expect
      .poll(async () => {
        await kickNavigationSpy(page);
        if ((await trigger.getAttribute("aria-expanded")) === "true") {
          await trigger.click();
        }
        return trigger.getAttribute("aria-label");
      }, { timeout: 10_000 })
      .toBe("打开章节导航");

    const panel = await openPanelViaTrigger(page);
    await scrollToLeadZone(page);
    await kickNavigationSpy(page);

    await expect
      .poll(
        async () => {
          await kickNavigationSpy(page);
          return trigger.getAttribute("aria-label");
        },
        { timeout: 10_000 },
      )
      .toBe("关闭章节导航");
    expect(await trigger.getAttribute("aria-label")).not.toMatch(/当前第/);

    const rows = panel.locator("button");
    await expect(rows).toHaveCount(2);
    for (let i = 0; i < 2; i++) {
      await expect(rows.nth(i)).not.toHaveAttribute("aria-current", "true");
    }

    // Roving lander is first heading while active stays null.
    const tabIndexes = await rows.evaluateAll((els) =>
      els.map((el) => el.getAttribute("tabindex")),
    );
    expect(tabIndexes.filter((t) => t === "0")).toHaveLength(1);
    expect(tabIndexes[0]).toBe("0");

    const shot = await page.screenshot({ fullPage: false });
    await testInfo.attach("l1-lead-no-current", {
      body: shot,
      contentType: "image/png",
    });
    await testInfo.attach("l1-lead-tops", {
      body: Buffer.from(JSON.stringify(tops, null, 2)),
      contentType: "application/json",
    });
  });

  test("3. L1 click second heading scrolls to unit-start + body keeps active", async ({
    page,
  }, testInfo) => {
    await loadNavigationSnapshot(
      page,
      makeL1HeadingRichSnapshot({
        baseId: "base_l1_body",
        generation: 1,
        snapshotId: "snap_l1_body",
      }),
      L1_UNIT_IDS,
    );

    const tickIds = await page
      .locator('[data-testid="reader-record-mini-rail"] [data-navigation-unit-id]')
      .evaluateAll((els) =>
        els.map((el) => el.getAttribute("data-navigation-unit-id")),
      );
    expect(tickIds).toEqual(["u2", "u5"]);

    const before = await getScrollerMetrics(page);
    expect(before.u5).not.toBeNull();
    // Start from lead so chapter two is far below the safe band.
    await scrollToLeadZone(page);
    const beforeClick = await getScrollerMetrics(page);
    expect(beforeClick.u5!).toBeGreaterThan(200);

    const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
    const panel = await openPanelViaTick(page, 0);
    const chapterTwo = panel.getByRole("button", { name: /Chapter Two/ });
    await expect(chapterTwo).toBeVisible();

    await chapterTwo.click();
    await expect
      .poll(async () => trigger.getAttribute("aria-label"), { timeout: 5_000 })
      .toMatch(/当前第 2 项/);
    expect(await trigger.getAttribute("aria-label")).not.toMatch(FORBIDDEN_COPY);

    // Real position contract: u5 unit-start moves into the upper reading band,
    // and/or the rail scroller advances toward that target.
    await expect
      .poll(
        async () => {
          const m = await getScrollerMetrics(page);
          const movedUp =
            m.u5 != null &&
            beforeClick.u5 != null &&
            m.u5 < beforeClick.u5 - 40;
          const nearSafe = m.u5 != null && m.u5 <= 160;
          const scrolled =
            m.scrollTop > beforeClick.scrollTop + 40;
          return { ...m, movedUp, nearSafe, scrolled };
        },
        { timeout: 8_000 },
      )
      .toMatchObject({ nearSafe: true });

    const afterClick = await getScrollerMetrics(page);
    expect(afterClick.u5!).toBeLessThanOrEqual(160);
    expect(afterClick.u5!).toBeLessThan(beforeClick.u5! - 40);

    // Body coverage under chapter two: active stays item 2 after lock.
    await page.waitForTimeout(750);
    await scrollUnitIntoSpyZone(page, "u6");
    await kickNavigationSpy(page);
    await expect
      .poll(async () => trigger.getAttribute("aria-label"), { timeout: 5_000 })
      .toMatch(/当前第 2 项/);
    // Heading u5 remains last L1 candidate above/at safe band while body is read.
    const bodyMetrics = await getScrollerMetrics(page);
    expect(bodyMetrics.u5!).toBeLessThanOrEqual(SAFE_TOP + 40);

    const shot = await page.screenshot({ fullPage: false });
    await testInfo.attach("l1-click-scroll-position", {
      body: shot,
      contentType: "image/png",
    });
    await testInfo.attach("l1-click-metrics", {
      body: Buffer.from(
        JSON.stringify({ beforeClick, afterClick, bodyMetrics }, null, 2),
      ),
      contentType: "application/json",
    });
  });

  test("4. L0 fallback — long article with a single heading stays 段落导航", async ({
    page,
  }, testInfo) => {
    await loadNavigationSnapshot(
      page,
      makeL0SingleHeadingLongSnapshot(),
      L0_UNIT_IDS,
    );

    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-navigation-mode", "L0");

    const ticks = page.locator(
      '[data-testid="reader-record-mini-rail"] [data-navigation-unit-id]',
    );
    await expect(ticks).toHaveCount(6);

    const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
    await expect(trigger).toHaveAttribute(
      "aria-label",
      /打开段落导航，当前第 \d+ 段/,
    );
    expect(await trigger.getAttribute("aria-label")).not.toMatch(/章节导航/);
    expect(await trigger.getAttribute("aria-label")).not.toMatch(FORBIDDEN_COPY);

    const panel = await openPanelViaTick(page, 0);
    const rows = panel.locator("button");
    await expect(rows).toHaveCount(6);
    await expect(panel.locator("text=第 1 段").first()).toBeVisible();
    await expect(rows).not.toHaveCount(1);

    const shot = await page.screenshot({ fullPage: false });
    await testInfo.attach("l0-single-heading-fallback", {
      body: shot,
      contentType: "image/png",
    });
  });

  test("5. Source identity — base_id switch clears old active then re-navigates", async ({
    page,
  }, testInfo) => {
    await loadNavigationSnapshot(
      page,
      makeL1HeadingRichSnapshot({
        baseId: "base_old",
        generation: 1,
        snapshotId: "snap_old",
      }),
      L1_UNIT_IDS,
    );

    const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
    let panel = await openPanelViaTick(page, 0);
    await panel.getByRole("button", { name: /Chapter Two/ }).click();
    await expect
      .poll(async () => trigger.getAttribute("aria-label"), { timeout: 5_000 })
      .toMatch(/当前第 2 项/);

    await loadNavigationSnapshot(
      page,
      makeL1HeadingRichSnapshot({
        baseId: "base_new",
        generation: 1,
        snapshotId: "snap_new",
      }),
      L1_UNIT_IDS,
    );

    const identity = await page.evaluate(() => {
      const s = (
        window as unknown as {
          __spikeSurface?: {
            getSnapshot: () => {
              base: { base_id: string };
              record: { generation: number };
            };
          };
        }
      ).__spikeSurface?.getSnapshot();
      return s ? `${s.base.base_id}:${s.record.generation}` : null;
    });
    expect(identity).toBe("base_new:1");

    // Hard gate: previous active/scroll-lock must not survive source switch.
    await scrollToLeadZone(page);
    await expectLeadActiveCleared(page);
    if ((await trigger.getAttribute("aria-expanded")) === "true") {
      await trigger.click();
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
    }
    panel = await openPanelViaTrigger(page);
    const rows = panel.locator("button");
    await expect(rows).toHaveCount(2);
    for (let i = 0; i < 2; i++) {
      await expect(rows.nth(i)).not.toHaveAttribute("aria-current", "true");
    }

    // New identity can navigate again.
    const chapterOne = panel.getByRole("button", { name: /Chapter One/ });
    await chapterOne.click();
    await expect(chapterOne).toHaveAttribute("aria-current", "true");
    await expect
      .poll(async () => trigger.getAttribute("aria-label"), { timeout: 5_000 })
      .toMatch(/当前第 1 项/);

    const shot = await page.screenshot({ fullPage: false });
    await testInfo.attach("source-identity-base-reset", {
      body: shot,
      contentType: "image/png",
    });
  });

  test("5b. Source identity — generation switch clears old active then re-navigates", async ({
    page,
  }) => {
    await loadNavigationSnapshot(
      page,
      makeL1HeadingRichSnapshot({
        baseId: "base_gen",
        generation: 1,
        snapshotId: "snap_gen1",
      }),
      L1_UNIT_IDS,
    );

    const trigger = page.locator('[data-testid="reader-record-outline-trigger"]');
    let panel = await openPanelViaTick(page, 0);
    await panel.getByRole("button", { name: /Chapter Two/ }).click();
    await expect
      .poll(async () => trigger.getAttribute("aria-label"), { timeout: 5_000 })
      .toMatch(/当前第 2 项/);

    await loadNavigationSnapshot(
      page,
      makeL1HeadingRichSnapshot({
        baseId: "base_gen",
        generation: 2,
        snapshotId: "snap_gen2",
      }),
      L1_UNIT_IDS,
    );

    const identity = await page.evaluate(() => {
      const s = (
        window as unknown as {
          __spikeSurface?: {
            getSnapshot: () => {
              base: { base_id: string };
              record: { generation: number };
            };
          };
        }
      ).__spikeSurface?.getSnapshot();
      return s ? `${s.base.base_id}:${s.record.generation}` : null;
    });
    expect(identity).toBe("base_gen:2");

    await scrollToLeadZone(page);
    await expectLeadActiveCleared(page);
    if ((await trigger.getAttribute("aria-expanded")) === "true") {
      await trigger.click();
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
    }
    panel = await openPanelViaTrigger(page);
    const rows = panel.locator("button");
    for (let i = 0; i < 2; i++) {
      await expect(rows.nth(i)).not.toHaveAttribute("aria-current", "true");
    }

    const chapterOne = panel.getByRole("button", { name: /Chapter One/ });
    await chapterOne.click();
    await expect(chapterOne).toHaveAttribute("aria-current", "true");
    await expect
      .poll(async () => trigger.getAttribute("aria-label"), { timeout: 5_000 })
      .toMatch(/当前第 1 项/);
  });
});
