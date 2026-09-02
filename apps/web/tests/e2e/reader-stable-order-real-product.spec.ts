import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { promisify } from "node:util";

import { expect, test, type Page } from "@playwright/test";

import {
  apiPythonPath,
  cleanupRealProductSession,
  fixtureEmail,
  provisionRealProductSession,
  repoRoot,
  type ProvisionedSession,
} from "./real-product-session";

const execFileAsync = promisify(execFile);

type JsonBody = Record<string, unknown>;
type BrowserJsonResult = { status: number; body: unknown; text: string };

const FIXTURE_ARTICLE_TEXT = [
  "Calm systems make the first decision visible. A second sentence fixes the opening context.",
  "Reliable readers preserve source ownership. Another sentence completes the second unit.",
  "The third unit begins with a stable premise. Its second sentence receives a deterministic analysis. The third sentence receives another deterministic analysis. The final sentence forms its own translation group.",
  "A fourth unit proves that later source content remains after every u3 overlay.",
].join("\n\n");

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

async function installSessionCookie(page: Page, session: ProvisionedSession) {
  await page.goto("/");
  await page.context().addCookies([
    {
      name: "claread_web_session",
      value: session.sessionToken,
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
      secure: false,
    },
  ]);
  await page.goto("/app/read");
  await page.waitForURL((url) => url.pathname === "/app/read");
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
  expect(response.status, `real unified input: ${response.text}`).toBe(200);
  const payload = asObject(response.body, "unified input payload");
  expect(payload.ok).toBe(true);
  expect(payload.outcome).toBe("stable_document_ready");
  return asString(payload.reading_record_id, "reading_record_id");
}

async function runFixtureHelper(args: string[]): Promise<JsonBody> {
  const root = repoRoot();
  const apiRoot = resolve(root, "services", "api");
  const helper = resolve(
    apiRoot,
    "tests",
    "reader_stable_order_real_product_fixture.py",
  );
  const python = apiPythonPath();
  const { stdout, stderr } = await execFileAsync(python, [helper, ...args], {
    cwd: apiRoot,
    env: { ...process.env },
    timeout: 180_000,
    maxBuffer: 4 * 1024 * 1024,
  });
  const output = stdout.trim();
  if (!output) {
    throw new Error(`stable-order fixture helper returned no output: ${stderr}`);
  }
  return asObject(JSON.parse(output), "stable-order fixture helper result");
}

async function providerGuardReport(): Promise<JsonBody> {
  const base = (process.env.CLAREAD_FASTAPI_BASE_URL ?? "").replace(/\/$/, "");
  expect(base, "deterministic FastAPI base must be explicit").toBe(
    "http://127.0.0.1:8010",
  );
  const response = await fetch(`${base}/__deterministic_guard__/provider-calls`);
  expect(response.status, "deterministic provider guard status").toBe(200);
  return asObject(await response.json(), "deterministic provider guard report");
}

/**
 * Ordered signature of outermost navigable blocks
 * (`node-kind:unit-id`, translation lanes marked).
 */
async function plateBlockSignature(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const root = document.querySelector(".reader-record-plate-document");
    if (!root) return [];
    const sequence: string[] = [];
    root
      .querySelectorAll("[data-reader-record-node][data-unit-id]")
      .forEach((el) => {
        if (el.parentElement?.closest("[data-reader-record-node][data-unit-id]")) {
          return;
        }
        const kind = el.getAttribute("data-reader-record-node");
        const unitId = el.getAttribute("data-unit-id");
        const lane = el.hasAttribute("data-reader-record-translation-lane");
        sequence.push(`${kind}:${unitId}${lane ? "(lane)" : ""}`);
      });
    return sequence;
  });
}

async function expectStableOrder(page: Page): Promise<string[]> {
  const signature = await plateBlockSignature(page);
  const u3First = signature.indexOf("paragraph:u3");
  expect(u3First, "first u3 source span present").toBeGreaterThanOrEqual(0);
  expect(signature[u3First + 1], "g(s5-s7) translation follows its span").toBe(
    "blockquote:u3(lane)",
  );

  const u3Second = signature.indexOf("paragraph:u3", u3First + 1);
  expect(u3Second, "second u3 source span present").toBeGreaterThan(u3First);
  const between = signature.slice(u3First + 2, u3Second);
  expect(between.length, "per-sentence analyses stay between the u3 spans").toBe(
    2,
  );
  for (const entry of between) {
    expect(entry.startsWith("paragraph:"), "no source span hoisted forward").toBe(
      false,
    );
  }
  expect(signature[u3Second + 1], "g(s8) translation follows the second span").toBe(
    "blockquote:u3(lane)",
  );
  expect(signature.indexOf("paragraph:u4"), "u4 follows the second u3 translation").toBe(
    u3Second + 2,
  );

  const u3SourceNodes = page.locator(
    '.reader-record-plate-document [data-reader-record-node="paragraph"][data-unit-id="u3"]',
  );
  await expect(u3SourceNodes).toHaveCount(2);
  await expect(u3SourceNodes.first()).toHaveAttribute(
    "data-reader-record-unit-start",
    "true",
  );
  return signature;
}

test.describe("Stable Document display order (self-provisioned real product record)", () => {
  test("multi-translation-group unit renders source spans interleaved with their translations", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    expect(process.env.CLAREAD_FASTAPI_BASE_URL).toBe("http://127.0.0.1:8010");
    await page.setViewportSize({ width: 1280, height: 900 });
    const email = fixtureEmail();
    let recordId: string | null = null;
    let ownsEmailSession = false;
    let testFailure: unknown = null;
    const consoleProblems: string[] = [];
    const externalRequests: string[] = [];
    page.on("pageerror", (error) => {
      consoleProblems.push(`pageerror: ${error.message}`);
    });
    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleProblems.push(`console.error: ${message.text()}`);
      }
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.protocol.startsWith("http") && !["127.0.0.1", "localhost"].includes(url.hostname)) {
        externalRequests.push(request.url());
      }
    });

    try {
      const provision = await provisionRealProductSession(email);
      console.log("[stable-order-session-provision] complete");
      expect(provision.sessionToken).toMatch(/^[A-Za-z0-9_-]{32,}$/);
      ownsEmailSession = true;

      await installSessionCookie(page, provision);
      recordId = await createLiveRecord(page);
      const fixture = await runFixtureHelper(["build", recordId]);
      console.log(`[stable-order-fixture-build] ${JSON.stringify(fixture)}`);
      expect(fixture.executor_mode).toBe("fake");
      const contract = asObject(fixture.contract, "fixture contract");
      expect(contract.u3_anchor_segment_ids).toEqual(["s5", "s6", "s7", "s8"]);
      expect(contract.sentence_analysis_anchor_segment_ids).toEqual(["s6", "s7"]);
      expect(contract.snapshot_reload_equal).toBe(true);

      const snapshotResponse = await browserJson(
        page,
        `/api/web/reader/records/${recordId}/snapshot`,
      );
      expect(snapshotResponse.status, `real snapshot BFF: ${snapshotResponse.text}`).toBe(
        200,
      );
      expect(asObject(snapshotResponse.body, "snapshot BFF payload").ok).toBe(true);

      await page.goto(`/app/reader/${recordId}`);
      await expect(page.locator(".reader-record-plate-document")).toBeVisible({
        timeout: 30_000,
      });
      await expect(
        page.locator("[data-reader-record-translation-lane]").first(),
      ).toBeVisible({ timeout: 30_000 });
      const beforeReload = await expectStableOrder(page);

      await page.reload();
      await expect(page.locator(".reader-record-plate-document")).toBeVisible({
        timeout: 30_000,
      });
      await expect(
        page.locator("[data-reader-record-translation-lane]").first(),
      ).toBeVisible({ timeout: 30_000 });
      expect(await expectStableOrder(page)).toEqual(beforeReload);

      expect(consoleProblems, "no app-level console errors / page errors").toEqual([]);
      expect(externalRequests, "no browser external requests").toEqual([]);
    } catch (error) {
      testFailure = error;
    } finally {
      let guardFailure: unknown = null;
      let pageCloseFailure: unknown = null;
      let cleanupFailure: unknown = null;

      try {
        const guard = await providerGuardReport();
        console.log(`[stable-order-provider-guard] ${JSON.stringify(guard)}`);
        expect(guard.installed).toBe(true);
        expect(guard.blocked_call_count).toBe(0);
        expect(guard.blocked_attempts).toEqual([]);
      } catch (error) {
        guardFailure = error;
      }

      try {
        if (!page.isClosed()) {
          await page.close();
        }
      } catch (error) {
        pageCloseFailure = error;
      }

      if (ownsEmailSession) {
        try {
          const cleanup = await cleanupRealProductSession(
            email,
            recordId ?? undefined,
          );
          console.log(`[stable-order-session-cleanup] ${JSON.stringify(cleanup)}`);
          expect(cleanup.residualTotal, "fixture residual rows").toBe(0);
        } catch (error) {
          cleanupFailure = error;
        }
      }

      const failures = [
        ["cleanup", cleanupFailure],
        ["test body", testFailure],
        ["provider guard", guardFailure],
        ["page close", pageCloseFailure],
      ].filter((failure): failure is [string, unknown] => failure[1] !== null);
      if (failures.length > 0) {
        const details = failures.map(([stage, error]) => {
          const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
          return `${stage}: ${message}`;
        });
        throw new AggregateError(
          failures.map(([, error]) => error),
          `stable-order test failures after cleanup:\n${details.join("\n")}`,
        );
      }
    }
  });
});
