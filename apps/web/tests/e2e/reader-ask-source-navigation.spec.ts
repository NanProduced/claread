import { randomUUID } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

import {
  cleanupRealProductSession,
  fixtureEmail,
  installRealProductSession,
} from "./real-product-session";

/**
 * Live product acceptance for the Ask article-citation source navigation
 * chain: citation detail → 定位原文 → secure navigate BFF (record + message
 * + public citation id only) → server-verified typed location → Reader DOM
 * scroll/focus → safe Chinese feedback. Runs against the deterministic Ask
 * runtime; the provider guard must report zero real provider calls.
 */

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

// Each test provisions an isolated email identity + session so cleanup can
// delete the whole account without touching shared fixture data.

type JsonBody = Record<string, unknown>;

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

function parseCompletedPayload(body: string): JsonBody {
  const frames = parseSseFrames(body);
  const completed = frames.filter((frame) => frame.event === "message.completed");
  expect(completed, "canonical message.completed SSE frame").toHaveLength(1);
  const payload = asObject(JSON.parse(completed[0]!.data), "message.completed payload");
  expect(payload.final_status).toBe("ok");
  return payload;
}

async function browserJson(
  page: Page,
  path: string,
  input?: { method?: string; body?: JsonBody },
): Promise<{ status: number; body: unknown }> {
  const serializedBody = input?.body === undefined ? null : JSON.stringify(input.body);
  const result = await page.evaluate(
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
      return { status: response.status, body: parsed };
    },
    {
      requestPath: path,
      method: input?.method ?? "GET",
      body: serializedBody,
    },
  );
  return result;
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

async function loginWithEmailSession(page: Page, email: string) {
  await installRealProductSession(page, email, "/app/read");
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

function attachConsoleWatch(page: Page, collected: string[]): void {
  page.on("pageerror", (error) => {
    collected.push(`pageerror: ${error.message}`);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      collected.push(`console.error: ${message.text()}`);
    }
  });
}

async function installSseCapture(page: Page): Promise<void> {
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
      if (requestUrl.pathname.endsWith("/messages/stream")) {
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
}

/** Send the fixture question and return message/citation ids from the SSE. */
async function sendFixtureQuestion(
  page: Page,
): Promise<{ messageId: string; citationId: string }> {
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
  const sseBody = await readCapturedSseBody(page, new URL(sendResponse.url()).pathname);
  const completed = parseCompletedPayload(sseBody);
  const messageId = asString(completed.message_id, "message_id");
  const citations = completed.citations;
  expect(Array.isArray(citations), "canonical citations").toBe(true);
  const citationList = citations as unknown[];
  expect(citationList.length, "at least one article citation").toBeGreaterThan(0);
  const citationId = asString(
    asObject(citationList[0], "first citation").citation_id,
    "citation_id",
  );
  await expect(page.getByTestId("agentic-answer-blocks")).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByRole("button", { name: /查看来源 .*详情/ }).first(),
  ).toBeVisible();
  return { messageId, citationId };
}

/** Open the citation detail card and click the explicit locate action. */
async function clickLocateSource(page: Page, citationId: string): Promise<void> {
  const trigger = page.getByRole("button", { name: /查看来源 .*详情/ }).first();
  await trigger.hover();
  const card = page.locator('[data-slot="inline-citation-card-body"]');
  await expect(card).toBeVisible();
  const locateButton = page.getByTestId(`locate-citation-${citationId}`);
  await expect(locateButton).toBeVisible();
  await locateButton.click();
}

/**
 * Assert the real browser focus landed on the navigable Reader block that
 * owns the navigation target. Paragraph/heading blocks are not natively
 * focusable, so this proves the programmatic focus contract end to end.
 */
async function expectReaderBlockFocused(page: Page, targetSelector: string): Promise<void> {
  await expect
    .poll(
      async () =>
        page.evaluate((sel) => {
          const active = document.activeElement as HTMLElement | null;
          if (!active || active === document.body) return "no-focus";
          const target = document.querySelector(sel);
          const isBlock = active.matches(
            "[data-reader-record-node][data-unit-id]",
          );
          const ownsTarget =
            target !== null && (active === target || active.contains(target));
          return isBlock && ownsTarget ? "focused" : "wrong-focus";
        }, targetSelector),
      { timeout: 15_000, intervals: [200, 500, 1_000] },
    )
    .toBe("focused");
}

/** Assert the target Reader DOM node is inside the viewport (polled). */
async function expectNodeInViewport(page: Page, selector: string): Promise<void> {
  await expect
    .poll(
      async () =>
        page.evaluate((sel) => {
          const el = document.querySelector(sel);
          if (!el) return "missing";
          const rect = el.getBoundingClientRect();
          const visible =
            rect.bottom > 0 &&
            rect.top < window.innerHeight &&
            rect.height >= 0;
          return visible ? "visible" : "offscreen";
        }, selector),
      { timeout: 15_000, intervals: [200, 500, 1_000] },
    )
    .toBe("visible");
}

async function scrollReaderToBottom(page: Page): Promise<void> {
  await page.evaluate(() => {
    window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" as ScrollBehavior });
    document
      .querySelectorAll("[data-reader-record-scroll-container], .reader-record-plate-document")
      .forEach((el) => {
        el.scrollTop = el.scrollHeight;
      });
  });
}

test.describe("Ask article citation source navigation (live deterministic)", () => {
  let email: string;

  test.beforeEach(() => {
    email = fixtureEmail();
  });

  test.afterEach(async () => {
    const cleanup = await cleanupRealProductSession(email);
    expect(cleanup.residualTotal, "real-product session residual rows").toBe(0);
  });

  test("desktop: citation 定位原文 scrolls to the real anchor, survives reload, fails safely on mismatch", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1280, height: 900 });
    await installSseCapture(page);
    const consoleProblems: string[] = [];
    attachConsoleWatch(page, consoleProblems);

    const guardBefore = await providerGuardReport();
    expect(guardBefore.installed).toBe(true);
    expect(guardBefore.blocked_call_count).toBe(0);
    expect(guardBefore.blocked_attempts).toEqual([]);

    await loginWithEmailSession(page, email);
    const recordId = await createLiveRecord(page);
    await waitForRecordSnapshot(page, recordId);
    await page.goto(`/app/reader/${recordId}`);
    await expect(page.locator(".reader-record-plate-document")).toBeVisible({
      timeout: 30_000,
    });

    await page.getByRole("button", { name: "打开 Ask Claread" }).first().click();
    const { messageId, citationId } = await sendFixtureQuestion(page);

    // Move the Reader away from the cited location so the navigation scroll
    // is observable, then click the explicit 定位原文 action.
    await scrollReaderToBottom(page);

    const navigateResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(
          `/ask/messages/${messageId}/citations/${citationId}/navigate`,
        ),
    );
    await clickLocateSource(page, citationId);
    const navigateResponse = await navigateResponsePromise;
    expect(navigateResponse.status(), "secure citation navigate BFF status").toBe(200);
    const navigatePayload = asObject(
      await navigateResponse.json(),
      "secure citation navigate payload",
    );
    expect(navigatePayload.status).toBe("ok");
    const location = asObject(navigatePayload.location, "typed location");
    const anchorSegmentId =
      typeof location.anchor_segment_id === "string" ? location.anchor_segment_id : null;
    const unitId = typeof location.unit_id === "string" ? location.unit_id : null;
    expect(anchorSegmentId ?? unitId, "typed location has a locator").toBeTruthy();

    // The navigate request must carry no client-supplied fence fields.
    const navigateRequest = navigateResponse.request();
    expect(navigateRequest.postData(), "navigate request has no body").toBeNull();

    const targetSelector = anchorSegmentId
      ? `[data-anchor-segment-id="${anchorSegmentId}"]`
      : `[data-reader-record-node][data-unit-id="${unitId}"]`;
    await expectNodeInViewport(page, targetSelector);
    await expectReaderBlockFocused(page, targetSelector);
    await expect(page.getByTestId("ai-workspace-live-announcement")).toHaveText(
      "已定位到文章中的相关位置",
    );

    // Reload: the same citation must navigate again from cold history.
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
        `[data-testid="ask-assistant-message"][data-message-id="${messageId}"]`,
      ),
    ).toHaveCount(1);
    await scrollReaderToBottom(page);
    const reloadNavigatePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/navigate"),
    );
    // Keyboard activation: focus the locate action and press Enter instead
    // of a pointer click.
    const reloadTrigger = page
      .getByRole("button", { name: /查看来源 .*详情/ })
      .first();
    await reloadTrigger.hover();
    await expect(
      page.locator('[data-slot="inline-citation-card-body"]'),
    ).toBeVisible();
    const reloadLocateButton = page.getByTestId(`locate-citation-${citationId}`);
    await expect(reloadLocateButton).toBeVisible();
    await reloadLocateButton.focus();
    await page.keyboard.press("Enter");
    const reloadNavigate = await reloadNavigatePromise;
    expect(reloadNavigate.status(), "reload navigate BFF status").toBe(200);
    await expectNodeInViewport(page, targetSelector);
    await expectReaderBlockFocused(page, targetSelector);
    await expect(page.getByTestId("ai-workspace-live-announcement")).toHaveText(
      "已定位到文章中的相关位置",
    );

    // Mismatch fixture: an unknown citation id resolves server-side to a
    // fenced failure — no typed location, no navigation, nothing internal.
    // (The panel-side safe feedback for this status is covered by the
    // component tests; the UI only ever submits real citation ids.)
    const mismatch = await browserJson(
      page,
      `/api/web/reader/records/${recordId}/ask/messages/${messageId}/citations/cite-not-exist/navigate`,
      { method: "POST" },
    );
    expect(mismatch.status, "mismatch navigate BFF status").toBe(200);
    const mismatchPayload = asObject(mismatch.body, "mismatch payload");
    expect(mismatchPayload.status).toBe("not_found");
    expect(mismatchPayload.location ?? null).toBeNull();

    const guardAfter = await providerGuardReport();
    expect(guardAfter.installed).toBe(true);
    expect(guardAfter.blocked_call_count).toBe(0);
    expect(guardAfter.blocked_attempts).toEqual([]);
    expect(consoleProblems, "no app-level console errors / page errors").toEqual([]);
  });

  test("mobile 390px: citation 定位原文 works from the floating Ask surface", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 390, height: 844 });
    await installSseCapture(page);
    const consoleProblems: string[] = [];
    attachConsoleWatch(page, consoleProblems);

    const guardBefore = await providerGuardReport();
    expect(guardBefore.installed).toBe(true);
    expect(guardBefore.blocked_call_count).toBe(0);

    await loginWithEmailSession(page, email);
    const recordId = await createLiveRecord(page);
    await waitForRecordSnapshot(page, recordId);
    await page.goto(`/app/reader/${recordId}`);
    await expect(page.locator(".reader-record-plate-document")).toBeVisible({
      timeout: 30_000,
    });

    await page.getByRole("button", { name: "打开 Ask Claread" }).first().click();
    const { citationId } = await sendFixtureQuestion(page);

    await scrollReaderToBottom(page);
    const navigateResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/navigate"),
    );
    await clickLocateSource(page, citationId);
    const navigateResponse = await navigateResponsePromise;
    expect(navigateResponse.status(), "secure citation navigate BFF status").toBe(200);
    const navigatePayload = asObject(await navigateResponse.json(), "typed payload");
    expect(navigatePayload.status).toBe("ok");
    const location = asObject(navigatePayload.location, "typed location");
    const anchorSegmentId =
      typeof location.anchor_segment_id === "string" ? location.anchor_segment_id : null;
    const unitId = typeof location.unit_id === "string" ? location.unit_id : null;
    const targetSelector = anchorSegmentId
      ? `[data-anchor-segment-id="${anchorSegmentId}"]`
      : `[data-reader-record-node][data-unit-id="${unitId}"]`;
    await expectNodeInViewport(page, targetSelector);
    await expectReaderBlockFocused(page, targetSelector);
    await expect(page.getByTestId("ai-workspace-live-announcement")).toHaveText(
      "已定位到文章中的相关位置",
    );

    const guardAfter = await providerGuardReport();
    expect(guardAfter.installed).toBe(true);
    expect(guardAfter.blocked_call_count).toBe(0);
    expect(guardAfter.blocked_attempts).toEqual([]);
    expect(consoleProblems, "no app-level console errors / page errors").toEqual([]);
  });
});
