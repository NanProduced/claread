/**
 * ASK-UX-HISTORY-COT-R2 P0-1 — Cold-load history user message visibility.
 *
 * Real link: reader_ask_messages → FastAPI thread detail → Next BFF
 * (passthrough) → normalizeReaderAskMessages → Conversation DOM.
 *
 * The backend quarantine fix (repository.py) scopes isolation to
 * assistant rows only; user messages that carry execution_version in
 * metadata_json (retry-snapshot marker) must keep their content_md.
 * This spec guards the full browser path: a cold-loaded thread with a
 * real persisted user message + v2 assistant message must render the
 * user's text visibly, before the assistant answer — not an empty
 * bubble, not just a role/data-testid attribute.
 *
 * Coverage: desktop 1440×900 + mobile 390×844, with a page-refresh
 * (true cold reload) pass.
 */

import { expect, test, type Page } from "@playwright/test";

const HARNESS_URL = "/e2e-plate-spike/ask-activity";

test.beforeEach(() => {
  test.skip(
    true,
    "CUTOVER-WEB-LONG: cold-history coverage is retained in Ask v2 Vitest; this legacy harness suite awaits Physical deletion.",
  );
});
const RECORD_ID = "test-record-cold-load";
const THREAD_ID = "test-thread-cold-load";

const USER_TEXT = "这篇文章的主旨是什么？";
const ASSISTANT_TEXT = "这篇文章讨论了气候变化对全球生态系统的长期影响。";

// ---------------------------------------------------------------------------
// Wire-contract payloads — real persisted shape (post-backend-fix).
// The user message has NO execution_version on the DTO (it lives only in
// metadata_json server-side). The assistant message is agentic v2.
// ---------------------------------------------------------------------------

function userHistoryMessage() {
  return {
    id: "msg-user-cold-1",
    thread_id: THREAD_ID,
    role: "user",
    status: "completed",
    content_md: USER_TEXT,
    submission_mode: "chat",
    resolved_intent: null,
    context_anchors: [],
    citations: [],
    action_proposals: [],
    tool_trace: [],
    evidence: [],
    trace_summary: null,
    disambiguation: null,
    external_asset_disambiguation: null,
    response_cards: [],
    resolved_context: null,
    context_plan: null,
    resolved_context_input: null,
    run_info: null,
    supplement_candidates: [],
    persisted_supplements: [],
    reasoning_md: null,
    reasoning_status: null,
    follow_up_suggestions: null,
    article_rag: null,
    usage_event_id: null,
    current_turn_run_id: null,
    current_turn_run: null,
    created_at: "2026-07-14T00:00:00+00:00",
    updated_at: "2026-07-14T00:00:01+00:00",
  };
}

function v2AssistantHistoryMessage() {
  return {
    id: "msg-assistant-cold-1",
    thread_id: THREAD_ID,
    role: "assistant",
    status: "completed",
    content_md: ASSISTANT_TEXT,
    submission_mode: "chat",
    resolved_intent: "explain",
    context_anchors: [],
    citations: [],
    action_proposals: [],
    tool_trace: [],
    evidence: [],
    trace_summary: null,
    disambiguation: null,
    external_asset_disambiguation: null,
    response_cards: [],
    resolved_context: null,
    context_plan: null,
    resolved_context_input: null,
    run_info: null,
    supplement_candidates: [],
    persisted_supplements: [],
    reasoning_md: null,
    reasoning_status: null,
    follow_up_suggestions: null,
    article_rag: null,
    usage_event_id: null,
    current_turn_run_id: "turn-run-cold-1",
    current_turn_run: {
      id: "turn-run-cold-1",
      message_id: "msg-assistant-cold-1",
      thread_id: THREAD_ID,
      execution_version: "reader_record_ask_agentic_v2",
      final_status: "ok",
      status: "completed",
      terminal_reason: null,
      started_at: "2026-07-14T00:00:01+00:00",
      completed_at: "2026-07-14T00:00:05+00:00",
    },
    execution_version: "reader_record_ask_agentic_v2",
    final_status: "ok",
    agentic_answer_blocks: [
      { text: ASSISTANT_TEXT, citation_ids: [] },
    ],
    agentic_citations: [],
    agentic_web_search: null,
    created_at: "2026-07-14T00:00:01+00:00",
    updated_at: "2026-07-14T00:00:05+00:00",
  };
}

function threadDetailWithHistory() {
  return {
    id: THREAD_ID,
    record_id: RECORD_ID,
    title: "Cold Load Test",
    is_default: true,
    selected_model: {
      key: "deepseek-v4-flash",
      label: "DeepSeek V4 Flash",
      price_multiplier: 1.0,
    },
    created_at: "2026-07-14T00:00:00+00:00",
    updated_at: "2026-07-14T00:00:05+00:00",
    last_message_at: "2026-07-14T00:00:05+00:00",
    messages: [userHistoryMessage(), v2AssistantHistoryMessage()],
  };
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
        body: JSON.stringify({
          id: THREAD_ID,
          record_id: RECORD_ID,
          title: "Cold Load Test",
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

  // Thread detail — return the cold-load history (user + v2 assistant).
  await page.route(`**/api/web/reader-ask/threads/${THREAD_ID}*`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(threadDetailWithHistory()),
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
  await mockApiRoutes(page);
  await page.goto("/login");
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await page.goto(HARNESS_URL);
  // Wait for the panel to mount and load thread detail.
  await page.waitForSelector('[data-ask-composer-textarea="true"]');
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("ASK-UX-HISTORY-COT-R2 P0-1 — cold-load user message visibility", () => {
  test.describe.configure({ mode: "serial" });

  test("desktop 1440×900 — user text visible before assistant answer on cold load", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAndOpenHarness(page);

    // The user message text must be visible in the DOM — not an empty
    // bubble, not just a role/data-testid attribute.
    await expect(page.getByText(USER_TEXT)).toBeVisible();

    // The assistant answer must also be visible.
    await expect(page.getByText(ASSISTANT_TEXT)).toBeVisible();

    // The user message must appear before the assistant answer in DOM
    // order (source order = conversation order).
    const userLocator = page.locator(`[data-message-role="user"]`).filter({
      hasText: USER_TEXT,
    });
    const assistantLocator = page
      .locator(`[data-message-role="assistant"]`)
      .filter({ hasText: ASSISTANT_TEXT });

    await expect(userLocator).toHaveCount(1);
    await expect(assistantLocator).toHaveCount(1);

    // Assert DOM order: user before assistant.
    const userBox = await userLocator.boundingBox();
    const assistantBox = await assistantLocator.boundingBox();
    expect(userBox).not.toBeNull();
    expect(assistantBox).not.toBeNull();
    expect(userBox!.y).toBeLessThan(assistantBox!.y);

    // No empty user bubble — the user text is genuinely rendered, not
    // hidden via CSS or masked by an empty container.
    const userTextContent = await userLocator.textContent();
    expect(userTextContent).toContain(USER_TEXT);
  });

  test("mobile 390×844 — user text visible before assistant answer on cold load", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAndOpenHarness(page);

    await expect(page.getByText(USER_TEXT)).toBeVisible();
    await expect(page.getByText(ASSISTANT_TEXT)).toBeVisible();

    const userLocator = page.locator(`[data-message-role="user"]`).filter({
      hasText: USER_TEXT,
    });
    const assistantLocator = page
      .locator(`[data-message-role="assistant"]`)
      .filter({ hasText: ASSISTANT_TEXT });

    await expect(userLocator).toHaveCount(1);
    await expect(assistantLocator).toHaveCount(1);

    const userBox = await userLocator.boundingBox();
    const assistantBox = await assistantLocator.boundingBox();
    expect(userBox).not.toBeNull();
    expect(assistantBox).not.toBeNull();
    expect(userBox!.y).toBeLessThan(assistantBox!.y);
  });

  test("desktop 1440×900 — user text survives a page refresh (true cold reload)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAndOpenHarness(page);

    await expect(page.getByText(USER_TEXT)).toBeVisible();

    // True cold reload — re-fetch the thread detail from the mocked API.
    await page.reload();

    // Wait for the panel to re-mount and re-load history.
    await page.waitForSelector('[data-ask-composer-textarea="true"]');

    // The user text must still be visible after reload.
    await expect(page.getByText(USER_TEXT)).toBeVisible();
    await expect(page.getByText(ASSISTANT_TEXT)).toBeVisible();

    // DOM leak scan: no raw internal IDs, no evh_ handles, no query/URL
    // provider payloads leaked into the visible DOM.
    const bodyText = await page.locator("body").textContent();
    expect(bodyText).not.toContain("evh_");
    expect(bodyText).not.toContain("turn-run-cold-1");
  });
});
