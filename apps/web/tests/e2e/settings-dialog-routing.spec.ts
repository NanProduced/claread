import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * Settings Dialog routing regression — real Chromium, mock auth.
 *
 * Verifies the AppShell-owned Settings Dialog contract:
 *   - Opening Settings never changes URL (pathname, search, hash).
 *   - Switching sections inside the Dialog never changes URL.
 *   - Browser Back closes the Dialog and returns to the host page.
 *   - Escape / close button / overlay click close the Dialog.
 *   - Direct visits to legacy /app/settings, /app/settings?section=*,
 *     /app/settings/feedback, /app/settings/ledger redirect to /app/read.
 *   - Reloading the page while a Settings history marker is present
 *     restores the Dialog and it can be closed normally.
 *
 * The Dialog is rendered by SettingsDialogProvider at the AppShell level.
 * Opening it does NOT navigate: history.pushState writes a marker into
 * history.state but uses location.href as the URL so the address bar
 * is unchanged. Section switches use history.replaceState (no new entry).
 */

// ---------------------------------------------------------------------------
// Auth + BFF mocks (mirrors modal-backdrop-contract.spec.ts)
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
  await page.waitForURL("**/app/read", { timeout: 90_000 });
}

async function mockBffRoutes(page: Page) {
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

  await page.route("**/api/web/reader/records**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        items: [
          {
            readingRecordId: "mock-record-settings-1",
            readerUrl: "/app/reader/mock-record-settings-1",
            title: "Settings Routing Test Article",
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

async function lockSidebar(page: Page) {
  const trigger = page.locator("[data-app-sidebar-trigger='true']");
  await trigger.click();
  await expect(page.locator("[data-app-sidebar='rail']")).toHaveAttribute(
    "data-app-sidebar-state",
    "locked",
  );
}

async function openUserMenuAndClickSettings(page: Page, label: "个人资料" | "偏好设置" | "用量与积分") {
  await page.getByRole("button", { name: "打开用户菜单" }).click();
  await page.getByRole("menuitem", { name: label }).click();
}

async function openMobileUserMenuAndClickSettings(
  page: Page,
  label: "个人资料" | "偏好设置" | "用量与积分",
) {
  const trigger = page.locator('[data-mobile-user-menu-trigger="true"]');
  await expect(trigger).toBeVisible();
  await trigger.click();
  const menuitem = page.getByRole("menuitem", { name: label });
  await expect(menuitem).toBeVisible();
  await menuitem.click();
}

async function assertTouchTargetAtLeast44px(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width, "touch target width must be at least 44px").toBeGreaterThanOrEqual(44);
  expect(box!.height, "touch target height must be at least 44px").toBeGreaterThanOrEqual(44);
}

async function openUserMenuWithKeyboard(
  page: Page,
  label: "个人资料" | "偏好设置" | "用量与积分",
) {
  const trigger = page.getByRole("button", { name: "打开用户菜单" });
  await trigger.focus();
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");

  const menuitem = page.getByRole("menuitem", { name: label });
  await expect(menuitem).toBeVisible();

  // Radix DropdownMenu focuses the first item automatically. Navigate down to
  // the requested item without assuming mouse position.
  let attempts = 0;
  while (attempts < 6) {
    const isTarget = await page.evaluate(
      (target) => {
        const active = document.activeElement;
        return (
          active?.getAttribute("role") === "menuitem" &&
          active?.textContent?.includes(target)
        );
      },
      label,
    );
    if (isTarget) break;
    await page.keyboard.press("ArrowDown");
    attempts++;
  }

  await page.keyboard.press("Enter");
}

async function expectDialogClosedAndBackOnReader(page: Page) {
  await expect(page.getByRole("dialog", { name: "设置" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/app\/read$/);
}

/**
 * Assert that the current page URL does NOT contain any legacy Settings
 * route pattern. Used to enforce the "URL never changes to /app/settings"
 * contract across open / section-switch / close actions.
 */
async function expectNoSettingsUrl(page: Page) {
  await expect(page).not.toHaveURL(/\/app\/settings/);
}

/**
 * Assert that the page URL equals the captured snapshot, supporting both
 * exact string and regex matchers. Used to enforce "address bar must not
 * change at all" while opening or switching sections.
 */
async function expectUrlUnchanged(page: Page, expected: string | RegExp) {
  await expect(page).toHaveURL(expected);
}

async function setTheme(page: Page, theme: "light" | "dark") {
  await page.evaluate((t) => {
    localStorage.setItem("claread.theme.v1", t);
  }, theme);
}

function parseRgb(rgb: string): [number, number, number, number] {
  const m = rgb.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
  if (!m) return [0, 0, 0, 0];
  const r = parseInt(m[1], 10);
  const g = parseInt(m[2], 10);
  const b = parseInt(m[3], 10);
  const a = m[4] !== undefined ? parseFloat(m[4]) : 1;
  return [r, g, b, a];
}

function colorDistance(left: string, right: string): number {
  const [r1, g1, b1] = parseRgb(left);
  const [r2, g2, b2] = parseRgb(right);
  return Math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2);
}

async function getBackgroundColor(locator: Locator): Promise<string> {
  return locator.evaluate((el) => getComputedStyle(el).backgroundColor);
}

async function assertNoHorizontalOverflow(page: Page, dialog: Locator) {
  const overflows = await dialog.evaluate((dialogEl) => {
    const html = document.documentElement;
    const rightPanel = dialogEl.querySelector(
      ".relative.flex.min-h-0.flex-1.flex-col",
    ) as HTMLElement | null;
    const body = dialogEl.querySelector(
      ".min-h-0.overflow-y-auto",
    ) as HTMLElement | null;
    return {
      page: html.scrollWidth > html.clientWidth,
      dialog: dialogEl.scrollWidth > (dialogEl as HTMLElement).clientWidth,
      rightPanel: rightPanel
        ? rightPanel.scrollWidth > rightPanel.clientWidth
        : false,
      body: body ? body.scrollWidth > body.clientWidth : false,
    };
  });
  expect(overflows.page, "page must not scroll horizontally").toBe(false);
  expect(overflows.dialog, "dialog must not overflow horizontally").toBe(false);
  expect(overflows.rightPanel, "right panel must not overflow horizontally").toBe(
    false,
  );
  expect(overflows.body, "body must not overflow horizontally").toBe(false);
}

async function assertAllVisibleControlsWithinBounds(page: Page, dialog: Locator) {
  const body = dialog.locator(".min-h-0.overflow-y-auto").first();
  await expect(body).toBeVisible();

  const bodyBox = await body.boundingBox();
  expect(bodyBox).not.toBeNull();
  const bodyLeft = bodyBox!.x;
  const bodyRight = bodyBox!.x + bodyBox!.width;

  // Covers visible labels for sr-only native radios; the input itself is
  // hidden and therefore excluded by the visible filter.
  const controls = await body
    .locator("button, a, textarea, input, label")
    .filter({ visible: true })
    .all();
  expect(controls.length).toBeGreaterThan(0);

  for (const control of controls) {
    const box = await control.boundingBox();
    if (!box) continue;

    // Allow 1px sub-pixel tolerance.
    expect(
      box.x,
      "control left edge must sit inside the scroll body viewport",
    ).toBeGreaterThanOrEqual(bodyLeft - 1);
    expect(
      box.x + box.width,
      "control right edge must sit inside the scroll body viewport",
    ).toBeLessThanOrEqual(bodyRight + 1);
  }
}

// ---------------------------------------------------------------------------
// Desktop opening from Reader sidebar — URL never changes
// ---------------------------------------------------------------------------

test.describe("Settings Dialog opening from Reader sidebar", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);
  });

  test("个人资料 opens dialog at account section without changing URL", async ({ page }) => {
    const urlBefore = page.url();

    await openUserMenuAndClickSettings(page, "个人资料");

    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "个人资料", level: 2 }),
    ).toBeVisible();
  });

  test("偏好设置 opens dialog at preferences section without changing URL", async ({ page }) => {
    const urlBefore = page.url();

    await openUserMenuAndClickSettings(page, "偏好设置");

    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();
  });

  test("用量与积分 opens dialog at usage section without changing URL", async ({ page }) => {
    const urlBefore = page.url();

    await openUserMenuAndClickSettings(page, "用量与积分");

    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "用量与积分", level: 2 }),
    ).toBeVisible();
    // The header description also reads "当前无需操作。", so target the last
    // occurrence (the body paragraph) to avoid a strict-mode violation.
    await expect(dialog.getByText("当前无需操作。").last()).toBeVisible();

    // Old usage counters / ledger UI must not appear.
    await expect(dialog.getByText(/今日解析点数/)).toHaveCount(0);
    await expect(dialog.getByText("查看明细账单")).toHaveCount(0);
  });

  test("switching sections inside dialog does not change URL and Back returns to Reader", async ({ page }) => {
    const urlBefore = page.url();

    await openUserMenuAndClickSettings(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    const nav = dialog.getByRole("navigation", { name: "设置分区" });

    await nav.getByRole("button", { name: "偏好" }).click();
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);
    await expect(
      dialog.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();

    await nav.getByRole("button", { name: "用量与积分" }).click();
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);
    await expect(
      dialog.getByRole("heading", { name: "用量与积分", level: 2 }),
    ).toBeVisible();

    // Section switches use replaceState (no history accumulation). Browser
    // Back therefore returns to the underlying Reader page.
    await page.goBack();
    await expect(page).toHaveURL(/\/app\/read$/);
    await expect(dialog).toHaveCount(0);
  });

  test("close button closes dialog and returns to Reader", async ({ page }) => {
    const urlBefore = page.url();
    await openUserMenuAndClickSettings(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    // Click the desktop close button (the last/visible one in DOM order).
    await dialog.getByRole("button", { name: "关闭设置" }).last().click();
    await expectDialogClosedAndBackOnReader(page);

    // Reader page is interactive again.
    const libraryLink = page.getByRole("link", { name: "全部阅读记录" }).first();
    await expect(libraryLink).toBeVisible();
    await expect(libraryLink).toBeEnabled();
  });

  test("Escape closes dialog and returns to Reader", async ({ page }) => {
    const urlBefore = page.url();
    await openUserMenuAndClickSettings(page, "偏好设置");
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expectDialogClosedAndBackOnReader(page);
  });

  test("overlay click intercepts a sidebar click and closes dialog via history.back", async ({ page }) => {
    // Use a wider viewport so the app sidebar is fully outside the centered
    // dialog content; the click then lands on the overlay, not on the dialog's
    // own rail, and should trigger history.back().
    await page.setViewportSize({ width: 1920, height: 1080 });

    const urlBefore = page.url();

    // Capture the "全部阅读记录" link's bounding box BEFORE opening the dialog.
    // Radix Dialog marks portal-external siblings (including the sidebar) as
    // aria-hidden/inert while open, so we capture the position up-front.
    const libraryLink = page.getByRole("link", { name: "全部阅读记录" }).first();
    await expect(libraryLink).toBeVisible();
    const libraryBox = await libraryLink.boundingBox();
    expect(libraryBox).not.toBeNull();
    const targetX = libraryBox!.x + libraryBox!.width / 2;
    const targetY = libraryBox!.y + libraryBox!.height / 2;

    await openUserMenuAndClickSettings(page, "用量与积分");
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    // Click at the pre-captured sidebar link coordinates. The overlay is above
    // the sidebar and will intercept; for the AppShell-owned dialog the overlay
    // click triggers closeSettings() → history.back(), keeping the URL on
    // /app/read (since the host page was already at /app/read).
    await page.mouse.click(targetX, targetY);
    await expect(dialog).toHaveCount(0, { timeout: 10_000 });
    await expect(page).toHaveURL(/\/app\/read$/);
    await expect(page).not.toHaveURL(/\/app\/library/);

    // After close, the sidebar link is visible and clickable again.
    await expect(libraryLink).toBeVisible();
    await expect(libraryLink).toBeEnabled();
  });
});

// ---------------------------------------------------------------------------
// Mobile viewport sheet behaviour
// ---------------------------------------------------------------------------

test.describe("Settings Dialog mobile viewport", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);

    // Open the dialog on desktop first, then resize to mobile to exercise
    // the responsive sheet layout. The user menu is a desktop sidebar affordance.
    await openUserMenuAndClickSettings(page, "个人资料");
    await expectNoSettingsUrl(page);
    await page.setViewportSize({ width: 390, height: 844 });
  });

  test("dialog fills the viewport as a full-screen sheet", async ({ page }) => {
    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    const box = await dialog.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBe(0);
    expect(box!.y).toBe(0);
    expect(box!.width).toBe(390);
    expect(box!.height).toBe(844);
  });

  test("close button and section nav touch targets meet 44px", async ({ page }) => {
    const dialog = page.getByRole("dialog", { name: "设置" });
    const closeBtn = dialog.getByRole("button", { name: "关闭设置" }).first();
    await expect(closeBtn).toHaveCSS("height", "44px");
    await expect(closeBtn).toHaveCSS("width", "44px");

    const nav = dialog.getByRole("navigation", { name: "设置分区" });
    const buttons = await nav.getByRole("button").all();
    expect(buttons.length).toBeGreaterThan(0);
    for (const btn of buttons) {
      await expect(btn).toHaveCSS("min-height", "44px");
    }
  });

  test("section nav is horizontally scrollable on mobile", async ({ page }) => {
    const dialog = page.getByRole("dialog", { name: "设置" });
    const nav = dialog.getByRole("navigation", { name: "设置分区" });
    await expect(nav).toHaveCSS("overflow-x", "auto");
  });

  test("content region scrolls independently and fixed chrome stays visible", async ({ page }) => {
    const dialog = page.getByRole("dialog", { name: "设置" });

    // Inject a tall spacer into the scrollable body to force scrolling.
    await dialog.evaluate((dialogEl) => {
      const body = dialogEl.querySelector(".min-h-0.overflow-y-auto") as HTMLElement | null;
      if (!body) return;
      const spacer = document.createElement("div");
      spacer.style.height = "2000px";
      spacer.style.background = "transparent";
      spacer.setAttribute("data-testid", "scroll-spacer");
      body.appendChild(spacer);
    });

    const body = dialog.locator(".min-h-0.overflow-y-auto").first();
    await body.evaluate((el) => el.scrollTo(0, 500));
    const scrollTop = await body.evaluate((el) => el.scrollTop);
    expect(scrollTop).toBe(500);

    // Fixed header (the section title) must still be visible.
    const header = dialog.getByRole("heading", { name: "个人资料", level: 2 }).first();
    await expect(header).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Keyboard focus and reading continuity
// ---------------------------------------------------------------------------

test.describe("Settings Dialog keyboard focus and reading continuity", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);
  });

  test("opens 个人资料 from user menu via keyboard without changing URL", async ({ page }) => {
    const urlBefore = page.url();

    await openUserMenuWithKeyboard(page, "个人资料");

    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    const focusInsideDialog = await page.evaluate(
      () => !!document.activeElement?.closest('[role="dialog"]'),
    );
    expect(focusInsideDialog).toBe(true);
  });

  test("focus is trapped inside dialog and aria-current updates uniquely", async ({ page }) => {
    const urlBefore = page.url();

    await openUserMenuWithKeyboard(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await expect(
      dialog.getByRole("button", { name: "个人资料" }),
    ).toHaveAttribute("aria-current", "page");

    // Tab across the whole dialog chrome several times; focus must stay inside
    // on every single step, not just at the end.
    for (let i = 0; i < 10; i++) {
      await page.keyboard.press("Tab");
      const focusInsideDialog = await page.evaluate(
        () => !!document.activeElement?.closest('[role="dialog"]'),
      );
      expect(focusInsideDialog).toBe(true);
    }

    // Switch sections and assert aria-current is unique and correct.
    await dialog.getByRole("button", { name: "偏好" }).click();
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);
    await expect(
      dialog.getByRole("button", { name: "偏好" }),
    ).toHaveAttribute("aria-current", "page");
    await expect(
      dialog.getByRole("button", { name: "个人资料" }),
    ).not.toHaveAttribute("aria-current", "page");

    await dialog.getByRole("button", { name: "用量与积分" }).click();
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);
    await expect(
      dialog.getByRole("button", { name: "用量与积分" }),
    ).toHaveAttribute("aria-current", "page");
    await expect(dialog.locator('button[aria-current="page"]')).toHaveCount(1);
  });

  test("Escape closes dialog, restores focus to sidebar trigger and keeps Reader DOM inert", async ({ page }) => {
    const trigger = page.getByRole("button", { name: "打开用户菜单" });
    const urlBefore = page.url();

    await openUserMenuWithKeyboard(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    // Reader remains mounted in DOM but is hidden/inert while the modal is open.
    const readerState = await page.evaluate(() => {
      const main = document.querySelector("main");
      return {
        present: !!main,
        ariaHidden:
          main?.getAttribute("aria-hidden") === "true" ||
          !!main?.closest('[aria-hidden="true"]'),
        inert: main?.hasAttribute("inert") ?? false,
        hasHeading: !!main?.querySelector("h1"),
      };
    });
    expect(readerState.present).toBe(true);
    expect(readerState.ariaHidden || readerState.inert).toBe(true);
    expect(readerState.hasHeading).toBe(true);

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/read$/);

    const active = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return el
        ? {
            tag: el.tagName,
            ariaLabel: el.getAttribute("aria-label"),
            role: el.getAttribute("role"),
            text: el.textContent?.slice(0, 40) ?? "",
          }
        : null;
    });
    expect(active).not.toBeNull();
    expect(active?.tag).toBe("BUTTON");
    expect(active?.ariaLabel).toBe("打开用户菜单");

    // Reader is interactive again: reopen the user menu via keyboard.
    await expect(trigger).toBeVisible();
    await expect(trigger).toBeEnabled();
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: "个人资料" })).toBeVisible();
  });

  test("close button closes dialog, restores focus and keeps Reader link clickable", async ({ page }) => {
    const urlBefore = page.url();
    await openUserMenuAndClickSettings(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "关闭设置" }).last().click();
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/read$/);

    const active = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return el
        ? {
            tag: el.tagName,
            ariaLabel: el.getAttribute("aria-label"),
          }
        : null;
    });
    expect(active?.tag).toBe("BUTTON");
    expect(active?.ariaLabel).toBe("打开用户菜单");

    const libraryLink = page.getByRole("link", { name: "全部阅读记录" }).first();
    await expect(libraryLink).toBeVisible();
    await expect(libraryLink).toBeEnabled();
    await libraryLink.click();
    await expect(page).toHaveURL(/\/app\/library/);
  });

  test("closing dialog restores focus to visible desktop trigger, never hidden mobile trigger", async ({ page }) => {
    const urlBefore = page.url();
    // Desktop viewport: mobile bottom nav is inside md:hidden (display:none).
    await openUserMenuAndClickSettings(page, "偏好设置");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "关闭设置" }).last().click();
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/read$/);

    const focusState = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return {
        tag: el?.tagName ?? null,
        ariaLabel: el?.getAttribute("aria-label") ?? null,
        matchesDesktopTrigger: el?.matches('[data-desktop-user-menu-trigger="true"]') ?? false,
        matchesMobileTrigger: el?.matches('[data-mobile-user-menu-trigger="true"]') ?? false,
        mobileTriggerHidden: (() => {
          const mobile = document.querySelector('[data-mobile-user-menu-trigger="true"]');
          if (!mobile) return null;
          let current: Element | null = mobile;
          while (current) {
            const style = getComputedStyle(current);
            if (style.display === "none") return true;
            if (style.visibility === "hidden" || style.visibility === "collapse") return true;
            if (current instanceof HTMLElement && current.hidden) return true;
            current = current.parentElement;
          }
          return false;
        })(),
      };
    });

    expect(focusState.tag).toBe("BUTTON");
    expect(focusState.ariaLabel).toBe("打开用户菜单");
    expect(focusState.matchesDesktopTrigger).toBe(true);
    expect(focusState.matchesMobileTrigger).toBe(false);
    expect(focusState.mobileTriggerHidden).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Desktop layout geometry — centered workspace, rail width, scroll contracts
// ---------------------------------------------------------------------------

const DESKTOP_VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
] as const;

for (const viewport of DESKTOP_VIEWPORTS) {
  test.describe(`Settings Dialog desktop layout geometry (${viewport.width}x${viewport.height})`, () => {
    test.setTimeout(180_000);

    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await mockBffRoutes(page);
      await loginWithMockPhone(page);
      await expect(page).toHaveURL(/\/app\/read$/);
      await lockSidebar(page);
    });

    test("dialog is centered and stays within safe margins", async ({ page }) => {
      const urlBefore = page.url();
      await openUserMenuAndClickSettings(page, "个人资料");
      await expectUrlUnchanged(page, urlBefore);

      const dialog = page.getByRole("dialog", { name: "设置" });
      await expect(dialog).toBeVisible();

      const box = await dialog.boundingBox();
      expect(box).not.toBeNull();

      const rem = 16;
      const maxWidth = 76 * rem;
      const maxHeight = 60 * rem;
      const safeMargin = 2 * rem;
      const expectedWidth = Math.min(maxWidth, viewport.width - safeMargin * 2);
      const expectedHeight = Math.min(maxHeight, viewport.height - safeMargin * 2);

      expect(box!.width).toBe(expectedWidth);
      expect(box!.height).toBe(expectedHeight);
      expect(box!.x).toBeCloseTo((viewport.width - expectedWidth) / 2, 0);
      expect(box!.y).toBeCloseTo((viewport.height - expectedHeight) / 2, 0);

      expect(box!.x).toBeGreaterThanOrEqual(safeMargin - 1);
      expect(box!.y).toBeGreaterThanOrEqual(safeMargin - 1);
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width - safeMargin + 1);
      expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height - safeMargin + 1);
    });

    test("rail is 12rem and content area shrinks without horizontal overflow", async ({ page }) => {
      const urlBefore = page.url();
      await openUserMenuAndClickSettings(page, "偏好设置");
      await expectUrlUnchanged(page, urlBefore);

      const dialog = page.getByRole("dialog", { name: "设置" });
      const nav = dialog.getByRole("navigation", { name: "设置分区" });
      await expect(nav).toBeVisible();

      const navBox = await nav.boundingBox();
      expect(navBox).not.toBeNull();
      expect(navBox!.width).toBeCloseTo(12 * 16, 0);

      const dialogBox = await dialog.boundingBox();
      const rightPanel = dialog.locator(".relative.flex.min-h-0.flex-1.flex-col").first();
      const rightBox = await rightPanel.boundingBox();
      expect(rightBox).not.toBeNull();
      expect(rightBox!.x).toBeCloseTo(navBox!.x + navBox!.width, 0);
      // Allow a 2px tolerance for hairline borders / sub-pixel rounding.
      const expectedRightWidth = dialogBox!.width - navBox!.width;
      expect(rightBox!.width).toBeGreaterThanOrEqual(expectedRightWidth - 2);
      expect(rightBox!.width).toBeLessThanOrEqual(expectedRightWidth + 2);

      await assertNoHorizontalOverflow(page, dialog);
    });

    test("section header stays fixed while body scrolls independently", async ({ page }) => {
      const urlBefore = page.url();
      await openUserMenuAndClickSettings(page, "个人资料");
      await expectUrlUnchanged(page, urlBefore);

      const dialog = page.getByRole("dialog", { name: "设置" });
      const header = dialog.getByRole("heading", { name: "个人资料", level: 2 }).first();
      const body = dialog.locator(".min-h-0.overflow-y-auto").first();
      await expect(body).toBeVisible();

      const headerBoxBefore = await header.boundingBox();
      expect(headerBoxBefore).not.toBeNull();

      // Inject a tall, transparent spacer so the body has scrollable range.
      await dialog.evaluate((dialogEl) => {
        const target = dialogEl.querySelector(".min-h-0.overflow-y-auto") as HTMLElement | null;
        if (!target) return;
        const spacer = document.createElement("div");
        spacer.style.height = "2000px";
        spacer.style.background = "transparent";
        spacer.setAttribute("data-testid", "scroll-spacer");
        target.appendChild(spacer);
      });

      await body.evaluate((el) => el.scrollTo(0, 500));
      expect(await body.evaluate((el) => el.scrollTop)).toBe(500);
      expect(await page.evaluate(() => window.scrollY)).toBe(0);

      const headerBoxAfter = await header.boundingBox();
      expect(headerBoxAfter!.y).toBeCloseTo(headerBoxBefore!.y, 0);

      // Clean up the spacer so it cannot affect downstream tests.
      await dialog.evaluate((dialogEl) => {
        dialogEl.querySelector("[data-testid='scroll-spacer']")?.remove();
      });
    });

    test("standard frame content column maxes at 40rem", async ({ page }) => {
      const urlBefore = page.url();
      await openUserMenuAndClickSettings(page, "个人资料");
      await expectUrlUnchanged(page, urlBefore);

      const dialog = page.getByRole("dialog", { name: "设置" });
      await dialog.getByRole("button", { name: "支持" }).click();
      // Section switch must NOT change URL.
      await expectUrlUnchanged(page, urlBefore);
      await expectNoSettingsUrl(page);

      const wrapper = dialog.locator(".min-h-0.overflow-y-auto > div").first();
      const maxWidth = await wrapper.evaluate((el) => getComputedStyle(el).maxWidth);
      expect(maxWidth).toBe("640px");
    });

    test("preferences and support content are not clipped at 1440 width", async ({ page }) => {
      // Skip the 1920 variant; the 1440 width is the tightest desktop case.
      test.skip(viewport.width !== 1440, "only relevant at 1440px");

      const sections = [
        { menuLabel: "偏好设置" as const, navLabel: "偏好" as const, heading: "偏好" as const },
        { menuLabel: "个人资料" as const, navLabel: "支持" as const, heading: "支持" as const },
      ];

      for (const [index, section] of sections.entries()) {
        const dialog = page.getByRole("dialog", { name: "设置" });
        const urlBefore = page.url();
        if (index === 0) {
          await openUserMenuAndClickSettings(page, section.menuLabel);
          await expect(dialog).toBeVisible();
          await expectUrlUnchanged(page, urlBefore);
        }
        await dialog.getByRole("button", { name: section.navLabel }).click();
        await expectUrlUnchanged(page, urlBefore);
        await expectNoSettingsUrl(page);
        const heading = dialog.getByRole("heading", { name: section.heading, level: 2 });
        await expect(heading).toBeVisible();
        await assertNoHorizontalOverflow(page, dialog);
        await assertAllVisibleControlsWithinBounds(page, dialog);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Mobile entry parity — discover and open Settings from the bottom nav
// ---------------------------------------------------------------------------

test.describe("Settings Dialog mobile entry from /app/read", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
  });

  test("个人资料 opens dialog at account section from mobile bottom nav without changing URL", async ({ page }) => {
    const urlBefore = page.url();

    await openMobileUserMenuAndClickSettings(page, "个人资料");

    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "个人资料", level: 2 }),
    ).toBeVisible();

    const trigger = page.locator('[data-mobile-user-menu-trigger="true"]');
    await assertTouchTargetAtLeast44px(trigger);
  });

  test("偏好设置 opens dialog at preferences section from mobile bottom nav without changing URL", async ({ page }) => {
    const urlBefore = page.url();

    await openMobileUserMenuAndClickSettings(page, "偏好设置");

    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();

    const trigger = page.locator('[data-mobile-user-menu-trigger="true"]');
    await assertTouchTargetAtLeast44px(trigger);
  });

  test("用量与积分 opens dialog at usage section from mobile bottom nav without changing URL", async ({ page }) => {
    const urlBefore = page.url();

    await openMobileUserMenuAndClickSettings(page, "用量与积分");

    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "用量与积分", level: 2 }),
    ).toBeVisible();

    const trigger = page.locator('[data-mobile-user-menu-trigger="true"]');
    await assertTouchTargetAtLeast44px(trigger);
  });

  test("closing dialog returns to /app/read and restores focus to mobile trigger", async ({ page }) => {
    const urlBefore = page.url();
    await openMobileUserMenuAndClickSettings(page, "偏好设置");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "关闭设置" }).first().click();
    await expectDialogClosedAndBackOnReader(page);

    const trigger = page.locator('[data-mobile-user-menu-trigger="true"]');
    await expect(trigger).toBeVisible();
    await expect(trigger).toBeEnabled();

    // Focus must be restored to the mobile user-menu trigger, not just body.
    await expect
      .poll(async () => {
        return page.evaluate(
          (selector) => document.activeElement?.matches(selector) ?? false,
          '[data-mobile-user-menu-trigger="true"]',
        );
      })
      .toBe(true);

    // The restored trigger remains keyboard-operable.
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: "个人资料" })).toBeVisible();
  });

  test("pressing Escape closes dialog and restores focus to mobile trigger", async ({ page }) => {
    const urlBefore = page.url();
    await openMobileUserMenuAndClickSettings(page, "偏好设置");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expectDialogClosedAndBackOnReader(page);

    await expect
      .poll(async () => {
        return page.evaluate(
          (selector) => document.activeElement?.matches(selector) ?? false,
          '[data-mobile-user-menu-trigger="true"]',
        );
      })
      .toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Mobile layout geometry — full-screen sheet, chrome, scroll, overflow
// ---------------------------------------------------------------------------

test.describe("Settings Dialog mobile layout geometry", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);

    await openUserMenuAndClickSettings(page, "个人资料");
    await expectNoSettingsUrl(page);
    await page.setViewportSize({ width: 390, height: 844 });
  });

  test("dialog fills the viewport precisely", async ({ page }) => {
    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    const box = await dialog.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBe(0);
    expect(box!.y).toBe(0);
    expect(box!.width).toBe(390);
    expect(box!.height).toBe(844);
  });

  test("close button and nav buttons meet 44px touch targets", async ({ page }) => {
    const dialog = page.getByRole("dialog", { name: "设置" });
    const closeBtn = dialog.getByRole("button", { name: "关闭设置" }).first();
    await expect(closeBtn).toHaveCSS("height", "44px");
    await expect(closeBtn).toHaveCSS("width", "44px");

    const nav = dialog.getByRole("navigation", { name: "设置分区" });
    const buttons = await nav.getByRole("button").all();
    expect(buttons.length).toBeGreaterThan(0);
    for (const btn of buttons) {
      await expect(btn).toHaveCSS("min-height", "44px");
    }
  });

  test("top nav is horizontally scrollable and body scrolls independently", async ({ page }) => {
    const dialog = page.getByRole("dialog", { name: "设置" });
    const nav = dialog.getByRole("navigation", { name: "设置分区" });
    await expect(nav).toHaveCSS("overflow-x", "auto");

    const closeBar = dialog.locator(".flex.shrink-0.justify-end.p-2").first();
    const navBox = await nav.boundingBox();
    const closeBarBox = await closeBar.boundingBox();
    expect(closeBarBox).not.toBeNull();
    expect(navBox).not.toBeNull();
    expect(closeBarBox!.y + closeBarBox!.height).toBeLessThanOrEqual(navBox!.y + 1);

    const body = dialog.locator(".min-h-0.overflow-y-auto").first();
    await dialog.evaluate((dialogEl) => {
      const target = dialogEl.querySelector(".min-h-0.overflow-y-auto") as HTMLElement | null;
      if (!target) return;
      const spacer = document.createElement("div");
      spacer.style.height = "2000px";
      spacer.style.background = "transparent";
      spacer.setAttribute("data-testid", "mobile-scroll-spacer");
      target.appendChild(spacer);
    });

    const navBoxBefore = navBox;
    await body.evaluate((el) => el.scrollTo(0, 500));
    expect(await body.evaluate((el) => el.scrollTop)).toBe(500);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);

    const navBoxAfter = await nav.boundingBox();
    expect(navBoxAfter!.y).toBeCloseTo(navBoxBefore!.y, 0);

    await dialog.evaluate((dialogEl) => {
      dialogEl.querySelector("[data-testid='mobile-scroll-spacer']")?.remove();
    });
  });

  test("no horizontal page overflow and close button does not overlap content", async ({ page }) => {
    const dialog = page.getByRole("dialog", { name: "设置" });
    await assertNoHorizontalOverflow(page, dialog);

    const closeBtn = dialog.getByRole("button", { name: "关闭设置" }).first();
    const closeBox = await closeBtn.boundingBox();
    const body = dialog.locator(".min-h-0.overflow-y-auto").first();
    const bodyBox = await body.boundingBox();
    expect(closeBox).not.toBeNull();
    expect(bodyBox).not.toBeNull();
    expect(closeBox!.x + closeBox!.width).toBeLessThanOrEqual(390 + 1);
    expect(closeBox!.y + closeBox!.height).toBeLessThanOrEqual(bodyBox!.y + 1);
  });
});

// ---------------------------------------------------------------------------
// Light / Dark computed style contract
// ---------------------------------------------------------------------------

for (const theme of ["light", "dark"] as const) {
  test.describe(`Settings Dialog ${theme} theme computed styles`, () => {
    test.setTimeout(180_000);

    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await mockBffRoutes(page);
      // Set the theme on the login origin before authenticating so the
      // /app/read page mounts with the resolved theme already applied.
      await page.goto("/login?next=/app/read");
      await setTheme(page, theme);
      await loginWithMockPhone(page);
      await expect(page).toHaveURL(/\/app\/read$/);

      // Explicitly verify the resolved theme class is applied before any
      // color assertions; do not rely solely on localStorage or color distance.
      const hasDarkClass = await page.evaluate(() =>
        document.documentElement.classList.contains("dark"),
      );
      expect(hasDarkClass).toBe(theme === "dark");

      await lockSidebar(page);
    });

    test("dialog and rail have opaque, layered backgrounds", async ({ page }) => {
      const urlBefore = page.url();
      await openUserMenuAndClickSettings(page, "个人资料");
      await expectUrlUnchanged(page, urlBefore);

      const dialog = page.getByRole("dialog", { name: "设置" });
      const nav = dialog.getByRole("navigation", { name: "设置分区" });

      const dialogBg = await getBackgroundColor(dialog);
      const railBg = await getBackgroundColor(nav);

      const [, , , dialogAlpha] = parseRgb(dialogBg);
      const [, , , railAlpha] = parseRgb(railBg);
      expect(dialogAlpha).toBe(1);
      expect(railAlpha).toBe(1);
      expect(colorDistance(dialogBg, railBg)).toBeGreaterThan(10);

      // Selected nav item sits on a visibly different surface than the rail.
      const selectedBtn = dialog.locator('button[aria-current="page"]').first();
      await expect(selectedBtn).toBeVisible();
      const selectedBg = await getBackgroundColor(selectedBtn);
      expect(colorDistance(selectedBg, railBg)).toBeGreaterThan(5);
    });

    test("header hairline and focus ring are visible", async ({ page }) => {
      const urlBefore = page.url();
      await openUserMenuAndClickSettings(page, "个人资料");
      await expectUrlUnchanged(page, urlBefore);

      const dialog = page.getByRole("dialog", { name: "设置" });
      const header = dialog.locator(".shrink-0.border-b.border-hairline").first();
      const borderColor = await header.evaluate((el) => getComputedStyle(el).borderBottomColor);
      const [, , , borderAlpha] = parseRgb(borderColor);
      expect(borderAlpha).toBeGreaterThan(0);

      // Focus a rail button and confirm a focus ring is painted.
      const navBtn = dialog.getByRole("button", { name: "偏好" });
      await navBtn.focus();
      await expect(navBtn).toBeFocused();
      const ring = await navBtn.evaluate((el) => {
        const style = getComputedStyle(el);
        return {
          boxShadow: style.boxShadow,
          outlineWidth: style.outlineWidth,
        };
      });
      const hasVisibleRing =
        ring.boxShadow !== "none" || parseFloat(ring.outlineWidth) > 0;
      expect(hasVisibleRing).toBe(true);
    });
  });
}

// ---------------------------------------------------------------------------
// Library host page — opening Settings from a non-Reader host page
// ---------------------------------------------------------------------------

test.describe("Settings Dialog opens from Library host page without URL change", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);

    // Navigate to /app/library via the sidebar link so the host page is
    // the Library, not the Reader. The user menu is rendered by AppShell
    // and is shared across all private routes.
    const libraryLink = page.getByRole("link", { name: "全部阅读记录" }).first();
    await expect(libraryLink).toBeVisible();
    await libraryLink.click();
    await expect(page).toHaveURL(/\/app\/library/);
  });

  test("个人资料 opens from Library at account section without changing URL", async ({ page }) => {
    const urlBefore = page.url();
    expect(urlBefore).toContain("/app/library");

    await openUserMenuAndClickSettings(page, "个人资料");

    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "个人资料", level: 2 }),
    ).toBeVisible();
  });

  test("switching sections inside dialog opened from Library does not change URL", async ({ page }) => {
    const urlBefore = page.url();
    expect(urlBefore).toContain("/app/library");

    await openUserMenuAndClickSettings(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    const nav = dialog.getByRole("navigation", { name: "设置分区" });

    await nav.getByRole("button", { name: "偏好" }).click();
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    await nav.getByRole("button", { name: "用量与积分" }).click();
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);
  });

  test("Back from Dialog opened on Library returns to Library", async ({ page }) => {
    const urlBefore = page.url();
    expect(urlBefore).toContain("/app/library");

    await openUserMenuAndClickSettings(page, "偏好设置");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    // Browser Back should close the dialog and return to the Library host page.
    await page.goBack();
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/library/);
  });

  test("close button closes dialog and returns to Library", async ({ page }) => {
    const urlBefore = page.url();
    expect(urlBefore).toContain("/app/library");

    await openUserMenuAndClickSettings(page, "用量与积分");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "关闭设置" }).last().click();
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/library/);
  });

  test("Escape closes dialog and returns to Library", async ({ page }) => {
    const urlBefore = page.url();
    expect(urlBefore).toContain("/app/library");

    await openUserMenuAndClickSettings(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/library/);
  });
});

// ---------------------------------------------------------------------------
// Command Palette entry — open Settings without changing URL
// ---------------------------------------------------------------------------

test.describe("Command Palette opens Settings Dialog without changing URL", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);
  });

  test("selecting 设置 from Command Palette opens preferences section without changing URL (Reader host)", async ({ page }) => {
    const urlBefore = page.url();

    // Trigger Cmd/Ctrl+K to open the Command Palette.
    await page.keyboard.press("ControlOrMeta+K");

    const palette = page.getByRole("dialog", { name: "命令面板" });
    await expect(palette).toBeVisible();

    // The page-settings command lives in the "页面" group. Use a scoped
    // locator to avoid matching the nav button inside the Settings Dialog.
    const settingsItem = palette.getByRole("option", { name: "设置" });
    await expect(settingsItem).toBeVisible();
    await settingsItem.click();

    // Command Palette closes and Settings Dialog opens at preferences.
    await expect(palette).toHaveCount(0);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();

    // URL must NOT have changed at any point.
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);
  });

  test("selecting 设置 from Command Palette opens preferences section without changing URL (Library host)", async ({ page }) => {
    // Navigate to /app/library via the sidebar link so the host page is
    // the Library, not the Reader. The user menu and Command Palette are
    // both rendered by AppShell and shared across all private routes.
    const libraryLink = page.getByRole("link", { name: "全部阅读记录" }).first();
    await expect(libraryLink).toBeVisible();
    await libraryLink.click();
    await expect(page).toHaveURL(/\/app\/library/);

    const urlBefore = page.url();
    expect(urlBefore).toContain("/app/library");

    await page.keyboard.press("ControlOrMeta+K");

    const palette = page.getByRole("dialog", { name: "命令面板" });
    await expect(palette).toBeVisible();

    const settingsItem = palette.getByRole("option", { name: "设置" });
    await expect(settingsItem).toBeVisible();
    await settingsItem.click();

    await expect(palette).toHaveCount(0);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();

    // URL must NOT have changed at any point.
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    // Closing via Escape must keep the user on the Library host page.
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/library/);
    await expectNoSettingsUrl(page);
  });
});

// ---------------------------------------------------------------------------
// Legacy /app/settings routes — server redirect to /app/read
// ---------------------------------------------------------------------------

test.describe("Legacy /app/settings routes redirect to /app/read", () => {
  test.setTimeout(120_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
  });

  test("/app/settings redirects to /app/read and renders no full-page Settings UI", async ({ page }) => {
    await page.goto("/app/settings");
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);

    // No full-page Settings UI must be rendered after the redirect.
    await expect(page.getByRole("heading", { name: "设置", level: 1 })).toHaveCount(0);
    // AppShell Reader shell is the visible surface.
    await expect(page.locator("[data-app-sidebar='rail']")).toBeVisible();
  });

  test("/app/settings?section=account redirects to /app/read", async ({ page }) => {
    await page.goto("/app/settings?section=account");
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
  });

  test("/app/settings?section=preferences redirects to /app/read", async ({ page }) => {
    await page.goto("/app/settings?section=preferences");
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
  });

  test("/app/settings?section=usage redirects to /app/read", async ({ page }) => {
    await page.goto("/app/settings?section=usage");
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
  });

  test("/app/settings?section=support redirects to /app/read", async ({ page }) => {
    await page.goto("/app/settings?section=support");
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
  });

  test("/app/settings/feedback redirects to /app/read", async ({ page }) => {
    await page.goto("/app/settings/feedback");
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
    // No legacy feedback form surface.
    await expect(page.getByRole("heading", { name: "意见反馈", level: 1 })).toHaveCount(0);
  });

  test("/app/settings/ledger redirects to /app/read", async ({ page }) => {
    await page.goto("/app/settings/ledger");
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
    // No legacy ledger surface.
    await expect(page.getByRole("heading", { name: "积分明细", level: 1 })).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Page reload restoration — history marker survives reload
// ---------------------------------------------------------------------------

test.describe("Settings Dialog restores after page reload when history marker present", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);
  });

  test("reloading after opening Settings restores the Dialog and can be closed normally", async ({ page }) => {
    const urlBefore = page.url();
    expect(urlBefore).toMatch(/\/app\/read$/);

    // Open Settings — pushes a marker into history.state without changing URL.
    await openUserMenuAndClickSettings(page, "偏好设置");
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);

    const dialogBefore = page.getByRole("dialog", { name: "设置" });
    await expect(dialogBefore).toBeVisible();
    await expect(
      dialogBefore.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();

    // Reload: browser preserves history.state across reload, so the marker
    // survives and SettingsDialogProvider re-opens the Dialog on mount.
    await page.reload();

    // URL must remain on the host page (no /app/settings).
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);

    const dialogAfter = page.getByRole("dialog", { name: "设置" });
    await expect(dialogAfter).toBeVisible();

    // Close button must still work and return to the host page.
    await dialogAfter.getByRole("button", { name: "关闭设置" }).last().click();
    await expect(dialogAfter).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
  });

  test("reloading after switching sections restores the Dialog at the latest section", async ({ page }) => {
    const urlBefore = page.url();

    await openUserMenuAndClickSettings(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    const nav = dialog.getByRole("navigation", { name: "设置分区" });

    // Switch to usage — replaceState updates the marker's section field.
    await nav.getByRole("button", { name: "用量与积分" }).click();
    await expectUrlUnchanged(page, urlBefore);
    await expectNoSettingsUrl(page);
    await expect(
      dialog.getByRole("heading", { name: "用量与积分", level: 2 }),
    ).toBeVisible();

    await page.reload();

    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);

    const dialogAfter = page.getByRole("dialog", { name: "设置" });
    await expect(dialogAfter).toBeVisible();
    // The latest section (usage) must be the one restored after reload.
    await expect(
      dialogAfter.getByRole("heading", { name: "用量与积分", level: 2 }),
    ).toBeVisible();

    // Escape must still close the dialog.
    await page.keyboard.press("Escape");
    await expect(dialogAfter).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
  });

  test("Back after reload closes the restored Dialog and returns to the host page", async ({ page }) => {
    const urlBefore = page.url();

    await openUserMenuAndClickSettings(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    await page.reload();
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    // Browser Back: the marker entry was created by pushState, so Back
    // returns to the host page entry and popstate closes the Dialog.
    await page.goBack();
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
  });
});
