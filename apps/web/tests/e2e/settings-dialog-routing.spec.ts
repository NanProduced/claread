import { expect, test, type Page } from "@playwright/test";

/**
 * Settings Dialog routing regression — real Chromium, mock auth.
 *
 * Verifies that opening Settings from the Reader sidebar user menu lands on
 * the correct intercepted route (?section=), keeps the underlying Reader page
 * in history, switches sections with replace semantics, and closes correctly
 * via close button, Escape, and overlay click. Also covers mobile viewport
 * sheet behaviour without modifying production UI, routing, or config.
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

  await page.route("**/api/web/reading-records**", async (route) => {
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
  const section =
    label === "个人资料"
      ? "account"
      : label === "偏好设置"
        ? "preferences"
        : "usage";
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
  await expect(page).toHaveURL(
    new RegExp(`/app/settings\\?section=${section}$`),
  );
}

async function expectDialogClosedAndBackOnReader(page: Page) {
  await expect(page.getByRole("dialog", { name: "设置" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/app\/read$/);
}

// ---------------------------------------------------------------------------
// Desktop routing from Reader sidebar
// ---------------------------------------------------------------------------

test.describe("Settings Dialog routing from Reader sidebar", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithMockPhone(page);
    await expect(page).toHaveURL(/\/app\/read$/);
    await lockSidebar(page);
  });

  test("个人资料 opens dialog at ?section=account and shows account heading", async ({ page }) => {
    await openUserMenuAndClickSettings(page, "个人资料");

    await expect(page).toHaveURL(/\/app\/settings\?section=account$/);
    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "个人资料", level: 2 }),
    ).toBeVisible();
  });

  test("偏好设置 opens dialog at ?section=preferences and shows preferences heading", async ({ page }) => {
    await openUserMenuAndClickSettings(page, "偏好设置");

    await expect(page).toHaveURL(/\/app\/settings\?section=preferences$/);
    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();
  });

  test("用量与积分 opens dialog at ?section=usage and shows placeholder", async ({ page }) => {
    await openUserMenuAndClickSettings(page, "用量与积分");

    await expect(page).toHaveURL(/\/app\/settings\?section=usage$/);
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

  test("switching sections inside dialog updates URL via replace and back returns to Reader", async ({ page }) => {
    await openUserMenuAndClickSettings(page, "个人资料");
    await expect(page).toHaveURL(/\/app\/settings\?section=account$/);

    const dialog = page.getByRole("dialog", { name: "设置" });
    const nav = dialog.getByRole("navigation", { name: "设置分区" });

    await nav.getByRole("button", { name: "偏好" }).click();
    await expect(page).toHaveURL(/\/app\/settings\?section=preferences$/);
    await expect(
      dialog.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();

    await nav.getByRole("button", { name: "用量与积分" }).click();
    await expect(page).toHaveURL(/\/app\/settings\?section=usage$/);
    await expect(
      dialog.getByRole("heading", { name: "用量与积分", level: 2 }),
    ).toBeVisible();

    // Because section switches use router.replace, browser back should skip
    // them and return directly to the underlying Reader page.
    await page.goBack();
    await expect(page).toHaveURL(/\/app\/read$/);
    await expect(dialog).toHaveCount(0);
  });

  test("close button closes dialog and returns to Reader", async ({ page }) => {
    await openUserMenuAndClickSettings(page, "个人资料");
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
    await openUserMenuAndClickSettings(page, "偏好设置");
    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expectDialogClosedAndBackOnReader(page);
  });

  test("overlay click intercepts a sidebar click and closes dialog via router.back", async ({ page }) => {
    // Use a wider viewport so the app sidebar is fully outside the centered
    // dialog content; the click then lands on the overlay, not on the dialog's
    // own rail, and should trigger router.back().
    await page.setViewportSize({ width: 1920, height: 1080 });

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
    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    await expect(page).toHaveURL(/\/app\/settings\?section=usage$/);

    // Click at the pre-captured sidebar link coordinates. The overlay is above
    // the sidebar and will intercept; for the route-based dialog the overlay
    // click triggers router.back(), returning to /app/read.
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
    await expect(page).toHaveURL(/\/app\/settings\?section=account$/);
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

  test("opens 个人资料 from user menu via keyboard and moves focus into dialog", async ({ page }) => {
    await openUserMenuWithKeyboard(page, "个人资料");

    await expect(page).toHaveURL(/\/app\/settings\?section=account$/);
    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();

    const focusInsideDialog = await page.evaluate(
      () => !!document.activeElement?.closest('[role="dialog"]'),
    );
    expect(focusInsideDialog).toBe(true);
  });

  test("focus is trapped inside dialog and aria-current updates uniquely", async ({ page }) => {
    await openUserMenuWithKeyboard(page, "个人资料");
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
    await expect(
      dialog.getByRole("button", { name: "偏好" }),
    ).toHaveAttribute("aria-current", "page");
    await expect(
      dialog.getByRole("button", { name: "个人资料" }),
    ).not.toHaveAttribute("aria-current", "page");

    await dialog.getByRole("button", { name: "用量与积分" }).click();
    await expect(
      dialog.getByRole("button", { name: "用量与积分" }),
    ).toHaveAttribute("aria-current", "page");
    await expect(dialog.locator('button[aria-current="page"]')).toHaveCount(1);
  });

  test("Escape closes dialog, restores focus to sidebar trigger and keeps Reader DOM inert", async ({ page }) => {
    const trigger = page.getByRole("button", { name: "打开用户菜单" });
    await openUserMenuWithKeyboard(page, "个人资料");
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
    await openUserMenuAndClickSettings(page, "个人资料");
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
});
