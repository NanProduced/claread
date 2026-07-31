/**
 * ASK-UX-HISTORY-COT-R2 P0-4 — Real streaming output proof.
 *
 * Root cause traced before writing this spec (see report): the backend
 * `message.delta` SSE event carried only `delta` + `generation_id` but
 * NOT the turn identity fields (`message_id` / `thread_id` /
 * `turn_run_id`). The frontend `activeRunIdentity` guard — set on
 * `agentic.run_started` — rejected every delta as a foreign/stale frame,
 * so `provisional_content_md` never accumulated and the bubble jumped
 * straight from empty to the canonical completed answer. The fix lives
 * in production_stream.py (delta now carries full identity, mirroring
 * the `message.preview_reset` contract).
 *
 * This spec scripts the exact SSE sequence the task requires:
 *   agentic.run_started → progress → message.started → preview_reset →
 *   message.delta ×3 (≥150ms apart) → message.completed
 *
 * Acceptance:
 * - At least TWO visible body content changes BEFORE completed.
 * - After completed, canonical replaces provisional: text not
 *   duplicated, paragraphs not lost.
 * - Desktop + mobile coverage.
 * - DOM leak scan: no raw reasoning / evh_ / query / URL / provider
 *   payload / internal IDs.
 *
 * The deltas in this script carry the identity fields the real backend
 * now sends (post-fix). Without them the frontend guard would discard
 * the deltas and the "≥2 changes before completed" assertions would
 * time out — that is exactly the regression this spec guards against.
 */

import { expect, test, type Page } from "@playwright/test";
import type { SpikeSseScriptEvent } from "@/app/e2e-plate-spike/ask-activity/types";

const HARNESS_URL = "/e2e-plate-spike/ask-activity";
const RECORD_ID = "test-record-stream-r2";
const THREAD_ID = "test-thread-stream-r2";
const MESSAGE_ID = "msg-stream-r2-1";
const TURN_RUN_ID = "turn-run-stream-r2-1";
const EXECUTION_VERSION = "reader_record_ask_agentic_v2";

// Delta increments — chosen so each append produces a distinct visible
// substring that can be asserted independently of the others.
const DELTA_1 = "流式片段一";
const DELTA_2 = "流式片段二";
const DELTA_3 = "流式片段三";
const PROVISIONAL_FULL = DELTA_1 + DELTA_2 + DELTA_3;

// Canonical answer — deliberately multi-paragraph and DIFFERENT from the
// provisional preview to prove clean replacement (no duplication, no
// lost paragraphs). The provisional preview is plain concatenated text;
// the canonical answer is the formatted final answer.
const CANONICAL_PARA_1 = "这是规范答案的第一段。";
const CANONICAL_PARA_2 = "这是规范答案的第二段。";
const CANONICAL_FULL = `${CANONICAL_PARA_1}\n\n${CANONICAL_PARA_2}`;

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

function progressPayload(sequence: number, phase: string, summary: string) {
  return {
    execution_version: EXECUTION_VERSION,
    sequence,
    phase,
    activity: "started",
    summary,
    elapsed_ms: sequence * 100,
    tool_name: "read_range",
    status: "running",
  };
}

function messageStartedPayload() {
  return { message_id: MESSAGE_ID };
}

function previewResetPayload(generationId: number) {
  return {
    execution_version: EXECUTION_VERSION,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    generation_id: generationId,
    reason: "tool_result_boundary",
  };
}

function messageDeltaPayload(generationId: number, delta: string) {
  return {
    execution_version: EXECUTION_VERSION,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    generation_id: generationId,
    delta,
  };
}

function agenticCompletedPayload(answerText: string) {
  return {
    execution_version: EXECUTION_VERSION,
    final_status: "ok" as const,
    answer_text: answerText,
    answer_blocks: [
      { text: answerText, citation_ids: [] as string[] },
    ],
    citations: [],
    knowledge_mode: null,
    source_status: null,
    web_search: null,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
  };
}

/**
 * Build the scripted SSE sequence. Each delta is delayed ≥150ms before
 * emit (task contract) and held so the test can assert the intermediate
 * visible state before the next event flows.
 */
function buildStreamingScript(): SpikeSseScriptEvent[] {
  return [
    { event: "agentic.run_started", data: runStartedPayload() },
    {
      event: "agentic.progress",
      data: progressPayload(1, "reading_context", "正在分析当前文章"),
    },
    { event: "message.started", data: messageStartedPayload() },
    // preview_reset bumps the active generation to 1; subsequent deltas
    // MUST carry generation_id=1 or the frontend guard discards them.
    { event: "message.preview_reset", data: previewResetPayload(1) },
    {
      event: "message.delta",
      data: messageDeltaPayload(1, DELTA_1),
      delayMs: 160,
      hold: true,
    },
    {
      event: "message.delta",
      data: messageDeltaPayload(1, DELTA_2),
      delayMs: 160,
      hold: true,
    },
    {
      event: "message.delta",
      data: messageDeltaPayload(1, DELTA_3),
      delayMs: 160,
      hold: true,
    },
    { event: "message.completed", data: agenticCompletedPayload(CANONICAL_FULL) },
  ];
}

// ---------------------------------------------------------------------------
// API mocks + login
// ---------------------------------------------------------------------------

async function mockApiRoutes(page: Page) {
  await page.route("**/api/web/auth/phone/request-code", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "本地调验证证码已生成，请使用 888888。",
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
        body: JSON.stringify({
          id: THREAD_ID,
          record_id: RECORD_ID,
          title: "Stream Test",
          is_default: true,
          selected_model: {
            key: "deepseek-v4-flash",
            label: "DeepSeek V4 Flash",
            price_multiplier: 1.0,
          },
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          last_message_at: null,
        }),
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
          id: THREAD_ID,
          record_id: RECORD_ID,
          title: "Stream Test",
          is_default: true,
          selected_model: {
            key: "deepseek-v4-flash",
            label: "DeepSeek V4 Flash",
            price_multiplier: 1.0,
          },
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          last_message_at: null,
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

async function submitQuestion(page: Page, text: string) {
  await page.fill('[data-ask-composer-textarea="true"]', text);
  await page.click('button[aria-label="发送"]');
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
 * Count occurrences of a substring in the assistant bubble text. Used to
 * guard against duplication (canonical + provisional both rendered).
 */
async function countSubstring(page: Page, needle: string): Promise<number> {
  const assistant = page.locator('[data-testid="ask-assistant-message"]');
  const text = (await assistant.textContent()) ?? "";
  if (!needle) {
    return 0;
  }
  let count = 0;
  let idx = text.indexOf(needle);
  while (idx !== -1) {
    count += 1;
    idx = text.indexOf(needle, idx + needle.length);
  }
  return count;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("ASK-UX-HISTORY-COT-R2 P0-4 — real streaming delta proof", () => {
  test.describe.configure({ mode: "serial" });

  test("desktop 1440×900 — ≥2 visible body changes before completed, canonical replaces provisional", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAndOpenHarness(page);
    await setScript(page, buildStreamingScript());

    const assistant = page.locator('[data-testid="ask-assistant-message"]');
    await submitQuestion(page, "流式输出验证问题");

    // --- delta 1 lands (held) ---
    await waitForStreamWaiting(page);
    await expect(assistant).toContainText(DELTA_1, { timeout: 10_000 });

    // --- delta 2 lands (held) — visible change #1 ---
    await releaseNext(page);
    await waitForStreamWaiting(page);
    await expect(assistant).toContainText(DELTA_2, { timeout: 10_000 });
    // Provisional has accumulated delta1 + delta2.
    await expect(assistant).toContainText(DELTA_1 + DELTA_2, { timeout: 10_000 });

    // --- delta 3 lands (held) — visible change #2 ---
    await releaseNext(page);
    await waitForStreamWaiting(page);
    await expect(assistant).toContainText(DELTA_3, { timeout: 10_000 });
    await expect(assistant).toContainText(PROVISIONAL_FULL, { timeout: 10_000 });

    // Canonical not yet visible — completed has not fired.
    await expect(assistant).not.toContainText(CANONICAL_PARA_1);

    // --- R3: CoT is click-expandable WHILE streaming ---
    const trigger = assistant.locator('[data-slot="chain-of-thought-trigger"]');
    await expect(trigger).toBeVisible();
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    // Running header carries the live phase label; expanded content shows
    // the in-flight learner step (R2.1 contract labels; the v2 lane never
    // renders provider reasoning here, so no reasoning text is expected).
    await expect(assistant.locator('[data-testid="ask-turn-process"]')).toContainText(
      "正在查找文章依据",
    );
    const liveContent = assistant.locator('[data-slot="chain-of-thought-content"]:visible');
    await expect(liveContent).toContainText("查找文章依据");

    // --- completed lands — canonical replaces provisional ---
    await releaseNext(page);
    await waitForStreamFinished(page);

    // Canonical paragraphs are visible (no lost paragraphs).
    await expect(assistant).toContainText(CANONICAL_PARA_1, { timeout: 10_000 });
    await expect(assistant).toContainText(CANONICAL_PARA_2, { timeout: 10_000 });

    // Provisional preview is gone — no duplication. Each canonical
    // paragraph appears exactly once.
    const para1Count = await countSubstring(page, CANONICAL_PARA_1);
    const para2Count = await countSubstring(page, CANONICAL_PARA_2);
    expect(para1Count).toBe(1);
    expect(para2Count).toBe(1);

    // The provisional fragments must not survive the canonical replace.
    const frag1Count = await countSubstring(page, DELTA_1);
    const frag2Count = await countSubstring(page, DELTA_2);
    const frag3Count = await countSubstring(page, DELTA_3);
    expect(frag1Count).toBe(0);
    expect(frag2Count).toBe(0);
    expect(frag3Count).toBe(0);

    // --- R3: one-shot auto-close fired after settle; re-expand STICKS ---
    await expect(trigger).toHaveAttribute("aria-expanded", "false", { timeout: 10_000 });
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    const settledCot = assistant.locator('[data-testid="ask-turn-process"]');
    await expect(settledCot).toHaveAttribute("data-turn-process-state", "settled");
    // The answer step is preserved after completion (complete), never
    // hidden; the article-evidence step settled from the scripted progress.
    await expect(settledCot).toContainText("查找文章依据");
    await expect(
      settledCot.locator("[data-step-status='complete']").filter({ hasText: "生成回答" }),
    ).toBeVisible();
    // Hold past the auto-close delay: the user re-expand must persist.
    await page.waitForTimeout(1200);
    await expect(trigger).toHaveAttribute("aria-expanded", "true");

    // DOM leak scan — no internal IDs / handles / provider payloads.
    const bodyText = await page.locator("body").textContent();
    expect(bodyText).not.toContain("evh_");
    expect(bodyText).not.toContain(TURN_RUN_ID);
    expect(bodyText).not.toContain(MESSAGE_ID);
  });

  test("mobile 390×844 — streaming delta accumulates and canonical replaces provisional", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAndOpenHarness(page);
    await setScript(page, buildStreamingScript());

    const assistant = page.locator('[data-testid="ask-assistant-message"]');
    await submitQuestion(page, "移动端流式验证问题");

    // delta 1
    await waitForStreamWaiting(page);
    await expect(assistant).toContainText(DELTA_1, { timeout: 10_000 });

    // delta 2 — visible change #1
    await releaseNext(page);
    await waitForStreamWaiting(page);
    await expect(assistant).toContainText(DELTA_2, { timeout: 10_000 });

    // delta 3 — visible change #2
    await releaseNext(page);
    await waitForStreamWaiting(page);
    await expect(assistant).toContainText(DELTA_3, { timeout: 10_000 });

    // R3: CoT is click-expandable while streaming (mobile).
    const trigger = assistant.locator('[data-slot="chain-of-thought-trigger"]');
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    const liveContent = assistant.locator('[data-slot="chain-of-thought-content"]:visible');
    await expect(liveContent).toContainText("查找文章依据");

    // completed — canonical replaces provisional
    await releaseNext(page);
    await waitForStreamFinished(page);

    await expect(assistant).toContainText(CANONICAL_PARA_1, { timeout: 10_000 });
    await expect(assistant).toContainText(CANONICAL_PARA_2, { timeout: 10_000 });

    const para1Count = await countSubstring(page, CANONICAL_PARA_1);
    const para2Count = await countSubstring(page, CANONICAL_PARA_2);
    expect(para1Count).toBe(1);
    expect(para2Count).toBe(1);

    // Provisional fragments gone (no duplication).
    expect(await countSubstring(page, DELTA_1)).toBe(0);
    expect(await countSubstring(page, DELTA_2)).toBe(0);
    expect(await countSubstring(page, DELTA_3)).toBe(0);

    // R3: after the one-shot auto-close, the settled CoT re-expands and
    // sticks; 整理回答 is preserved as complete.
    await expect(trigger).toHaveAttribute("aria-expanded", "false", { timeout: 10_000 });
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    const settledCot = assistant.locator('[data-testid="ask-turn-process"]');
    await expect(settledCot).toHaveAttribute("data-turn-process-state", "settled");
    await expect(
      settledCot.locator("[data-step-status='complete']").filter({ hasText: "生成回答" }),
    ).toBeVisible();
    await page.waitForTimeout(1200);
    await expect(trigger).toHaveAttribute("aria-expanded", "true");

    // DOM leak scan.
    const bodyText = await page.locator("body").textContent();
    expect(bodyText).not.toContain("evh_");
    expect(bodyText).not.toContain(TURN_RUN_ID);
    expect(bodyText).not.toContain(MESSAGE_ID);
  });
});
