/**
 * R-Aside-1 E2E — Source Callout 全链路真实 Chromium 验收。
 *
 * 覆盖三条归一路径 + 安全回归 + 序列化 round-trip：
 * 1. text/plain 纯 Markdown `<aside>` 粘贴
 * 2. text/html `<aside class="callout-warning">` 粘贴
 * 3. GFM alert `> [!NOTE]` 纯 Markdown 粘贴
 * 4. 安全回归：script/iframe/event handler/不完整 aside/转义 aside
 * 5. 序列化：canonical `<aside>` 表达，无可见 `[!NOTE]` marker
 *
 * 验收标准：
 * - 输入页 DOM 中无可见 `<aside>` / `</aside>` / `[!NOTE]` 标签文本
 * - 渲染为 `<aside role="note">` 语义结构（Notion 风格 callout）
 * - 提交文本包含 canonical `<aside>...</aside>`
 * - 危险内容不进入 DOM 或序列化输出
 */

import { expect, test, type Page } from "@playwright/test";

import {
  ASIDE_HTML_WITH_CLASS,
  ASIDE_MARKDOWN_MULTIPARA,
  ASIDE_MARKDOWN_PLAIN,
  ASIDE_WITH_TRAILING_TEXT_MD,
  DANGEROUS_ASIDE_HTML,
  ESCAPED_ASIDE_MD,
  GFM_ALERT_NOTE_MD,
  GFM_ALERT_WARNING_MD,
  NOTION_CALLOUT_DUAL_MIME_HTML,
  NOTION_CALLOUT_DUAL_MIME_PLAIN,
  UNCLOSED_ASIDE_MD,
} from "./fixtures/clipboard-fixtures";

const HARNESS_URL = "/e2e-plate-paste-spike/input";

interface InputSpikeWindow {
  __inputSpikeReady?: boolean;
  __inputSpike?: {
    handle: {
      getSubmitText: () => string;
      getMarkdown: () => string;
      clear: () => void;
      setValue: (md: string) => void;
      focus: () => void;
    } | null;
    lastChange: string;
    lastDegraded: string | null;
    lastLint: string | null;
  };
}

async function waitForHarnessReady(page: Page) {
  await page.goto(HARNESS_URL);
  await page.waitForFunction(
    () => (window as unknown as InputSpikeWindow).__inputSpikeReady === true,
    undefined,
    { timeout: 30_000 },
  );
}

async function dispatchRealPaste(
  page: Page,
  payload: { html?: string; plain?: string },
  options: { settleMs?: number } = {},
) {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.evaluate(async ({ html, plain }) => {
    const items: Record<string, Blob> = {};
    if (html !== undefined)
      items["text/html"] = new Blob([html], { type: "text/html" });
    if (plain !== undefined)
      items["text/plain"] = new Blob([plain], { type: "text/plain" });
    await navigator.clipboard.write([new ClipboardItem(items)]);
  }, payload);
  await page.locator("[data-slate-editor]").first().click();
  await page.keyboard.press("Control+V");
  await page.waitForTimeout(options.settleMs ?? 500);
}

async function getMarkdown(page: Page): Promise<string> {
  return page.evaluate(
    () =>
      (window as unknown as InputSpikeWindow).__inputSpike!.handle!.getMarkdown(),
  );
}

async function getSubmitText(page: Page): Promise<string> {
  return page.evaluate(
    () =>
      (window as unknown as InputSpikeWindow).__inputSpike!.handle!.getSubmitText(),
  );
}

async function clearEditor(page: Page) {
  await page.evaluate(
    () =>
      (window as unknown as InputSpikeWindow).__inputSpike!.handle!.clear(),
  );
}

const editorDom = (page: Page) => page.locator("[data-slate-editor]").first();

// ---------------------------------------------------------------------------

test.describe("R-Aside-1: text/plain Markdown <aside> paste", () => {
  test("纯 Markdown aside 渲染为 source_callout，无可见标签", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: ASIDE_MARKDOWN_PLAIN });

    // 渲染为 <aside role="note">，不是 blockquote
    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);
    await expect(editorDom(page).locator("blockquote")).toHaveCount(0);

    // 用户不可见 <aside> / </aside> 标签文本
    const text = await editorDom(page).innerText();
    expect(text).toContain("source callout");
    expect(text).toContain("bold");
    expect(text).not.toContain("<aside>");
    expect(text).not.toContain("</aside>");
    expect(text).not.toContain("[!NOTE]");

    // 序列化为 canonical <aside>
    const md = await getMarkdown(page);
    expect(md).toContain("<aside>");
    expect(md).toContain("</aside>");
    expect(md).not.toContain("[!NOTE]");

    // 提交文本包含 canonical aside
    const submitted = await getSubmitText(page);
    expect(submitted).toContain("<aside>");
    expect(submitted).toContain("</aside>");
  });

  test("多段落 aside 保留段落结构", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: ASIDE_MARKDOWN_MULTIPARA });

    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);

    const text = await editorDom(page).innerText();
    expect(text).toContain("First paragraph in callout.");
    expect(text).toContain("Second paragraph in callout.");
    expect(text).not.toContain("<aside>");
    expect(text).not.toContain("</aside>");

    const md = await getMarkdown(page);
    expect(md).toContain("<aside>");
    expect(md).toContain("First paragraph");
    expect(md).toContain("Second paragraph");
    expect(md).toContain("</aside>");
  });
});

test.describe("R-Aside-1: text/html <aside class> paste", () => {
  test("带 class 的 aside HTML 渲染为 source_callout", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, {
      html: ASIDE_HTML_WITH_CLASS,
      plain: "Warning callout body",
    });

    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);
    await expect(editorDom(page).locator("blockquote")).toHaveCount(0);

    const text = await editorDom(page).innerText();
    expect(text).toContain("Warning callout body");
    expect(text).not.toContain("<aside>");
    expect(text).not.toContain("</aside>");
    expect(text).not.toContain("[!NOTE]");

    // canonical 序列化不携带 class
    const md = await getMarkdown(page);
    expect(md).toContain("<aside>");
    expect(md).toContain("</aside>");
    expect(md).not.toContain("callout-warning");
    expect(md).not.toContain("class=");

    const submitted = await getSubmitText(page);
    expect(submitted).toContain("<aside>");
    expect(submitted).toContain("Warning callout body");
  });
});

test.describe("R-Aside-1: Notion dual-MIME aside negotiation", () => {
  test("locally fuses the plain aside while preserving the complete HTML article", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, {
      html: NOTION_CALLOUT_DUAL_MIME_HTML,
      plain: NOTION_CALLOUT_DUAL_MIME_PLAIN,
    });

    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(2);
    const callouts = editorDom(page).locator("aside[role='note']");
    await expect(callouts.nth(0).getByRole("list")).toHaveCount(2);
    await expect(callouts.nth(0).getByRole("listitem")).toHaveCount(3);
    await expect(callouts.nth(1).getByRole("list")).toHaveCount(2);
    await expect(callouts.nth(1).getByRole("listitem")).toHaveCount(3);
    const text = await editorDom(page).innerText();
    expect(text).toContain("Reader Goals");
    expect(text).toContain("first point");
    expect(text).toContain("second point");
    expect(text).toContain("Reference list");
    expect(text).toContain("Reference A");
    expect(text).toContain("rich structure");
    expect(text).toContain("Trailing paragraph remains independent.");
    expect(text).toContain("Alignment");
    expect(text).toContain("Warning");
    expect(text).toContain("🎯");
    expect(text).toContain("⚠️");
    expect(text.match(/🎯/gu)).toHaveLength(1);
    expect(text.match(/⚠️/gu)).toHaveLength(1);
    await expect(editorDom(page).getByRole("link", { name: "safe link" })).toHaveCount(1);
    await expect(editorDom(page).getByRole("link", { name: "first callout link" })).toHaveCount(1);
    await expect(editorDom(page).getByRole("link", { name: "second callout link" })).toHaveCount(1);
    await expect(callouts.nth(0).getByRole("link", { name: "first list link" })).toHaveAttribute(
      "href",
      "https://example.com/first-list",
    );
    await expect(callouts.nth(0).getByText("Nested first detail", { exact: false })).toHaveCount(1);
    await expect(callouts.nth(1).getByText("Nested warning detail", { exact: false })).toHaveCount(1);
    await expect(editorDom(page).getByRole("link", { name: "Reference A" })).toHaveCount(1);
    await expect(editorDom(page).getByRole("cell", { name: "rich structure" })).toHaveCount(1);
    expect(text).not.toContain("<aside>");
    expect(text).not.toContain("</aside>");

    const submitted = await getSubmitText(page);
    expect(submitted.match(/<aside>/g) ?? []).toHaveLength(2);
    expect(submitted.match(/<\/aside>/g) ?? []).toHaveLength(2);
    expect(submitted).toContain("🎯");
    expect(submitted).toContain("⚠️");
    expect(submitted.match(/🎯/gu)).toHaveLength(1);
    expect(submitted.match(/⚠️/gu)).toHaveLength(1);
    expect(submitted).toContain("**Alignment**");
    expect(submitted).toContain("*Warning*");
    expect(submitted).toContain("https://example.com/first");
    expect(submitted).toContain("https://example.com/second");
    expect(submitted).toContain("https://example.com/first-list");
    expect(submitted).toContain("Nested first detail");
    expect(submitted).toContain("Nested warning detail");
    expect(submitted).not.toContain("&lt;aside&gt;");
  });

  test("same label with different safe URL visibly declines instead of adopting plain URL", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, {
      html: "<h2>URL check</h2><p>&lt;aside&gt;</p><p>Read the <a href=\"https://trusted.example/guide\">guide</a>.</p><p>&lt;/aside&gt;</p><p>Trailing text.</p>",
      plain: "## URL check\n\n<aside>\nRead the [guide](https://other.example/guide).\n</aside>\n\nTrailing text.",
    });

    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(0);
    const text = await editorDom(page).innerText();
    expect(text).toContain("<aside>");
    expect(text).toContain("</aside>");
    await expect(editorDom(page).getByRole("link", { name: "guide" })).toHaveAttribute(
      "href",
      "https://trusted.example/guide",
    );
    expect(text).not.toContain("https://other.example/guide");
  });
});

test.describe("R-Aside-1: GFM alert > [!NOTE] paste", () => {
  test("GFM NOTE alert 归一为 source_callout，marker 不可见", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: GFM_ALERT_NOTE_MD });

    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);
    await expect(editorDom(page).locator("blockquote")).toHaveCount(0);

    const text = await editorDom(page).innerText();
    expect(text).toContain("note callout");
    expect(text).toContain("multiple lines");
    // GFM marker 不得出现在用户可见界面
    expect(text).not.toContain("[!NOTE]");
    expect(text).not.toContain("<aside>");
    expect(text).not.toContain("</aside>");

    // 序列化为 canonical <aside>（不是 GFM marker）
    const md = await getMarkdown(page);
    expect(md).toContain("<aside>");
    expect(md).toContain("</aside>");
    expect(md).toContain("note callout");
    expect(md).not.toContain("[!NOTE]");

    const submitted = await getSubmitText(page);
    expect(submitted).toContain("<aside>");
    expect(submitted).toContain("note callout");
    expect(submitted).not.toContain("[!NOTE]");
  });

  test("GFM WARNING alert 归一为 source_callout kind=warning", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: GFM_ALERT_WARNING_MD });

    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);

    const text = await editorDom(page).innerText();
    expect(text).toContain("Be careful");
    expect(text).not.toContain("[!WARNING]");
    expect(text).not.toContain("<aside>");

    const md = await getMarkdown(page);
    expect(md).toContain("<aside>");
    expect(md).toContain("Be careful");
    expect(md).not.toContain("[!WARNING]");
  });
});

test.describe("R-Aside-1: safety regression", () => {
  test("转义 \\<aside> 保持字面文本，不变成 source_callout", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: ESCAPED_ASIDE_MD });

    // 不渲染为 source_callout
    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(0);

    // 字面文本保留
    const text = await editorDom(page).innerText();
    expect(text).toContain("<aside>");
    expect(text).toContain("</aside>");
  });

  test("不完整 <aside>（无闭合）不变成 source_callout", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: UNCLOSED_ASIDE_MD });

    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(0);

    const text = await editorDom(page).innerText();
    expect(text).toContain("No closing tag");
  });

  test("危险 aside（event handler）安全降级，content 可见", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, {
      html: DANGEROUS_ASIDE_HTML,
      plain: "safe content",
    });

    // 仍渲染为 source_callout（属性被清洗，content 保留）
    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);

    // event handler 不进入 DOM
    expect(await editorDom(page).locator("[onclick]").count()).toBe(0);

    // content 可见
    const text = await editorDom(page).innerText();
    expect(text).toContain("safe content");
    expect(text).not.toContain("<aside>");
    expect(text).not.toContain("steal");
  });

  test("script/iframe 不进入 DOM（与 aside 无关的通用安全）", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, {
      html: `<aside><p>safe</p></aside><script>window.__pwned=true</script><iframe src="evil"></iframe>`,
      plain: "safe",
    });

    expect(await editorDom(page).locator("script").count()).toBe(0);
    expect(await editorDom(page).locator("iframe").count()).toBe(0);
    expect(await editorDom(page).locator("[onclick]").count()).toBe(0);

    const pwned = await page.evaluate(
      () => (window as unknown as { __pwned?: boolean }).__pwned,
    );
    expect(pwned).toBeUndefined();
  });
});

test.describe("R-Aside-1: clear and re-paste stability", () => {
  test("清空后再粘贴 aside 结构不丢失", async ({ page }) => {
    await waitForHarnessReady(page);

    // 第一次粘贴
    await dispatchRealPaste(page, { plain: ASIDE_MARKDOWN_PLAIN });
    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);

    // 清空
    await clearEditor(page);
    await page.waitForTimeout(200);
    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(0);

    // 第二次粘贴（不同内容）
    await dispatchRealPaste(page, { plain: GFM_ALERT_NOTE_MD });
    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);

    const text = await editorDom(page).innerText();
    expect(text).toContain("note callout");
    expect(text).not.toContain("[!NOTE]");
  });
});

// ---------------------------------------------------------------------------
// R-Aside-1R A1: </aside> 后紧接正文必须成为独立段落，不吞入 callout
// ---------------------------------------------------------------------------

test.describe("R-Aside-1R A1: trailing text after </aside>", () => {
  test("</aside> 后正文渲染为独立段落，不在 callout 内", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: ASIDE_WITH_TRAILING_TEXT_MD });

    // 渲染为 1 个 source_callout
    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);

    const text = await editorDom(page).innerText();

    // callout 内部内容保留
    expect(text).toContain("this is a callout body");
    expect(text).toContain("Second paragraph inside callout");

    // trailing text 作为独立段落出现
    expect(text).toContain("Peer discussion continues here");

    // 无可见标签
    expect(text).not.toContain("<aside>");
    expect(text).not.toContain("</aside>");

    // 序列化：canonical <aside> + 后续段落
    const md = await getMarkdown(page);
    expect(md).toContain("<aside>");
    expect(md).toContain("this is a callout body");
    expect(md).toContain("Second paragraph inside callout");
    expect(md).toContain("</aside>");
    // trailing 文本在 </aside> 之后独立存在
    expect(md).toContain("Peer discussion continues here");

    // 提交文本同样保留 callout + trailing
    const submitted = await getSubmitText(page);
    expect(submitted).toContain("<aside>");
    expect(submitted).toContain("</aside>");
    expect(submitted).toContain("Peer discussion continues here");
  });
});
