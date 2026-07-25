/**
 * R2.5 - Activity + 对话滚动真实浏览器验收
 *
 * 使用 /e2e-plate-spike/ask-activity harness 挂载真实 AiWorkspacePanel。
 * SSE 通过 harness 的 gated fetch interceptor 驱动；事件 payload 必须
 * 符合真实 Agentic wire contract（run_started / progress / message.completed /
 * agentic.terminal）。禁止使用不存在的 agentic.completed。
 */

import { expect, test, type Page } from "@playwright/test";
import type { SpikeSseScriptEvent } from "@/app/e2e-plate-spike/ask-activity/types";

const HARNESS_URL = "/e2e-plate-spike/ask-activity";
const RECORD_ID = "test-record-r2-activity";
const THREAD_ID = "test-thread-r2-activity";
const MESSAGE_ID = "msg-agentic-r2-1";
const TURN_RUN_ID = "turn-run-r2-1";
// ASK-REASONING-R2: every Agentic helper/payload in this spec uses the
// current public v2 wire contract (no envelope_fingerprint / evidence /
// rejected_handles — those keys are public-forbidden and would be
// rejected by the typed guards). ENVELOPE_FINGERPRINT is retained ONLY
// as a canary value for leak-absence assertions.
const ENVELOPE_FINGERPRINT =
  "a1b2c3d4e5f60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00";
const EXECUTION_VERSION = "reader_record_ask_agentic_v2";
const browserConsoleErrors = new WeakMap<Page, string[]>();

// ---------------------------------------------------------------------------
// Wire-contract payloads (Agentic v2)
// ---------------------------------------------------------------------------

function runStartedPayload(overrides: Record<string, unknown> = {}) {
  return {
    execution_version: EXECUTION_VERSION,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    has_initial_selection: false,
    ...overrides,
  };
}

function progressPayload(
  sequence: number,
  phase: string,
  summary: string,
  extras: Record<string, unknown> = {},
) {
  return {
    execution_version: EXECUTION_VERSION,
    sequence,
    phase,
    activity: "started",
    summary,
    elapsed_ms: sequence * 100,
    ...extras,
  };
}

function agenticCompletedPayload(answerText: string) {
  return {
    execution_version: EXECUTION_VERSION,
    final_status: "ok" as const,
    answer_text: answerText,
    answer_blocks: [{ text: answerText, citation_ids: [] as string[] }],
    citations: [],
    knowledge_mode: null,
    source_status: null,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
  };
}

function agenticTerminalPayload(
  finalStatus: "failed" | "cancelled" | "context_stale" | "invalid_citations" = "failed",
  overrides: Record<string, unknown> = {},
) {
  return {
    execution_version: EXECUTION_VERSION,
    final_status: finalStatus,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    terminal_reason: null,
    ...overrides,
  };
}

// Legacy reader_ask payload — used ONLY by the isolated legacy regression
// test (17). Never mixed with the Agentic v2 helpers above.
function legacyCompletedPayload(contentMd: string) {
  return {
    id: "msg-legacy-1",
    thread_id: THREAD_ID,
    content_md: contentMd,
    submission_mode: "chat",
    resolved_intent: "explain",
    citations: [],
    action_proposals: [],
    tool_trace: [],
    evidence: [],
    response_cards: [],
    supplement_candidates: [],
    persisted_supplements: [],
    billed_points: 0,
    resolved_context: {
      record_id: RECORD_ID,
      record_title: "测试文章 - R2.5 Activity 验收",
      anchor_count: 0,
      explicit_attachment_count: 0,
      used_cross_record_context: false,
      current_sentence_used: false,
      current_paragraph_used: false,
      used_record_insights: false,
      used_dictionary: false,
      source_labels: [],
    },
  };
}

// ---------------------------------------------------------------------------
// ASK-REASONING-R1/R2: reasoning projection wire payloads (v2 contract)
// ---------------------------------------------------------------------------

const REASONING_POLICY_VERSION = "reasoning_projection_v1";

function reasoningStartedPayload(seq = 0) {
  return {
    execution_version: EXECUTION_VERSION,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    seq,
    projection_policy_version: REASONING_POLICY_VERSION,
  };
}

function reasoningDeltaPayload(seq: number, delta: string) {
  return { ...reasoningStartedPayload(seq), delta };
}

function reasoningCompletedPayload(seq: number) {
  return {
    ...reasoningStartedPayload(seq),
    has_content: true,
    truncated: false,
  };
}

const SHORT_ANSWER = "根据文章内容，这个问题的答案是：主要观点集中在制度记忆如何塑造政策选择。";

const LONG_ANSWER_UNIT =
  "这是一段用于制造滚动溢出的长回答正文。Institutional memory shapes policy choices in subtle ways. ";
const LONG_ANSWER = LONG_ANSWER_UNIT.repeat(80);

function buildSuccessScript(options: {
  includeSearchUnavailable?: boolean;
  longAnswer?: boolean;
  holdBeforeComplete?: boolean;
  holdAfterEarlyProgress?: boolean;
} = {}): SpikeSseScriptEvent[] {
  const {
    includeSearchUnavailable = false,
    longAnswer = false,
    holdBeforeComplete = false,
    holdAfterEarlyProgress = false,
  } = options;

  const events: SpikeSseScriptEvent[] = [
    { event: "agentic.run_started", data: runStartedPayload() },
    {
      event: "agentic.progress",
      data: progressPayload(1, "reading_context", "正在分析当前文章", {
        tool_name: "read_range",
        status: "running",
      }),
      hold: holdAfterEarlyProgress,
    },
    {
      event: "agentic.progress",
      data: progressPayload(2, "searching_article", "正在搜索相关内容", {
        tool_name: "search_current_article",
        status: "running",
      }),
    },
  ];

  if (includeSearchUnavailable) {
    events.push({
      event: "agentic.progress",
      data: progressPayload(3, "searching_article", "文章搜索暂不可用", {
        activity: "unavailable",
        tool_name: "search_current_article",
        status: "unavailable",
        duration_ms: 40,
      }),
    });
  }

  events.push({
    event: "agentic.progress",
    data: progressPayload(
      includeSearchUnavailable ? 4 : 3,
      "composing_answer",
      "正在组织回答",
      { status: "running" },
    ),
    hold: holdBeforeComplete,
  });

  events.push({
    event: "message.completed",
    data: agenticCompletedPayload(longAnswer ? LONG_ANSWER : SHORT_ANSWER),
  });

  return events;
}

function buildTerminalScript(
  finalStatus: "failed" | "cancelled" | "context_stale" | "invalid_citations" = "failed",
  overrides: Record<string, unknown> = {},
): SpikeSseScriptEvent[] {
  return [
    { event: "agentic.run_started", data: runStartedPayload(overrides) },
    {
      event: "agentic.progress",
      data: progressPayload(1, "reading_context", "正在分析当前文章", {
        tool_name: "read_range",
        status: "running",
      }),
    },
    {
      event: "agentic.progress",
      data: progressPayload(2, "composing_answer", "正在组织回答", {
        status: "running",
      }),
    },
    { event: "agentic.terminal", data: agenticTerminalPayload(finalStatus, overrides) },
  ];
}

function buildLegacyScript(contentMd: string): SpikeSseScriptEvent[] {
  return [{ event: "message.completed", data: legacyCompletedPayload(contentMd) }];
}

// ---------------------------------------------------------------------------
// API mocks + login
// ---------------------------------------------------------------------------

function threadSummary() {
  return {
    id: THREAD_ID,
    record_id: RECORD_ID,
    title: "Test Thread",
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

async function mockApiRoutes(page: Page) {
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
        body: JSON.stringify({ items: [] }),
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

  await page.route(`**/api/web/reader-ask/threads/${THREAD_ID}*`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...threadSummary(),
          messages: [],
        }),
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

  await page.route("**/api/web/dict/lookup*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, entries: [] }),
    });
  });
}

async function loginAndOpenHarness(page: Page) {
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
  // Scope console-error assertions to the mounted Ask harness, not login redirects.
  browserConsoleErrors.get(page)?.splice(0);
}

async function setScript(page: Page, events: SpikeSseScriptEvent[]) {
  await page.evaluate((script) => {
    window.__spikeAskActivity?.setScript(script);
  }, events);
}

async function releaseNext(page: Page) {
  await page.evaluate(() => {
    window.__spikeAskActivity?.releaseNext();
  });
}

async function releaseAll(page: Page) {
  await page.evaluate(() => {
    window.__spikeAskActivity?.releaseAll();
  });
}

async function submitQuestion(page: Page, text: string) {
  await page.fill('[data-ask-composer-textarea="true"]', text);
  await page.click('button[aria-label="发送"]');
}

async function getScrollMetrics(page: Page) {
  return page.evaluate(() => {
    const el = document.querySelector(".ask-conversation-scroll");
    if (!el) {
      return { scrollTop: 0, scrollHeight: 0, clientHeight: 0, exists: false };
    }
    return {
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      exists: true,
    };
  });
}

async function waitForActivityPhase(page: Page, phase: string, timeout = 10_000) {
  await page.waitForFunction(
    (expected) => {
      const activity = document.querySelector('[data-testid="ask-agentic-activity"]');
      return activity?.getAttribute("data-activity-phase") === expected;
    },
    phase,
    { timeout },
  );
}

async function waitForActivityStatus(page: Page, status: string, timeout = 10_000) {
  await page.waitForFunction(
    (expected) => {
      const activity = document.querySelector('[data-testid="ask-agentic-activity"]');
      return activity?.getAttribute("data-activity-status") === expected;
    },
    status,
    { timeout },
  );
}

async function waitForStreamWaiting(page: Page, timeout = 10_000) {
  await page.waitForFunction(
    () => window.__spikeAskActivity?.getStreamState().waiting === true,
    undefined,
    { timeout },
  );
}

async function waitForStreamFinished(page: Page, timeout = 15_000) {
  await page.waitForFunction(
    () => window.__spikeAskActivity?.getStreamState().finished === true,
    undefined,
    { timeout },
  );
}

/**
 * Programmatic scrollTop assignment is ignored by use-stick-to-bottom's escape
 * logic (it treats it as animation ignore). Emit a real wheel-up so the library
 * sets escapedFromLock and stops auto-follow.
 */
async function userScrollUp(page: Page, deltaY = -400) {
  const scroll = page.locator(".ask-conversation-scroll");
  await scroll.hover();
  await page.mouse.wheel(0, deltaY);
  await page.evaluate(() =>
    new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    ),
  );
}

async function waitAtNaturalBottom(page: Page, timeout = 10_000) {
  await page.waitForFunction(() => {
    const el = document.querySelector(".ask-conversation-scroll");
    if (!el) return false;
    return Math.max(0, el.scrollHeight - el.clientHeight) - el.scrollTop <= 2;
  }, undefined, { timeout });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("R2.5 - Agentic Ask Activity Browser Acceptance", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    const errors: string[] = [];
    browserConsoleErrors.set(page, errors);
    page.on("console", (message) => {
      if (message.type() === "error") {
        errors.push(message.text());
      }
    });
  });


  test.afterEach(async ({ page }) => {
    expect(browserConsoleErrors.get(page) ?? []).toEqual([]);
  });
  test("1. 提交问题后,用户消息立即出现", async ({ page }) => {
    await loginAndOpenHarness(page);
    // Hold the whole stream so we can assert optimistic user message first.
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload(), hold: true },
      {
        event: "message.completed",
        data: agenticCompletedPayload(SHORT_ANSWER),
      },
    ]);

    await submitQuestion(page, "这篇文章的主要观点是什么?");

    const userMessage = page.locator('[data-testid="ask-user-message"]');
    await expect(userMessage).toBeVisible({ timeout: 5000 });
    await expect(userMessage).toContainText("这篇文章的主要观点是什么?");

    await releaseAll(page);
  });

  test("2. run_started/progress 到达后,Activity status row 可见", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      {
        event: "agentic.progress",
        data: progressPayload(1, "reading_context", "正在分析当前文章"),
        hold: true,
      },
      {
        event: "message.completed",
        data: agenticCompletedPayload(SHORT_ANSWER),
      },
    ]);

    await submitQuestion(page, "测试问题");

    const activity = page.locator('[data-testid="ask-agentic-activity"]');
    await expect(activity).toBeVisible({ timeout: 5000 });
    await expect(activity).toHaveAttribute("data-activity-status", "running");

    await releaseAll(page);
  });

  test("3. phase/summary 按 sequence 更新", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, buildSuccessScript({ holdBeforeComplete: true }));

    await submitQuestion(page, "测试问题");
    await waitForActivityPhase(page, "composing_answer");

    const phase = await page.locator('[data-testid="ask-agentic-activity"]').getAttribute(
      "data-activity-phase",
    );
    expect(phase).toBe("composing_answer");
    await expect(page.locator('[data-testid="ask-agentic-activity"]')).toContainText(
      "正在组织回答",
    );

    await releaseAll(page);
  });

  test("4. duplicate/out-of-order progress 不造成 UI 回退", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      {
        event: "agentic.progress",
        data: progressPayload(2, "searching_article", "正在搜索"),
      },
      {
        event: "agentic.progress",
        data: progressPayload(2, "searching_article", "重复的搜索"),
      },
      {
        event: "agentic.progress",
        data: progressPayload(1, "reading_context", "乱序的阅读"),
      },
      {
        event: "agentic.progress",
        data: progressPayload(3, "composing_answer", "正在组织回答"),
        hold: true,
      },
      {
        event: "message.completed",
        data: agenticCompletedPayload(SHORT_ANSWER),
      },
    ]);

    await submitQuestion(page, "测试问题");
    await waitForActivityPhase(page, "composing_answer");

    const phase = await page.locator('[data-testid="ask-agentic-activity"]').getAttribute(
      "data-activity-phase",
    );
    const summary = await page.locator('[data-testid="ask-agentic-activity"]').textContent();
    expect(phase).toBe("composing_answer");
    expect(summary).toContain("正在组织回答");
    expect(summary).not.toContain("乱序的阅读");
    expect(summary).not.toContain("重复的搜索");

    await releaseAll(page);
  });

  test("5. search unavailable 使用中性降级提示,最终仍可成功回答", async ({ page }) => {
    await loginAndOpenHarness(page);
    // Hold right after the unavailable progress so the degraded row is stable.
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      {
        event: "agentic.progress",
        data: progressPayload(1, "reading_context", "正在分析当前文章", {
          tool_name: "read_range",
          status: "running",
        }),
      },
      {
        event: "agentic.progress",
        data: progressPayload(2, "searching_article", "正在搜索相关内容", {
          tool_name: "search_current_article",
          status: "running",
        }),
      },
      {
        event: "agentic.progress",
        data: progressPayload(3, "searching_article", "文章搜索暂不可用", {
          activity: "unavailable",
          tool_name: "search_current_article",
          status: "unavailable",
          duration_ms: 40,
        }),
        hold: true,
      },
      {
        event: "agentic.progress",
        data: progressPayload(4, "composing_answer", "正在组织回答", {
          status: "running",
        }),
      },
      {
        event: "message.completed",
        data: agenticCompletedPayload(SHORT_ANSWER),
      },
    ]);

    await submitQuestion(page, "测试问题");
    await waitForActivityStatus(page, "degraded");
    await expect(page.locator('[data-testid="ask-agentic-activity"]')).toContainText(
      "文章搜索暂不可用",
    );

    await releaseAll(page);

    const assistantMessage = page.locator('[data-testid="ask-assistant-message"]');
    await expect(assistantMessage).toContainText("主要观点", { timeout: 10_000 });
  });

  test("6. completed 后 Activity 收起,回答正常展示", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, buildSuccessScript());

    await submitQuestion(page, "测试问题");

    const assistantMessage = page.locator('[data-testid="ask-assistant-message"]');
    await expect(assistantMessage).toContainText("主要观点", { timeout: 10_000 });

    await page.waitForFunction(() => {
      const activity = document.querySelector('[data-testid="ask-agentic-activity"]');
      return !activity;
    });

    await expect(page.locator('[data-testid="ask-agentic-activity"]')).toHaveCount(0);
  });

  test("7. terminal 后不写伪答案,显示安全失败提示", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, buildTerminalScript("failed"));

    await submitQuestion(page, "测试问题");
    await waitForStreamFinished(page);

    const userMessage = page.locator('[data-testid="ask-user-message"]');
    await expect(userMessage).toBeVisible();
    await expect(userMessage).toContainText("测试问题");

    // Activity must not remain running.
    await page.waitForFunction(() => {
      const activity = document.querySelector('[data-testid="ask-agentic-activity"]');
      return !activity;
    });

    // Safe failure copy (production path uses neutral copy, not internal reason).
    await expect(page.getByText("Ask Claread 暂时不可用。")).toBeVisible({ timeout: 5000 });

    const assistantMessage = page.locator('[data-testid="ask-assistant-message"]');
    const count = await assistantMessage.count();
    if (count > 0) {
      const text = (await assistantMessage.first().textContent()) ?? "";
      expect(text).not.toContain("根据文章内容");
      expect(text).not.toContain(SHORT_ANSWER);
      expect(text).not.toContain("agentic_model_unconfigured");
      expect(text).not.toContain(ENVELOPE_FINGERPRINT);
    }
  });

  test("8. user message 在 progress、completed、terminal 后仍存在", async ({ page }) => {
    await loginAndOpenHarness(page);

    // progress still open
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      {
        event: "agentic.progress",
        data: progressPayload(1, "reading_context", "正在分析当前文章"),
        hold: true,
      },
      {
        event: "message.completed",
        data: agenticCompletedPayload(SHORT_ANSWER),
      },
    ]);
    await submitQuestion(page, "进度阶段用户问题");
    await waitForActivityPhase(page, "reading_context");
    await expect(page.locator('[data-testid="ask-user-message"]')).toContainText(
      "进度阶段用户问题",
    );
    await releaseAll(page);
    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "主要观点",
      { timeout: 10_000 },
    );
    await expect(page.locator('[data-testid="ask-user-message"]')).toContainText(
      "进度阶段用户问题",
    );

    // terminal path in a fresh turn
    await setScript(page, buildTerminalScript("failed", {
      message_id: "msg-agentic-r2-8-terminal",
      turn_run_id: "turn-run-r2-8-terminal",
    }));
    await submitQuestion(page, "终态阶段用户问题");
    await waitForStreamFinished(page);
    await expect(page.locator('[data-testid="ask-user-message"]').last()).toContainText(
      "终态阶段用户问题",
    );
  });

  test("9. 自动模式下,本轮用户问题保持在合理可视位置", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, buildSuccessScript({ longAnswer: true }));

    await submitQuestion(page, "锚点问题");
    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );

    const userMessageRect = await page.evaluate(() => {
      const userMessage = document.querySelector('[data-testid="ask-user-message"]');
      const scroll = document.querySelector(".ask-conversation-scroll");
      if (!userMessage || !scroll) return null;
      const userRect = userMessage.getBoundingClientRect();
      const scrollRect = scroll.getBoundingClientRect();
      return {
        topInContainer: userRect.top - scrollRect.top,
        bottomInContainer: userRect.bottom - scrollRect.top,
        containerHeight: scrollRect.height,
      };
    });

    expect(userMessageRect).not.toBeNull();
    // Question-anchor keeps the latest user question near the top of the viewport.
    expect(userMessageRect!.topInContainer).toBeGreaterThanOrEqual(-8);
    expect(userMessageRect!.topInContainer).toBeLessThan(userMessageRect!.containerHeight * 0.5);
  });

  test("10. 回答超出视口后,'跳到最新消息'按钮按真实自然底部出现", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, buildSuccessScript({ longAnswer: true }));

    await submitQuestion(page, "滚动溢出问题");
    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );

    // Prove overflow first.
    await page.waitForFunction(() => {
      const el = document.querySelector(".ask-conversation-scroll");
      return !!el && el.scrollHeight > el.clientHeight;
    });
    const metricsBefore = await getScrollMetrics(page);
    expect(metricsBefore.scrollHeight).toBeGreaterThan(metricsBefore.clientHeight);

    // Leave natural bottom so jump button can appear (real user wheel).
    await userScrollUp(page, -500);
    await page.waitForFunction(() => {
      const el = document.querySelector(".ask-conversation-scroll");
      if (!el) return false;
      return el.scrollHeight - el.clientHeight - el.scrollTop > 2;
    });

    const jumpButton = page.locator('[data-testid="ask-jump-to-latest"]');
    await expect(jumpButton).toBeVisible({ timeout: 5000 });

    // Evidence for the report.
    const metrics = await getScrollMetrics(page);
    expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
    expect(metrics.scrollHeight - metrics.clientHeight - metrics.scrollTop).toBeGreaterThan(2);
  });

  test("11. 点击后真实到达回答末尾,而不是用户问题锚点", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, buildSuccessScript({ longAnswer: true }));

    await submitQuestion(page, "跳转末尾问题");
    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );

    await page.waitForFunction(() => {
      const el = document.querySelector(".ask-conversation-scroll");
      return !!el && el.scrollHeight > el.clientHeight;
    });

    await userScrollUp(page, -500);

    const jumpButton = page.locator('[data-testid="ask-jump-to-latest"]');
    await expect(jumpButton).toBeVisible({ timeout: 5000 });
    await jumpButton.click();
    await waitAtNaturalBottom(page);

    const metrics = await getScrollMetrics(page);
    const naturalBottom = Math.max(0, metrics.scrollHeight - metrics.clientHeight);
    expect(naturalBottom - metrics.scrollTop).toBeLessThanOrEqual(2);
    // Evidence fields for report
    expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
    expect(metrics.scrollTop).toBeGreaterThan(0);
  });

  test("12. 点击后同一轮后续内容增长时持续跟随自然底部", async ({ page }) => {
    await loginAndOpenHarness(page);

    // Seed turn 1: long answer so the conversation already overflows.
    await setScript(page, buildSuccessScript({ longAnswer: true }));
    await submitQuestion(page, "种子问题以制造初始溢出");
    await expect(page.locator('[data-testid="ask-assistant-message"]').last()).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );
    await page.waitForFunction(() => {
      const el = document.querySelector(".ask-conversation-scroll");
      return !!el && el.scrollHeight > el.clientHeight;
    });

    // Turn 2: hold after early progress so we can jump while stream is live,
    // then release a much longer answer to grow content.
    const longerAnswer = LONG_ANSWER + "\n\n" + LONG_ANSWER_UNIT.repeat(60);
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload({ message_id: "msg-agentic-r2-2", turn_run_id: "turn-run-r2-2" }) },
      {
        event: "agentic.progress",
        data: progressPayload(1, "reading_context", "正在分析当前文章"),
      },
      {
        event: "agentic.progress",
        data: progressPayload(2, "composing_answer", "正在组织回答"),
        hold: true,
      },
      {
        event: "message.completed",
        data: {
          ...agenticCompletedPayload(longerAnswer),
          message_id: "msg-agentic-r2-2",
          turn_run_id: "turn-run-r2-2",
        },
      },
    ]);

    await submitQuestion(page, "持续跟随问题");
    await waitForActivityPhase(page, "composing_answer");
    await waitForStreamWaiting(page);

    // Leave natural bottom while stream still open (real user wheel).
    await userScrollUp(page, -300);

    const jumpButton = page.locator('[data-testid="ask-jump-to-latest"]');
    await expect(jumpButton).toBeVisible({ timeout: 5000 });
    await jumpButton.click();
    await waitAtNaturalBottom(page);

    const beforeGrowth = await getScrollMetrics(page);
    expect(beforeGrowth.scrollHeight - beforeGrowth.clientHeight - beforeGrowth.scrollTop).toBeLessThanOrEqual(2);

    // Release the long completed answer — content grows under natural-bottom follow.
    await releaseAll(page);
    await expect(page.locator('[data-testid="ask-assistant-message"]').last()).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );

    await page.waitForFunction((prevHeight) => {
      const el = document.querySelector(".ask-conversation-scroll");
      return !!el && el.scrollHeight > prevHeight;
    }, beforeGrowth.scrollHeight);

    // Follow proof: after growth, jump button stays hidden (still at natural bottom).
    await expect(page.locator('[data-testid="ask-jump-to-latest"]')).toHaveCount(0, {
      timeout: 15_000,
    });

    const afterGrowth = await getScrollMetrics(page);
    expect(afterGrowth.scrollHeight).toBeGreaterThan(beforeGrowth.scrollHeight);
    // scrollTop must advance with content growth (not stay parked).
    expect(afterGrowth.scrollTop).toBeGreaterThan(beforeGrowth.scrollTop);
    const naturalBottom = Math.max(0, afterGrowth.scrollHeight - afterGrowth.clientHeight);
    expect(naturalBottom - afterGrowth.scrollTop).toBeLessThanOrEqual(4);
  });

  test("13. 发送下一条用户问题后重新进入 question-anchor", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, buildSuccessScript({ longAnswer: true }));

    await submitQuestion(page, "第一个问题");
    await expect(page.locator('[data-testid="ask-assistant-message"]').last()).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );

    // Explicit jump leaves turn 1 in natural-bottom follow.
    await page.waitForFunction(() => {
      const el = document.querySelector(".ask-conversation-scroll");
      return !!el && el.scrollHeight > el.clientHeight;
    });
    await userScrollUp(page, -500);
    const jumpButton = page.locator('[data-testid="ask-jump-to-latest"]');
    await expect(jumpButton).toBeVisible({ timeout: 5000 });
    await jumpButton.click();
    await waitAtNaturalBottom(page);
    const afterJump = await getScrollMetrics(page);
    expect(afterJump.scrollHeight - afterJump.clientHeight - afterJump.scrollTop).toBeLessThanOrEqual(2);

    // Turn 2: long answer under question-anchor (mode resets on new user id).
    // Question-anchor only visibly differs from natural-bottom once content grows
    // below the latest user message — measure after the answer lands.
    await setScript(page, [
      {
        event: "agentic.run_started",
        data: runStartedPayload({ message_id: "msg-agentic-r2-13", turn_run_id: "turn-run-r2-13" }),
      },
      {
        event: "agentic.progress",
        data: progressPayload(1, "reading_context", "正在分析当前文章"),
      },
      {
        event: "agentic.progress",
        data: progressPayload(2, "composing_answer", "正在组织回答"),
      },
      {
        event: "message.completed",
        data: {
          ...agenticCompletedPayload(LONG_ANSWER),
          message_id: "msg-agentic-r2-13",
          turn_run_id: "turn-run-r2-13",
        },
      },
    ]);
    await submitQuestion(page, "第二个问题");
    await expect(page.locator('[data-testid="ask-user-message"]')).toHaveCount(2, {
      timeout: 5000,
    });
    await expect(page.locator('[data-testid="ask-assistant-message"]').last()).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );

    // Under question-anchor, the latest user question stays near the top while
    // the long answer grows below. Natural-bottom follow would leave the user
    // message far above the viewport.
    await page.waitForFunction(() => {
      const users = document.querySelectorAll('[data-testid="ask-user-message"]');
      const last = users[users.length - 1];
      const scroll = document.querySelector(".ask-conversation-scroll");
      if (!last || !scroll) return false;
      if (scroll.scrollHeight <= scroll.clientHeight) return false;
      const topInContainer = last.getBoundingClientRect().top - scroll.getBoundingClientRect().top;
      return topInContainer >= -8 && topInContainer < scroll.clientHeight * 0.55;
    }, undefined, { timeout: 15_000 });

    const rect = await page.evaluate(() => {
      const users = document.querySelectorAll('[data-testid="ask-user-message"]');
      const last = users[users.length - 1];
      const scroll = document.querySelector(".ask-conversation-scroll");
      if (!last || !scroll) return null;
      const userRect = last.getBoundingClientRect();
      const scrollRect = scroll.getBoundingClientRect();
      return {
        topInContainer: userRect.top - scrollRect.top,
        containerHeight: scrollRect.height,
        scrollTop: scroll.scrollTop,
        naturalBottom: Math.max(0, scroll.scrollHeight - scroll.clientHeight),
      };
    });
    expect(rect).not.toBeNull();
    expect(rect!.topInContainer).toBeGreaterThanOrEqual(-8);
    expect(rect!.topInContainer).toBeLessThan(rect!.containerHeight * 0.55);
    // Not parked at natural bottom (that would be the previous jump mode).
    expect(rect!.naturalBottom - rect!.scrollTop).toBeGreaterThan(2);
  });

  test("14. jump 后用户主动上滚,后续 progress/content 不抢回", async ({ page }) => {
    await loginAndOpenHarness(page);

    // Seed overflow so upward scroll is meaningful.
    await setScript(page, buildSuccessScript({ longAnswer: true }));
    await submitQuestion(page, "种子溢出");
    await expect(page.locator('[data-testid="ask-assistant-message"]').last()).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );

    await setScript(page, [
      {
        event: "agentic.run_started",
        data: runStartedPayload({ message_id: "msg-agentic-r2-14", turn_run_id: "turn-run-r2-14" }),
      },
      {
        event: "agentic.progress",
        data: progressPayload(1, "reading_context", "正在分析当前文章"),
        hold: true,
      },
      {
        event: "agentic.progress",
        data: progressPayload(5, "composing_answer", "正在组织回答"),
        hold: true,
      },
      {
        event: "message.completed",
        data: {
          ...agenticCompletedPayload(LONG_ANSWER),
          message_id: "msg-agentic-r2-14",
          turn_run_id: "turn-run-r2-14",
        },
      },
    ]);

    await submitQuestion(page, "活跃流滚动保护");
    await waitForActivityPhase(page, "reading_context");
    await waitForStreamWaiting(page);
    await userScrollUp(page, -300);
    const jumpButton = page.locator('[data-testid="ask-jump-to-latest"]');
    await expect(jumpButton).toBeVisible({ timeout: 5000 });
    await jumpButton.click();
    await waitAtNaturalBottom(page);

    // Real user wheel-up while stream is still open.
    await userScrollUp(page, -220);
    const distanceFromBottomBefore = await page.evaluate(() => {
      const el = document.querySelector(".ask-conversation-scroll");
      if (!el) return 0;
      return Math.max(0, el.scrollHeight - el.clientHeight) - el.scrollTop;
    });
    expect(distanceFromBottomBefore).toBeGreaterThan(2);

    // Capture sequence at user-scroll time.
    const sequenceAtScroll = await page
      .locator('[data-testid="ask-agentic-activity"]')
      .getAttribute("data-activity-sequence");
    expect(Number(sequenceAtScroll)).toBe(1);

    // Release higher-sequence progress while user is away from bottom.
    await releaseNext(page);
    await waitForActivityPhase(page, "composing_answer");
    await waitForStreamWaiting(page);

    const sequenceAfterProgress = await page
      .locator('[data-testid="ask-agentic-activity"]')
      .getAttribute("data-activity-sequence");
    expect(Number(sequenceAfterProgress)).toBe(5);

    const distanceFromBottomAfter = await page.evaluate(() => {
      const el = document.querySelector(".ask-conversation-scroll");
      if (!el) return 0;
      return Math.max(0, el.scrollHeight - el.clientHeight) - el.scrollTop;
    });

    // Native scroll anchoring may adjust absolute scrollTop as content height changes.
    // The stable invariant is that the viewport keeps its distance from natural
    // bottom instead of being yanked back to it.
    expect(distanceFromBottomAfter).toBeGreaterThan(2);
    expect(Math.abs(distanceFromBottomAfter - distanceFromBottomBefore)).toBeLessThanOrEqual(8);

    await releaseAll(page);
    await expect(page.locator('[data-testid="ask-assistant-message"]').last()).toContainText(
      "Institutional memory",
      { timeout: 10_000 },
    );
    const afterCompletion = await getScrollMetrics(page);
    const naturalBottom = Math.max(
      0,
      afterCompletion.scrollHeight - afterCompletion.clientHeight,
    );
    expect(naturalBottom - afterCompletion.scrollTop).toBeGreaterThan(2);
  });

  test("15. reduced-motion 环境下 pulse 没有持续动画", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginAndOpenHarness(page);
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      {
        event: "agentic.progress",
        data: progressPayload(1, "reading_context", "正在分析当前文章"),
        hold: true,
      },
      {
        event: "message.completed",
        data: agenticCompletedPayload(SHORT_ANSWER),
      },
    ]);

    await submitQuestion(page, "测试问题");
    await expect(page.locator('[data-testid="ask-agentic-activity"]')).toBeVisible();

    const pulseAnimation = await page.evaluate(() => {
      const pulse = document.querySelector('[data-testid="ask-agentic-activity-pulse"]');
      if (!pulse) return null;
      const style = window.getComputedStyle(pulse);
      return {
        animationName: style.animationName,
        animationDuration: style.animationDuration,
        animationPlayState: style.animationPlayState,
      };
    });

    expect(pulseAnimation).not.toBeNull();
    expect(
      pulseAnimation!.animationName === "none" ||
        pulseAnimation!.animationDuration === "0s" ||
        pulseAnimation!.animationPlayState === "paused",
    ).toBeTruthy();

    await releaseAll(page);
  });

  test("16. 页面不得出现敏感信息", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, buildSuccessScript());

    await submitQuestion(page, "测试问题");
    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "主要观点",
      { timeout: 10_000 },
    );

    const pageContent = await page.evaluate(() => document.body.innerText);

    // Assert against actual sensitive values, not generic English words.
    expect(pageContent).not.toContain(ENVELOPE_FINGERPRINT);
    expect(pageContent).not.toContain("reasoning_content");
    expect(pageContent).not.toContain("evh_");
    expect(pageContent).not.toContain("agentic_model_unconfigured");
    expect(pageContent).not.toContain("handle_id");
    expect(pageContent).not.toContain("envelope_fingerprint");
  });

  test("17. Legacy Ask 行为不回归", async ({ page }) => {
    await loginAndOpenHarness(page);
    const legacyBody = "Legacy 回答正文：这是兼容路径的最终答案。";
    await setScript(page, buildLegacyScript(legacyBody));

    await submitQuestion(page, "legacy 回归问题");

    const assistantMessage = page.locator('[data-testid="ask-assistant-message"]');
    await expect(assistantMessage).toContainText("Legacy 回答正文", { timeout: 10_000 });
    await expect(assistantMessage).toContainText("兼容路径的最终答案");

    // Must not enter agentic activity state machine for this turn.
    await expect(page.locator('[data-testid="ask-agentic-activity"]')).toHaveCount(0);

    // No agentic evidence disclosure / fingerprint leakage.
    await expect(page.locator('[data-testid="agentic-evidence-disclosure"]')).toHaveCount(0);
    const pageContent = await page.evaluate(() => document.body.innerText);
    expect(pageContent).not.toContain(ENVELOPE_FINGERPRINT);
    expect(pageContent).not.toContain("agentic_evidence");
  });

  // -------------------------------------------------------------------------
  // ASK-REASONING-R1: real reasoning projection UI acceptance
  // -------------------------------------------------------------------------

  test("18. Reasoning 默认折叠 + 展开实时追加 + 完成后回看", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      { event: "agentic.reasoning.started", data: reasoningStartedPayload() },
      {
        event: "agentic.reasoning.delta",
        data: reasoningDeltaPayload(1, "先判断句子主干。"),
        hold: true,
      },
      {
        event: "agentic.reasoning.delta",
        data: reasoningDeltaPayload(2, "再确认从句关系。"),
        hold: true,
      },
      { event: "agentic.reasoning.completed", data: reasoningCompletedPayload(3) },
      { event: "message.completed", data: agenticCompletedPayload(SHORT_ANSWER) },
    ]);

    await submitQuestion(page, "reasoning 流式测试问题");

    // Reasoning appears collapsed by default (shimmer trigger, no auto-open).
    const trigger = page.locator('[data-slot="reasoning-trigger"]');
    await expect(trigger).toBeVisible({ timeout: 10_000 });
    await expect(trigger).toHaveAttribute("aria-expanded", "false");

    // Expanding reveals the first projected delta (stream still held).
    // Scope to :visible so stale hidden Radix nodes from remounts (the
    // streaming→completed layout swap re-keys the message block) are ignored.
    await trigger.click();
    const visibleContent = page.locator('[data-slot="reasoning-content"]:visible');
    await expect(visibleContent).toContainText("先判断句子主干。", { timeout: 10_000 });

    // Release delta2 only (completed / message.completed stay gated): the
    // second delta appends live while the panel is still open.
    await releaseNext(page);
    await expect(visibleContent).toContainText("先判断句子主干。再确认从句关系。", {
      timeout: 10_000,
    });

    // Release the rest: the answer completes. The completed layout re-keys
    // the message block into a default-collapsed reasoning panel, which
    // remains fully reviewable on re-expand.
    await releaseAll(page);
    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "主要观点",
      { timeout: 10_000 },
    );
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await trigger.click();
    await expect(page.locator('[data-slot="reasoning-content"]:visible')).toContainText(
      "先判断句子主干。再确认从句关系。",
      { timeout: 10_000 },
    );
  });

  test("19. 无 reasoning 时不渲染任何 reasoning 元素", async ({ page }) => {
    await loginAndOpenHarness(page);
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      {
        event: "agentic.progress",
        data: progressPayload(1, "agent_running", "开始分析"),
      },
      {
        event: "agentic.progress",
        data: progressPayload(2, "agent_running", "分析完成"),
      },
      { event: "message.completed", data: agenticCompletedPayload(SHORT_ANSWER) },
    ]);

    await submitQuestion(page, "无 reasoning 问题");

    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "主要观点",
      { timeout: 10_000 },
    );

    // No reasoning events ⇒ no reasoning element, no empty placeholder.
    await expect(page.locator('[data-slot="reasoning"]')).toHaveCount(0);
    const pageContent = await page.evaluate(() => document.body.innerText);
    expect(pageContent).not.toContain("本轮模型未返回可展示的思考内容");
    expect(pageContent).not.toContain("思考过程");
  });

  test("20. Reasoning 投影泄漏扫描", async ({ page }) => {
    await loginAndOpenHarness(page);
    // Deltas carry the already-projected text (neutral citation marker);
    // raw sentinels never enter the scripted wire.
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      { event: "agentic.reasoning.started", data: reasoningStartedPayload() },
      {
        event: "agentic.reasoning.delta",
        data: reasoningDeltaPayload(1, "检查〔引用〕的范围后得出结论。"),
      },
      { event: "agentic.reasoning.completed", data: reasoningCompletedPayload(2) },
      { event: "message.completed", data: agenticCompletedPayload(SHORT_ANSWER) },
    ]);

    await submitQuestion(page, "reasoning 泄漏扫描问题");

    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "主要观点",
      { timeout: 10_000 },
    );

    // Projected text is reviewable.
    const trigger = page.locator('[data-slot="reasoning-trigger"]');
    await trigger.click();
    await expect(page.locator('[data-slot="reasoning-content"]')).toContainText(
      "检查〔引用〕的范围后得出结论。",
      { timeout: 10_000 },
    );

    const pageContent = await page.evaluate(() => document.body.innerText);
    expect(pageContent).not.toContain(ENVELOPE_FINGERPRINT);
    expect(pageContent).not.toContain("reasoning_content");
    expect(pageContent).not.toContain("evh_");
    expect(pageContent).not.toContain("handle_id");
    expect(pageContent).not.toContain("envelope_fingerprint");
    expect(pageContent).not.toContain("turn_run_id");
    expect(pageContent).not.toContain("projection_policy_version");
  });

  test("21. 外轮 reasoning.started 被忽略,不建立 reasoning 状态 (R3 P1b)", async ({
    page,
  }) => {
    await loginAndOpenHarness(page);
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      // Foreign-turn reasoning.started (turn_run_id mismatch) → ignored.
      {
        event: "agentic.reasoning.started",
        data: { ...reasoningStartedPayload(), turn_run_id: "turn-run-FOREIGN" },
      },
      {
        event: "agentic.reasoning.delta",
        data: { ...reasoningDeltaPayload(1, "外来思考。"), turn_run_id: "turn-run-FOREIGN" },
      },
      { event: "message.completed", data: agenticCompletedPayload(SHORT_ANSWER) },
    ]);

    await submitQuestion(page, "外轮 reasoning 测试问题");

    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "主要观点",
      { timeout: 10_000 },
    );

    // Foreign reasoning is ignored ⇒ no reasoning element, no foreign text.
    await expect(page.locator('[data-slot="reasoning"]')).toHaveCount(0);
    const pageContent = await page.evaluate(() => document.body.innerText);
    expect(pageContent).not.toContain("外来思考");
  });

  test("22. reasoning 未完成即 message.completed → interrupted 冻结且保留投影 (R3 P2)", async ({
    page,
  }) => {
    await loginAndOpenHarness(page);
    await setScript(page, [
      { event: "agentic.run_started", data: runStartedPayload() },
      { event: "agentic.reasoning.started", data: reasoningStartedPayload() },
      {
        event: "agentic.reasoning.delta",
        data: reasoningDeltaPayload(1, "部分投影思考。"),
      },
      // No reasoning.completed — the answer completes while reasoning is
      // still streaming. message.completed must freeze it as interrupted
      // (not force completed) while preserving the projected text.
      { event: "message.completed", data: agenticCompletedPayload(SHORT_ANSWER) },
    ]);

    await submitQuestion(page, "未完成 reasoning 测试问题");

    await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
      "主要观点",
      { timeout: 10_000 },
    );

    // Reasoning frozen as interrupted: trigger present, projected text
    // preserved and re-expandable.
    const trigger = page.locator('[data-slot="reasoning-trigger"]');
    await expect(trigger).toBeVisible({ timeout: 10_000 });
    await trigger.click();
    await expect(page.locator('[data-slot="reasoning-content"]:visible')).toContainText(
      "部分投影思考。",
      { timeout: 10_000 },
    );
  });
});
