/**
 * ASK-COT (B1) — Ask Claread Chain of Thought browser acceptance.
 *
 * Reuses the /e2e-plate-spike/ask-activity harness (real AiWorkspacePanel,
 * gated fetch interceptor scripting real Agentic v2 wire payloads).
 *
 * Coverage matrix: 360×800 / 390×844 / 430×932 / 1440×900 ×
 *  1. reasoning frames are fail-closed in v2 (no reasoning rendered); the
 *     progress-derived learner step + single-scroll-owner + composer + leak
 *     scan still hold;
 *  2. non-ok terminal freezes the in-flight answer step as failed (cancelled
 *     remains interrupted) — never a success checkmark;
 *  3. pure-answer turn renders a settled disclosure with NO fabricated
 *     steps (v2 only shows steps the host can prove) and no reasoning;
 *  4. web search step: attempt hint + non-interactive domain chips,
 *     interactive sources stay with the web sources list; leak scan;
 *  5. truncated reasoning is fail-closed (no truncation notice, no
 *     reasoning body) — the v2 lane never renders provider reasoning.
 *
 * Coverage mapping for tests removed in ASK-CLAREAD-FINAL-CLOSEOUT-R1
 * (their R3-era semantics — a host "理解问题" step synthesized from
 * run_started, an always-on "整理回答" step, and compaction rendered as a
 * process STEP — were deliberately replaced by the R2.1 contract, which
 * derives steps only from analysis/article/web progress + the answer step
 * from the first identity-valid delta, and renders compaction as the
 * disclosure header live-summary, not a step):
 *  - "all steps scrollable inside the single scroll owner" → covered by
 *    reader-record-ask-process-target-r0.spec.ts (single scroll owner +
 *    composer-visible, desktop + 390×844).
 *  - "context compaction stays inside the same disclosure and precedes
 *    answer work" → covered by reader-record-ask-process-target-r0.spec.ts
 *    (compaction lifecycle: header live-summary precedes answer work, one
 *    disclosure, detail_code never leaks).
 *
 * No real providers: every event is scripted through the harness.
 */

import { expect, test, type Page } from "@playwright/test";
import type { SpikeSseScriptEvent } from "@/app/e2e-plate-spike/ask-activity/types";

const HARNESS_URL = "/e2e-plate-spike/ask-activity";
const RECORD_ID = "test-record-cot";
const THREAD_ID = "test-thread-cot";
const MESSAGE_ID = "msg-cot-1";
const TURN_RUN_ID = "turn-run-cot-1";
const EXECUTION_VERSION = "reader_record_ask_agentic_v2";
const REASONING_POLICY_VERSION = "reasoning_projection_v1";
const ENVELOPE_FINGERPRINT =
  "a1b2c3d4e5f60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00";

const VIEWPORTS = [
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 1440, height: 900 },
];

const SHORT_ANSWER = "根据文章内容，答案是：制度记忆塑造政策选择。";

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
    elapsed_ms: sequence * 700,
    ...extras,
  };
}

function completedPayload(overrides: Record<string, unknown> = {}) {
  return {
    execution_version: EXECUTION_VERSION,
    final_status: "ok" as const,
    answer_text: SHORT_ANSWER,
    answer_blocks: [{ text: SHORT_ANSWER, citation_ids: [] as string[] }],
    citations: [],
    knowledge_mode: null,
    source_status: null,
    web_search: null,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    ...overrides,
  };
}

function terminalPayload() {
  return {
    execution_version: EXECUTION_VERSION,
    final_status: "failed" as const,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    terminal_reason: null,
  };
}

function reasoningStartedPayload() {
  return {
    execution_version: EXECUTION_VERSION,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    seq: 0,
    projection_policy_version: REASONING_POLICY_VERSION,
  };
}

function reasoningDeltaPayload(seq: number, delta: string) {
  return { ...reasoningStartedPayload(), seq, delta };
}

function reasoningCompletedPayload(seq: number, truncated = false) {
  return {
    execution_version: EXECUTION_VERSION,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    seq,
    has_content: true,
    truncated,
    projection_policy_version: REASONING_POLICY_VERSION,
  };
}

// ---------------------------------------------------------------------------
// API mocks + harness control
// ---------------------------------------------------------------------------

function threadSummary() {
  return {
    id: THREAD_ID,
    record_id: RECORD_ID,
    title: "CoT Thread",
    is_default: true,
    selected_model: { key: "deepseek-v4-flash", label: "DeepSeek V4 Flash", price_multiplier: 1.0 },
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
      body: JSON.stringify({ ok: true, message: "本地调试验证码已生成，请使用 888888。" }),
    });
  });
  await page.route("**/api/web/auth/phone/verify-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "set-cookie": "claread_web_phone=13800138000; Path=/; SameSite=Lax; HttpOnly" },
      body: JSON.stringify({ ok: true, redirect_url: "/" }),
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
          { key: "deepseek-v4-flash", label: "DeepSeek V4 Flash", price_multiplier: 1.0, is_default: true },
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
}

async function setScript(page: Page, events: SpikeSseScriptEvent[]) {
  await page.evaluate((script) => {
    window.__spikeAskActivity?.setScript(script);
  }, events);
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

function lastBubble(page: Page) {
  return page.locator('[data-testid="ask-assistant-message"]').last();
}

function cotTrigger(page: Page) {
  return lastBubble(page).locator('[data-slot="chain-of-thought-trigger"]');
}

function cotRoot(page: Page) {
  return lastBubble(page).locator('[data-testid="ask-turn-process"]');
}

/** Number of scrollable descendants INSIDE the single scroll owner. */
async function nestedScrollableCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const owner = document.querySelector(".ask-conversation-scroll");
    if (!owner) return -1;
    let count = 0;
    owner.querySelectorAll("*").forEach((el) => {
      const style = window.getComputedStyle(el);
      const overflowY = style.overflowY;
      if ((overflowY === "auto" || overflowY === "scroll") && el.scrollHeight > el.clientHeight) {
        count += 1;
      }
    });
    return count;
  });
}

async function expectComposerVisible(page: Page) {
  const composer = page.locator('[data-ask-composer-textarea="true"]');
  await expect(composer).toBeVisible();
  const box = await composer.boundingBox();
  const size = page.viewportSize();
  expect(box, "composer must have a bounding box").not.toBeNull();
  expect(size, "viewport must be set").not.toBeNull();
  expect(box!.y + box!.height).toBeLessThanOrEqual(size!.height + 1);
  expect(box!.y).toBeGreaterThanOrEqual(-1);
}

// ---------------------------------------------------------------------------
// Scenario scripts
// ---------------------------------------------------------------------------

function reasoningFlowScript(options: { holdSecondDelta?: boolean } = {}): SpikeSseScriptEvent[] {
  return [
    { event: "agentic.run_started", data: runStartedPayload() },
    { event: "agentic.reasoning.started", data: reasoningStartedPayload() },
    { event: "agentic.reasoning.delta", data: reasoningDeltaPayload(1, "先判断句子主干。") },
    {
      event: "agentic.reasoning.delta",
      data: reasoningDeltaPayload(2, "再确认从句关系。"),
      hold: options.holdSecondDelta === true,
    },
    {
      event: "agentic.progress",
      data: progressPayload(1, "reading_context", "正在读取文章上下文", {
        tool_name: "read_range",
        status: "running",
      }),
    },
    {
      event: "agentic.progress",
      data: progressPayload(2, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 900,
      }),
    },
    { event: "agentic.reasoning.completed", data: reasoningCompletedPayload(3) },
    { event: "message.completed", data: completedPayload() },
  ];
}

function webSearchScript(): SpikeSseScriptEvent[] {
  return [
    { event: "agentic.run_started", data: runStartedPayload() },
    {
      event: "agentic.progress",
      data: progressPayload(1, "searching_web", "正在搜索网页", {
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 1,
        status: "running",
      }),
    },
    {
      event: "agentic.progress",
      data: progressPayload(2, "searching_web", "已完成网页搜索", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 2,
        attempt_count: 2,
        status: "ok",
        duration_ms: 1600,
      }),
    },
    {
      event: "message.completed",
      data: completedPayload({
        citations: [
          {
            citation_id: "w1",
            source_kind: "web",
            url: "https://www.example.com/article?q=session-token",
            title: "Example — Institutional Memory",
            description: null,
            published_at: null,
            retrieved_at: "2026-07-29T00:00:00Z",
          },
          {
            citation_id: "w2",
            source_kind: "web",
            url: "https://docs.policy.org/guide",
            title: "Policy Guide",
            description: null,
            published_at: null,
            retrieved_at: "2026-07-29T00:00:00Z",
          },
        ],
        web_search: { outcome: "completed", cited_source_count: 2 },
      }),
    },
  ];
}

// ---------------------------------------------------------------------------
// Tests — one describe per viewport
// ---------------------------------------------------------------------------

for (const viewport of VIEWPORTS) {
  test.describe(`ASK-COT Chain of Thought @ ${viewport.width}x${viewport.height}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport);
    });

    test("reasoning frames are fail-closed in v2; progress step + scroll + leak hold", async ({
      page,
    }) => {
      await loginAndOpenHarness(page);
      await setScript(page, reasoningFlowScript({ holdSecondDelta: true }));
      await submitQuestion(page, "reasoning fail-closed 与过程步骤");

      // v2 never auto-opens the disclosure and never renders provider
      // reasoning — even though the script emits agentic.reasoning.* frames.
      const trigger = cotTrigger(page);
      await expect(trigger).toBeVisible({ timeout: 10_000 });
      await expect(trigger).toHaveAttribute("aria-expanded", "false");

      // Settle, then expand: the progress-derived learner step renders with
      // its typed label, the reasoning deltas render NOWHERE (fail-closed).
      await releaseAll(page);
      await expect(lastBubble(page)).toContainText(SHORT_ANSWER, { timeout: 10_000 });
      await expect(cotRoot(page)).toHaveAttribute("data-turn-process-state", "settled");
      await cotTrigger(page).click();
      const settledContent = lastBubble(page).locator(
        '[data-slot="chain-of-thought-content"]:visible',
      );
      await expect(settledContent).toContainText("查找文章依据", { timeout: 10_000 });
      await expect(settledContent).not.toContainText("先判断句子主干。");
      await expect(settledContent).not.toContainText("再确认从句关系。");
      await expect(lastBubble(page).locator('[data-slot="reasoning"]')).toHaveCount(0);
      // Fixed typed step label — never server summary copy.
      expect(await settledContent.innerText()).not.toContain("已读取相关上下文");
      expect(await nestedScrollableCount(page)).toBe(0);
      await expectComposerVisible(page);

      // Leak scan over the whole page text.
      const pageText = await page.evaluate(() => document.body.innerText);
      expect(pageText).not.toContain("先判断句子主干。");
      expect(pageText).not.toContain("再确认从句关系。");
      expect(pageText).not.toContain("evh_");
      expect(pageText).not.toContain(ENVELOPE_FINGERPRINT);
      expect(pageText).not.toContain("turn_run_id");
      expect(pageText).not.toContain("projection_policy_version");
      expect(pageText).not.toContain("已读取相关上下文");
    });

    // "all steps scrollable inside the single scroll owner; composer stays
    // visible" was removed in ASK-CLAREAD-FINAL-CLOSEOUT-R1: its R3 four-step
    // shape (host 理解问题 + 整理回答) no longer exists in the R2.1 contract,
    // and its single-scroll-owner + composer-visible coverage now lives in
    // reader-record-ask-process-target-r0.spec.ts (see file header mapping).

    test("failed terminal freezes the in-flight answer step as failed — never a success checkmark", async ({
      page,
    }) => {
      await loginAndOpenHarness(page);
      await setScript(page, [
        { event: "agentic.run_started", data: runStartedPayload() },
        {
          event: "agentic.progress",
          data: progressPayload(1, "reading_context", "已读取相关上下文", {
            activity: "completed",
            tool_name: "read_range",
            status: "ok",
            duration_ms: 700,
          }),
        },
        // A real identity-valid delta creates the answer step (v2 derives the
        // answering step from the first delta, not from a composing progress).
        {
          event: "message.delta",
          data: {
            execution_version: EXECUTION_VERSION,
            message_id: MESSAGE_ID,
            thread_id: THREAD_ID,
            turn_run_id: TURN_RUN_ID,
            generation_id: 0,
            delta: "部分回答",
          },
        },
        { event: "agentic.terminal", data: terminalPayload() },
      ]);
      await submitQuestion(page, "terminal 冻结测试");

      // Turn notice (SystemMessage) owns the failure copy.
      await expect(lastBubble(page).locator('[data-testid="ask-turn-notice"]')).toBeVisible({
        timeout: 10_000,
      });

      // CoT keeps the safe process, frozen settled.
      await expect(cotRoot(page)).toHaveAttribute("data-turn-process-state", "settled");
      await cotTrigger(page).click();
      const steps = cotRoot(page).locator("[data-step-status]");
      const answering = cotRoot(page).locator("[data-step-status='failed']");
      await expect(answering).toContainText("生成回答");
      // A failed terminal marks the answer step failed; cancelled terminals
      // use interrupted. The only complete mark is the ok article-evidence
      // step (v2 synthesizes no host analysis step).
      expect(await cotRoot(page).locator("[data-step-status='complete']").count()).toBe(1);
      expect(await steps.count()).toBe(2);
      // Terminal explanation / server summary never leaks into the CoT.
      expect(await cotRoot(page).innerText()).not.toContain("回答生成失败");
      expect(await cotRoot(page).innerText()).not.toContain("正在组织回答");
      // Composer unlocks after terminal.
      await expect(page.locator('button[aria-label="发送"]')).toBeVisible();
    });

    // "context compaction stays inside the same disclosure and precedes answer
    // work" was removed in ASK-CLAREAD-FINAL-CLOSEOUT-R1: in R2.1 compaction is
    // the disclosure header live-summary ("正在整理较早对话"), NOT a process step,
    // so the R3 step-shaped assertions no longer apply. The same intent
    // (compaction perceivable in the one disclosure, preceding answer work,
    // detail_code never leaking) is covered by
    // reader-record-ask-process-target-r0.spec.ts (see file header mapping).

    test("pure-answer turn renders a settled disclosure with no fabricated steps or reasoning", async ({
      page,
    }) => {
      await loginAndOpenHarness(page);
      await setScript(page, [
        { event: "agentic.run_started", data: runStartedPayload() },
        { event: "message.completed", data: completedPayload() },
      ]);
      await submitQuestion(page, "无过程问题");
      await expect(lastBubble(page)).toContainText(SHORT_ANSWER, { timeout: 10_000 });

      // v2 shows a real collapse control but fabricates NO steps for a turn
      // with no provable host events (no analysis progress, no delta).
      const cot = lastBubble(page).locator('[data-testid="ask-turn-process"]');
      await expect(cot).toHaveCount(1);
      await expect(cot).toHaveAttribute("data-turn-process-state", "settled");
      await cotTrigger(page).click();
      const steps = cot.locator("[data-step-status]");
      expect(await steps.count()).toBe(0);
      // The legacy reasoning disclosure never appears for v2 turns.
      await expect(page.locator('[data-slot="reasoning"]')).toHaveCount(0);
    });

    test("truncated reasoning is fail-closed — no notice, no reasoning body in v2", async ({ page }) => {
      await loginAndOpenHarness(page);
      await setScript(page, [
        { event: "agentic.run_started", data: runStartedPayload() },
        { event: "agentic.reasoning.started", data: reasoningStartedPayload() },
        { event: "agentic.reasoning.delta", data: reasoningDeltaPayload(1, "部分可展示的思考。") },
        { event: "agentic.reasoning.completed", data: reasoningCompletedPayload(2, true) },
        { event: "message.completed", data: completedPayload() },
      ]);
      await submitQuestion(page, "截断 reasoning 问题");
      await expect(lastBubble(page)).toContainText(SHORT_ANSWER, { timeout: 10_000 });

      // The v2 lane never renders provider reasoning — so neither the
      // truncation notice nor the reasoning body may appear, even when the
      // script claims a truncated projection.
      await expect(lastBubble(page).locator('[data-testid="ask-reasoning-truncated"]')).toHaveCount(0);
      await expect(lastBubble(page).locator('[data-testid="ask-turn-process-reasoning"]')).toHaveCount(0);
      await expect(lastBubble(page).locator('[data-slot="reasoning"]')).toHaveCount(0);
      const pageText = await page.evaluate(() => document.body.innerText);
      expect(pageText).not.toContain("部分可展示的思考。");
      expect(pageText).not.toContain("已达到展示上限");
    });

    test("web step shows attempt hint and non-interactive domain chips; sources stay single-truth", async ({
      page,
    }) => {
      await loginAndOpenHarness(page);
      await setScript(page, webSearchScript());
      await submitQuestion(page, "web search 过程展示");
      await expect(lastBubble(page)).toContainText(SHORT_ANSWER, { timeout: 10_000 });

      await cotTrigger(page).click();
      const webStep = cotRoot(page).locator("[data-step-status='complete']").filter({
        hasText: "查询网页",
      });
      await expect(webStep).toBeVisible();
      await expect(webStep).toContainText("已尝试 2 次");
      expect(await webStep.innerText()).not.toContain("已完成网页搜索");

      // Compact domain chips — hostname only, NON-interactive spans.
      await expect(webStep.locator('[data-slot="chain-of-thought-search-result"]')).toHaveCount(2);
      await expect(webStep).toContainText("example.com");
      await expect(webStep).toContainText("docs.policy.org");
      expect(await webStep.locator("a").count()).toBe(0);
      // The full URL (with query string) never enters the CoT subtree.
      const cotHtml = await cotRoot(page).innerHTML();
      expect(cotHtml).not.toContain("https://");
      expect(cotHtml).not.toContain("session-token");

      // Interactive citation/navigation truth remains AgenticWebSources.
      const sources = lastBubble(page).locator('[data-testid="web-source-list"]');
      await expect(sources).toBeVisible();
      expect(await sources.locator("a").count()).toBeGreaterThan(0);

      // Leak scan.
      const pageText = await page.evaluate(() => document.body.innerText);
      expect(pageText).not.toContain("evh_");
      expect(pageText).not.toContain("handle_id");
      expect(pageText).not.toContain("session-token");
    });
  });
}
