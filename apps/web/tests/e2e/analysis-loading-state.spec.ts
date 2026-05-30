import { expect, test, type Page } from "@playwright/test";

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
  await page.waitForURL("**/app/read");
}

test("analysis loading state uses compact reassurance copy without progress CTA", async ({ page }) => {
  await page.setViewportSize({ width: 2048, height: 1280 });

  await page.route("**/api/web/analysis/current", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        hasActive: true,
        task: {
          taskId: "mock-active-task",
          recordId: "mock-active-record",
          status: "running",
          readerUrl: "/app/reader/mock-active-record",
          failureCode: null,
          failureMessage: null,
        },
      }),
    });
  });

  await page.route("**/api/web/analysis/tasks/mock-active-task", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        taskId: "mock-active-task",
        recordId: "mock-active-record",
        status: "running",
        readerUrl: "/app/reader/mock-active-record",
      }),
    });
  });

  await loginWithMockPhone(page);

  await expect(page.getByRole("heading", { name: "有一篇文章正在透读" })).toBeVisible();
  await expect(page.getByText("正在梳理文章结构")).toBeVisible();
  await expect(page.getByText("离开本页不会影响透读，完成后会保存到阅读记录")).toBeVisible();
  await expect(page.getByRole("button", { name: "去记录页" })).toHaveCount(0);

  const statusText = await page.locator("body").innerText();
  expect(statusText).not.toMatch(/\d{1,2}:\d{2}/);
  expect(statusText).not.toContain("%");
  expect(statusText).not.toContain("第");
  expect(statusText).not.toContain("共");

  await page.screenshot({
    path: "test-results/analysis-loading-status-desktop.png",
    fullPage: false,
  });
});
