import { expect, test, type Page } from "@playwright/test";

/**
 * Settings Dialog browser journeys: history/Back, focus/Escape, reload.
 * Geometry/theme/host/section matrix and legacy redirects live in Vitest
 * owners plus routes.spec.ts.
 */

// ---------------------------------------------------------------------------
// Auth + BFF mocks (mirrors modal-backdrop-contract.spec.ts)
// ---------------------------------------------------------------------------

async function loginWithSessionCookie(page: Page) {
  await page.goto("/");
  await page.context().addCookies([
    { name: "claread_web_session", value: "e2e-session", path: "/", domain: "127.0.0.1" },
  ]);
  await page.goto("/app/read");
  await page.waitForURL((url) => url.pathname === "/app/read", { timeout: 90_000 });
}

async function mockBffRoutes(page: Page) {
  await page.route("**/api/web/settings-dialog", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        data: {
          accountData: {
            nickname: "调试用户",
            displayFallback: "调试用户",
            status: "ready",
            avatarText: "调",
          },
          preferencesData: {
            readingGoal: "daily_reading",
            readingVariant: "intermediate_reading",
            canEdit: true,
          },
        },
      }),
    });
  });

  await page.route("**/api/web/profile", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        status: "ready",
        session: {
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

test.describe("Settings Dialog history, focus, and reload", () => {
  test.setTimeout(180_000);

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockBffRoutes(page);
    await loginWithSessionCookie(page);
    await expect(page).toHaveURL((url) => url.pathname === "/app/read");
    await lockSidebar(page);
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
    await expect(
      dialog.getByRole("heading", { name: "偏好", level: 2 }),
    ).toBeVisible();

    await page.goBack();
    await expectDialogClosedAndBackOnReader(page);
  });

  test("Escape closes dialog, restores focus to sidebar trigger and keeps Reader DOM inert", async ({ page }) => {
    const trigger = page.getByRole("button", { name: "打开用户菜单" });
    const urlBefore = page.url();
    await openUserMenuWithKeyboard(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await expect(dialog).toBeVisible();
    const readerState = await page.evaluate(() => {
      const main = document.querySelector("main");
      return {
        present: !!main,
        ariaHidden:
          main?.getAttribute("aria-hidden") === "true" ||
          !!main?.closest('[aria-hidden="true"]'),
        inert: main?.hasAttribute("inert") ?? false,
      };
    });
    expect(readerState.present).toBe(true);
    expect(readerState.ariaHidden || readerState.inert).toBe(true);

    await page.keyboard.press("Escape");
    await expectDialogClosedAndBackOnReader(page);
    await expect(trigger).toBeFocused();
  });

  test("reloading after switching sections restores the Dialog; Back closes it", async ({ page }) => {
    const urlBefore = page.url();
    await openUserMenuAndClickSettings(page, "个人资料");
    await expectUrlUnchanged(page, urlBefore);

    const dialog = page.getByRole("dialog", { name: "设置" });
    await dialog.getByRole("navigation", { name: "设置分区" }).getByRole("button", { name: "用量与积分" }).click();
    await expect(
      dialog.getByRole("heading", { name: "用量与积分", level: 2 }),
    ).toBeVisible();

    await page.reload();
    await expect(page).toHaveURL(/\/app\/read$/);
    await expectNoSettingsUrl(page);
    const restored = page.getByRole("dialog", { name: "设置" });
    await expect(restored).toBeVisible();
    await expect(
      restored.getByRole("heading", { name: "用量与积分", level: 2 }),
    ).toBeVisible();

    await page.goBack();
    await expect(restored).toHaveCount(0);
    await expect(page).toHaveURL(/\/app\/read$/);
  });
});
