/**
 * ASK-WEB-G1-R1 — Web Search UI 真实浏览器验收
 *
 * 使用 /e2e-plate-spike/ask-activity harness 挂载真实 AiWorkspacePanel
 * (recordScope="reading_record")，验证：
 * 1. Search toggle 在 reading_record scope 下可见
 * 2. 开启 Search toggle 后发送消息，web 来源使用 prompt-kit Source 渲染
 * 3. 同一 canonical URL 的 web citation 被去重（首次出现保留）
 * 4. web_search outcome 为 non-completed 时显示固定中文提示
 *
 * SSE 通过 harness 的 gated fetch interceptor 驱动；事件 payload 符合
 * 真实 Agentic wire contract。
 */

import { expect, test, type Page } from "@playwright/test";
import type { SpikeSseScriptEvent } from "@/app/e2e-plate-spike/ask-activity/types";

const HARNESS_URL = "/e2e-plate-spike/ask-activity";
const RECORD_ID = "test-record-web-search";
const THREAD_ID = "test-thread-web-search";
const MESSAGE_ID = "msg-web-search-1";
const TURN_RUN_ID = "turn-run-web-search-1";
const EXECUTION_VERSION = "reader_record_ask_agentic_v2";

// ---------------------------------------------------------------------------
// Wire-contract payloads (Agentic v2)
// ---------------------------------------------------------------------------

function runStartedPayload() {
  return {
    execution_version: EXECUTION_VERSION,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    has_initial_selection: false,
  };
}

function completedPayloadWithWebSources() {
  return {
    execution_version: EXECUTION_VERSION,
    final_status: "ok" as const,
    answer_text: "根据网络来源，气候变化影响深远。",
    answer_blocks: [
      {
        text: "根据网络来源，气候变化影响深远。",
        citation_ids: ["c-web-1", "c-web-2", "c-web-3"],
      },
    ],
    citations: [
      {
        citation_id: "c-web-1",
        source_kind: "web" as const,
        url: "https://example.com/climate",
        title: "Climate Impact Report",
        description: "A comprehensive report on climate change.",
        snippet: "Climate change has far-reaching effects.",
      },
      {
        citation_id: "c-web-2",
        source_kind: "web" as const,
        url: "https://other.org/article",
        title: "Other Article",
        snippet: "Additional context from another source.",
      },
      {
        citation_id: "c-web-3",
        source_kind: "web" as const,
        // Duplicate of c-web-1 — must be dropped by the frontend dedup.
        url: "https://example.com/climate",
        title: "Duplicate Climate Report",
        snippet: "This should not appear.",
      },
    ],
    knowledge_mode: "web_grounded" as const,
    source_status: null,
    web_search: {
      outcome: "completed" as const,
      cited_source_count: 2,
    },
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
  };
}

function completedPayloadWithNoResults() {
  return {
    execution_version: EXECUTION_VERSION,
    final_status: "ok" as const,
    answer_text: "未能找到相关网络来源。",
    answer_blocks: [
      {
        text: "未能找到相关网络来源。",
        citation_ids: [],
      },
    ],
    citations: [],
    knowledge_mode: "article_grounded" as const,
    source_status: null,
    web_search: {
      outcome: "no_results" as const,
      cited_source_count: 0,
    },
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
  };
}

function buildScriptWithWebSources(): SpikeSseScriptEvent[] {
  return [
    { event: "agentic.run_started", data: runStartedPayload() },
    {
      event: "agentic.progress",
      data: {
        execution_version: EXECUTION_VERSION,
        sequence: 1,
        phase: "searching_web",
        activity: "started",
        summary: "正在搜索网络",
        elapsed_ms: 100,
      },
    },
    {
      event: "agentic.progress",
      data: {
        execution_version: EXECUTION_VERSION,
        sequence: 2,
        phase: "composing_answer",
        activity: "started",
        summary: "正在组织回答",
        elapsed_ms: 200,
      },
    },
    { event: "message.completed", data: completedPayloadWithWebSources() },
  ];
}

function buildScriptWithNoResults(): SpikeSseScriptEvent[] {
  return [
    { event: "agentic.run_started", data: runStartedPayload() },
    {
      event: "agentic.progress",
      data: {
        execution_version: EXECUTION_VERSION,
        sequence: 1,
        phase: "searching_web",
        activity: "started",
        summary: "正在搜索网络",
        elapsed_ms: 100,
      },
    },
    { event: "message.completed", data: completedPayloadWithNoResults() },
  ];
}

function threadSummary() {
  return {
    id: THREAD_ID,
    record_id: RECORD_ID,
    title: "Web Search Test Thread",
    is_default: true,
    selected_model: {
      key: "deepseek-v4-flash",
      label: "DeepSeek V4 Flash",
      price_multiplier: 1,
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    last_message_at: null,
  };
}

async function mockApiRoutes(page: Page) {
  await page.route("**/api/web/auth/phone/request-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, message: "验证码已生成。" }),
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
        message: "已进入测试登录态。",
      }),
    });
  });
  await page.route("**/api/web/reader-ask/threads*", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
      return;
    }
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(threadSummary()),
      });
      return;
    }
    await route.fallback();
  });
  await page.route(
    `**/api/web/reader-ask/threads/${THREAD_ID}*`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...threadSummary(), messages: [] }),
      });
    },
  );
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
            price_multiplier: 1,
            is_default: true,
          },
        ],
      }),
    });
  });
  await page.route("**/api/web/reader-ask/context-records*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    });
  });
}

async function openHarness(page: Page) {
  await mockApiRoutes(page);
  await page.goto("/login");
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await page.goto(HARNESS_URL);
  await page.waitForSelector('[data-ask-composer-textarea="true"]');
  await page.waitForFunction(() => window.__spikeAskActivity?.ready === true);
}

test.describe("Web Search UI (ASK-WEB-G1-R1)", () => {
  test("Search toggle is visible in reading_record scope", async ({ page }) => {
    await openHarness(page);

    // The Search toggle must be visible because the harness uses
    // recordScope="reading_record" which supports web search capability.
    const toggle = page.getByTestId("ask-composer-web-search-toggle");
    await expect(toggle).toBeVisible({ timeout: 10_000 });
    // Default state is off.
    await expect(toggle).toHaveAttribute("data-state", "off");
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  test("toggling Search on then sending renders web sources via prompt-kit", async ({
    page,
  }) => {
    await openHarness(page);

    // Set the SSE script before sending so the harness can replay it.
    await page.evaluate((script) => {
      window.__spikeAskActivity?.setScript(script);
    }, buildScriptWithWebSources());

    // Toggle Search on.
    const toggle = page.getByTestId("ask-composer-web-search-toggle");
    await expect(toggle).toBeVisible({ timeout: 10_000 });
    await toggle.click();
    await expect(toggle).toHaveAttribute("data-state", "on");
    await expect(toggle).toHaveAttribute("aria-pressed", "true");

    // Send a message.
    await page.fill(
      '[data-ask-composer-textarea="true"]',
      "气候变化有什么网络来源？",
    );
    await page.click('button[aria-label="发送"]');

    // Wait for the answer to render.
    await expect(page.getByTestId("agentic-answer-blocks")).toBeVisible({
      timeout: 15_000,
    });

    // Web sources must be rendered using prompt-kit Source primitives
    // (data-slot="prompt-kit-source-trigger"), not a custom HoverCard.
    const sourceTriggers = page.locator(
      '[data-slot="prompt-kit-source-trigger"]',
    );
    await expect(sourceTriggers).toHaveCount(2, { timeout: 10_000 });

    // First occurrence wins for dedup: c-web-1 and c-web-2, c-web-3 dropped.
    const hrefs = await sourceTriggers.evaluateAll((els) =>
      els.map((el) => (el as HTMLAnchorElement).getAttribute("href")),
    );
    expect(hrefs).toEqual([
      "https://example.com/climate",
      "https://other.org/article",
    ]);

    // Each pill opens in a new tab with rel=noopener noreferrer.
    for (let i = 0; i < await sourceTriggers.count(); i++) {
      const pill = sourceTriggers.nth(i);
      await expect(pill).toHaveAttribute("target", "_blank");
      await expect(pill).toHaveAttribute("rel", "noopener noreferrer");
    }

    // The duplicate citation's title must not appear.
    const body = await page.locator("body").innerHTML();
    expect(body).not.toContain("Duplicate Climate Report");
  });

  test("no_results outcome shows fixed Chinese notice without source pills", async ({
    page,
  }) => {
    await openHarness(page);

    await page.evaluate((script) => {
      window.__spikeAskActivity?.setScript(script);
    }, buildScriptWithNoResults());

    // Toggle Search on and send.
    const toggle = page.getByTestId("ask-composer-web-search-toggle");
    await expect(toggle).toBeVisible({ timeout: 10_000 });
    await toggle.click();

    await page.fill(
      '[data-ask-composer-textarea="true"]',
      "搜索一个不存在的话题",
    );
    await page.click('button[aria-label="发送"]');

    // Wait for the answer to render.
    await expect(page.getByTestId("agentic-answer-blocks")).toBeVisible({
      timeout: 15_000,
    });

    // The fixed Chinese outcome notice must appear.
    await expect(page.getByTestId("web-search-outcome-notice")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("web-search-outcome-notice")).toHaveText(
      "未找到可用网页来源",
    );

    // No web source pills.
    await expect(
      page.locator('[data-slot="prompt-kit-source-trigger"]'),
    ).toHaveCount(0);
  });
});
