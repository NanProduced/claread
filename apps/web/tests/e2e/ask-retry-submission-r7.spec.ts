/**
 * ASK-RETRY-CONTRACT-R7 — hard Playwright gates (no real model).
 *
 * Hard rules:
 * - No soft-pass (`if visible return`, optional answer asserts, etc.)
 * - Unconditional expect on counts, paths, and client_submission_id
 * - Uses exclusive free port via ask-retry-r7-server-setup (3410–3429)
 *
 * Run:
 *   pnpm exec playwright test --config=playwright.ask-retry-r7.config.ts
 */

import { expect, test, type Page, type Route } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import type { SpikeSseScriptEvent } from "@/app/e2e-plate-spike/ask-activity/types";

const HARNESS_PATH = "/e2e-plate-spike/ask-activity";

test.beforeEach(() => {
  test.skip(
    true,
    "CUTOVER-WEB-LONG: retry/reconcile coverage is retained in Ask v2 Vitest; this legacy harness suite awaits Physical deletion.",
  );
});
/** Must match E2EAskActivityHarness RECORD_ID (fixed prop, not URL). */
const RECORD = "test-record-r2-activity";
const THREAD = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee1";
const CANONICAL_ASSISTANT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee3";
const CANONICAL_USER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee2";
const CLIENT_SUBMISSION = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff";
const ANSWER = "R7 完整幂等回答正文。";
const EXECUTION_VERSION = "reader_record_ask_agentic_v2";

function r7BaseUrl(): string {
  const file = path.resolve(__dirname, "../../.claread-r7-e2e-url");
  const fromFile = fs.existsSync(file)
    ? fs.readFileSync(file, "utf8").trim()
    : "";
  const base = fromFile || process.env.CLAREAD_R7_BASE_URL || "";
  if (!base) {
    throw new Error(
      "R7 base URL missing — globalSetup must write .claread-r7-e2e-url",
    );
  }
  return base;
}

function threadSummary() {
  return {
    id: THREAD,
    record_id: RECORD,
    title: "R7 Thread",
    is_default: true,
    selected_model: {
      key: "deepseek-v4-flash",
      label: "DeepSeek V4 Flash",
      price_multiplier: 1.0,
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    last_message_at: null,
  };
}

async function mockBaseApis(page: Page) {
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
  await page.route("**/api/web/reader-ask/threads*", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [threadSummary()] }),
      });
      return;
    }
    if (method === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(threadSummary()),
      });
      return;
    }
    await route.fallback();
  });
  await page.route(`**/api/web/reader-ask/threads/${THREAD}*`, async (route) => {
    if (route.request().method() === "GET" && !route.request().url().includes("/submissions/")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...threadSummary(), messages: [] }),
      });
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/web/reader-ask/context-records*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    });
  });
  await page.route("**/api/web/reader-ask/model-options*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        default_key: "deepseek-v4-flash",
        items: [
          {
            key: "deepseek-v4-flash",
            label: "DeepSeek V4 Flash",
            price_multiplier: 1.0,
            is_default: true,
          },
        ],
      }),
    });
  });
}

async function loginAndOpenHarness(page: Page) {
  await mockBaseApis(page);
  const base = r7BaseUrl();
  await page.goto(`${base}/login`, { waitUntil: "domcontentloaded" });
  const phone = page.getByLabel("手机号");
  await expect(phone).toBeVisible({ timeout: 60_000 });
  await phone.fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 30_000,
  });
  await page.goto(`${base}${HARNESS_PATH}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-ask-composer-textarea="true"]', {
    timeout: 60_000,
  });
  await page.waitForFunction(() => window.__spikeAskActivity?.ready === true, {
    timeout: 30_000,
  });
}

function messageStartedScript(): SpikeSseScriptEvent[] {
  return [
    {
      event: "message.started",
      data: {
        message_id: CANONICAL_ASSISTANT,
        thread_id: THREAD,
        execution_version: EXECUTION_VERSION,
      },
    },
  ];
}

async function setScript(page: Page, events: SpikeSseScriptEvent[]) {
  await page.evaluate((script) => {
    window.__spikeAskActivity?.setScript(script);
  }, events);
}

/** Capture stream POST bodies via page-level fetch wrap (harness synthetic). */
async function installStreamBodyCapture(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as {
      __r7StreamBodies?: string[];
      __r7RetryUrls?: string[];
      fetch: typeof fetch;
    };
    w.__r7StreamBodies = [];
    w.__r7RetryUrls = [];
    const prev = w.fetch.bind(window);
    w.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url.includes("/messages/stream") && init?.body) {
        w.__r7StreamBodies = w.__r7StreamBodies ?? [];
        w.__r7StreamBodies.push(String(init.body));
      }
      if (url.includes("/retry")) {
        w.__r7RetryUrls = w.__r7RetryUrls ?? [];
        w.__r7RetryUrls.push(url);
      }
      return prev(input, init);
    };
  });
}

async function getStreamBodies(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const w = window as unknown as { __r7StreamBodies?: string[] };
    return w.__r7StreamBodies ?? [];
  });
}

async function getRetryUrls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const w = window as unknown as { __r7RetryUrls?: string[] };
    return w.__r7RetryUrls ?? [];
  });
}

async function submitQuestion(page: Page, text: string) {
  await page.fill('[data-ask-composer-textarea="true"]', text);
  await page.click('button[aria-label="发送"]');
}

function completedReconcileJson(answer: string = ANSWER) {
  return {
    client_submission_id: CLIENT_SUBMISSION,
    thread_id: THREAD,
    status: "completed",
    user_message_id: CANONICAL_USER,
    assistant_message_id: CANONICAL_ASSISTANT,
    terminal_code: "submission_completed",
    action_hint: "none",
    claim_generation: 1,
    user_message: {
      id: CANONICAL_USER,
      thread_id: THREAD,
      role: "user",
      status: "completed",
      content_md: "幂等问题",
    },
    assistant_message: {
      id: CANONICAL_ASSISTANT,
      thread_id: THREAD,
      role: "assistant",
      status: "completed",
      content_md: answer,
      execution_version: EXECUTION_VERSION,
    },
  };
}

function failedReconcileJson() {
  return {
    client_submission_id: CLIENT_SUBMISSION,
    thread_id: THREAD,
    status: "failed",
    user_message_id: CANONICAL_USER,
    assistant_message_id: CANONICAL_ASSISTANT,
    terminal_code: "submission_failed",
    action_hint: "retry",
    claim_generation: 1,
    assistant_message: {
      id: CANONICAL_ASSISTANT,
      thread_id: THREAD,
      role: "assistant",
      status: "failed",
      content_md: "fallback body",
    },
  };
}

test.describe("ASK-RETRY-CONTRACT-R7 hard gates", () => {
  test("1. persisted regenerate uses Browser /messages/{uuid}/retry only", async ({
    page,
  }) => {
    const retryUrls: string[] = [];
    await page.route("**/api/web/reader-ask/**/retry**", async (route: Route) => {
      retryUrls.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body:
          `event: agentic.terminal\ndata: ${JSON.stringify({
            execution_version: EXECUTION_VERSION,
            final_status: "failed",
            message_id: CANONICAL_ASSISTANT,
            thread_id: THREAD,
            turn_run_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee9",
            terminal_reason: "harness",
          })}\n\n`,
      });
    });

    await loginAndOpenHarness(page);
    await installStreamBodyCapture(page);

    // Drive a real completed turn so the footer exposes 重新生成 on a UUID.
    await setScript(page, [
      {
        event: "agentic.run_started",
        data: {
          execution_version: EXECUTION_VERSION,
          message_id: CANONICAL_ASSISTANT,
          thread_id: THREAD,
          turn_run_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee9",
          has_initial_selection: false,
        },
      },
      {
        event: "message.completed",
        data: {
          execution_version: EXECUTION_VERSION,
          final_status: "ok",
          answer_text: "已完成的回答。",
          answer_blocks: [{ text: "已完成的回答。", citation_ids: [] }],
          citations: [],
          knowledge_mode: null,
          source_status: null,
          web_search: null,
          message_id: CANONICAL_ASSISTANT,
          thread_id: THREAD,
          turn_run_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee9",
        },
      },
    ]);
    await submitQuestion(page, "原问题");
    await expect(page.getByText("已完成的回答。")).toBeVisible({
      timeout: 15_000,
    });

    const regen = page
      .getByTestId("ask-assistant-message")
      .getByRole("button", { name: "重新生成" });
    await expect(regen).toBeVisible({ timeout: 15_000 });
    await regen.click();

    await expect.poll(() => retryUrls.length, { timeout: 15_000 }).toBe(1);
    const url = retryUrls[0];
    // Hard path: …/messages/{uuid}/retry and never /retry/stream
    expect(url).toMatch(/\/messages\/[0-9a-f-]{36}\/retry(?:\?|$)/i);
    expect(url).not.toContain("/retry/stream");
    expect(url).toContain("/messages/");
    expect(url).toContain("/retry");
  });

  test("2. message.started then EOF + GET completed → one canonical hydrate", async ({
    page,
  }) => {
    let getCount = 0;
    await page.route("**/api/web/reader-ask/**/submissions/**", async (route) => {
      getCount += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(completedReconcileJson()),
      });
    });

    await loginAndOpenHarness(page);
    await installStreamBodyCapture(page);
    await setScript(page, messageStartedScript());

    await submitQuestion(page, "幂等问题");

    // Exact: one stream POST + at least one GET hydrate (may poll 1× if completed)
    await expect.poll(() => getCount, { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
    const bodies = await getStreamBodies(page);
    expect(bodies.length).toBe(1);
    // completed on first poll → typically exactly 1 GET
    expect(getCount).toBe(1);

    await expect(page.getByText(ANSWER)).toHaveCount(1, { timeout: 15_000 });
    await expect(
      page.locator(
        '[id^="local-assistant-"], [data-message-id^="local-assistant-"]',
      ),
    ).toHaveCount(0);
    await expect(page.getByTestId("ask-assistant-message")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "重新发送" })).toHaveCount(0);
    const retries = await getRetryUrls(page);
    expect(retries).toEqual([]);
  });

  test("3. message.started + streaming timeout resend: POST exactly 2, same client_submission_id", async ({
    page,
  }) => {
    let streamScriptPhase = 0;
    let getCount = 0;

    await page.route("**/api/web/reader-ask/**/submissions/**", async (route) => {
      getCount += 1;
      if (streamScriptPhase < 2) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            client_submission_id: CLIENT_SUBMISSION,
            thread_id: THREAD,
            status: "streaming",
            user_message_id: CANONICAL_USER,
            assistant_message_id: CANONICAL_ASSISTANT,
            action_hint: "wait",
            claim_generation: 1,
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(completedReconcileJson()),
      });
    });

    await loginAndOpenHarness(page);
    await installStreamBodyCapture(page);
    await setScript(page, messageStartedScript());
    await submitQuestion(page, "幂等重发");

    const resend = page
      .getByTestId("ask-turn-notice")
      .getByRole("button", { name: "重新发送" });
    await expect(resend).toBeVisible({ timeout: 30_000 });

    streamScriptPhase = 2;
    await setScript(page, [
      {
        event: "submission.reconcile",
        data: {
          client_submission_id: "will-be-ignored-by-hydrate",
          thread_id: THREAD,
          status: "completed",
          user_message_id: CANONICAL_USER,
          assistant_message_id: CANONICAL_ASSISTANT,
          terminal_code: "submission_completed",
          action_hint: "none",
          claim_generation: 1,
        },
      },
    ]);
    await resend.click();

    await expect
      .poll(async () => (await getStreamBodies(page)).length, {
        timeout: 20_000,
      })
      .toBe(2);

    const allBodies = await getStreamBodies(page);
    expect(allBodies.length).toBe(2);
    const parsed = allBodies.map(
      (b) => JSON.parse(b) as { client_submission_id?: string },
    );
    const id0 = parsed[0]?.client_submission_id;
    const id1 = parsed[1]?.client_submission_id;
    expect(typeof id0).toBe("string");
    expect(id0).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(id1).toBe(id0);

    expect(await getRetryUrls(page)).toEqual([]);

    await expect(page.getByText(ANSWER)).toHaveCount(1, { timeout: 20_000 });
    await expect(page.getByTestId("ask-assistant-message")).toHaveCount(1);
    await expect(
      page.locator(
        '[id^="local-assistant-"], [data-message-id^="local-assistant-"]',
      ),
    ).toHaveCount(0);
    // First wave: streaming polls (up to 9) + second wave hydrate GETs
    expect(getCount).toBeGreaterThanOrEqual(2);
  });

  test("5. message.started + parse_error → GET completed: no local, no resend, no /retry", async ({
    page,
  }) => {
    let getCount = 0;
    await page.route("**/api/web/reader-ask/**/submissions/**", async (route) => {
      getCount += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(completedReconcileJson()),
      });
    });

    await loginAndOpenHarness(page);
    await installStreamBodyCapture(page);
    await setScript(page, [
      ...messageStartedScript(),
      {
        event: "message.delta",
        data: {},
        raw: "event: message.delta\ndata: {not-valid-json\n\n",
      },
    ]);

    await submitQuestion(page, "parse error path");

    await expect.poll(() => getCount, { timeout: 20_000 }).toBe(1);
    expect((await getStreamBodies(page)).length).toBe(1);
    await expect(page.getByText(ANSWER)).toHaveCount(1, { timeout: 15_000 });
    await expect(page.getByTestId("ask-assistant-message")).toHaveCount(1);
    await expect(
      page.locator(
        '[id^="local-assistant-"], [data-message-id^="local-assistant-"]',
      ),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: "重新发送" })).toHaveCount(0);
    expect(await getRetryUrls(page)).toEqual([]);
  });

  test("6. message.started + transport failure + streaming timeout → resend same id, no /retry", async ({
    page,
  }) => {
    let getCount = 0;
    await page.route("**/api/web/reader-ask/**/submissions/**", async (route) => {
      getCount += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          client_submission_id: CLIENT_SUBMISSION,
          thread_id: THREAD,
          status: "streaming",
          user_message_id: CANONICAL_USER,
          assistant_message_id: CANONICAL_ASSISTANT,
          action_hint: "wait",
          claim_generation: 1,
        }),
      });
    });

    await loginAndOpenHarness(page);
    await installStreamBodyCapture(page);

    // Override stream fetch: emit message.started then transport error.
    await page.evaluate(
      ({ messageId, threadId, execVer }) => {
        const w = window as unknown as {
          __r7StreamBodies?: string[];
          __r7RetryUrls?: string[];
          fetch: typeof fetch;
        };
        const prev = w.fetch.bind(window);
        w.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
          const url =
            typeof input === "string"
              ? input
              : input instanceof URL
                ? input.toString()
                : input.url;
          if (url.includes("/messages/stream")) {
            if (init?.body) {
              w.__r7StreamBodies = w.__r7StreamBodies ?? [];
              w.__r7StreamBodies.push(String(init.body));
            }
            const started =
              `event: message.started\ndata: ${JSON.stringify({
                message_id: messageId,
                thread_id: threadId,
                execution_version: execVer,
              })}\n\n`;
            const enc = new TextEncoder();
            const body = new ReadableStream<Uint8Array>({
              start(controller) {
                controller.enqueue(enc.encode(started));
                controller.error(new TypeError("Failed to fetch"));
              },
            });
            return new Response(body, {
              status: 200,
              headers: {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
              },
            });
          }
          if (url.includes("/retry")) {
            w.__r7RetryUrls = w.__r7RetryUrls ?? [];
            w.__r7RetryUrls.push(url);
          }
          return prev(input, init);
        };
      },
      {
        messageId: CANONICAL_ASSISTANT,
        threadId: THREAD,
        execVer: EXECUTION_VERSION,
      },
    );

    await submitQuestion(page, "transport fail path");

    const resend = page
      .getByTestId("ask-turn-notice")
      .getByRole("button", { name: "重新发送" });
    await expect(resend).toBeVisible({ timeout: 30_000 });
    expect(getCount).toBeGreaterThanOrEqual(1);
    expect(await getRetryUrls(page)).toEqual([]);

    // Second POST with same client_submission_id via harness after re-wrap.
    // Re-install capture + streaming→completed for resend.
    await installStreamBodyCapture(page);
    await setScript(page, [
      {
        event: "submission.reconcile",
        data: {
          client_submission_id: CLIENT_SUBMISSION,
          thread_id: THREAD,
          status: "completed",
          user_message_id: CANONICAL_USER,
          assistant_message_id: CANONICAL_ASSISTANT,
          terminal_code: "submission_completed",
          action_hint: "none",
          claim_generation: 1,
        },
      },
    ]);
    // Switch GET to completed for second wave
    await page.unroute("**/api/web/reader-ask/**/submissions/**");
    await page.route("**/api/web/reader-ask/**/submissions/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(completedReconcileJson()),
      });
    });

    await resend.click();

    await expect
      .poll(async () => (await getStreamBodies(page)).length, {
        timeout: 20_000,
      })
      .toBe(2);
    const bodies = await getStreamBodies(page);
    expect(bodies.length).toBe(2);
    const ids = bodies.map(
      (b) =>
        (JSON.parse(b) as { client_submission_id?: string }).client_submission_id,
    );
    expect(ids[0]).toBeTruthy();
    expect(ids[1]).toBe(ids[0]);
    expect(await getRetryUrls(page)).toEqual([]);
  });

  test("4. failed reconcile promotes regenerate CTA → Browser /retry only", async ({
    page,
  }) => {
    const retryUrls: string[] = [];
    await page.route("**/api/web/reader-ask/**/submissions/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(failedReconcileJson()),
      });
    });
    await page.route("**/api/web/reader-ask/**/retry**", async (route) => {
      retryUrls.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body:
          `event: agentic.terminal\ndata: ${JSON.stringify({
            execution_version: EXECUTION_VERSION,
            final_status: "failed",
            message_id: CANONICAL_ASSISTANT,
            thread_id: THREAD,
            turn_run_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee9",
            terminal_reason: "harness",
          })}\n\n`,
      });
    });

    await loginAndOpenHarness(page);
    await installStreamBodyCapture(page);
    await setScript(page, [
      {
        event: "submission.reconcile",
        data: {
          client_submission_id: CLIENT_SUBMISSION,
          thread_id: THREAD,
          status: "failed",
          user_message_id: CANONICAL_USER,
          assistant_message_id: CANONICAL_ASSISTANT,
          terminal_code: "submission_failed",
          action_hint: "retry",
          claim_generation: 1,
        },
      },
    ]);

    await submitQuestion(page, "失败路径");

    // Failed hydrate shows turn-notice CTA 重新生成 (not footer on failed).
    const regen = page
      .getByTestId("ask-turn-notice")
      .getByRole("button", { name: "重新生成" });
    await expect(regen).toBeVisible({ timeout: 20_000 });
    // No second local pair
    await expect(
      page.locator(
        '[id^="local-assistant-"], [data-message-id^="local-assistant-"]',
      ),
    ).toHaveCount(0);

    await regen.click();
    await expect.poll(() => retryUrls.length, { timeout: 15_000 }).toBe(1);
    expect(retryUrls[0]).toContain(`/messages/${CANONICAL_ASSISTANT}/retry`);
    expect(retryUrls[0]).not.toContain("/retry/stream");
  });
});
