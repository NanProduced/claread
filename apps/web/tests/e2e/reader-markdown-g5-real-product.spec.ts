import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

const execFileAsync = promisify(execFile);
const MARKDOWN_FENCE = String.fromCharCode(96).repeat(3);

const MARKDOWN_SOURCE = `## The Reading Desk

Reading systems become useful when structure survives the journey from source to screen. This paragraph carries **strong** and *emphasis* so the rich HTML path has visible inline marks.

> A short quotation should remain visibly distinct from surrounding prose.

- First structured point with a [safe link](https://example.com/point)
  - Nested detail that must remain nested
- Second structured point

### Reference list

1. [Reference A](https://example.com/reference)
2. Reference B

| Signal | Meaning |
| --- | --- |
| source | original structure |
| snapshot | reloaded projection |

${MARKDOWN_FENCE}python
print("stable source")
${MARKDOWN_FENCE}

<aside>
🎯

**Source note.** Keep this callout visible and preserve its link to [the guide](https://example.com/guide).
- Read the [first list link](https://example.com/first-list)
  - Nested source detail
- *Keep* the first list explicit.
</aside>

<aside>
⚠️

*Warning note.* Keep the second callout link to [the warning guide](https://example.com/warning).
1. First warning item
   - Nested warning detail
2. **Second warning item**
</aside>

Trailing prose must remain visible after every structured block.`;

const NOTION_HTML_SOURCE = `
<h2>The Reading Desk</h2>
<p>Reading systems become useful when structure survives the journey from source to screen. This paragraph carries <strong>strong</strong> and <em>emphasis</em> so the rich HTML path has visible inline marks.</p>
<blockquote><p>A short quotation should remain visibly distinct from surrounding prose.</p></blockquote>
<ul><li>First structured point with a <a href="https://example.com/point">safe link</a><ul><li>Nested detail that must remain nested</li></ul></li><li>Second structured point</li></ul>
<h3>Reference list</h3>
<ol><li><a href="https://example.com/reference">Reference A</a></li><li>Reference B</li></ol>
<table><thead><tr><th>Signal</th><th>Meaning</th></tr></thead><tbody><tr><td>source</td><td>original structure</td></tr><tr><td>snapshot</td><td>reloaded projection</td></tr></tbody></table>
<pre><code class="language-python">print("stable source")</code></pre>
<p>&lt;aside&gt;</p>
<p>🎯</p>
<p><strong>Source note.</strong> Keep this callout visible and preserve its link to <a href="https://example.com/guide">the guide</a>.</p>
<ul><li>Read the <a href="https://example.com/first-list">first list link</a><ul><li>Nested source detail</li></ul></li><li><em>Keep</em> the first list explicit.</li></ul>
<p>&lt;/aside&gt;</p>
<p>&lt;aside&gt;</p>
<p>⚠️</p>
<p><em>Warning note.</em> Keep the second callout link to <a href="https://example.com/warning">the warning guide</a>.</p>
<ol><li>First warning item<ul><li>Nested warning detail</li></ul></li><li><strong>Second warning item</strong></li></ol>
<p>&lt;/aside&gt;</p>
<p>Trailing prose must remain visible after every structured block.</p>
`.trim();

function repoRoot(): string {
  const configuredRoot = process.env.CLAREAD_E2E_API_REPO_ROOT?.trim();
  const cwdCandidates = [
    configuredRoot ? resolve(configuredRoot) : null,
    resolve(process.cwd()),
    resolve(process.cwd(), "..", ".."),
  ].filter((candidate): candidate is string => Boolean(candidate));
  const root = cwdCandidates.find((candidate) =>
    existsSync(resolve(candidate, "services", "api", "pyproject.toml")),
  );
  if (!root) {
    throw new Error(
      "Unable to locate Claread repository root for G5 helper; set CLAREAD_E2E_API_REPO_ROOT to the integration API worktree",
    );
  }
  return root;
}

async function pasteNotionDualMime(
  page: Page,
  html: string,
  plain: string,
) {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.evaluate(async ({ html: htmlText, plain: plainText }) => {
    const items = {
      "text/html": new Blob([htmlText], { type: "text/html" }),
      "text/plain": new Blob([plainText], { type: "text/plain" }),
    };
    await navigator.clipboard.write([new ClipboardItem(items)]);
  }, { html, plain });
  const editor = page.locator("[data-slate-editor]").first();
  await editor.click();
  await page.keyboard.press("Control+V");
}

async function loginWithPhoneAuth(page: Page) {
  await page.goto("/login?next=%2Fapp%2Fread");
  await page.getByLabel("手机号").fill("13800138000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await page.getByLabel("验证码").fill("888888");
  await page.getByRole("button", { name: "登录并继续" }).click();
  await page.waitForURL((url) => url.pathname === "/app/read");
}

async function runDeterministicFakePipeline(recordId: string, source: string) {
  const root = repoRoot();
  const apiRoot = resolve(root, "services", "api");
  const helper = resolve(
    apiRoot,
    "tests",
    "reader_markdown_g5_fake_runner.py",
  );
  const python = resolve(
    apiRoot,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  const expectedHash = createHash("sha256").update(source, "utf8").digest("hex");
  const { stdout, stderr } = await execFileAsync(
    python,
    [helper, recordId, expectedHash],
    {
      cwd: apiRoot,
      env: { ...process.env },
      timeout: 180_000,
      maxBuffer: 2 * 1024 * 1024,
    },
  );
  const output = stdout.trim();
  if (!output) {
    throw new Error(`G5 fake runner returned no output: ${stderr}`);
  }
  return JSON.parse(output) as {
    executor_mode: string;
    fake_layer_count: number;
    fake_job_count: number;
    stable_document: { block_count: number; parented_block_count: number };
    snapshot: { stable_tree_node_count: number; enhancement_layer_count: number };
  };
}

test.describe("Reader Markdown Structured Source G5 real product path", () => {
  test("/app/read -> real FastAPI/PostgreSQL -> fake enhancement -> reload keeps structure", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginWithPhoneAuth(page);
    await expect(page.getByRole("heading", { name: "Bring it to Claread." })).toBeVisible();

    await pasteNotionDualMime(page, NOTION_HTML_SOURCE, MARKDOWN_SOURCE);
    const inputEditor = page.locator("[data-slate-editor]").first();
    await expect(inputEditor).toContainText("The Reading Desk");
    await expect(inputEditor).toContainText("Reference list");
    await expect(inputEditor).toContainText("Source note.");
    await expect(inputEditor).toContainText("Warning note.");
    await expect(inputEditor).toContainText("Nested source detail");
    await expect(inputEditor).toContainText("Nested warning detail");
    await expect(inputEditor).toContainText("Trailing prose must remain visible");
    const inputText = await inputEditor.innerText();
    expect(inputText.match(/🎯/g) ?? []).toHaveLength(1);
    expect(inputText.match(/⚠️/g) ?? []).toHaveLength(1);
    expect(inputText).not.toContain("<aside>");
    expect(inputText).not.toContain("</aside>");
    expect(inputText).not.toContain("[!NOTE]");
    await expect(page.getByRole("button", { name: "开始透读" })).toBeEnabled({
      timeout: 10_000,
    });

    const submitResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/api/web/reader/records/input")
        && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "开始透读" }).click();
    const submitResponse = await submitResponsePromise;
    if (submitResponse.status() !== 200) {
      throw new Error(
        `reader-plate input failed: ${submitResponse.status()} ${await submitResponse.text()} body=${submitResponse.request().postData()}`,
      );
    }
    expect(submitResponse.status()).toBe(200);
    const submitPayload = (await submitResponse.json()) as {
      ok: boolean;
      outcome: string;
      reading_record_id?: string;
    };
    expect(submitPayload.ok).toBe(true);
    expect(submitPayload.outcome).toBe("stable_document_ready");
    expect(submitPayload.reading_record_id).toMatch(/^[0-9a-f-]{36}$/);
    const submittedBody = submitResponse.request().postDataJSON() as {
      text?: string;
    };
    expect(submittedBody.text).toBeTruthy();
    const canonicalSource = submittedBody.text as string;
    expect(canonicalSource.match(/<aside>/g) ?? []).toHaveLength(2);
    expect(canonicalSource.match(/<\/aside>/g) ?? []).toHaveLength(2);
    expect(canonicalSource.match(/🎯/g) ?? []).toHaveLength(1);
    expect(canonicalSource.match(/⚠️/g) ?? []).toHaveLength(1);
    expect(canonicalSource).toContain("The Reading Desk");
    expect(canonicalSource).toContain("Reference A");
    expect(canonicalSource).toContain("warning guide");
    expect(canonicalSource).toContain("first-list");
    expect(canonicalSource).toContain("Nested source detail");
    expect(canonicalSource).toContain("Nested warning detail");
    expect(canonicalSource).toContain("Trailing prose must remain visible");
    expect(canonicalSource).not.toContain("&lt;aside&gt;");
    expect(canonicalSource).not.toContain("&lt;/aside&gt;");
    expect(canonicalSource).not.toContain("[!NOTE]");
    expect(canonicalSource).not.toMatch(/class\s*=|style\s*=|on[a-z]+\s*=/i);

    await expect(page).toHaveURL(
      new RegExp(`/app/reader/${submitPayload.reading_record_id}$`),
      { timeout: 20_000 },
    );
    const recordId = submitPayload.reading_record_id as string;

    const fakeResult = await runDeterministicFakePipeline(
      recordId,
      submittedBody.text as string,
    );
    expect(fakeResult.executor_mode).toBe("fake");
    expect(fakeResult.fake_layer_count).toBeGreaterThan(0);
    expect(fakeResult.fake_job_count).toBeGreaterThan(0);
    expect(fakeResult.stable_document.block_count).toBeGreaterThan(8);
    expect(fakeResult.stable_document.parented_block_count).toBeGreaterThan(0);
    expect(fakeResult.snapshot.stable_tree_node_count).toBeGreaterThan(0);

    await page.reload();
    const plateDocument = page.locator(".reader-record-plate-document");
    await expect(plateDocument).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("The Reading Desk", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("A short quotation", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("Source note.", { exact: false }).first()).toBeVisible();
    const sourceCallout = plateDocument.locator(
      'aside[data-reader-record-stable-block-type="source_callout"]',
    );
    await expect(sourceCallout).toHaveCount(2);
    await expect(sourceCallout.locator('[aria-hidden="true"]')).toHaveText([
      "🎯",
      "⚠️",
    ]);
    await expect(
      sourceCallout.nth(0).getByText("Source note.", { exact: false }),
    ).toHaveCount(1);
    await expect(
      sourceCallout.nth(0).getByRole("link", { name: "the guide" }),
    ).toHaveCount(1);
    await expect(sourceCallout.nth(0).getByRole("list")).toHaveCount(2);
    await expect(sourceCallout.nth(0).getByRole("listitem")).toHaveCount(3);
    await expect(
      sourceCallout.nth(0).getByRole("link", { name: "first list link" }),
    ).toHaveAttribute("href", "https://example.com/first-list");
    await expect(
      sourceCallout.nth(0).getByText("Nested source detail", { exact: false }),
    ).toHaveCount(1);
    await expect(
      sourceCallout.nth(1).getByText("Warning note.", { exact: false }),
    ).toHaveCount(1);
    await expect(
      sourceCallout.nth(1).getByRole("link", { name: "the warning guide" }),
    ).toHaveCount(1);
    await expect(sourceCallout.nth(1).getByRole("list")).toHaveCount(2);
    await expect(sourceCallout.nth(1).getByRole("listitem")).toHaveCount(3);
    await expect(
      sourceCallout.nth(1).getByText("Nested warning detail", { exact: false }),
    ).toHaveCount(1);
    await expect(
      page
        .getByRole("listitem")
        .filter({ hasText: "Nested detail that must remain nested" })
        .first(),
    ).toBeVisible();
    await expect(page.getByRole("cell", { name: "original structure" })).toBeVisible();
    await expect(
      plateDocument.getByRole("code").getByText('print("stable source")', {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      plateDocument.locator("p").filter({
        hasText: "Trailing prose must remain visible after every structured block.",
      }),
    ).toBeVisible();

    type TranslationRequestBody = Record<string, unknown> & {
      startUnitId?: string;
      endUnitId?: string;
      startAnchorSegmentId?: string;
      endAnchorSegmentId?: string;
    };
    let translationRequestBody: TranslationRequestBody | null = null;
    await page.route(
      "**/api/web/reader/records/*/section-translation",
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.continue();
          return;
        }
        translationRequestBody = route.request().postDataJSON() as TranslationRequestBody;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            outcome: "succeeded",
            job_id: "fake-section-translation-job",
            detail: null,
          }),
        });
      },
    );

    const calloutTextLeaf = sourceCallout
      .locator('[data-reader-record-leaf="segment_text"]')
      .filter({ hasText: "Source note." })
      .first();
    await expect(calloutTextLeaf).toBeVisible();
    await calloutTextLeaf.scrollIntoViewIfNeeded();
    await calloutTextLeaf.selectText();
    const selectionToolbar = page.locator(
      '[data-reader-record-floating-toolbar="selection-actions"]',
    );
    await expect(selectionToolbar).toBeVisible({ timeout: 10_000 });
    const copyButton = selectionToolbar.locator(
      '[data-reader-record-toolbar-action="copy"]',
    );
    await expect(copyButton).toBeEnabled();
    await copyButton.click();
    await expect(
      page.getByTestId("reader-record-plate-copy-status"),
    ).toHaveText("已复制");
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toContain("Source note.");

    const translationResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/section-translation") &&
        response.request().method() === "POST",
    );
    const translateButton = selectionToolbar.locator(
      '[data-reader-record-toolbar-action="translate"]',
    );
    await expect(translateButton).toBeEnabled();
    await translateButton.click();
    const translationResponse = await translationResponsePromise;
    expect(translationResponse.status()).toBe(200);
    await expect.poll(() => translationRequestBody).not.toBeNull();
    const submittedTranslationRequest = translationRequestBody as unknown as TranslationRequestBody;
    expect(submittedTranslationRequest).toEqual(
      expect.objectContaining({
        startUnitId: expect.any(String),
        endUnitId: expect.any(String),
        startAnchorSegmentId: expect.any(String),
        endAnchorSegmentId: expect.any(String),
        nodeId: expect.any(String),
        outlineRevision: null,
      }),
    );
    expect(submittedTranslationRequest.startUnitId).toBe(
      submittedTranslationRequest.endUnitId,
    );
    expect(submittedTranslationRequest.startAnchorSegmentId).toBe(
      submittedTranslationRequest.endAnchorSegmentId,
    );
    expect(submittedTranslationRequest).not.toHaveProperty("recordId");
    expect(submittedTranslationRequest).not.toHaveProperty("baseId");
    expect(submittedTranslationRequest).not.toHaveProperty("generation");
    await expect(
      page.getByTestId("reader-record-plate-translation-status"),
    ).toHaveText("翻译已提交");

    await expect(
      plateDocument.locator('[data-reader-record-translation-lane="true"]').first(),
    ).toBeVisible({
      timeout: 20_000,
    });

    const beforeReload = await plateDocument.innerText();
    const bodyText = await plateDocument.innerText();
    expect(bodyText).not.toContain("```");
    expect(bodyText).not.toContain("<aside>");
    expect(bodyText).not.toContain("\\<");
    await expect(
      plateDocument.locator("p").filter({
        hasText: "Trailing prose must remain visible after every structured block.",
      }),
    ).toHaveCount(1);

    await page.screenshot({
      path: "test-results/reader-markdown-g5-real-product.png",
      fullPage: true,
    });

    await page.reload();
    await expect(page.locator(".reader-record-plate-document")).toBeVisible({
      timeout: 20_000,
    });
    const reloadedPlateDocument = page.locator(".reader-record-plate-document");
    const afterReload = await reloadedPlateDocument.innerText();
    expect(afterReload).toEqual(beforeReload);
    await expect(
      reloadedPlateDocument.locator(
        'aside[data-reader-record-stable-block-type="source_callout"]',
      ),
    ).toHaveCount(2);
    await expect(
      reloadedPlateDocument.locator(
        'aside[data-reader-record-stable-block-type="source_callout"] [aria-hidden="true"]',
      ),
    ).toHaveText(["🎯", "⚠️"]);
    await expect(
      reloadedPlateDocument
        .locator('aside[data-reader-record-stable-block-type="source_callout"]')
        .nth(0)
        .getByText("Source note.", { exact: false }),
    ).toHaveCount(1);
    await expect(
      reloadedPlateDocument
        .locator('aside[data-reader-record-stable-block-type="source_callout"]')
        .nth(1)
        .getByText("Warning note.", { exact: false }),
    ).toHaveCount(1);
    await expect(
      page.locator(".reader-record-plate-document p").filter({
        hasText: "Trailing prose must remain visible after every structured block.",
      }),
    ).toHaveCount(1);
  });
});
