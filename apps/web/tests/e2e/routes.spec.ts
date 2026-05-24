import { expect, test, type Page } from "@playwright/test";

async function loginAsDebugUser(page: Page) {
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
        message: "已进入本地调试登录态；未配置 FastAPI debug session，真实账户数据不可用。",
      }),
    });
  });

  await page.goto("/login?next=/app/read");
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await expect(page.getByText("本地调试验证码已生成，请使用 888888。")).toBeVisible();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL("**/app/read");
}

test.describe("Claread web routes", () => {
  test("public pages render and unauthenticated app routes redirect to login", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: "每日精读", exact: true })).toBeVisible();
    await expect(page.locator("header").getByRole("link", { name: "登录", exact: true })).toBeVisible();

    await page.goto("/daily");
    await expect(page.locator("header").getByRole("link", { name: "公开示例", exact: true })).toBeVisible();

    await page.goto("/examples/news-brief");
    await expect(page.getByRole("link", { name: "返回 Daily" })).toBeVisible();

    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "继续使用 Claread" })).toBeVisible();

    await page.goto("/app/read");
    await page.waitForURL("**/login?next=%2Fapp%2Fread");

    await page.goto("/app/library");
    await page.waitForURL("**/login?next=%2Fapp%2Flibrary");

    await page.goto("/app/settings");
    await page.waitForURL("**/login?next=%2Fapp%2Fsettings");
  });

  test("login flows into new app routes and public/app navigation stays connected", async ({ page }) => {
    await loginAsDebugUser(page);

    await expect(page).toHaveURL(/\/app\/read$/);
    await expect(page.getByRole("button", { name: "开始透读" })).toBeVisible();

    await page.goto("/app/library");
    await expect(page).toHaveURL(/\/app\/library$/);
    await expect(page.getByRole("heading", { name: "Reading Archive." })).toBeVisible();
    await expect(page.getByRole("link", { name: "阅读记录" })).toHaveClass(/app-nav-item--active/);

    await page.goto("/app/vocabulary");
    await expect(page).toHaveURL(/\/app\/vocabulary$/);
    await expect(page.getByRole("heading", { name: "生词本" })).toBeVisible();
    await expect(page.getByRole("link", { name: "生词本" })).toHaveClass(/app-nav-item--active/);

    await page.goto("/app/settings");
    await expect(page).toHaveURL(/\/app\/settings$/);
    await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
    await expect(page.getByRole("link", { name: "设置" })).toHaveClass(/app-nav-item--active/);

    await expect(page.getByRole("link", { name: "返回公共首页" })).toBeVisible();
    await page.getByRole("link", { name: "返回公共首页" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator("header").getByRole("link", { name: "打开调试工作区", exact: true })).toBeVisible();
  });

  test("analysis submit can navigate into the new reader route", async ({ page }) => {
    await loginAsDebugUser(page);

    await page.route("**/api/web/analysis/submit", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          status: "succeeded",
          message: "解析完成。",
          recordId: "mock-record",
          readerUrl: "/app/reader/mock-record",
        }),
      });
    });

    await page.getByPlaceholder("在此粘贴文章正文...").fill("Cities are not only built to be crossed, but also to be read.");
    await page.getByRole("button", { name: "开始透读" }).click();

    await page.waitForURL("**/app/reader/mock-record");
    await expect(page.getByRole("heading", { name: "无法打开阅读记录" })).toBeVisible();
  });
});
