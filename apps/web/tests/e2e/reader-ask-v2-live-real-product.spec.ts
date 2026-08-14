import { randomUUID } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

const FIXTURE_ARTICLE_TEXT =
  "Riverside Library was founded in 1998 by Maria Chen. " +
  "It began as a single reading room above a tea shop on Harbour Street. " +
  "Chen stocked the first shelves with donated novels and travel guides, " +
  "and she hand-painted the opening hours on the window. " +
  "By 2005 the library had expanded to three floors after a successful " +
  "community fundraiser. " +
  "Volunteers ran weekend English reading clubs for children and new " +
  "immigrants. " +
  "Today the library lends more than forty thousand books each year and " +
  "hosts a small local history archive. " +
  "The building overlooks the river promenade, and its reading room " +
  "still keeps the original wooden desks from the tea shop era.";

const FIXTURE_QUESTION =
  "Who founded Riverside Library and where did it begin? " +
  "谁创立了 Riverside Library，它最初在哪里？";

const DETERMINISTIC_MARKER = "deterministic-e2e-r0";
const EXECUTION_V2 = "reader_record_ask_agentic_v2";

type JsonBody = Record<string, unknown>;

type BrowserJsonResult = {
  status: number;
  body: unknown;
  text: string;
};

type SseFrame = {
  event: string | null;
  data: string;
};

type CapturedSseBody = {
  path: string;
  status: number;
  body: string;
};

function asObject(value: unknown, label: string): JsonBody {
  expect(value, label).toBeTruthy();
  expect(typeof value, label).toBe("object");
  return value as JsonBody;
}

function asString(value: unknown, label: string): string {
  expect(typeof value, label).toBe("string");
  expect(String(value), label).not.toHaveLength(0);
  return String(value);
}

function parseSseFrames(body: string): SseFrame[] {
  const frames: SseFrame[] = [];
  let event: string | null = null;
  let dataLines: string[] = [];

  for (const line of body.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    } else if (line.trim() === "") {
      if (event !== null || dataLines.length > 0) {
        frames.push({ event, data: dataLines.join("\n") });
      }
      event = null;
      dataLines = [];
    }
  }

  if (event !== null || dataLines.length > 0) {
    frames.push({ event, data: dataLines.join("\n") });
  }

  return frames;
}

function parseCompletedSse(body: string): JsonBody {
  const frames = parseSseFrames(body);
  const runStarted = frames.filter((frame) => frame.event === "agentic.run_started");
  expect(runStarted, "canonical agentic.run_started SSE frame").toHaveLength(1);
  const runStartedPayload = asObject(
    JSON.parse(runStarted[0]!.data),
    "agentic.run_started payload",
  );
  expect(runStartedPayload.execution_version).toBe(EXECUTION_V2);
  asString(runStartedPayload.message_id, "agentic.run_started message_id");
  asString(runStartedPayload.thread_id, "agentic.run_started thread_id");
  asString(runStartedPayload.turn_run_id, "agentic.run_started turn_run_id");
  const completed = frames.filter((frame) => frame.event === "message.completed");
  expect(completed, "canonical message.completed SSE frame").toHaveLength(1);
  expect(body).not.toContain("evh_");
  expect(body).not.toContain("envelope_fingerprint");
  expect(body).not.toContain("source_fingerprint");
  return asObject(JSON.parse(completed[0]!.data), "message.completed payload");
}

async function browserJson(
  page: Page,
  path: string,
  input?: { method?: string; body?: JsonBody },
): Promise<BrowserJsonResult> {
  const serializedBody = input?.body === undefined ? null : JSON.stringify(input.body);
  return page.evaluate(
    async ({ requestPath, method, body }) => {
      const response = await fetch(requestPath, {
        method,
        headers: body === null ? undefined : { "content-type": "application/json" },
        body: body ?? undefined,
        credentials: "same-origin",
      });
      const text = await response.text();
      let parsed: unknown = null;
      try {
        parsed = text ? JSON.parse(text) : null;
      } catch {
        parsed = null;
      }
      return { status: response.status, body: parsed, text };
    },
    {
      requestPath: path,
      method: input?.method ?? "GET",
      body: serializedBody,
    },
  );
}

async function readCapturedSseBody(page: Page, path: string): Promise<string> {
  await expect
    .poll(
      async () =>
        page.evaluate((expectedPath) => {
          const scope = window as unknown as {
            __clareadAskSseBodies?: CapturedSseBody[];
          };
          return (scope.__clareadAskSseBodies ?? []).filter(
            (entry) => entry.path === expectedPath && entry.body.length > 0,
          ).length;
        }, path),
      {
        timeout: 60_000,
        intervals: [100, 250, 500, 1_000],
        message: `browser SSE clone did not complete for ${path}`,
      },
    )
    .toBeGreaterThan(0);

  return page.evaluate((expectedPath) => {
    const scope = window as unknown as {
      __clareadAskSseBodies?: CapturedSseBody[];
    };
    const entry = [...(scope.__clareadAskSseBodies ?? [])]
      .reverse()
      .find((candidate) => candidate.path === expectedPath && candidate.body.length > 0);
    return entry?.body ?? "";
  }, path);
}

async function loginWithPhoneAuth(page: Page) {
  await page.goto("/login?next=%2Fapp%2Fread");
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL((url) => url.pathname === "/app/read");
}

async function createLiveRecord(page: Page): Promise<string> {
  const response = await browserJson(page, "/api/web/reader/records/input", {
    method: "POST",
    body: {
      text: FIXTURE_ARTICLE_TEXT,
      sourceType: "pasted_text",
      filename: null,
      language: "en",
      clientRecordId: randomUUID(),
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
    },
  });
  expect(response.status, "real unified input status").toBe(200);
  const payload = asObject(response.body, "unified input payload");
  expect(payload.ok).toBe(true);
  expect(payload.outcome).toBe("stable_document_ready");
  return asString(payload.reading_record_id, "reading_record_id");
}

async function waitForRecordSnapshot(page: Page, recordId: string): Promise<void> {
  await expect
    .poll(
      async () =>
        (await browserJson(page, `/api/web/reader/records/${recordId}/snapshot`)).status,
      {
        timeout: 30_000,
        intervals: [250, 500, 1_000, 2_000],
        message: "unified input must materialize the canonical Reader snapshot",
      },
    )
    .toBe(200);
}

async function waitForAskHydrated(page: Page): Promise<void> {
  await expect(page.getByRole("button", { name: "重新开始" })).toBeEnabled({
    timeout: 30_000,
  });
}

async function providerGuardReport(): Promise<JsonBody> {
  const base = (process.env.CLAREAD_FASTAPI_BASE_URL ?? "http://127.0.0.1:8010").replace(
    /\/$/,
    "",
  );
  const response = await fetch(`${base}/__deterministic_guard__/provider-calls`);
  expect(response.status, "deterministic provider guard status").toBe(200);
  return asObject(await response.json(), "deterministic provider guard report");
}

function messageList(history: JsonBody): JsonBody[] {
  expect(Array.isArray(history.messages), "cold history messages").toBe(true);
  return history.messages as JsonBody[];
}

function assistantForMessage(history: JsonBody, messageId: string): JsonBody {
  const matches = messageList(history).filter(
    (message) => message.role === "assistant" && message.id === messageId,
  );
  expect(matches, `one persisted assistant message ${messageId}`).toHaveLength(1);
  return matches[0]!;
}

function citationIds(message: JsonBody): string[] {
  expect(Array.isArray(message.agentic_citations), "persisted canonical citations").toBe(true);
  return (message.agentic_citations as JsonBody[]).map((citation) =>
    asString(citation.citation_id, "citation_id"),
  );
}

test.describe("CUTOVER-WEB-LACCEPT Ask v2 live Reader path", () => {
  test("real composer sends, retries, cites, navigates securely, and cold-loads identically", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => {
      const scope = window as unknown as {
        __clareadAskSseBodies?: CapturedSseBody[];
      };
      scope.__clareadAskSseBodies = [];
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const response = await originalFetch(input, init);
        const requestUrl = new URL(
          input instanceof Request ? input.url : String(input),
          window.location.href,
        );
        if (
          requestUrl.pathname.endsWith("/messages/stream") ||
          requestUrl.pathname.endsWith("/retry")
        ) {
          void response
            .clone()
            .text()
            .then((body) => {
              scope.__clareadAskSseBodies?.push({
                path: requestUrl.pathname,
                status: response.status,
                body,
              });
            })
            .catch(() => undefined);
        }
        return response;
      };
    });
    page.on("console", (message) => {
      if (message.text().includes("[AskTurnLifecycle]")) {
        console.log(`[browser:${message.type()}] ${message.text()}`);
      }
    });
    page.on("pageerror", (error) => {
      console.log(`[browser:pageerror] ${error.message}`);
    });

    const guardBefore = await providerGuardReport();
    expect(guardBefore.installed).toBe(true);
    expect(guardBefore.blocked_call_count).toBe(0);
    expect(guardBefore.blocked_attempts).toEqual([]);

    const askRequests: Array<{ path: string; body: JsonBody | null }> = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (
        request.method() === "POST" &&
        (url.pathname.endsWith("/messages/stream") || url.pathname.endsWith("/retry"))
      ) {
        const rawBody = request.postData();
        let body: JsonBody | null = null;
        if (rawBody) {
          try {
            body = JSON.parse(rawBody) as JsonBody;
          } catch {
            body = null;
          }
        }
        askRequests.push({ path: url.pathname, body });
      }
    });

    await loginWithPhoneAuth(page);
    const recordId = await createLiveRecord(page);
    await waitForRecordSnapshot(page, recordId);
    await page.goto(`/app/reader/${recordId}`);
    await expect(page.locator(".reader-record-plate-document")).toBeVisible({
      timeout: 30_000,
    });

    await page.getByRole("button", { name: "打开 Ask Claread" }).first().click();
    const composer = page.locator('[data-ask-composer-textarea="true"]');
    await expect(composer).toBeVisible({ timeout: 30_000 });
    await waitForAskHydrated(page);

    await composer.fill(FIXTURE_QUESTION);
    const sendResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/messages/stream"),
    );
    await page.getByRole("button", { name: "发送" }).click();
    const sendResponse = await sendResponsePromise;
    expect(sendResponse.status(), "BFF send response").toBe(200);
    const sendSseBody = await readCapturedSseBody(
      page,
      new URL(sendResponse.url()).pathname,
    );
    await test.info().attach("ask-live-send.sse", {
      body: Buffer.from(sendSseBody, "utf8"),
      contentType: "text/event-stream",
    });
    const completed = parseCompletedSse(sendSseBody);
    const sendFrames = parseSseFrames(sendSseBody);
    console.log(
      `[ask-live] send frames=${sendFrames.map((frame) => frame.event).join(",")}`,
    );
    console.log(
      `[ask-live] send run_started=${sendFrames.find((frame) => frame.event === "agentic.run_started")?.data ?? "missing"}`,
    );
    await expect(page.getByTestId("agentic-answer-blocks")).toBeVisible({ timeout: 30_000 });

    expect(completed.execution_version).toBe(EXECUTION_V2);
    expect(completed.final_status).toBe("ok");
    expect(String(completed.answer_text)).toContain(DETERMINISTIC_MARKER);
    expect(String(completed.answer_text)).toContain("Riverside Library");
    expect(String(completed.answer_text)).toContain("社区图书馆通常依靠志愿者");
    const messageId = asString(completed.message_id, "hot assistant message_id");
    const threadId = asString(completed.thread_id, "hot thread_id");
    expect(asString(completed.turn_run_id, "hot turn_run_id")).not.toHaveLength(0);
    expect(Array.isArray(completed.citations), "hot canonical citations").toBe(true);
    const hotCitations = completed.citations as JsonBody[];
    expect(hotCitations.length).toBeGreaterThan(0);
    const hotCitation = hotCitations[0]!;
    const citationId = asString(hotCitation.citation_id, "hot citation_id");
    expect(hotCitation.source_kind).toBe("article");
    expect(asString(hotCitation.snippet, "hot citation snippet")).toContain("Riverside Library");

    await expect(page.getByText("Riverside Library", { exact: false }).last()).toBeVisible();

    const initialRequest = askRequests.find((request) => request.path.endsWith("/messages/stream"));
    expect(initialRequest?.body, "real composer request body").toEqual(
      expect.objectContaining({
        content: FIXTURE_QUESTION,
        client_submission_id: expect.any(String),
        attachments: [],
      }),
    );
    const pageIdentity = asObject(initialRequest?.body?.page_identity, "composer page_identity");
    expect(pageIdentity.record_id).toBe(recordId);

    const citationTrigger = page.getByRole("button", { name: /查看来源 .*详情/ }).first();
    await expect(citationTrigger).toBeVisible();
    await citationTrigger.hover();
    const citationCard = page.locator('[data-slot="inline-citation-card-body"]');
    await expect(citationCard).toBeVisible();
    await expect(citationCard.locator('[data-slot="inline-citation-source"]')).toContainText(
      "文章依据",
    );
    if (typeof hotCitation.title === "string" && hotCitation.title.length > 0) {
      await expect(citationCard.locator('[data-slot="inline-citation-source"]')).toContainText(
        hotCitation.title,
      );
    }
    await expect(citationCard.locator('[data-slot="inline-citation-quote"]')).toContainText(
      asString(hotCitation.snippet, "hot citation snippet"),
    );
    await page.mouse.move(8, 8);
    await expect(citationCard).toBeHidden({ timeout: 5_000 });

    const secureNavigate = await browserJson(
      page,
      `/api/web/reader/records/${recordId}/ask/messages/${messageId}/citations/${citationId}/navigate`,
      { method: "POST" },
    );
    expect(secureNavigate.status, "secure citation navigate BFF status").toBe(200);
    const navigatePayload = asObject(secureNavigate.body, "secure citation navigate payload");
    expect(navigatePayload.status).toBe("ok");
    const location = asObject(navigatePayload.location, "secure citation typed location");
    expect(location.anchor_segment_id ?? location.unit_id).toBeTruthy();

    const retryButton = page
      .locator(
        `[data-testid="ask-assistant-message"][data-message-role="assistant"][data-message-id="${messageId}"]`,
      )
      .getByRole("button", { name: "重新生成" });
    await expect(retryButton).toBeVisible({ timeout: 30_000 });
    const retryResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(`/messages/${messageId}/retry`),
    );
    await retryButton.click();
    const retryResponse = await retryResponsePromise;
    expect(retryResponse.status(), "BFF retry response").toBe(200);
    const retrySseBody = await readCapturedSseBody(
      page,
      new URL(retryResponse.url()).pathname,
    );
    const retried = parseCompletedSse(retrySseBody);
    expect(retried.execution_version).toBe(EXECUTION_V2);
    expect(retried.final_status).toBe("ok");
    expect(retried.message_id).toBe(messageId);
    expect(retried.thread_id).toBe(threadId);

    const assistantMessages = page.locator(
      '[data-testid="ask-assistant-message"][data-message-role="assistant"]',
    );
    await expect(
      assistantMessages,
      "retry must not render a duplicate assistant",
    ).toHaveCount(1);
    await expect(
      page.locator(
        `[data-testid="ask-assistant-message"][data-message-role="assistant"][data-message-id="${messageId}"]`,
      ),
    ).toHaveCount(1);
    await expect(page.getByTestId("agentic-answer-blocks")).toBeVisible({ timeout: 30_000 });

    const historyPath = `/api/web/reader/records/${recordId}/ask/threads/${threadId}`;
    const coldBeforeReload = await browserJson(page, historyPath);
    expect(coldBeforeReload.status, "cold history after retry").toBe(200);
    const coldAssistant = assistantForMessage(
      asObject(coldBeforeReload.body, "cold history after retry payload"),
      messageId,
    );
    expect(coldAssistant.execution_version).toBe(EXECUTION_V2);
    expect(citationIds(coldAssistant)).toEqual(
      (retried.citations as JsonBody[]).map((citation) =>
        asString(citation.citation_id, "retried citation_id"),
      ),
    );
    expect(coldAssistant.agentic_answer_blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ text: expect.stringContaining(DETERMINISTIC_MARKER) }),
      ]),
    );

    await page.reload();
    await expect(page.locator(".reader-record-plate-document")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "打开 Ask Claread" }).first().click();
    await expect(page.locator('[data-ask-composer-textarea="true"]')).toBeVisible({
      timeout: 30_000,
    });
    await waitForAskHydrated(page);
    await expect(
      page.locator(
        `[data-testid="ask-assistant-message"][data-message-role="assistant"][data-message-id="${messageId}"]`,
      ),
      "cold history must retain the same assistant identity",
    ).toHaveCount(1);
    await expect(page.getByTestId("agentic-answer-blocks")).toContainText(DETERMINISTIC_MARKER);

    const reloadCitationTrigger = page
      .getByRole("button", { name: /查看来源 .*详情/ })
      .first();
    await reloadCitationTrigger.hover();
    await expect(page.locator('[data-slot="inline-citation-card-body"]')).toBeVisible();

    const coldAfterReload = await browserJson(page, historyPath);
    expect(coldAfterReload.status, "cold history after reload").toBe(200);
    const reloadedAssistant = assistantForMessage(
      asObject(coldAfterReload.body, "cold history after reload payload"),
      messageId,
    );
    expect(reloadedAssistant.execution_version).toBe(EXECUTION_V2);
    expect(citationIds(reloadedAssistant)).toEqual(citationIds(coldAssistant));
    expect(reloadedAssistant.agentic_answer_blocks).toEqual(coldAssistant.agentic_answer_blocks);

    for (const oldAskPath of [
      "/api/web/reader-ask/model-options",
      "/api/web/reader-ask/threads",
      "/api/web/reader-ask/threads/default",
    ]) {
      const oldResponse = await browserJson(page, oldAskPath);
      expect(oldResponse.status, oldAskPath).toBe(404);
    }

    const guardAfter = await providerGuardReport();
    expect(guardAfter.installed).toBe(true);
    expect(guardAfter.blocked_call_count).toBe(0);
    expect(guardAfter.blocked_attempts).toEqual([]);
  });
});
