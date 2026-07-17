import { expect, test, type Page } from "@playwright/test";

/**
 * Modal backdrop contract — Settings Dialog & Ctrl+K Command Palette.
 *
 * Verifies that both modals:
 * 1. Render their overlay in a portal on document.body (not inside Sidebar).
 * 2. Overlay computed z-index > Sidebar computed z-index.
 * 3. Overlay covers Sidebar's viewport coordinates.
 * 4. Overlay provides REAL visual dimming — computed backgroundColor is not
 *    transparent, alpha > 0, and pointer-events is not "none". This rules
 *    out "invisible stacking-context layer" regressions where the overlay
 *    occupies space but does not dim or intercept clicks.
 * 5. Sidebar links ("新解读" / "全部阅读记录") don't trigger navigation
 *    while a modal is open — clicks are intercepted by the overlay. (Radix
 *    Dialog also marks portal-external siblings aria-hidden/inert, so the
 *    links are non-interactive at the DOM level as a second layer of defense.
 *    We capture each link's bounding box BEFORE opening the dialog because
 *    getByRole cannot resolve aria-hidden elements from the a11y tree.)
 * 6. After closing, Sidebar links become clickable again (aria-hidden removed).
 *
 * If these tests pass, the existing z-index token contract
 * (--app-z-shell-navigation: 70 < --app-z-modal-backdrop: 90) plus the
 * `--app-overlay` dimming token are sufficient — no z-9999, no
 * stacking-context hacks, no raw rgba() overrides are needed.
 *
 * Note on historical screenshots: this suite verifies the current build's
 * structural stacking, interactive blocking, and non-transparent dimming
 * contract. The specific runtime origin of any historical "not dimmed"
 * screenshot is NOT determined by this test — it could be stale CSS, a
 * non-latest build, or any other environmental factor outside this suite's
 * scope. This suite only asserts what the current build delivers.
 */

// ---------------------------------------------------------------------------
// Auth + BFF mocks
// ---------------------------------------------------------------------------

async function loginWithMockPhone(page: Page) {
  await page.route("**/api/web/auth/phone/request-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "本地调试验证码已生成，请使用 888888。",
      }),
    });
  });

  await page.route("**/api/web/auth/phone/verify-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: {
        "set-cookie": "claread_web_phone=13800138000; Path=/; SameSite=Lax; HttpOnly",
      },
      body: JSON.stringify({
        ok: true,
        phone: "13800138000",
        message: "已进入本地调试登录态。",
      }),
    });
  });

  await page.goto("/login?next=/app/read");
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  // /app/read has server-side BFF calls (getProfileSettings) that may
  // take up to 60s to timeout when the backend is unavailable in the
  // mock-auth E2E environment. Wait generously so the page fully renders
  // (including the AppShell layout with the sidebar trigger).
  await page.waitForURL("**/app/read", { timeout: 90_000 });
}

async function mockBffRoutes(page: Page) {
  // Profile — needed by Settings Dialog (intercepted route server loader).
  await page.route("**/api/web/profile", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        status: "ready",
        session: {
          phone: "13800138000",
          isAuthenticated: true,
        },
        profile: {
          userId: "debug-user-1",
          sessionId: "debug-session-1",
          nickname: "调试用户",
          avatarUrl: "",
          cumulativeArticleCount: 3,
        },
        quota: {
          profileId: "debug-user-1",
          quotaUsed: 3,
          quotaLimit: 10,
          quotaType: "daily",
          dailyFreePoints: 10,
          dailyUsedPoints: 3,
          bonusPoints: 5,
          remainingPoints: 7,
        },
      }),
    });
  });

  // Reading records — needed by Ctrl+K command palette + sidebar recent list.
  await page.route("**/api/web/reading-records**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        items: [
          {
            readingRecordId: "mock-record-backdrop-1",
            readerUrl: "/app/reader/mock-record-backdrop-1",
            title: "Backdrop Contract Test Article",
            createdAt: "2026-07-16T08:00:00.000Z",
            sourceType: "text",
            productState: "readable_enhancing",
            readinessState: "article_ready",
            lastEventSequence: 1,
            sourceLabel: "粘贴文本",
          },
        ],
        total: 1,
        limit: 8,
      }),
    });
  });

  // Credit ledger — needed by Settings usage section (showLedger=true).
  await page.route("**/api/web/credit-ledger**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        items: [],
        total: 0,
        limit: 20,
        offset: 0,
      }),
    });
  });

  // Feedback — needed by Settings support section.
  await page.route("**/api/web/feedback**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        items: [],
        total: 0,
        limit: 20,
        offset: 0,
      }),
    });
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function lockSidebar(page: Page) {
  // The sidebar starts in "closed" mode. Clicking the "打开导航" button
  // (data-app-sidebar-trigger) locks it so it becomes visible and fixed.
  const trigger = page.locator("[data-app-sidebar-trigger='true']");
  await trigger.click();
  // Sidebar should now be visible with data-app-sidebar-state="locked".
  await expect(page.locator("[data-app-sidebar='rail']")).toHaveAttribute(
    "data-app-sidebar-state",
    "locked",
  );
}

async function getOverlay(page: Page) {
  return page.locator(".app-overlay.fixed.inset-0").first();
}

async function getSidebar(page: Page) {
  return page.locator("[data-app-sidebar='rail']");
}

async function assertOverlayPortalOnBody(page: Page) {
  const overlay = await getOverlay(page);
  await expect(overlay).toBeVisible();
  // The overlay must be a descendant of document.body, NOT inside the
  // sidebar's stacking context. Radix DialogPortal renders into body by
  // default; this assertion guards against custom portal containers that
  // would inherit the sidebar's stacking context.
  const isInsideBody = await overlay.evaluate((el) => {
    return el.closest("body") !== null;
  });
  expect(isInsideBody).toBe(true);

  const isInsideSidebar = await overlay.evaluate((el) => {
    return el.closest("[data-app-sidebar='rail']") !== null;
  });
  expect(isInsideSidebar).toBe(false);
}

async function assertOverlayZIndexAboveSidebar(page: Page) {
  const overlay = await getOverlay(page);
  const sidebar = await getSidebar(page);

  const overlayZ = await overlay.evaluate((el) => {
    return parseInt(getComputedStyle(el).zIndex, 10);
  });
  const sidebarZ = await sidebar.evaluate((el) => {
    return parseInt(getComputedStyle(el).zIndex, 10);
  });

  // Both must resolve to real numbers (not NaN from auto/none).
  expect(Number.isFinite(overlayZ)).toBe(true);
  expect(Number.isFinite(sidebarZ)).toBe(true);
  // Overlay must be strictly above the sidebar.
  expect(overlayZ).toBeGreaterThan(sidebarZ);
}

async function assertOverlayCoversSidebar(page: Page) {
  const overlay = await getOverlay(page);
  const sidebar = await getSidebar(page);

  const overlayBox = await overlay.boundingBox();
  const sidebarBox = await sidebar.boundingBox();

  expect(overlayBox).not.toBeNull();
  expect(sidebarBox).not.toBeNull();

  // Overlay must cover the sidebar's bounding box on all four sides.
  // The overlay is `fixed inset-0` so it spans the entire viewport.
  expect(overlayBox!.x).toBeLessThanOrEqual(sidebarBox!.x);
  expect(overlayBox!.y).toBeLessThanOrEqual(sidebarBox!.y);
  expect(overlayBox!.x + overlayBox!.width).toBeGreaterThanOrEqual(
    sidebarBox!.x + sidebarBox!.width,
  );
  expect(overlayBox!.y + overlayBox!.height).toBeGreaterThanOrEqual(
    sidebarBox!.y + sidebarBox!.height,
  );
}

/**
 * Assert the overlay actually dims the page (non-transparent background),
 * not just occupies a transparent layer. Verifies:
 *   - computed backgroundColor is not "transparent" and not rgba(...,0)
 *   - parsed alpha channel is > 0
 *   - pointer-events is not "none" (overlay can still intercept clicks)
 *
 * Together with assertOverlayCoversSidebar + the click-blocking assertion,
 * this proves the overlay provides real visual dimming over the sidebar
 * in the current build — not just an invisible stacking-context layer.
 */
async function assertOverlayDimming(page: Page) {
  const overlay = await getOverlay(page);
  await expect(overlay).toBeVisible();

  const result = await overlay.evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      backgroundColor: cs.backgroundColor,
      pointerEvents: cs.pointerEvents,
    };
  });

  // Must not be fully transparent.
  expect(result.backgroundColor).not.toBe("transparent");
  expect(result.backgroundColor).not.toBe("");

  // Parse the alpha channel. getComputedStyle returns colors in one of:
  //   rgb(r, g, b)            -> alpha 1
  //   rgba(r, g, b, a)        -> alpha a
  //   #rrggbb / #rrggbbaa     -> hex form (rare from computed style)
  // We only accept rgb()/rgba() forms here because browsers serialize
  // computed backgroundColor to one of those.
  const rgbaMatch = result.backgroundColor.match(
    /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)$/i,
  );
  expect(rgbaMatch).not.toBeNull();
  const alpha = rgbaMatch![4] === undefined ? 1 : parseFloat(rgbaMatch![4]);
  expect(Number.isFinite(alpha)).toBe(true);
  expect(alpha).toBeGreaterThan(0);

  // Overlay must keep pointer events enabled so it can intercept clicks.
  expect(result.pointerEvents).not.toBe("none");
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Modal backdrop contract — Ctrl+K Command Palette", () => {
  // /app/read server-side BFF calls can take ~60s in mock-auth environment.
  test.setTimeout(180_000);
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    // Login lands on /app/read. Stay here — the sidebar and "新解读" link
    // are available on every app page. For navigation detection we use
    // the recent reading item link (goes to /app/reader/{recordId}),
    // which produces a detectable URL change from /app/read.
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);
  });

  test("overlay is in document.body portal, above Sidebar, covers Sidebar, blocks clicks, recovers after close", async ({ page }) => {
    // Capture the "新解读" link's bounding box BEFORE opening the dialog.
    // Radix Dialog marks portal-external siblings (including the sidebar) as
    // aria-hidden/inert while open, so getByRole cannot resolve the link
    // from the accessibility tree while the dialog is open. Querying the
    // link position up-front avoids that a11y-tree limitation and mirrors
    // real user behavior (the user sees the link before opening the modal).
    const newReadLinkBefore = page
      .getByRole("link", { name: "新解读" })
      .first();
    await expect(newReadLinkBefore).toBeVisible();
    const newReadBox = await newReadLinkBefore.boundingBox();
    expect(newReadBox).not.toBeNull();
    const targetX = newReadBox!.x + newReadBox!.width / 2;
    const targetY = newReadBox!.y + newReadBox!.height / 2;

    // Open Ctrl+K by clicking the sidebar "搜索或跳转" button.
    await page.getByRole("button", { name: /搜索或跳转/ }).click();

    const dialog = page.getByRole("dialog", { name: "命令面板" });
    await expect(dialog).toBeVisible();

    // 1. Overlay is in document.body portal (not inside Sidebar).
    await assertOverlayPortalOnBody(page);

    // 2. Overlay computed z-index > Sidebar computed z-index.
    await assertOverlayZIndexAboveSidebar(page);

    // 3. Overlay covers Sidebar viewport coordinates.
    await assertOverlayCoversSidebar(page);

    // 4. Overlay provides REAL visual dimming (non-transparent backgroundColor,
    //    alpha > 0, pointer-events not none). This proves the overlay is not
    //    an invisible layer — it visually dims the sidebar and can intercept.
    await assertOverlayDimming(page);

    // 5. Click at the "新解读" link's pre-captured coordinates. The overlay
    //    is fixed inset-0 with a higher z-index, so the click lands on the
    //    overlay — NOT the (now inert) link. Radix Dialog's overlay onClick
    //    closes the palette, proving the overlay (not the link) received
    //    the click. The URL must not change.
    const urlBeforeClick = page.url();
    await page.mouse.click(targetX, targetY);
    // Dialog must close via overlay click (not via Esc fallback). If the
    // overlay failed to intercept, the dialog would stay open and this
    // assertion would fail — surfacing a real stacking-context regression.
    await expect(dialog).toHaveCount(0, { timeout: 3_000 });
    expect(page.url()).toBe(urlBeforeClick);

    // 6. After close, "新解读" link is visible and clickable (recovered).
    //    aria-hidden/inert has been removed, so getByRole resolves again.
    const newReadLinkAfter = page
      .getByRole("link", { name: "新解读" })
      .first();
    await expect(newReadLinkAfter).toBeVisible();
    await expect(newReadLinkAfter).toBeEnabled();
  });
});

test.describe("Modal backdrop contract — Settings Dialog", () => {
  test.setTimeout(180_000);
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    // Start on /app/read so Settings Dialog's router.back() returns here.
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);
  });

  test("overlay is in document.body portal, above Sidebar, covers Sidebar, blocks clicks, recovers after close", async ({ page }) => {
    // Capture the "全部阅读记录" link's bounding box BEFORE opening the
    // dialog. We use "全部阅读记录" (→ /app/library) instead of "新解读"
    // (→ /app/read) because the Settings Dialog is route-based: clicking
    // its overlay triggers router.back(), which returns to /app/read —
    // the SAME url as "新解读"'s href. Using a link with a distinct
    // destination (/app/library) lets us definitively prove the link was
    // NOT clicked (URL stays at /app/read, never becomes /app/library).
    // (Recent-reading items can't be used here because they are populated
    // by a server-side BFF fetch that page.route() cannot intercept.)
    //
    // As with Ctrl+K, Radix Dialog marks the sidebar aria-hidden/inert
    // while open, so we capture the bounding box before opening the dialog.
    const libraryLink = page
      .getByRole("link", { name: "全部阅读记录" })
      .first();
    await expect(libraryLink).toBeVisible();
    const libraryBox = await libraryLink.boundingBox();
    expect(libraryBox).not.toBeNull();
    const targetX = libraryBox!.x + libraryBox!.width / 2;
    const targetY = libraryBox!.y + libraryBox!.height / 2;

    // Open Settings Dialog via client-side navigation from the sidebar
    // user menu. The intercepting route (@settings/(.)settings) catches
    // this and renders the dialog overlay.
    // NOTE: Radix DropdownMenuItem with asChild overrides the Link's role
    // to "menuitem" (not "link"), so we query by role=menuitem.
    await page.getByRole("button", { name: "打开用户菜单" }).click();
    await page.getByRole("menuitem", { name: "偏好设置" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // 1. Overlay is in document.body portal (not inside Sidebar).
    await assertOverlayPortalOnBody(page);

    // 2. Overlay computed z-index > Sidebar computed z-index.
    await assertOverlayZIndexAboveSidebar(page);

    // 3. Overlay covers Sidebar viewport coordinates.
    await assertOverlayCoversSidebar(page);

    // 4. Overlay provides REAL visual dimming (non-transparent backgroundColor,
    //    alpha > 0, pointer-events not none). Same contract as Ctrl+K — the
    //    overlay is not an invisible layer.
    await assertOverlayDimming(page);

    // 5. Click at the "全部阅读记录" link's pre-captured coordinates.
    //    The overlay intercepts; for the route-based Settings Dialog,
    //    overlay click triggers router.back(), which returns to /app/read.
    //    The URL must NOT become /app/library — that would prove the
    //    underlying link was clicked instead of the overlay.
    await page.mouse.click(targetX, targetY);

    // After overlay click, the Settings Dialog should close via router.back().
    await expect(dialog).toHaveCount(0, { timeout: 10_000 });

    // URL should be back at /app/read (router.back returns here).
    // It must NOT be /app/library (the link's href).
    expect(page.url()).toMatch(/\/app\/read$/);
    expect(page.url()).not.toMatch(/\/app\/library/);

    // 6. After close, the "全部阅读记录" link is visible and clickable
    //    (recovered). aria-hidden/inert has been removed.
    await expect(libraryLink).toBeVisible();
    await expect(libraryLink).toBeEnabled();
  });
});

test.describe("Modal backdrop contract — same semantic contract for both modals", () => {
  test.setTimeout(180_000);
  test("both Ctrl+K and Settings overlay use backdrop-blur-none (no blur)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);

    // Ctrl+K overlay
    await page.getByRole("button", { name: /搜索或跳转/ }).click();
    const cmdkOverlay = await getOverlay(page);
    await expect(cmdkOverlay).toBeVisible();
    const cmdkBlur = await cmdkOverlay.evaluate((el) => {
      return getComputedStyle(el).backdropFilter;
    });
    // backdrop-filter should be "none" (no blur).
    expect(cmdkBlur === "none" || cmdkBlur === "").toBe(true);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);

    // Settings overlay
    await page.getByRole("button", { name: "打开用户菜单" }).click();
    await page.getByRole("menuitem", { name: "偏好设置" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    const settingsOverlay = await getOverlay(page);
    await expect(settingsOverlay).toBeVisible();
    const settingsBlur = await settingsOverlay.evaluate((el) => {
      return getComputedStyle(el).backdropFilter;
    });
    expect(settingsBlur === "none" || settingsBlur === "").toBe(true);
  });
});

test.describe("Modal backdrop contract — Dark theme dimming (both modals)", () => {
  // This suite verifies the dimming contract under the Dark theme.
  // The Light theme is already covered by the per-modal tests above
  // (which run with the default theme). Here we only need to assert
  // that the Dark overlay also has a non-transparent backgroundColor
  // with alpha > 0 — for BOTH modals — not the full click-blocking
  // matrix (those assertions are theme-independent at the DOM/stacking
  // level and already covered in Light).
  test.setTimeout(180_000);
  test.beforeEach(async ({ page }) => {
    // Force Dark theme BEFORE any navigation so AppearanceProvider
    // (next-themes, attribute="class", storageKey="claread.theme.v1")
    // resolves to "dark" on first paint. addInitScript runs before any
    // page script on every navigation, keeping the theme stable across
    // the login redirect.
    await page.addInitScript(() => {
      localStorage.setItem("claread.theme.v1", "dark");
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);
  });

  test("documentElement has dark class and both modals dim with alpha > 0", async ({ page }) => {
    // 1. Confirm Dark theme is actually applied.
    const hasDarkClass = await page.evaluate(() => {
      return document.documentElement.classList.contains("dark");
    });
    expect(hasDarkClass).toBe(true);

    // 2. Ctrl+K overlay — assert dimming (non-transparent, alpha > 0,
    //    pointer-events not none). This reuses the same helper as Light.
    await page.getByRole("button", { name: /搜索或跳转/ }).click();
    const cmdkDialog = page.getByRole("dialog", { name: "命令面板" });
    await expect(cmdkDialog).toBeVisible();
    await assertOverlayDimming(page);
    // Read the computed backgroundColor and assert alpha explicitly so
    // the test record contains the concrete dark-overlay value.
    const cmdkBg = await (await getOverlay(page)).evaluate((el) => {
      return getComputedStyle(el).backgroundColor;
    });
    const cmdkAlpha = parseRgbaAlpha(cmdkBg);
    expect(cmdkAlpha).toBeGreaterThan(0);
    await page.keyboard.press("Escape");
    await expect(cmdkDialog).toHaveCount(0);

    // 3. Settings overlay — same dimming contract.
    await page.getByRole("button", { name: "打开用户菜单" }).click();
    await page.getByRole("menuitem", { name: "偏好设置" }).click();
    const settingsDialog = page.getByRole("dialog");
    await expect(settingsDialog).toBeVisible();
    await assertOverlayDimming(page);
    const settingsBg = await (await getOverlay(page)).evaluate((el) => {
      return getComputedStyle(el).backgroundColor;
    });
    const settingsAlpha = parseRgbaAlpha(settingsBg);
    expect(settingsAlpha).toBeGreaterThan(0);
  });
});

/**
 * Parse the alpha channel from a browser-serialized rgb()/rgba() color.
 * Returns 1 for `rgb(r, g, b)` (implicit alpha) and NaN if the string
 * does not match the expected form. Callers should guard with
 * Number.isFinite before comparing.
 */
function parseRgbaAlpha(color: string): number {
  const m = color.match(
    /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)$/i,
  );
  if (!m) return Number.NaN;
  return m[4] === undefined ? 1 : parseFloat(m[4]);
}
