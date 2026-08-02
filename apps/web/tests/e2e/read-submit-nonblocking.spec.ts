/**
 * 阶段 3 e2e — 提交路径非阻断合同（fail-closed 已撤销）。
 *
 * Mock BFF（reader-plate-smoke.spec.ts 风格：page.route + mock_phone
 * 登录态），验证：
 *   1. 安全内容（普通链接 / aside / vector<T>）经按钮与 Ctrl/Cmd+Enter
 *      均正常发出提交请求；
 *   2. 含 script 的内容前端不阻断（请求照常发出）且 script 不执行；
 *   3. lint 警告 badge 非阻断展示（不阻止请求）。
 */

import { expect, test, type Page } from "@playwright/test";

import {
  RICH_HTML,
  RICH_HTML_PLAIN,
} from "./fixtures/clipboard-fixtures";

const READ_PATH = "/app/read";

// ---------------------------------------------------------------------------
// Mock BFF helpers（与 reader-plate-smoke.spec.ts 同模式）
// ---------------------------------------------------------------------------

async function loginWithMockPhone(page: Page, nextPath = READ_PATH) {
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
        "set-cookie":
          "claread_web_phone=13800138000; Path=/; SameSite=Lax; HttpOnly",
      },
      body: JSON.stringify({
        ok: true,
        phone: "13800138000",
        message: "已进入本地调试登录态。",
      }),
    });
  });

  await page.goto(`/login?next=${encodeURIComponent(nextPath)}`);
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL(`**${nextPath}`);
}

interface CapturedSubmit {
  text?: string;
  sourceType?: string;
}

/** 拦截提交端点，记录请求体，返回 stable_document_ready。 */
async function mockSubmitEndpoint(page: Page, captured: CapturedSubmit[]) {
  await page.route("**/api/web/reader/records/input", async (route) => {
    const request = route.request();
    try {
      captured.push(JSON.parse(request.postData() ?? "{}") as CapturedSubmit);
    } catch {
      captured.push({});
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        outcome: "stable_document_ready",
        reading_record_id: "rec_e2e_nonblocking",
        stable_document_id: "sd_e2e",
        base_id: "base_e2e",
        record_generation: 1,
        document_version: 1,
        title: "E2E nonblocking fixture",
        content_sha256: "abc",
        canonical_text_sha256: "def",
        block_count: 1,
        article_ready_event_id: "evt_e2e",
        article_ready_sequence: 1,
        suitability: {
          outcome: "stable_document_ready",
          source_type: "pasted_text",
          word_count: 10,
          english_word_ratio: 1,
          natural_language_score: 0.95,
          flags: [],
          reasons: [],
          normalized_preview: "Hello.",
        },
      }),
    });
  });
}

/** 真实 clipboard 粘贴（L0 spike 验证的唯一可靠路径）。 */
async function pasteText(page: Page, text: string) {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.evaluate(async (t) => {
    await navigator.clipboard.write([
      new ClipboardItem({ "text/plain": new Blob([t], { type: "text/plain" }) }),
    ]);
  }, text);
  await page.locator("[data-slate-editor]").first().click();
  await page.keyboard.press("Control+V");
  await page.waitForTimeout(500);
}

async function pasteRichClipboard(
  page: Page,
  payload: { html: string; plain: string },
) {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.evaluate(async ({ html, plain }) => {
    await navigator.clipboard.write([
      new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([plain], { type: "text/plain" }),
      }),
    ]);
  }, payload);
  await page.locator("[data-slate-editor]").first().click();
  await page.keyboard.press("Control+V");
  await expect(page.getByRole("button", { name: "开始透读" })).toBeEnabled({
    timeout: 10_000,
  });
}

async function waitForSubmitReady(page: Page) {
  await expect(
    page.getByRole("button", { name: "开始透读" }),
  ).toBeEnabled({ timeout: 10_000 });
}

// ---------------------------------------------------------------------------

test.describe("阶段 3: 提交路径非阻断", () => {
  test("富 HTML 提交 canonical Markdown 而非扁平 companion text", async ({
    page,
  }) => {
    const captured: CapturedSubmit[] = [];
    await mockSubmitEndpoint(page, captured);
    await loginWithMockPhone(page);

    await pasteRichClipboard(page, {
      html: RICH_HTML,
      plain: RICH_HTML_PLAIN,
    });
    await page.getByRole("button", { name: "开始透读" }).click();

    await expect
      .poll(() => captured.length, { timeout: 10_000 })
      .toBe(1);
    const submitted = captured[0].text ?? "";
    expect(submitted).toContain("# Title One");
    expect(submitted).toContain("## Section Two");
    expect(submitted).toContain("> quoted insight");
    expect(submitted).toContain("nested alpha");
    expect(submitted).toContain("def f():");
    expect(submitted).toContain("| Name | Value |");
    expect(submitted.replace(/\r\n/g, "\n")).not.toBe(RICH_HTML_PLAIN);
  });

  test("安全内容（普通链接 + vector<T>）经按钮正常提交", async ({ page }) => {
    const captured: CapturedSubmit[] = [];
    await mockSubmitEndpoint(page, captured);
    await loginWithMockPhone(page);

    await pasteText(
      page,
      "Read [the docs](https://example.com/docs) about std::vector<T> carefully.",
    );
    await waitForSubmitReady(page);
    await page.getByRole("button", { name: "开始透读" }).click();

    await expect
      .poll(() => captured.length, { timeout: 10_000 })
      .toBe(1);
    expect(captured[0].text).toContain("https://example.com/docs");
    expect(captured[0].text).toContain("vector<T>");
    expect(captured[0].sourceType).toBe("pasted_text");
  });

  test("安全内容经 Ctrl+Enter 正常提交", async ({ page }) => {
    const captured: CapturedSubmit[] = [];
    await mockSubmitEndpoint(page, captured);
    await loginWithMockPhone(page);

    await pasteText(page, "A short English article for the non-blocking gate.");
    await waitForSubmitReady(page);
    await page.locator("[data-slate-editor]").first().click();
    await page.keyboard.press("Control+Enter");

    await expect
      .poll(() => captured.length, { timeout: 10_000 })
      .toBe(1);
    expect(captured[0].text).toContain("non-blocking gate");
  });

  test("含 script 的内容前端不阻断、script 不执行、badge 展示", async ({
    page,
  }) => {
    const captured: CapturedSubmit[] = [];
    await mockSubmitEndpoint(page, captured);
    await loginWithMockPhone(page);

    await pasteText(
      page,
      "Hello <script>window.__pwned = true</script> world, keep reading.",
    );

    // 非阻断 badge 展示（debounce 后出现）
    await expect(page.getByTestId("read-source-lint-warning")).toBeVisible({
      timeout: 10_000,
    });

    await waitForSubmitReady(page);
    await page.getByRole("button", { name: "开始透读" }).click();

    // 前端不阻断：请求照常发出
    await expect
      .poll(() => captured.length, { timeout: 10_000 })
      .toBe(1);

    // script 未执行
    const pwned = await page.evaluate(
      () => (window as unknown as { __pwned?: boolean }).__pwned,
    );
    expect(pwned).toBeUndefined();
  });

  test("unsafe link 内容 badge 展示但提交不阻断", async ({ page }) => {
    const captured: CapturedSubmit[] = [];
    await mockSubmitEndpoint(page, captured);
    await loginWithMockPhone(page);

    await pasteText(
      page,
      "Click [here](javascript:alert(1)) for more context on this topic.",
    );

    await expect(page.getByTestId("read-source-lint-warning")).toBeVisible({
      timeout: 10_000,
    });

    await waitForSubmitReady(page);
    await page.locator("[data-slate-editor]").first().click();
    await page.keyboard.press("Control+Enter");

    await expect
      .poll(() => captured.length, { timeout: 10_000 })
      .toBe(1);
  });
});
