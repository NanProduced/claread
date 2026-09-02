/**
 * Submit-path browser fail-closed: unsafe content is not executed and
 * does not block submit. Safe submit / canonical Markdown live in Vitest.
 */

import { expect, test, type Page } from "@playwright/test";

const READ_PATH = "/app/read";

// ---------------------------------------------------------------------------
// Mock BFF helpers（与 reader-plate-smoke.spec.ts 同模式）
// ---------------------------------------------------------------------------

async function loginWithSessionCookie(page: Page, nextPath = READ_PATH) {
  await page.goto("/");
  await page.context().addCookies([
    { name: "claread_web_session", value: "e2e-session", path: "/", domain: "127.0.0.1" },
  ]);
  await page.goto(nextPath);
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

async function waitForSubmitReady(page: Page) {
  await expect(
    page.getByRole("button", { name: "开始透读" }),
  ).toBeEnabled({ timeout: 10_000 });
}

// ---------------------------------------------------------------------------

test.describe("提交路径非阻断", () => {
  test("unsafe script/link content does not execute and does not block submit", async ({
    page,
  }) => {
    const captured: CapturedSubmit[] = [];
    await mockSubmitEndpoint(page, captured);
    await loginWithSessionCookie(page);

    await pasteText(
      page,
      "Hello <script>window.__pwned = true</script> world, keep reading.",
    );
    await expect(page.getByTestId("read-source-lint-warning")).toBeVisible({
      timeout: 10_000,
    });
    await waitForSubmitReady(page);
    await page.getByRole("button", { name: "开始透读" }).click();
    await expect.poll(() => captured.length, { timeout: 10_000 }).toBe(1);
    const pwned = await page.evaluate(
      () => (window as unknown as { __pwned?: boolean }).__pwned,
    );
    expect(pwned).toBeUndefined();

    captured.length = 0;
    await page.goto(READ_PATH);
    await mockSubmitEndpoint(page, captured);
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
    await expect.poll(() => captured.length, { timeout: 10_000 }).toBe(1);
  });
});
