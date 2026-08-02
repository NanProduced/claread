/**
 * ASK-PROCESS-UX-TARGET-R0 — browser contract tests.
 *
 * These tests mount the real Ask panel and feed it scripted SSE through the
 * existing gated harness. No model, provider, database, or external search
 * service is called.
 */

import { expect, test, type Page } from "@playwright/test";
import type { SpikeSseScriptEvent } from "@/app/e2e-plate-spike/ask-activity/types";

const HARNESS_URL = "/e2e-plate-spike/ask-activity";

test.beforeEach(() => {
  test.skip(
    true,
    "CUTOVER-WEB-LONG: ChainOfThought/process coverage is retained in Ask v2 Vitest; this legacy harness suite awaits Physical deletion.",
  );
});
const RECORD_ID = "test-record-r2-activity";
const THREAD_ID = "test-thread-r2-activity";
const MESSAGE_ID = "msg-process-r0-1";
const TURN_RUN_ID = "turn-run-process-r0-1";
const VERSION = "reader_record_ask_agentic_v2";

const browserConsoleErrors = new WeakMap<Page, string[]>();

function threadSummary() {
  return {
    id: THREAD_ID,
    record_id: RECORD_ID,
    title: "回答过程验收",
    is_default: true,
    selected_model: {
      key: "deepseek-v4-flash",
      label: "DeepSeek V4 Flash",
      price_multiplier: 1,
    },
    created_at: "2026-07-31T00:00:00Z",
    updated_at: "2026-07-31T00:00:00Z",
    last_message_at: null,
  };
}

async function mockApiRoutes(page: Page, messages: Record<string, unknown>[] = []) {
  await page.route("**/api/web/auth/phone/request-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, message: "验证码已生成" }),
    });
  });

  await page.route("**/api/web/auth/phone/verify-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: {
        "set-cookie": "claread_web_phone=13800138000; Path=/; SameSite=Lax; HttpOnly",
      },
      body: JSON.stringify({ ok: true, phone: "13800138000" }),
    });
  });

  await page.route(/\/api\/web\/reader-ask\/threads(?:[/?]|$)/, async (route) => {
    const requestUrl = new URL(route.request().url());
    const method = route.request().method();
    if (method === "GET" && requestUrl.pathname.endsWith(`/threads/${THREAD_ID}`)) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...threadSummary(), messages }),
      });
      return;
    }
    if (method === "GET" && requestUrl.pathname.endsWith("/threads")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [threadSummary()] }),
      });
      return;
    }
    if (method === "POST" && requestUrl.pathname.endsWith("/threads")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(threadSummary()),
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
            price_multiplier: 1,
            is_default: true,
            web_search_capability: "available",
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
  await page.goto("/login");
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await page.goto(HARNESS_URL);
  await expect(page.locator('[data-ask-composer-textarea="true"]')).toBeVisible();
  await page.waitForFunction(() => window.__spikeAskActivity?.ready === true);
  browserConsoleErrors.get(page)?.splice(0);
}

async function setScript(page: Page, script: SpikeSseScriptEvent[]) {
  await page.evaluate((nextScript) => {
    window.__spikeAskActivity?.setScript(nextScript);
  }, script);
}

async function releaseNext(page: Page) {
  await page.evaluate(() => window.__spikeAskActivity?.releaseNext());
}

async function releaseAll(page: Page) {
  await page.evaluate(() => window.__spikeAskActivity?.releaseAll());
}

async function getStreamState(page: Page) {
  return page.evaluate(() => window.__spikeAskActivity?.getStreamState() ?? null);
}

async function submitQuestion(page: Page, text = "请解释这篇文章") {
  await page.locator('[data-ask-composer-textarea="true"]').fill(text);
  await page.getByRole("button", { name: "发送" }).click();
}

function runStarted(): SpikeSseScriptEvent {
  return {
    event: "agentic.run_started",
    data: {
      execution_version: VERSION,
      message_id: MESSAGE_ID,
      thread_id: THREAD_ID,
      turn_run_id: TURN_RUN_ID,
      has_initial_selection: false,
    },
  };
}

function progress(
  sequence: number,
  phase: string,
  activity: "started" | "completed" | "unavailable" | "failed",
  status: "running" | "ok" | "unavailable" | "failed" | null,
  extras: Record<string, unknown> = {},
  hold = false,
): SpikeSseScriptEvent {
  return {
    event: "agentic.progress",
    data: {
      execution_version: VERSION,
      sequence,
      phase,
      activity,
      summary: "PRIVATE_INTERNAL_SUMMARY",
      elapsed_ms: sequence * 100,
      status,
      outcome: null,
      ...extras,
    },
    hold,
  };
}

function delta(generationId: number, text: string, hold = false): SpikeSseScriptEvent {
  return {
    event: "message.delta",
    data: {
      execution_version: VERSION,
      message_id: MESSAGE_ID,
      thread_id: THREAD_ID,
      turn_run_id: TURN_RUN_ID,
      generation_id: generationId,
      delta: text,
    },
    hold,
  };
}

function previewReset(generationId: number, hold = false): SpikeSseScriptEvent {
  return {
    event: "message.preview_reset",
    data: {
      execution_version: VERSION,
      message_id: MESSAGE_ID,
      thread_id: THREAD_ID,
      turn_run_id: TURN_RUN_ID,
      generation_id: generationId,
      reason: "tool_result_boundary",
    },
    hold,
  };
}

function completed(
  answerText: string,
  webSearch: Record<string, unknown> | null = null,
  citations: Record<string, unknown>[] = [],
): SpikeSseScriptEvent {
  return {
    event: "message.completed",
    data: {
      execution_version: VERSION,
      final_status: "ok",
      answer_text: answerText,
      answer_blocks: [{ text: answerText, citation_ids: citations.map((item) => item.citation_id) }],
      citations,
      knowledge_mode: null,
      source_status: null,
      web_search: webSearch,
      message_id: MESSAGE_ID,
      thread_id: THREAD_ID,
      turn_run_id: TURN_RUN_ID,
    },
  };
}

function terminal(
  finalStatus: "failed" | "cancelled",
): SpikeSseScriptEvent {
  return {
    event: "agentic.terminal",
    data: {
      execution_version: VERSION,
      final_status: finalStatus,
      message_id: MESSAGE_ID,
      thread_id: THREAD_ID,
      turn_run_id: TURN_RUN_ID,
      terminal_reason: finalStatus === "cancelled" ? "cancelled_by_user" : "validation_failed",
    },
  };
}

async function openProcess(page: Page) {
  const trigger = page.getByRole("button", { name: /回答过程/ });
  await expect(trigger).toBeVisible();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  const content = page.locator('[data-slot="chain-of-thought-content"]:visible');
  await expect(content).toBeVisible();
  return content;
}

async function assertSingleScrollOwner(page: Page) {
  const metrics = await page.evaluate(() => ({
    conversationScrollOwners: document.querySelectorAll(".ask-conversation-scroll").length,
    chainContentOverflow: Array.from(
      document.querySelectorAll('[data-slot="chain-of-thought-content"]'),
    ).map((element) => getComputedStyle(element).overflowY),
    composerCount: document.querySelectorAll('[data-ask-composer-textarea="true"]').length,
  }));
  expect(metrics.conversationScrollOwners).toBe(1);
  expect(metrics.composerCount).toBe(1);
  expect(metrics.chainContentOverflow.every((value) => value !== "auto" && value !== "scroll")).toBe(
    true,
  );
}

function coldAssistant(): Record<string, unknown> {
  return {
    id: "msg-cold-process-r0",
    thread_id: THREAD_ID,
    role: "assistant",
    status: "completed",
    content_md: "冷加载答案。",
    submission_mode: "chat",
    resolved_intent: "explain",
    citations: [],
    action_proposals: [],
    tool_trace: [],
    evidence: [],
    trace_summary: null,
    disambiguation: null,
    external_asset_disambiguation: null,
    response_cards: [],
    supplement_candidates: [],
    persisted_supplements: [],
    created_at: "2026-07-31T00:00:00Z",
    updated_at: "2026-07-31T00:00:00Z",
    execution_version: VERSION,
    final_status: "ok",
    reasoning_md: "PRIVATE_REASONING_COMPATIBILITY_FIELD",
    reasoning_status: "completed",
    agentic_answer_blocks: [{ text: "冷加载答案。", citation_ids: [] }],
    agentic_citations: [],
    agentic_web_search: null,
  };
}

test.describe("ASK-PROCESS-UX-TARGET-R0", () => {
  test.beforeEach(async ({ page }) => {
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

  test("纯回答、preview reset、流式增长与单一滚动 owner", async ({ page }, testInfo) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    const firstChunk = "旧代回答。".repeat(24);
    const secondChunk = "新代回答。".repeat(24);
    await setScript(page, [
      runStarted(),
      delta(0, firstChunk, true),
      previewReset(1, true),
      delta(1, secondChunk, true),
      completed("最终答案已完成。"),
    ]);

    await submitQuestion(page, "回答过程流式问题");
    const assistant = page.getByTestId("ask-assistant-message");
    await expect(assistant).toContainText("旧代回答。");
    const process = page.getByTestId("ask-turn-process");
    await expect(process).toBeVisible();
    await expect(process).toContainText("回答过程");
    if (testInfo.project.name.includes("mobile")) {
      const headerBox = await page.getByRole("button", { name: /回答过程/ }).boundingBox();
      if (headerBox == null) {
        throw new Error("Answer Process header has no mobile bounding box");
      }
      expect(headerBox.height).toBeGreaterThanOrEqual(44);
    }
    await expect(page.locator('[data-ask-composer-textarea="true"]')).toBeVisible();
    const scroll = page.locator(".ask-conversation-scroll");
    const firstScrollHeight = await scroll.evaluate((element) => element.scrollHeight);

    await releaseNext(page);
    await expect(assistant).not.toContainText("旧代回答。");
    await releaseNext(page);
    await expect(assistant).toContainText("新代回答。");
    const secondScrollHeight = await scroll.evaluate((element) => element.scrollHeight);
    expect(secondScrollHeight).toBeGreaterThanOrEqual(firstScrollHeight);

    await releaseAll(page);
    await expect(assistant).toContainText("最终答案已完成。");
    await expect(process).toContainText("已完成");
    const content = await openProcess(page);
    await expect(content).toContainText("生成回答");
    await expect(page.locator('[data-step-id="citation-check"]')).toHaveCount(0);
    await assertSingleScrollOwner(page);
  });

  test("delta 后的 server progress sequence 不与 answering 合成事件碰撞", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    const final = completed("preview reset 后的最终答案。");
    final.hold = true;
    await setScript(page, [
      runStarted(),
      delta(0, "第一段回答。", true),
      previewReset(1, true),
      delta(1, "第二段回答。", true),
      progress(1, "searching_article", "started", "running", {
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "running",
      }, true),
      progress(2, "searching_web", "started", "running", {
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
      }, true),
      final,
    ]);

    await submitQuestion(page, "sequence 不碰撞问题");
    const content = await openProcess(page);
    const assistant = page.getByTestId("ask-assistant-message");
    await expect(assistant).toContainText("第一段回答。");
    await expect.poll(async () => (await getStreamState(page))?.waiting).toBe(true);
    await releaseNext(page);
    await expect(assistant).not.toContainText("第一段回答。");
    await expect.poll(async () => (await getStreamState(page))?.waiting).toBe(true);
    await releaseNext(page);
    await expect(assistant).toContainText("第二段回答。");
    await expect.poll(async () => (await getStreamState(page))?.waiting).toBe(true);
    await releaseNext(page);
    await expect(content.locator('[data-step-id="article-evidence"]')).toBeVisible();
    await expect.poll(async () => (await getStreamState(page))?.waiting).toBe(true);
    await releaseNext(page);
    await expect(content.locator('[data-step-id="web-evidence"]')).toBeVisible();
    await expect(content.locator('[data-step-id="answering"]')).toBeVisible();
    await expect(page.getByTestId("ask-assistant-message")).not.toContainText(
      "PRIVATE_INTERNAL_SUMMARY",
    );
    await releaseAll(page);
    await expect(page.getByText("preview reset 后的最终答案。")).toBeVisible();
    await assertSingleScrollOwner(page);
  });

  test("first-seen steps、文章工具合并、网页成功与隐私边界", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    const webCitation = {
      citation_id: "c-web-1",
      source_kind: "web",
      snippet: "公开网页片段",
      url: "https://example.test/private?token=secret",
      title: "公开网页",
      description: null,
    };
    await setScript(page, [
      runStarted(),
      progress(1, "analysis", "started", "running"),
      progress(2, "analysis", "completed", "ok", { outcome: "success" }),
      progress(3, "searching_web", "started", "running", {
        tool_name: "search_web",
        activity_id: "web_search",
        attempt_count: 1,
        call_sequence: 1,
      }),
      progress(4, "searching_web", "completed", "ok", {
        tool_name: "search_web",
        activity_id: "web_search",
        attempt_count: 2,
        call_sequence: 2,
        outcome: "success",
      }),
      progress(5, "searching_article", "started", "running", {
        tool_name: "expand_evidence",
        activity_id: "article_evidence",
      }),
      progress(6, "searching_article", "completed", "ok", {
        tool_name: "expand_evidence",
        activity_id: "article_evidence",
        outcome: "success",
      }),
      progress(7, "searching_article", "started", "running", {
        tool_name: "search_current_article",
        activity_id: "article_evidence",
      }),
      progress(8, "searching_article", "completed", "ok", {
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        outcome: "success",
      }),
      progress(9, "validating_evidence", "started", "running"),
      delta(0, "正文片段"),
      completed("带依据的回答。", { outcome: "completed", cited_source_count: 1 }, [webCitation]),
    ]);

    await submitQuestion(page, "顺序与依据问题");
    await expect(page.getByText("带依据的回答。")).toBeVisible();
    const content = await openProcess(page);
    const stepIds = await content.locator("[data-step-id]").evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("data-step-id")),
    );
    expect(stepIds).toEqual([
      "analysis",
      "web-evidence",
      "article-evidence",
      "answering",
    ]);
    await expect(content.getByText("分析问题")).toBeVisible();
    await expect(content.getByText("查找文章依据")).toBeVisible();
    await expect(content.getByText("查询网页")).toBeVisible();
    await expect(content.getByText("生成回答")).toBeVisible();
    await expect(content.getByText("example.test")).toBeVisible();
    await expect(content).not.toContainText("PRIVATE_INTERNAL_SUMMARY");
    await expect(content).not.toContainText("https://");
    await expect(content).not.toContainText("token=secret");
    await expect(content).not.toContainText("expand_evidence");
    await expect(content.locator('[data-step-id="citation-check"]')).toHaveCount(0);
    await assertSingleScrollOwner(page);
  });

  test("网页 no_results 是空结果而不是成功勾选", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    await setScript(page, [
      runStarted(),
      progress(1, "searching_web", "started", "running", {
        tool_name: "search_web",
        activity_id: "web_search",
        attempt_count: 1,
        call_sequence: 1,
      }),
      progress(2, "searching_web", "completed", "ok", {
        tool_name: "search_web",
        activity_id: "web_search",
        attempt_count: 2,
        call_sequence: 2,
        outcome: "empty",
      }, true),
      delta(0, "没有网页依据的回答。", true),
      completed("没有网页依据的回答。", { outcome: "no_results", cited_source_count: 0 }),
    ]);

    await submitQuestion(page, "没有结果问题");
    const content = await openProcess(page);
    await releaseNext(page);
    await expect
      .poll(async () => (await getStreamState(page))?.emitted)
      .toBe(4);
    expect(await getStreamState(page)).toMatchObject({
      emitted: 4,
      waiting: true,
      finished: false,
    });
    const webStep = content.locator('[data-step-id="web-evidence"]');
    await expect(webStep).toHaveAttribute("data-step-outcome", "empty");
    await expect(webStep).toContainText("未找到相关网页结果");
    await expect(webStep).toContainText("已尝试 2 次");
    await expect(webStep.locator("[data-step-icon-outcome='empty']")).toHaveCount(1);
    await expect(webStep.locator("[data-step-accessible-status]")).toHaveText("未找到结果");
    await expect(content.locator('[data-step-id="citation-check"]')).toHaveCount(0);
    await releaseAll(page);
    await expect(page.getByText("没有网页依据的回答。")).toBeVisible();
  });

  test("Web unavailable 重试 success 后以 completed 为终态且不显示 optional warning", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    const final = completed("网页恢复后的回答。", {
      outcome: "completed",
      cited_source_count: 0,
    });
    await setScript(page, [
      runStarted(),
      progress(1, "searching_web", "unavailable", "unavailable", {
        tool_name: "search_web",
        activity_id: "web_search",
        status: "unavailable",
        outcome: "degraded",
      }),
      progress(2, "searching_web", "completed", "ok", {
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        outcome: "success",
      }, true),
      delta(0, "网页恢复后的回答。", true),
      final,
    ]);

    await submitQuestion(page, "网页恢复问题");
    const content = await openProcess(page);
    const webStep = content.locator('[data-step-id="web-evidence"]');
    await releaseAll(page);

    await expect(page.getByText("网页恢复后的回答。")).toBeVisible();
    await expect(webStep).toHaveAttribute("data-step-outcome", "success");
    await expect(page.getByTestId("ask-turn-notice")).toHaveCount(0);
  });

  test("Web Host 聚合：no_results 到 timeout 最终为 degraded", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    const final = completed("网页超时后的回答。", {
      outcome: "timeout",
      cited_source_count: 0,
    });
    final.hold = true;
    await setScript(page, [
      runStarted(),
      progress(1, "searching_web", "started", "running", {
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
      }),
      progress(2, "searching_web", "completed", "ok", {
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        outcome: "empty",
      }, true),
      delta(0, "等待网页超时处理。", true),
      progress(3, "searching_web", "unavailable", "unavailable", {
        tool_name: "search_web",
        activity_id: "web_search",
        status: "unavailable",
        outcome: "degraded",
      }, true),
      final,
    ]);

    await submitQuestion(page, "网页聚合优先级问题");
    const content = await openProcess(page);
    await expect.poll(async () => (await getStreamState(page))?.waiting).toBe(true);
    await releaseNext(page);
    const webStep = content.locator('[data-step-id="web-evidence"]');
    await expect(webStep).toHaveAttribute("data-step-outcome", "empty");
    await expect.poll(async () => (await getStreamState(page))?.waiting).toBe(true);
    await releaseNext(page);
    await expect(webStep).toHaveAttribute("data-step-outcome", "degraded");
    await expect(webStep.locator("[data-step-accessible-status]")).toHaveText("部分不可用");
    expect(await getStreamState(page)).toMatchObject({ finished: false });
    await releaseAll(page);
    await expect(page.getByText("网页超时后的回答。")).toBeVisible();
    await expect(webStep).toHaveAttribute("data-step-outcome", "degraded");
    await expect(page.getByTestId("ask-turn-notice")).toHaveCount(1);
    await expect(page.getByTestId("ask-turn-notice")).toContainText(
      "部分可选能力暂不可用，回答已正常生成。",
    );
    await expect(page.getByTestId("ask-turn-notice")).not.toContainText(
      "回答生成失败",
    );
  });

  test("Web success 后的 started/null 不回退已确认结果", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    const final = completed("网页成功后的回答。", {
      outcome: "completed",
      cited_source_count: 1,
    });
    final.hold = true;
    await setScript(page, [
      runStarted(),
      progress(1, "searching_web", "completed", "ok", {
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        outcome: "success",
      }, true),
      progress(2, "searching_web", "started", "running", {
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
        outcome: null,
      }, true),
      final,
    ]);

    await submitQuestion(page, "网页结果保持问题");
    const content = await openProcess(page);
    const webStep = content.locator('[data-step-id="web-evidence"]');
    await expect.poll(async () => (await getStreamState(page))?.waiting).toBe(true);
    await releaseNext(page);
    await expect(webStep).toHaveAttribute("data-step-outcome", "success");
    await expect.poll(async () => (await getStreamState(page))?.waiting).toBe(true);
    await releaseNext(page);
    await expect(webStep).toHaveAttribute("data-step-outcome", "success");
    await releaseAll(page);
    await expect(page.getByText("网页成功后的回答。")).toBeVisible();
    await expect(webStep).toHaveAttribute("data-step-outcome", "success");
  });

  test("文章 empty 在 message.completed 前公开安全状态", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    await setScript(page, [
      runStarted(),
      progress(1, "searching_article", "started", "running", {
        tool_name: "search_current_article",
        activity_id: "article_evidence",
      }),
      progress(2, "searching_article", "completed", "ok", {
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        outcome: "empty",
      }, true),
      delta(0, "没有文章依据的回答。", true),
      completed("没有文章依据的回答。"),
    ]);

    await submitQuestion(page, "文章没有结果问题");
    const content = await openProcess(page);
    await releaseNext(page);
    await expect
      .poll(async () => (await getStreamState(page))?.emitted)
      .toBe(4);
    expect(await getStreamState(page)).toMatchObject({
      emitted: 4,
      waiting: true,
      finished: false,
    });
    const articleStep = content.locator('[data-step-id="article-evidence"]');
    await expect(articleStep).toHaveAttribute("data-step-outcome", "empty");
    await expect(articleStep).toContainText("未找到相关文章依据");
    await expect(articleStep.locator("[data-step-accessible-status]")).toHaveText("未找到结果");
    await releaseAll(page);
    await expect(page.getByText("没有文章依据的回答。")).toBeVisible();
  });

  test("网页 degraded、取消和未完成 validation 都不伪造成功", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    await setScript(page, [
      runStarted(),
      progress(1, "searching_article", "started", "running", {
        tool_name: "search_current_article",
        activity_id: "article_evidence",
      }),
      progress(2, "searching_web", "started", "running", {
        tool_name: "search_web",
        activity_id: "web_search",
        attempt_count: 1,
        call_sequence: 1,
      }),
      progress(3, "searching_web", "unavailable", "unavailable", {
        tool_name: "search_web",
        activity_id: "web_search",
        attempt_count: 1,
        call_sequence: 1,
        outcome: "degraded",
      }),
      progress(4, "validating_evidence", "started", "running"),
      terminal("cancelled"),
    ]);

    await submitQuestion(page, "取消问题");
    const content = await openProcess(page);
    await expect(content.locator('[data-step-id="article-evidence"]')).toHaveAttribute(
      "data-step-outcome",
      "interrupted",
    );
    await expect(content.locator('[data-step-id="web-evidence"]')).toHaveAttribute(
      "data-step-outcome",
      "degraded",
    );
    await expect(content.locator('[data-step-id="web-evidence"]')).toContainText(
      "部分可用信息",
    );
    await expect(content.locator('[data-step-id="citation-check"]')).toHaveCount(0);
    await expect(content).not.toContainText("PRIVATE_INTERNAL_SUMMARY");
  });

  test("validation failure 只保留可证明的步骤", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    await setScript(page, [
      runStarted(),
      progress(1, "searching_article", "started", "running", {
        tool_name: "search_current_article",
        activity_id: "article_evidence",
      }),
      progress(2, "searching_article", "completed", "ok", {
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        outcome: "success",
      }),
      progress(3, "validating_evidence", "failed", "failed"),
      terminal("failed"),
    ]);

    await submitQuestion(page, "引用检查失败问题");
    const content = await openProcess(page);
    await expect(content.locator('[data-step-id="article-evidence"]')).toHaveAttribute(
      "data-step-outcome",
      "success",
    );
    await expect(content.locator('[data-step-id="citation-check"]')).toHaveCount(0);
    await expect(page.getByRole("button", { name: /回答过程：本轮回答未能完成/ })).toBeVisible();
  });

  test("可感知 context compaction 是独立短状态，冷加载不恢复过程卡", async ({ page }) => {
    await mockApiRoutes(page);
    await loginAndOpenHarness(page);
    await setScript(page, [
      runStarted(),
      {
        event: "context.compaction.started",
        data: {
          execution_version: VERSION,
          message_id: MESSAGE_ID,
          thread_id: THREAD_ID,
          turn_run_id: TURN_RUN_ID,
          attempt_count: 1,
          elapsed_ms: 500,
          detail_code: null,
        },
        hold: true,
      },
      progress(1, "analysis", "started", "running"),
      delta(0, "压缩后回答。"),
      completed("压缩后回答。"),
    ]);

    await submitQuestion(page, "上下文压缩问题");
    await expect(page.getByTestId("ask-turn-process")).toContainText("正在整理较早对话");
    await expect(page.getByTestId("ask-turn-process")).not.toContainText("分析问题");
    await releaseAll(page);
    await expect(page.getByText("压缩后回答。")).toBeVisible();

    await mockApiRoutes(page, [coldAssistant()]);
    await page.reload();
    await expect(page.getByText("冷加载答案。")).toBeVisible();
    await expect(page.locator('[data-testid="ask-turn-process"]')).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("PRIVATE_REASONING_COMPATIBILITY_FIELD");
  });
});
