/**
 * ASK-UX-MOBILE-R3 — Floating Overlay acceptance spec.
 *
 * Mounts the R3 floating-overlay harness
 * (/e2e-plate-spike/ask-floating-overlay), which configures a REAL
 * AiWorkspacePanel with:
 *   - layout="overlay"
 *   - surface="floating"
 *   - onChangeSurface (wired to a React state setter)
 *   - hasSidecarCapacity (toggleable at runtime)
 *   - A SCROLLABLE BACKGROUND so body-lock verification has a real
 *     overflow condition to test against.
 *
 * Coverage matrix (Task 9):
 *   Mobile 360x800, 390x844, 430x932 + Desktop 1440x900:
 *     1. capacity=false: floating overlay renders, no clickable surface
 *        dropdown trigger, no "侧边栏" menuitem; static "浮窗" label visible.
 *     2. capacity=true: surface menu trigger reappears; dropdown contains
 *        both "浮窗" and "侧边栏" items and "侧边栏" is selectable.
 *     3. Overlay uses dvh-based height (`h-[min(85dvh,38rem)]`) and lives
 *        inside the visual viewport (no part overflows the viewport edges).
 *     4. Composer textarea is always visible at any conversation scroll
 *        position (top, middle, bottom).
 *     5. `.ask-conversation-scroll` is the only conversation scroll owner:
 *        header, panel-notice banner, and composer do NOT scroll with the
 *        conversation.
 *     6. Real `message.delta` streaming: text grows incrementally,
 *        scrollHeight grows; user can scroll while deltas are still
 *        arriving; after `message.completed` the canonical text replaces
 *        the provisional preview without duplication or lost segments.
 *     7. Body overflow lock:
 *        - Background is genuinely scrollable BEFORE the panel opens (or
 *          after it closes).
 *        - When the overlay is open, document.body.scrollTop cannot be
 *          moved away from 0 (lock holds against wheel events on the
 *          background).
 *        - After closing the panel, body overflow is restored to its
 *          pre-open value and the background scrolls again.
 *     8. Terminal error (final_status=failed) renders inside the owning
 *        turn (ask-turn-notice); the composer-area banner slot
 *        (ask-panel-notice) does NOT render; composer stays interactive.
 *
 * Streaming contract (Task C):
 *   The script sends REAL Agentic v2 `message.delta` events (not just a
 *   single `message.completed`). Each delta carries:
 *     - message_id / thread_id / turn_run_id (matches run_started)
 *     - generation_id (1, after a preview_reset)
 *     - delta: a non-empty string chunk
 *   A `message.preview_reset` (generation_id=1) precedes the deltas.
 *   `message.completed` carries the canonical answer_text + answer_blocks.
 */

import { expect, test, type Page } from "@playwright/test";
import type { SpikeSseScriptEvent } from "@/app/e2e-plate-spike/ask-floating-overlay/types";

const HARNESS_URL = "/e2e-plate-spike/ask-floating-overlay";
const RECORD_ID = "test-record-r3-floating-overlay";
const THREAD_ID = "test-thread-r3-floating-overlay";
const MESSAGE_ID = "msg-r3-floating-overlay-1";
const TURN_RUN_ID = "turn-run-r3-floating-overlay-1";
const EXECUTION_VERSION = "reader_record_ask_agentic_v2";

// ---------------------------------------------------------------------------
// Wire-contract payloads (Agentic v2) — message.delta streaming variant.
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

function previewResetPayload(generationId = 1) {
  return {
    execution_version: EXECUTION_VERSION,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
    generation_id: generationId,
    reason: "tool_result_boundary",
  };
}

function deltaPayload(delta: string, generationId = 1) {
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
    answer_blocks: [{ text: answerText, citation_ids: [] as string[] }],
    citations: [],
    knowledge_mode: null,
    source_status: null,
    web_search: null,
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

// The first delta deliberately overflows the floating panel at every tested
// viewport. This lets the test prove that the user can scroll the real
// conversation owner while the SSE stream is still held, rather than only
// after message.completed.
const STREAMING_OVERFLOW_PARAGRAPH =
  "Institutional memory shapes policy choices in subtle ways. " +
  "Past decisions create path dependencies, while shared narratives anchor collective identity. ";
const DELTA_CHUNKS = [
  STREAMING_OVERFLOW_PARAGRAPH.repeat(32),
  "shapes policy choices ",
  "in subtle ways. ",
  "First, past decisions ",
  "create path dependencies. ",
  "Second, shared narratives ",
  "anchor collective identity. ",
  "Together these forces produce durable policy regimes.",
];
const LONG_ANSWER = DELTA_CHUNKS.join("");

// ---------------------------------------------------------------------------
// API mocks + login — same shape as the R0–R2 spec, scoped to this harness.
// ---------------------------------------------------------------------------

function threadSummary() {
  return {
    id: THREAD_ID,
    record_id: RECORD_ID,
    title: "Test Thread R3",
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
  await page.waitForFunction(() => window.__spikeAskFloatingOverlay?.ready === true);
}

async function setScript(page: Page, events: SpikeSseScriptEvent[]) {
  await page.evaluate((script) => {
    window.__spikeAskFloatingOverlay?.setScript(script);
  }, events);
}

async function releaseAll(page: Page) {
  await page.evaluate(() => {
    window.__spikeAskFloatingOverlay?.releaseAll();
  });
}

async function submitQuestion(page: Page, text: string) {
  await page.fill('[data-ask-composer-textarea="true"]', text);
  await page.click('button[aria-label="发送"]');
}

async function waitForStreamFinished(page: Page, timeout = 15_000) {
  await page.waitForFunction(
    () => window.__spikeAskFloatingOverlay?.getStreamState().finished === true,
    undefined,
    { timeout },
  );
}

/**
 * Build a streaming script that emits REAL message.delta chunks. The
 * first delta is held so the spec can assert mid-stream state (text
 * growing, scrollHeight growing, composer visible, user can scroll)
 * before releaseAll() flushes the remaining deltas + message.completed.
 */
function buildStreamingScript(options: {
  holdAfterFirstDelta?: boolean;
  deltaDelayMs?: number;
} = {}): SpikeSseScriptEvent[] {
  const { holdAfterFirstDelta = false, deltaDelayMs = 30 } = options;
  const events: SpikeSseScriptEvent[] = [
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
      data: progressPayload(2, "composing_answer", "正在组织回答", {
        status: "running",
      }),
    },
    { event: "message.started", data: { message_id: MESSAGE_ID } },
    { event: "message.preview_reset", data: previewResetPayload(1) },
  ];

  DELTA_CHUNKS.forEach((chunk, index) => {
    const isFirst = index === 0;
    events.push({
      event: "message.delta",
      data: deltaPayload(chunk, 1),
      delayMs: deltaDelayMs,
      hold: isFirst && holdAfterFirstDelta,
    });
  });

  events.push({
    event: "message.completed",
    data: agenticCompletedPayload(LONG_ANSWER),
  });

  return events;
}

function buildTerminalScript(
  finalStatus: "failed" | "cancelled" | "context_stale" | "invalid_citations" = "failed",
): SpikeSseScriptEvent[] {
  return [
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
      data: progressPayload(2, "composing_answer", "正在组织回答", {
        status: "running",
      }),
    },
    { event: "agentic.terminal", data: agenticTerminalPayload(finalStatus) },
  ];
}

// ---------------------------------------------------------------------------
// Helpers — capacity, body lock, overlay DOM.
// ---------------------------------------------------------------------------

async function setCapacity(page: Page, capacity: boolean) {
  await page.evaluate((value) => {
    window.__spikeAskFloatingOverlay?.setCapacity(value);
  }, capacity);
}

async function getCapacity(page: Page): Promise<boolean> {
  return page.evaluate(() => window.__spikeAskFloatingOverlay?.getCapacity() ?? false);
}

async function closePanel(page: Page) {
  await page.evaluate(() => {
    window.__spikeAskFloatingOverlay?.closePanel();
  });
}

async function openPanel(page: Page) {
  await page.evaluate(() => {
    window.__spikeAskFloatingOverlay?.openPanel();
  });
}

async function isOpen(page: Page): Promise<boolean> {
  return page.evaluate(() => window.__spikeAskFloatingOverlay?.isOpen() ?? false);
}

type BodyScrollState = {
  bodyOverflow: string;
  bodyScrollTop: number;
  htmlScrollTop: number;
  docHeight: number;
  viewportHeight: number;
};

async function getBodyScrollState(page: Page): Promise<BodyScrollState> {
  return page.evaluate(() => {
    const body = document.body;
    const html = document.documentElement;
    return {
      bodyOverflow: body.style.overflow,
      bodyScrollTop: body.scrollTop,
      htmlScrollTop: html.scrollTop,
      docHeight: Math.max(body.scrollHeight, html.scrollHeight),
      viewportHeight: window.innerHeight,
    };
  });
}

async function scrollBackgroundDown(page: Page, amount = 800) {
  // Wheel over the body (not the panel) to verify background scroll behavior.
  await page.mouse.move(50, 50);
  for (let i = 0; i < 6; i += 1) {
    await page.mouse.wheel(0, amount);
    await page.waitForTimeout(30);
  }
}

type OverlayGeometry = {
  exists: boolean;
  rect: { top: number; right: number; bottom: number; left: number; width: number; height: number };
  viewport: { width: number; height: number };
  heightUsesDvh: boolean;
  conversationScrollExists: boolean;
};

async function getOverlayGeometry(page: Page): Promise<OverlayGeometry> {
  return page.evaluate(() => {
    const panel = document.querySelector(".ai-workspace-panel--layout-overlay.ai-workspace-panel--surface-floating");
    if (!panel) {
      return {
        exists: false,
        rect: { top: 0, right: 0, bottom: 0, left: 0, width: 0, height: 0 },
        viewport: { width: window.innerWidth, height: window.innerHeight },
        heightUsesDvh: false,
        conversationScrollExists: false,
      };
    }
    const rect = panel.getBoundingClientRect();
    const cls = panel.className || "";
    // The Tailwind class `h-[min(85dvh,38rem)]` is the canonical dvh height.
    const heightUsesDvh = /h-\[min\(85dvh/.test(cls) || /85dvh/.test(cls);
    const conversationScrollExists =
      !!panel.querySelector(".ask-conversation-scroll");
    return {
      exists: true,
      rect: {
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        left: rect.left,
        width: rect.width,
        height: rect.height,
      },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      heightUsesDvh,
      conversationScrollExists,
    };
  });
}

async function getConversationScrollMetrics(page: Page) {
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

async function getProvisionalText(page: Page): Promise<string> {
  return page.evaluate(() => {
    const msg = document.querySelector('[data-testid="ask-assistant-message"]');
    return msg ? (msg.textContent ?? "") : "";
  });
}

// ---------------------------------------------------------------------------
// Shared test bodies — registered inside each viewport describe so each
// test stays independent (re-logs in and re-opens the harness).
// ---------------------------------------------------------------------------

async function capacityFalseHidesSurfaceMenuTest(page: Page) {
  await loginAndOpenHarness(page);
  await setCapacity(page, false);
  await expect(getCapacity(page)).resolves.toBe(false);

  // No clickable surface dropdown trigger.
  await expect(
    page.locator('button[aria-label="选择 Ask Claread 面板形式"]'),
  ).toHaveCount(0);
  // Static "浮窗" label IS visible.
  await expect(page.locator('span[aria-label="当前以浮窗展示 Ask Claread"]')).toBeVisible();
  // No "侧边栏" menuitem (menu is closed/absent).
  await expect(page.getByRole("menuitem", { name: "侧边栏" })).toHaveCount(0);
}

async function capacityTrueShowsSurfaceMenuTest(page: Page) {
  await loginAndOpenHarness(page);
  await setCapacity(page, true);

  const trigger = page.locator('button[aria-label="选择 Ask Claread 面板形式"]');
  await expect(trigger).toBeVisible();
  await trigger.click();
  const menu = page.getByRole("menuitem", { name: "侧边栏" });
  await expect(menu).toBeVisible();
  // Selecting 侧边栏 calls onChangeSurface; the harness wires it to setSurface.
  await menu.click();
  await expect(page.evaluate(() => window.__spikeAskFloatingOverlay?.getSurface())).resolves.toBe(
    "sidecar",
  );
  // Switch back to 浮窗 to restore the floating surface for any later tests.
  await trigger.click();
  await page.getByRole("menuitem", { name: "浮窗" }).click();
  await expect(page.evaluate(() => window.__spikeAskFloatingOverlay?.getSurface())).resolves.toBe(
    "floating",
  );
}

async function overlayUsesDvhAndStaysInViewportTest(page: Page) {
  await loginAndOpenHarness(page);
  await setCapacity(page, false);

  // Submit a question with a completed stream so the conversation (and
  // `.ask-conversation-scroll`) actually renders. Without an active
  // conversation the panel only shows the composer — the scroll owner
  // element doesn't exist yet, and the geometry check would be testing
  // an empty-state layout rather than the real floating overlay.
  await setScript(page, buildStreamingScript());
  await submitQuestion(page, "请解释制度记忆如何影响政策选择");
  await waitForStreamFinished(page);
  await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
    "Institutional memory shapes policy choices",
    { timeout: 10_000 },
  );

  const geom = await getOverlayGeometry(page);
  expect(geom.exists).toBe(true);
  expect(geom.heightUsesDvh).toBe(true);
  // Overlay must fit inside the visual viewport (no edge overflow).
  expect(geom.rect.top).toBeGreaterThanOrEqual(0);
  expect(geom.rect.left).toBeGreaterThanOrEqual(0);
  expect(geom.rect.right).toBeLessThanOrEqual(geom.viewport.width);
  expect(geom.rect.bottom).toBeLessThanOrEqual(geom.viewport.height);
  expect(geom.conversationScrollExists).toBe(true);
}

async function realDeltaStreamingGrowsTextAndScrollHeightTest(page: Page) {
  await loginAndOpenHarness(page);
  await setCapacity(page, false);

  await setScript(page, buildStreamingScript({ holdAfterFirstDelta: true }));
  await submitQuestion(page, "请解释制度记忆如何影响政策选择");

  // Wait for the first delta to land (it is held). The provisional preview
  // should contain the first chunk. The stream is not yet finished.
  await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
    "Institutional memory shapes policy choices",
    { timeout: 10_000 },
  );
  await expect(
    page.evaluate(
      () => window.__spikeAskFloatingOverlay?.getStreamState().finished,
    ),
  ).resolves.toBe(false);

  const beforeRelease = await getConversationScrollMetrics(page);
  expect(beforeRelease.exists).toBe(true);
  expect(beforeRelease.scrollHeight).toBeGreaterThan(beforeRelease.clientHeight);

  // Scroll the sole conversation owner while the stream is still held.
  // This is the regression gate for the mobile "content cannot scroll"
  // failure: passing after completion is insufficient.
  const scroll = page.locator(".ask-conversation-scroll");
  await scroll.evaluate((element) => {
    element.scrollTop = 0;
  });
  await scroll.hover();
  await page.mouse.wheel(0, 500);
  await expect
    .poll(async () => (await getConversationScrollMetrics(page)).scrollTop)
    .toBeGreaterThan(0);
  await expect(page.locator('[data-ask-composer-textarea="true"]')).toBeVisible();

  // Release the remaining deltas + message.completed.
  await releaseAll(page);
  await waitForStreamFinished(page);

  // Canonical answer replaces provisional preview — no duplication, no loss.
  await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
    "Institutional memory shapes policy choices in subtle ways",
    { timeout: 10_000 },
  );
  await expect(page.locator('[data-testid="ask-assistant-message"]')).toContainText(
    "produce durable policy regimes",
    { timeout: 10_000 },
  );

  const after = await getConversationScrollMetrics(page);
  // The full long answer either fits or overflows — but the assistant message
  // must contain the full canonical text (no segment dropped during the
  // provisional → canonical swap).
  const finalText = await getProvisionalText(page);
  expect(finalText).toContain("Institutional memory");
  expect(finalText).toContain("durable policy regimes");
  // No accidental duplication of the first chunk (a regression where the
  // provisional preview is concatenated with the canonical content_md).
  const firstParagraphOccurrences =
    finalText.split(STREAMING_OVERFLOW_PARAGRAPH).length - 1;
  expect(firstParagraphOccurrences).toBe(32);
  expect(after.scrollHeight).toBeGreaterThanOrEqual(beforeRelease.scrollHeight);
}

async function composerAlwaysVisibleDuringScrollTest(page: Page) {
  await loginAndOpenHarness(page);
  await setCapacity(page, false);

  await setScript(page, buildStreamingScript());
  await submitQuestion(page, "合成器常驻测试");
  await waitForStreamFinished(page);

  // Wait for content to potentially overflow.
  await page.waitForFunction(
    () => {
      const el = document.querySelector(".ask-conversation-scroll");
      return !!el && el.scrollHeight > el.clientHeight;
    },
    undefined,
    { timeout: 10_000 },
  ).catch(() => {
    // If the long answer does not overflow at this viewport, the composer
    // visibility assertion below is still valid — it just verifies the
    // composer is visible at the natural scroll position.
  });

  const textarea = page.locator('[data-ask-composer-textarea="true"]');

  // Top: scroll to top via wheel-up.
  const scroll = page.locator(".ask-conversation-scroll");
  await scroll.hover();
  for (let i = 0; i < 6; i += 1) {
    await page.mouse.wheel(0, -400);
    await page.waitForTimeout(30);
  }
  await expect(textarea).toBeVisible();

  // Bottom: jump-to-latest (or direct scroll down).
  const jump = page.locator('[data-testid="ask-jump-to-latest"]');
  if (await jump.isVisible({ timeout: 1500 }).catch(() => false)) {
    await jump.click();
  } else {
    await scroll.hover();
    for (let i = 0; i < 10; i += 1) {
      await page.mouse.wheel(0, 400);
      await page.waitForTimeout(30);
    }
  }
  await expect(textarea).toBeVisible();

  // Mid-scroll: a few wheel-ups from the bottom.
  for (let i = 0; i < 3; i += 1) {
    await page.mouse.wheel(0, -200);
    await page.waitForTimeout(30);
  }
  await expect(textarea).toBeVisible();
}

async function conversationScrollIsOnlyScrollOwnerTest(page: Page) {
  await loginAndOpenHarness(page);
  await setCapacity(page, false);

  await setScript(page, buildStreamingScript());
  await submitQuestion(page, "唯一 scroll owner 测试");
  await waitForStreamFinished(page);

  // Header (panel heading) and composer textarea must NOT live inside
  // `.ask-conversation-scroll`. They sit outside the conversation scroller.
  const layout = await page.evaluate(() => {
    const panel = document.querySelector(".ai-workspace-panel--layout-overlay.ai-workspace-panel--surface-floating");
    const scroller = panel?.querySelector(".ask-conversation-scroll") ?? null;
    const heading = panel?.querySelector("#ask-claread-panel-heading") ?? null;
    const textarea = document.querySelector('[data-ask-composer-textarea="true"]') ?? null;
    if (!scroller || !heading || !textarea) {
      return { ok: false };
    }
    const scrollerEl = scroller as HTMLElement;
    const headingEl = heading as HTMLElement;
    const textareaEl = textarea as HTMLElement;
    return {
      ok: true,
      headingInsideScroller: scrollerEl.contains(headingEl),
      textareaInsideScroller: scrollerEl.contains(textareaEl),
      scrollerHasOverflow:
        scrollerEl.scrollHeight > scrollerEl.clientHeight,
    };
  });
  expect(layout.ok).toBe(true);
  expect(layout.headingInsideScroller).toBe(false);
  expect(layout.textareaInsideScroller).toBe(false);
}

async function bodyLockHoldsWhileOverlayOpenTest(page: Page) {
  await loginAndOpenHarness(page);
  await setCapacity(page, false);

  // Step 1: close the panel FIRST so we can verify the background is
  // genuinely scrollable before the lock engages.
  await closePanel(page);
  await expect(isOpen(page)).resolves.toBe(false);

  // Background must have real overflow.
  const closedState = await getBodyScrollState(page);
  expect(closedState.docHeight).toBeGreaterThan(closedState.viewportHeight);

  // Background scrolls when the panel is closed.
  await scrollBackgroundDown(page, 800);
  const scrolledState = await getBodyScrollState(page);
  expect(scrolledState.htmlScrollTop + scrolledState.bodyScrollTop).toBeGreaterThan(0);

  // Step 2: open the panel. The body lock effect runs on open.
  await openPanel(page);
  await expect(isOpen(page)).resolves.toBe(true);
  // Wait for the lock effect to commit. The lock sets body.style.overflow.
  await page.waitForFunction(
    () => document.body.style.overflow === "hidden",
    undefined,
    { timeout: 5_000 },
  );

  // Reset scroll to top before asserting the lock holds.
  await page.evaluate(() => {
    document.body.scrollTop = 0;
    document.documentElement.scrollTop = 0;
  });

  // Try to wheel-scroll the background. The lock must hold — body/html
  // scrollTop cannot move away from 0.
  await scrollBackgroundDown(page, 800);
  const lockedState = await getBodyScrollState(page);
  expect(lockedState.bodyOverflow).toBe("hidden");
  expect(lockedState.bodyScrollTop).toBe(0);
  expect(lockedState.htmlScrollTop).toBe(0);

  // Step 3: close the panel. The lock effect cleanup must restore the
  // previous body.style.overflow value (empty string for an unstyled body).
  await closePanel(page);
  await expect(isOpen(page)).resolves.toBe(false);
  await page.waitForFunction(
    () => document.body.style.overflow !== "hidden",
    undefined,
    { timeout: 5_000 },
  );

  // Background must be scrollable again.
  const restoredState = await getBodyScrollState(page);
  expect(restoredState.bodyOverflow).not.toBe("hidden");
  // Re-scroll to confirm the background actually moves.
  await scrollBackgroundDown(page, 800);
  const rescrolledState = await getBodyScrollState(page);
  expect(rescrolledState.htmlScrollTop + rescrolledState.bodyScrollTop).toBeGreaterThan(0);
}

async function terminalErrorInsideTurnTest(page: Page) {
  await loginAndOpenHarness(page);
  await setCapacity(page, false);

  await setScript(page, buildTerminalScript("failed"));
  await submitQuestion(page, "终态失败测试");
  await waitForStreamFinished(page);

  // Turn-scoped notice renders inside the conversation timeline.
  await expect(
    page.locator('.ask-conversation [data-testid="ask-turn-notice"]'),
  ).toBeVisible({ timeout: 5_000 });
  // Panel-notice banner slot is empty.
  await expect(page.locator('[data-testid="ask-panel-notice"]')).toHaveCount(0);
  // Composer stays interactive.
  await expect(page.locator('[data-ask-composer-textarea="true"]')).toBeVisible();
}

// ---------------------------------------------------------------------------
// Viewport suites — 4 viewports per the spec.
// ---------------------------------------------------------------------------

const VIEWPORTS: Array<{ name: string; width: number; height: number }> = [
  { name: "mobile-360x800", width: 360, height: 800 },
  { name: "mobile-390x844", width: 390, height: 844 },
  { name: "mobile-430x932", width: 430, height: 932 },
  { name: "desktop-1440x900", width: 1440, height: 900 },
];

for (const vp of VIEWPORTS) {
  test.describe(`ASK-UX-MOBILE-R3 floating-overlay — ${vp.name}`, () => {
    test.describe.configure({ mode: "serial" });
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test("1. capacity=false 隐藏面板形式菜单,显示静态浮窗标识", async ({ page }) => {
      await capacityFalseHidesSurfaceMenuTest(page);
    });

    test("2. capacity=true 恢复面板形式菜单,侧边栏可选", async ({ page }) => {
      await capacityTrueShowsSurfaceMenuTest(page);
    });

    test("3. overlay 使用 dvh 高度且不超出 visual viewport", async ({ page }) => {
      await overlayUsesDvhAndStaysInViewportTest(page);
    });

    test("4. 真实 message.delta 流式输出,文本与 scrollHeight 增长,completed 不重复不丢段", async ({ page }) => {
      await realDeltaStreamingGrowsTextAndScrollHeightTest(page);
    });

    test("5. 任意滚动位置 Composer 始终可见", async ({ page }) => {
      await composerAlwaysVisibleDuringScrollTest(page);
    });

    test("6. .ask-conversation-scroll 是唯一对话 scroll owner", async ({ page }) => {
      await conversationScrollIsOnlyScrollOwnerTest(page);
    });

    test("7. body overflow 锁定:打开时锁定,关闭后恢复", async ({ page }) => {
      await bodyLockHoldsWhileOverlayOpenTest(page);
    });

    test("8. 终态失败通知在轮次内,不在 composer 上方", async ({ page }) => {
      await terminalErrorInsideTurnTest(page);
    });
  });
}
