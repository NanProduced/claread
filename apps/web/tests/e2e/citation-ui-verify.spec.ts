import { expect, test, type Page } from "@playwright/test";
import type { SpikeSseScriptEvent } from "@/app/e2e-plate-spike/ask-activity/types";

const HARNESS_URL = "/e2e-plate-spike/ask-activity";
const THREAD_ID = "test-thread-citation";
const RECORD_ID = "test-record-r2-activity";
const MESSAGE_ID = "msg-citation-1";
const TURN_RUN_ID = "turn-run-citation-1";
const EXECUTION_VERSION = "reader_record_ask_agentic_v2";

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
  };
}

function completedPayload() {
  const citations = Array.from({ length: 3 }, (_, index) => {
    const number = index + 1;
    return {
      citation_id: `c${number}`,
      source_kind: "article" as const,
      snippet: `段落 ${number}：原文依据摘要，说明气候变化影响。`,
    };
  });

  return {
    execution_version: EXECUTION_VERSION,
    final_status: "ok",
    answer_text:
      "文章讨论了气候变化影响。\n\n通用知识可以补充背景，但不绑定原文依据。",
    answer_blocks: [
      {
        text: "文章讨论了气候变化影响。",
        citation_ids: ["c1", "c2", "c3"],
      },
      {
        text: "通用知识可以补充背景，但不绑定原文依据。",
        citation_ids: [],
      },
    ],
    citations,
    knowledge_mode: "mixed",
    source_status: null,
    message_id: MESSAGE_ID,
    thread_id: THREAD_ID,
    turn_run_id: TURN_RUN_ID,
  };
}

function buildScript(): SpikeSseScriptEvent[] {
  return [
    { event: "agentic.run_started", data: runStartedPayload() },
    {
      event: "agentic.progress",
      data: progressPayload(1, "searching_article", "正在检索文章"),
    },
    {
      event: "agentic.progress",
      data: progressPayload(2, "composing_answer", "正在组织回答"),
    },
    { event: "message.completed", data: completedPayload() },
  ];
}

function threadSummary() {
  return {
    id: THREAD_ID,
    record_id: RECORD_ID,
    title: "Citation Test Thread",
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
  await page.evaluate((script) => {
    window.__spikeAskActivity?.setScript(script);
  }, buildScript());
  await page.fill(
    '[data-ask-composer-textarea="true"]',
    "文章讨论了什么？",
  );
  await page.click('button[aria-label="发送"]');
}

test.describe("citation UI v2 public contract", () => {
  test("hover, carousel 1/3→2/3→1/3, snippets, no Sources/evh/jump", async ({
    page,
  }) => {
    await openHarness(page);

    await expect(page.getByTestId("agentic-answer-blocks")).toBeVisible({
      timeout: 15_000,
    });

    // No answer-end article Sources; no fake jump until typed-location adapter.
    await expect(page.getByTestId("agentic-sources")).toHaveCount(0);
    await expect(page.getByText("跳转到原文")).toHaveCount(0);
    await expect(page.getByTestId("agentic-citation-navigate-c1")).toHaveCount(0);
    await expect(page.getByText("已定位到文章中的相关位置")).toHaveCount(0);

    const body = await page.locator("body").innerHTML();
    expect(body).not.toContain("evh_");
    expect(body).not.toContain("handle_id");
    expect(body).not.toContain("envelope_fingerprint");

    const trigger = page.getByRole("button", { name: /查看来源/ }).first();
    await expect(trigger).toBeVisible();
    await trigger.hover();

    // Hover alone opens the preview; click is not required.
    await expect(page.getByText(/段落 1：原文依据摘要/)).toBeVisible({
      timeout: 5_000,
    });

    // Carousel required for 3 citations
    const index = page.getByTestId("inline-citation-carousel-index");
    const next = page.getByTestId("inline-citation-carousel-next");
    const prev = page.getByTestId("inline-citation-carousel-prev");
    await expect(index).toBeVisible();
    await expect(next).toBeVisible();
    await expect(prev).toBeVisible();

    // 1/3
    await expect(index).toHaveText("1/3");
    await expect(page.getByText(/段落 1：原文依据摘要/)).toBeVisible();

    // → 2/3
    await next.click();
    await expect(index).toHaveText("2/3");
    await expect(page.getByText(/段落 2：原文依据摘要/)).toBeVisible();

    // → back to 1/3
    await prev.click();
    await expect(index).toHaveText("1/3");
    await expect(page.getByText(/段落 1：原文依据摘要/)).toBeVisible();

    // General-only block present without forcing citation UI
    await expect(page.getByText("通用知识可以补充背景")).toBeVisible();
  });
});
