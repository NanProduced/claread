import { expect, test, type Page } from "@playwright/test";

async function loginAsDebugUser(page: Page) {
  await page.goto("/");
  await page.context().addCookies([
    { name: "claread_web_session", value: "e2e-session", path: "/", domain: "127.0.0.1" },
  ]);
  await page.goto("/app/read");
  await page.waitForURL((url) => url.pathname === "/app/read");
}

test.describe("Claread web routes", () => {
  test("public pages render and unauthenticated app routes redirect to login", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: "每日精读", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "打开 Claread", exact: true }).first()).toBeVisible();

    await page.goto("/daily");
    await expect(page.getByText("Claread Daily", { exact: true })).toBeVisible();

    const removedExample = await page.goto("/examples/news-brief");
    expect(removedExample?.status()).toBe(404);

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
    await expect(page.getByRole("heading", { name: "阅读记录" })).toBeVisible();
    await expect(page.getByRole("link", { name: "全部阅读记录", exact: true })).toHaveAttribute("aria-current", "page");

    await page.goto("/app/vocabulary");
    await expect(page).toHaveURL(/\/app\/vocabulary$/);
    await expect(page.getByRole("heading", { name: "Vocabulary Book." })).toBeVisible();
    await expect(page.getByRole("link", { name: "生词本", exact: true })).toHaveAttribute("aria-current", "page");

    for (const legacy of [
      "/app/settings",
      "/app/settings?section=account",
      "/app/settings?section=preferences",
      "/app/settings?section=usage",
      "/app/settings?section=support",
      "/app/settings/feedback",
      "/app/settings/ledger",
    ]) {
      await page.goto(legacy);
      await expect(page).toHaveURL(/\/app\/read$/);
      await expect(page.getByRole("heading", { name: "设置", exact: true })).toHaveCount(0);
    }

    await page.goto("/");
    const publicCta = page.locator("header").getByRole("link", { name: "打开 Claread", exact: true });
    await expect(publicCta).toBeVisible();
    await publicCta.click();
    await expect(page).toHaveURL(/\/app\/read$/);
  });

  test("Reader submit can navigate into the canonical Reader route", async ({ page }) => {
    const requestedPaths: string[] = [];
    page.on("request", (request) => {
      requestedPaths.push(new URL(request.url()).pathname);
    });
    await loginAsDebugUser(page);

    await page.route("**/api/web/reader/records/input", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          outcome: "stable_document_ready",
          reading_record_id: "mock-record",
          original_input_id: "mock-input",
        }),
      });
    });

    await page.route("**/api/web/reader/records/mock-record/snapshot", async (route) => {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          ok: false,
          status: 409,
          code: "record_not_ready",
          message: "阅读记录仍在准备中。",
        }),
      });
    });

    await page.getByRole("textbox", { name: "在此贴入或导入英文文章" }).fill("Cities are not only built to be crossed, but also to be read.");
    await page.getByRole("button", { name: "开始透读" }).click();

    await page.waitForURL("**/app/reader/mock-record");
    await expect(page.getByText("文档仍在解析")).toBeVisible();
    expect(requestedPaths.some((path) =>
      /\/api\/web\/(reader-plate|reader-ask|reading-records?|reader-notes|annotations|favorites|analysis)/.test(path),
    )).toBe(false);
  });

  test("retired Reader pages and BFF endpoints return 404 without aliases", async ({
    page,
    request,
  }) => {
    await loginAsDebugUser(page);

    for (const path of [
      "/app/reader-record/mock-record",
      "/app/reader-plate",
      "/app/f7-ask-fixture/mock-record",
    ]) {
      const response = await page.goto(path);
      expect(response?.status(), path).toBe(404);
    }

    for (const path of [
      "/api/web/reader-plate/mock-record/snapshot",
      "/api/web/reader-plate/submit",
      "/api/web/reader-ask/model-options",
      "/api/web/reader/records/plain-text",
      "/api/web/reading-records",
      "/api/web/reading-record/mock-record/submit",
      "/api/web/reader-notes",
      "/api/web/annotations",
      "/api/web/favorites/mock-record",
      "/api/web/analysis/current",
      "/api/web/reader/mock-record",
      "/api/web/records/mock-record",
    ]) {
      const response = await request.get(path);
      expect(response.status(), path).toBe(404);
    }

    for (const path of [
      "/api/web/reader/records/plain-text",
      "/api/web/reader-plate/submit",
    ]) {
      const response = await request.post(path, {
        data: { plainText: "retired route probe" },
      });
      expect(response.status(), path).toBe(404);
    }
  });
});
