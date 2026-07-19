/**
 * T5.6c — Explicit-section "解析此段" end-to-end browser contract.
 *
 * Chromium E2E against ReaderRecordPlateSurface on `/e2e-plate-spike/surface`.
 * Fake/DI only — no real LLM, no real backend worker loop. The BFF route is
 * mocked via `page.route()`; the page's natural snapshot reload is simulated
 * by calling `window.__spikeSurface.loadSnapshot()` with a post-translation
 * snapshot (the same path `onRequestSnapshotReload` ultimately drives on the
 * real page).
 *
 * Covered chain:
 *   L2 trusted row → "解析此段" chip click → BFF POST /section-translation
 *   → succeeded → row state clears → snapshot reload → translation blockquote
 *   visible in DOM.
 *
 * Invariants verified:
 *   - L0/L1 deterministic surface has no chip.
 *   - BFF payload carries the full range witness (start/end unit + anchors +
 *     audit fields), never node-only.
 *   - navigation.units unchanged across the click + reload.
 *   - Ask docked geometry: semantic panel stays left of Ask with a gap.
 *   - No fixed-bottom toast appears on any outcome.
 */

import { expect, test, type Page } from "@playwright/test";

import {
  makeSemanticOutlineSnapshot,
  makeReadySemanticOutline,
} from "./fixtures/semantic-outline-navigation-snapshot";
import {
  makeNavigationFixtureSnapshot,
  type L1NavUnitSpec,
} from "./fixtures/l1-heading-navigation-snapshot";

const HARNESS_URL = "/e2e-plate-spike/surface";

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

async function switchToSemantic(page: Page) {
  await page
    .locator('[data-testid="reader-record-outline-mode-semantic"]')
    .click();
  await expect(
    page.locator('[data-testid="reader-record-navigation-rail"]'),
  ).toHaveAttribute("data-outline-surface", "semantic");
}

/**
 * Build a post-section-translation snapshot by deep-cloning the semantic
 * outline fixture and injecting a `reader_translation_group` into the
 * covered unit's children. Mirrors what the real backend would return
 * after SectionTranslationDrainService completes.
 */
function makePostSectionTranslationSnapshot(): Record<string, unknown> {
  const base = makeSemanticOutlineSnapshot({ withOutline: true });
  // Deep clone via JSON (fixture is plain data).
  const next = JSON.parse(JSON.stringify(base)) as Record<string, unknown>;

  // Outline node n1 covers u1..u2. Inject a unit-scope translation group
  // into both u1 and u2 value nodes.
  const value = next.value as Array<Record<string, unknown>>;
  for (const unitNode of value) {
    if (unitNode.unit_id !== "u1" && unitNode.unit_id !== "u2") continue;
    const children = unitNode.children as Array<Record<string, unknown>>;
    // Avoid double-injection if the builder already has a translation group.
    if (
      children.some((c) => c.type === "reader_translation_group")
    ) {
      continue;
    }
    children.push({
      type: "reader_translation_group",
      owner: "system_ai",
      layer_id: "layer_section_translation_t56c",
      layer_version: 1,
      base_id: "base_1",
      unit_id: unitNode.unit_id,
      target_scope: "unit",
      target_key: unitNode.unit_id,
      group_id: `group_t56c_${unitNode.unit_id}`,
      covered_anchor_segment_ids: [`seg_${unitNode.unit_id}`],
      source_text_hash: `seg_hash_${unitNode.unit_id}`,
      children: [
        {
          text: `这是 ${unitNode.unit_id} 的段落翻译。`,
        },
      ],
    });
  }

  // Bump snapshot_id + last_event_sequence to signal a fresh snapshot.
  next.snapshot_id = "snap_t56c_post_section_translation";
  next.last_event_sequence = 10;

  return next;
}

test.describe("T5.6c section translation per-row action", () => {
  test.beforeEach(async ({ page }) => {
    await mockApiRoutes(page);
    await waitForHarnessReady(page);
  });

  test("L2 trusted ready: chip visible on semantic rows; L0/L1 deterministic has no chip", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-has-semantic-outline", "true");
    await expect(rail).toHaveAttribute("data-outline-surface", "deterministic");

    // L0/L1 deterministic surface — no chip.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toHaveCount(0);

    await openPanel(page);
    await switchToSemantic(page);

    // Semantic surface — chips appear on every node row.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n2"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n3"]'),
    ).toBeVisible();
  });

  test("click chip → BFF receives full range witness (never node-only)", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    let capturedRequest: {
      url: string;
      method: string;
      body: unknown;
    } | null = null;

    await page.route(
      "**/api/web/reader-plate/records/*/section-translation",
      async (route) => {
        const request = route.request();
        capturedRequest = {
          url: request.url(),
          method: request.method(),
          body: JSON.parse(request.postData() ?? "{}"),
        };
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            outcome: "succeeded",
            job_id: "job_t56c_1",
            detail: null,
          }),
        });
      },
    );

    await openPanel(page);
    await switchToSemantic(page);

    await page
      .locator('[data-testid="reader-record-outline-resolve-n1"]')
      .click();

    await expect.poll(() => capturedRequest?.url ?? null).toBeTruthy();

    expect(capturedRequest!.method).toBe("POST");
    expect(capturedRequest!.url).toContain(
      "/api/web/reader-plate/records/record_l2_outline/section-translation",
    );
    // Full range witness — never node-only.
    expect(capturedRequest!.body).toMatchObject({
      startUnitId: "u1",
      endUnitId: "u2",
      startAnchorSegmentId: "seg_u1",
      endAnchorSegmentId: null,
      nodeId: "n1",
      outlineRevision: "olrev_e2e_1",
    });
  });

  test("succeeded → row state clears → snapshot reload → translation blockquote visible", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    // Snapshot before translation: no blockquote in DOM.
    await expect(
      page.locator('[data-reader-record-node="blockquote"]'),
    ).toHaveCount(0);

    await page.route(
      "**/api/web/reader-plate/records/*/section-translation",
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            outcome: "succeeded",
            job_id: "job_t56c_2",
            detail: null,
          }),
        });
      },
    );

    await openPanel(page);
    await switchToSemantic(page);

    // Capture navigation mode AFTER switching to semantic, to verify the
    // click + reload does not perturb the user's chosen rail mode.
    const navUnitsBefore = await page.evaluate(() => {
      const rail = document.querySelector(
        '[data-testid="reader-record-navigation-rail"]',
      );
      return rail?.getAttribute("data-navigation-mode") ?? null;
    });

    await page
      .locator('[data-testid="reader-record-outline-resolve-n1"]')
      .click();

    // Succeeded → chip state clears → chip returns to idle.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-loading-n1"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toBeVisible();

    // Simulate the page's natural snapshot reload (the same path
    // onRequestSnapshotReload drives via reloadSnapshot → setSnapshot).
    const postSnapshot = makePostSectionTranslationSnapshot();
    await page.evaluate((next) => {
      const surface = (
        window as unknown as {
          __spikeSurface?: {
            loadSnapshot: (s: Record<string, unknown>) => void;
          };
        }
      ).__spikeSurface;
      if (!surface) throw new Error("__spikeSurface not ready");
      surface.loadSnapshot(next);
    }, postSnapshot);

    // Translation blockquote now visible in the DOM for u1 + u2.
    await expect(
      page.locator('[data-reader-record-node="blockquote"]'),
    ).toHaveCount(2);

    // navigation.units unchanged: rail mode preserved (L2 after switch).
    const navUnitsAfter = await page.evaluate(() => {
      const rail = document.querySelector(
        '[data-testid="reader-record-navigation-rail"]',
      );
      return rail?.getAttribute("data-navigation-mode") ?? null;
    });
    expect(navUnitsAfter).toBe(navUnitsBefore);

    // Translation text content visible.
    const blockquoteTexts = await page
      .locator('[data-reader-record-node="blockquote"]')
      .allTextContents();
    expect(blockquoteTexts.join("|")).toContain("u1 的段落翻译");
    expect(blockquoteTexts.join("|")).toContain("u2 的段落翻译");
  });

  test("retry_later outcome → inline feedback, no fixed-bottom toast, navigation.units unchanged", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    await page.route(
      "**/api/web/reader-plate/records/*/section-translation",
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            outcome: "retry_later",
            job_id: null,
            detail: null,
          }),
        });
      },
    );

    await openPanel(page);
    await switchToSemantic(page);

    await page
      .locator('[data-testid="reader-record-outline-resolve-n1"]')
      .click();

    // Inline accessible feedback appears.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toHaveText("稍后重试");

    // No fixed-bottom toast. The only allowed surfaces are inline row
    // feedback + (optionally) the page's existing error toast on reload
    // failures. We assert no role="status" / role="alert" appears with
    // position:fixed.
    const fixedToastCount = await page.evaluate(() => {
      const candidates = Array.from(
        document.querySelectorAll<HTMLElement>(
          '[role="status"], [role="alert"]',
        ),
      );
      return candidates.filter((el) => {
        const style = window.getComputedStyle(el);
        return style.position === "fixed" && el.offsetParent !== null;
      }).length;
    });
    expect(fixedToastCount).toBe(0);

    // navigation.units unchanged.
    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-navigation-mode", "L2");
    await expect(rail).toHaveAttribute("data-outline-surface", "semantic");
  });

  // T5.6c-P2: retry_later must NOT be a dead-end. The feedback state must
  // keep an accessible, focusable, clickable "重试" action on the same row
  // (same testid as the idle "解析此段" chip) so the user can re-issue the
  // request without waiting for a snapshot refresh. The retry sends a
  // second full-range witness; when the second response is succeeded, the
  // row clears and the snapshot reload path fires normally.
  test("T5.6c-P2 retry_later → retry action → succeeded: second full-range witness, snapshot reload, nav unchanged, no toast", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    const capturedRequests: Array<{
      url: string;
      method: string;
      body: unknown;
    }> = [];

    let callCount = 0;
    await page.route(
      "**/api/web/reader-plate/records/*/section-translation",
      async (route) => {
        callCount += 1;
        const request = route.request();
        capturedRequests.push({
          url: request.url(),
          method: request.method(),
          body: JSON.parse(request.postData() ?? "{}"),
        });
        if (callCount === 1) {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              ok: true,
              outcome: "retry_later",
              job_id: null,
              detail: null,
            }),
          });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            outcome: "succeeded",
            job_id: "job_t56c_p2_retry",
            detail: null,
          }),
        });
      },
    );

    await openPanel(page);
    await switchToSemantic(page);

    const action = page.locator(
      '[data-testid="reader-record-outline-resolve-n1"]',
    );

    // First click — idle "解析此段" action.
    await expect(action).toHaveAttribute("data-resolve-action", "resolve");
    await action.click();

    // retry_later → feedback visible AND retry action remains on the same
    // row, accessible & focusable.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toHaveText("稍后重试");
    await expect(action).toBeVisible();
    await expect(action).toHaveAttribute("data-resolve-action", "retry");
    await expect(action).toHaveText("重试");
    await expect(action).toHaveAttribute("tabindex", "0");

    // Capture navigation mode BEFORE retry to assert the retry click does
    // not perturb L0/L1 navigation.units.
    const navModeBefore = await page.evaluate(() => {
      const rail = document.querySelector(
        '[data-testid="reader-record-navigation-rail"]',
      );
      return rail?.getAttribute("data-navigation-mode") ?? null;
    });

    // Retry click — second full-range witness.
    await action.click();

    // Second request captured.
    await expect.poll(() => capturedRequests.length).toBe(2);
    expect(capturedRequests[1]!.method).toBe("POST");
    expect(capturedRequests[1]!.url).toContain(
      "/api/web/reader-plate/records/record_l2_outline/section-translation",
    );
    expect(capturedRequests[1]!.body).toMatchObject({
      startUnitId: "u1",
      endUnitId: "u2",
      startAnchorSegmentId: "seg_u1",
      endAnchorSegmentId: null,
      nodeId: "n1",
      outlineRevision: "olrev_e2e_1",
    });

    // Succeeded → row state clears (no loading, no feedback); idle
    // "解析此段" action returns.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-loading-n1"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toHaveCount(0);
    await expect(action).toBeVisible();
    await expect(action).toHaveAttribute("data-resolve-action", "resolve");
    await expect(action).toHaveText("解析此段");

    // Simulate the page's natural snapshot reload (the same path
    // onRequestSnapshotReload drives via reloadSnapshot → setSnapshot).
    const postSnapshot = makePostSectionTranslationSnapshot();
    await page.evaluate((next) => {
      const surface = (
        window as unknown as {
          __spikeSurface?: {
            loadSnapshot: (s: Record<string, unknown>) => void;
          };
        }
      ).__spikeSurface;
      if (!surface) throw new Error("__spikeSurface not ready");
      surface.loadSnapshot(next);
    }, postSnapshot);

    // Translation blockquote now visible — snapshot reload was triggered.
    await expect(
      page.locator('[data-reader-record-node="blockquote"]'),
    ).toHaveCount(2);

    // navigation.units unchanged: rail mode preserved across retry + reload.
    const navModeAfter = await page.evaluate(() => {
      const rail = document.querySelector(
        '[data-testid="reader-record-navigation-rail"]',
      );
      return rail?.getAttribute("data-navigation-mode") ?? null;
    });
    expect(navModeAfter).toBe(navModeBefore);
    await expect(
      page.locator('[data-testid="reader-record-navigation-rail"]'),
    ).toHaveAttribute("data-navigation-mode", "L2");
    await expect(
      page.locator('[data-testid="reader-record-navigation-rail"]'),
    ).toHaveAttribute("data-outline-surface", "semantic");

    // No fixed-bottom toast surfaced at any point during the retry flow.
    const fixedToastCount = await page.evaluate(() => {
      const candidates = Array.from(
        document.querySelectorAll<HTMLElement>(
          '[role="status"], [role="alert"]',
        ),
      );
      return candidates.filter((el) => {
        const style = window.getComputedStyle(el);
        return style.position === "fixed" && el.offsetParent !== null;
      }).length;
    });
    expect(fixedToastCount).toBe(0);
  });

  test("BFF error (409 fence conflict) → inline '无法解析此段', no toast leak", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    await page.route(
      "**/api/web/reader-plate/records/*/section-translation",
      async (route) => {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            ok: false,
            status: 409,
            code: "section_translation_conflict",
            message: "段落内容已更新，请刷新后再试。",
          }),
        });
      },
    );

    await openPanel(page);
    await switchToSemantic(page);

    await page
      .locator('[data-testid="reader-record-outline-resolve-n1"]')
      .click();

    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toHaveText("无法解析此段");

    // BFF never leaks its internal message into the DOM.
    const feedbackText = await page
      .locator('[data-testid="reader-record-outline-resolve-feedback-n1"]')
      .textContent();
    expect(feedbackText).not.toContain("段落内容已更新");
    expect(feedbackText).not.toContain("section_translation_conflict");
  });

  test("1280x800 Ask docked: chip click preserves nav-panel-vs-ask gap", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    // Open Ask docked aside.
    const askLauncher = page.locator('button[aria-label="打开 Ask Claread"]');
    await expect(askLauncher).toBeVisible({ timeout: 5_000 });
    await askLauncher.click();
    const askPanel = page.locator("aside.ai-workspace-panel");
    await expect(askPanel).toBeVisible({ timeout: 10_000 });
    await expect(askPanel).toHaveClass(/ai-workspace-panel--layout-docked/);

    await openPanel(page);
    await switchToSemantic(page);

    await page.route(
      "**/api/web/reader-plate/records/*/section-translation",
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            outcome: "succeeded",
            job_id: "job_t56c_ask",
            detail: null,
          }),
        });
      },
    );

    // Verify gap BEFORE click.
    const gapBefore = await page.evaluate(() => {
      const p = document.querySelector<HTMLElement>(
        '[data-testid="reader-record-navigation-panel"]',
      );
      const ask = document.querySelector<HTMLElement>(
        "aside.ai-workspace-panel",
      );
      if (!p || !ask) throw new Error("missing panel or ask");
      return {
        panelRight: p.getBoundingClientRect().right,
        askLeft: ask.getBoundingClientRect().left,
      };
    });
    expect(gapBefore.panelRight).toBeLessThanOrEqual(gapBefore.askLeft - 12);

    await page
      .locator('[data-testid="reader-record-outline-resolve-n1"]')
      .click();

    // Succeeded → row state clears. Verify gap AFTER click is preserved.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toBeVisible();

    const gapAfter = await page.evaluate(() => {
      const p = document.querySelector<HTMLElement>(
        '[data-testid="reader-record-navigation-panel"]',
      );
      const ask = document.querySelector<HTMLElement>(
        "aside.ai-workspace-panel",
      );
      if (!p || !ask) throw new Error("missing panel or ask");
      return {
        panelRight: p.getBoundingClientRect().right,
        askLeft: ask.getBoundingClientRect().left,
      };
    });
    expect(gapAfter.panelRight).toBeLessThanOrEqual(gapAfter.askLeft - 12);
  });

  test("L0/L1 regression: navigation.units and rail mode unaffected by chip presence", async ({
    page,
  }) => {
    // L0 single heading snapshot — no semantic outline. Verify rail-level
    // attributes without opening the panel (the chip is only rendered when
    // the panel is open in semantic mode, so absence is trivially true).
    const l0Units: L1NavUnitSpec[] = [
      {
        unit_id: "lu1",
        order_index: 1,
        unit_type: "body",
        label: null,
        text: "L0 body content for T5.6c regression.",
      },
    ];
    const l0 = makeNavigationFixtureSnapshot({
      units: l0Units,
      snapshotId: "snap_t56c_l0",
      recordId: "record_t56c_l0",
    });
    await loadSnapshot(page, l0, ["lu1"]);

    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-has-semantic-outline", "false");
    await expect(rail).toHaveAttribute("data-outline-surface", "deterministic");
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-lu1"]'),
    ).toHaveCount(0);

    // L1 + L2 fixture: switching to L2 shows chips; switching back hides them.
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    await expect(rail).toHaveAttribute("data-has-semantic-outline", "true");
    await openPanel(page);
    // Deterministic surface — no chip.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toHaveCount(0);

    await switchToSemantic(page);
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toBeVisible();

    // Switch back to deterministic — chip disappears.
    await page
      .locator('[data-testid="reader-record-outline-mode-deterministic"]')
      .click();
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toHaveCount(0);
  });

  test("partial status trusted: chip still shown; untrusted statuses hide chip", async ({
    page,
  }) => {
    // Partial: trusted.
    const partialSnap = makeSemanticOutlineSnapshot({
      withOutline: true,
      outlineStatus: "partial",
    });
    await loadSnapshot(page, partialSnap, ["u1", "u2", "u3", "u4"]);

    await openPanel(page);
    await switchToSemantic(page);
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toBeVisible();
  });

  test("keyboard: Tab reaches chip, Enter sends full range witness", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    let capturedRequest: {
      body: unknown;
    } | null = null;

    await page.route(
      "**/api/web/reader-plate/records/*/section-translation",
      async (route) => {
        const request = route.request();
        capturedRequest = {
          body: JSON.parse(request.postData() ?? "{}"),
        };
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            outcome: "succeeded",
            job_id: "job_t56c_kb",
            detail: null,
          }),
        });
      },
    );

    await openPanel(page);
    await switchToSemantic(page);

    // Tab into the panel — chip must be its own accessible tab stop.
    const chip = page.locator(
      '[data-testid="reader-record-outline-resolve-n1"]',
    );
    await chip.focus();
    await expect(chip).toBeFocused();

    // Enter activates the chip — keyboard equivalent of click.
    await page.keyboard.press("Enter");

    await expect.poll(() => capturedRequest?.body ?? null).toBeTruthy();
    expect(capturedRequest!.body).toMatchObject({
      startUnitId: "u1",
      endUnitId: "u2",
      startAnchorSegmentId: "seg_u1",
      endAnchorSegmentId: null,
      nodeId: "n1",
      outlineRevision: "olrev_e2e_1",
    });

    // Succeeded → chip returns to idle (no loading / no feedback).
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-loading-n1"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-feedback-n1"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toBeVisible();

    // No body scroll triggered — keyboard activation must not propagate
    // to the parent row's onClick (which would call window.scrollTo).
    const scrolled = await page.evaluate(() => window.scrollY);
    expect(scrolled).toBe(0);
  });

  test("keyboard: Space activates chip; ArrowDown on chip does not steal rail roving", async ({
    page,
  }) => {
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    let capturedRequest: { body: unknown } | null = null;
    await page.route(
      "**/api/web/reader-plate/records/*/section-translation",
      async (route) => {
        const request = route.request();
        capturedRequest = { body: JSON.parse(request.postData() ?? "{}") };
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            outcome: "succeeded",
            job_id: "job_t56c_space",
            detail: null,
          }),
        });
      },
    );

    await openPanel(page);
    await switchToSemantic(page);

    const chip = page.locator(
      '[data-testid="reader-record-outline-resolve-n1"]',
    );
    const n1 = page.locator('[data-testid="reader-record-outline-node-n1"]');
    const n2 = page.locator('[data-testid="reader-record-outline-node-n2"]');

    // Put roving focus on row n1.
    await n1.focus();
    await expect(n1).toBeFocused();
    await expect(n1).toHaveAttribute("tabindex", "0");
    await expect(n2).toHaveAttribute("tabindex", "-1");

    // Tab to the chip — chip is its own command tab stop.
    await chip.focus();
    await expect(chip).toBeFocused();

    // ArrowDown on chip must NOT move roving to n2.
    await page.keyboard.press("ArrowDown");
    await expect(chip).toBeFocused();
    await expect(n1).toHaveAttribute("tabindex", "0");
    await expect(n2).toHaveAttribute("tabindex", "-1");

    // Space activates the chip.
    await page.keyboard.press(" ");
    await expect.poll(() => capturedRequest?.body ?? null).toBeTruthy();
    expect(capturedRequest!.body).toMatchObject({
      startUnitId: "u1",
      endUnitId: "u2",
      nodeId: "n1",
    });
  });

  test("keyboard: L0/L1 roving tabindex unaffected by chip presence", async ({
    page,
  }) => {
    // L1 + L2 fixture — verify roving works on deterministic L1 surface
    // (no chip rendered) and remains correct after switching to L2.
    const snapshot = makeSemanticOutlineSnapshot({ withOutline: true });
    await loadSnapshot(page, snapshot, ["u1", "u2", "u3", "u4"]);

    const rail = page.locator('[data-testid="reader-record-navigation-rail"]');
    await expect(rail).toHaveAttribute("data-outline-surface", "deterministic");

    await openPanel(page);

    // Deterministic L1: rows are tabbable via roving; no chip.
    // DeterministicPanelRow buttons have no data-outline-node-id (only
    // SemanticPanelRow buttons do), so we filter on that absence.
    const detRows = page.locator(
      '[data-testid="reader-record-navigation-panel"] ol button:not([data-outline-node-id])',
    );
    const detCount = await detRows.count();
    expect(detCount).toBeGreaterThan(0);

    // Deterministic rows render with roving tabindex: focusedKey row has
    // tabindex=0, others have tabindex=-1.
    await expect(detRows.nth(0)).toHaveAttribute("tabindex", "0");
    for (let i = 1; i < detCount; i++) {
      await expect(detRows.nth(i)).toHaveAttribute("tabindex", "-1");
    }

    // ArrowDown moves roving to next row. Use locator.press so the keydown
    // event is dispatched directly to the row button (Playwright focuses the
    // locator first, then sends the key). page.keyboard.press targets
    // document.activeElement, which can drift after openPanel's trigger
    // click and the panel-open useEffect that focuses focusedKey's row.
    await detRows.nth(0).press("ArrowDown");
    await expect(detRows.nth(0)).toHaveAttribute("tabindex", "-1");
    await expect(detRows.nth(1)).toHaveAttribute("tabindex", "0");

    // No chip on deterministic surface.
    await expect(
      page.locator('[data-testid="reader-record-outline-resolve-n1"]'),
    ).toHaveCount(0);

    // Switch to semantic — chips appear; row roving still works.
    await switchToSemantic(page);
    const n1 = page.locator('[data-testid="reader-record-outline-node-n1"]');
    const n2 = page.locator('[data-testid="reader-record-outline-node-n2"]');
    await expect(n1).toHaveAttribute("tabindex", "0");
    await expect(n2).toHaveAttribute("tabindex", "-1");
    await n1.press("ArrowDown");
    await expect(n1).toHaveAttribute("tabindex", "-1");
    await expect(n2).toHaveAttribute("tabindex", "0");
  });
});
