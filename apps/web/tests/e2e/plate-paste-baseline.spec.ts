/**
 * L1 输入端 baseline — 生产 MarkdownTextInput 的真实浏览器粘贴合同。
 *
 * 挂载路由：/e2e-plate-paste-spike/input（生产组件，非 spike 装配）。
 * 每个 fixture 走真实 ClipboardEvent('paste')（DataTransfer 携带
 * text/html + text/plain），断言：
 *   - 结构进入 DOM（官方行为插件 + 现有视觉组件渲染）
 *   - 序列化 markdown 保留语义（getMarkdown / getSubmitText）
 *   - 危险内容被清洗（script、iframe、事件属性、危险 URL 不进 DOM 与序列化）
 *   - image / footnote / task list 不静默丢失
 */

import { expect, test, type Page } from "@playwright/test";

import {
  FOOTNOTE_MD,
  IMAGE_HTML,
  IMAGE_MD,
  LINKS_HTML,
  MALICIOUS_HTML,
  makeLongMarkdown,
  NOTION_CALLOUT_ASIDE_HTML,
  RICH_HTML,
  RICH_HTML_PLAIN,
  RICH_MARKDOWN,
  TABLE_HTML,
  TASK_LIST_HTML,
  TASK_LIST_MD,
  UNCLOSED_FENCE_MD,
  VECTOR_MD,
  WORD_HTML,
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

/**
 * 真实粘贴路径：先经 navigator.clipboard 写入真实 ClipboardItem
 * （text/html + text/plain 双格式），click 建立受信 selection，
 * 再 keyboard Ctrl+V 触发 isTrusted 的浏览器原生 paste。
 * （合成 ClipboardEvent + DataTransfer 在 headless 下无法建立 Slate
 * selection，insertData 会被静默丢弃——spike 已验证。）
 */
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

const editorDom = (page: Page) =>
  page.locator("[data-slate-editor]").first();

// ---------------------------------------------------------------------------

test.describe("L1 baseline: raw markdown paste", () => {
  test("rich markdown renders structure and serializes back", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: RICH_MARKDOWN });

    // DOM 结构
    await expect(editorDom(page).locator("h1")).toHaveCount(1);
    await expect(editorDom(page).locator("blockquote")).toHaveCount(1);
    await expect(editorDom(page).locator("ul li").first()).toBeVisible();
    await expect(editorDom(page).locator("ol li")).toHaveCount(2);
    await expect(editorDom(page).locator("pre")).toHaveCount(1);
    await expect(editorDom(page).locator("table")).toHaveCount(1);
    await expect(editorDom(page).locator("hr")).toHaveCount(1);
    await expect(
      editorDom(page).locator('a[href="https://example.com/r"]'),
    ).toHaveCount(1);

    // 序列化语义
    const md = await getMarkdown(page);
    expect(md).toContain("# Quarterly Report");
    expect(md).toContain("**bold**");
    expect(md).toContain("[link](https://example.com/r)");
    expect(md).toContain("| Name | Value |");
    expect(md).toContain("def f():");
    // C1.4 粘贴保真路径：未编辑时 getSubmitText 返回原始粘贴文本。
    // 真实 OS clipboard 会把 text/plain 换行规范化为 CRLF（Windows），
    // 因此按 LF 归一后比对——保真语义是"原样提交 OS 交付的粘贴文本"。
    const submitted = await getSubmitText(page);
    expect(submitted.replace(/\r\n/g, "\n")).toBe(RICH_MARKDOWN);
  });
});

test.describe("L1 baseline: dual-format html+plain paste", () => {
  test("html structure preserved (not flattened to paragraphs)", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { html: RICH_HTML, plain: RICH_HTML_PLAIN });

    await expect(editorDom(page).locator("h1")).toHaveCount(1);
    await expect(editorDom(page).locator("h2")).toHaveCount(1);
    await expect(editorDom(page).locator("blockquote")).toHaveCount(1);
    // 嵌套列表：两个 ul
    expect(await editorDom(page).locator("ul").count()).toBeGreaterThanOrEqual(2);
    await expect(editorDom(page).locator("ol li")).toHaveCount(2);
    await expect(editorDom(page).locator("pre")).toHaveCount(1);
    await expect(editorDom(page).locator("table")).toHaveCount(1);
    await expect(editorDom(page).locator("hr")).toHaveCount(1);
    await expect(
      editorDom(page).locator('a[href="https://example.com/docs"]'),
    ).toHaveCount(1);

    const md = await getMarkdown(page);
    expect(md).toContain("quoted insight");
    expect(md).toContain("| Name | Value |");

    // 双格式 clipboard 的 text/plain 故意缺少 quote/list/code/table。
    // 提交源必须来自清洗后的 Plate 结构，而不是扁平 companion text。
    const submitted = await getSubmitText(page);
    expect(submitted).toContain("# Title One");
    expect(submitted).toContain("## Section Two");
    expect(submitted).toContain("> quoted insight");
    expect(submitted).toContain("nested alpha");
    expect(submitted).toContain("def f():");
    expect(submitted).toContain("| Name | Value |");
    expect(submitted.replace(/\r\n/g, "\n")).not.toBe(RICH_HTML_PLAIN);
  });

  test("paste followed by immediate typing submits the user edit", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(
      page,
      { plain: "## Immediate edit\n\nOriginal body." },
      { settleMs: 0 },
    );
    await page.keyboard.type(" USEREDIT");

    await expect
      .poll(() => getSubmitText(page), { timeout: 10_000 })
      .toContain("USEREDIT");
  });
});

test.describe("L1 baseline: Notion callout", () => {
  test("aside HTML becomes source_callout (not blockquote, no visible tags)", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, {
      html: NOTION_CALLOUT_ASIDE_HTML,
      plain: "callout body",
    });

    // source_callout 渲染为 <aside role="note">，不是 blockquote
    await expect(editorDom(page).locator("aside[role='note']")).toHaveCount(1);
    await expect(editorDom(page).locator("blockquote")).toHaveCount(0);

    // 用户不可见 <aside> / </aside> 标签文本
    const text = await editorDom(page).innerText();
    expect(text).toContain("callout body with");
    expect(text).toContain("bold");
    expect(text).not.toContain("<aside>");
    expect(text).not.toContain("</aside>");

    // 序列化为 canonical <aside> 表达
    const md = await getMarkdown(page);
    expect(md).toContain("<aside>");
    expect(md).toContain("</aside>");
    expect(md).toContain("callout body");
    // 不应出现 GFM marker
    expect(md).not.toContain("[!NOTE]");

    const submitted = await getSubmitText(page);
    expect(submitted).toContain("<aside>");
    expect(submitted).toContain("</aside>");
    expect(submitted).toContain("callout body");
  });
});

test.describe("L1 baseline: Word-style html", () => {
  test("word html keeps text and emphasis", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { html: WORD_HTML, plain: "word" });
    const text = await editorDom(page).innerText();
    expect(text).toContain("Word bold heading");
    expect(text).toContain("word italic");
    const submitted = await getSubmitText(page);
    expect(submitted).toContain("**Word bold heading**");
    expect(submitted).toContain("*word italic*");
    expect(submitted).not.toBe("word");
  });
});

test.describe("L1 baseline: link safety", () => {
  test("dangerous URLs stripped, safe link kept", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { html: LINKS_HTML, plain: "links" });

    await expect(
      editorDom(page).locator('a[href="https://example.com/safe"]'),
    ).toHaveCount(1);
    // 危险链接：href 被摘除（可能残留 <a> 无 href 或退化为文本）
    expect(
      await editorDom(page).locator('a[href^="javascript:"]').count(),
    ).toBe(0);
    expect(await editorDom(page).locator('a[href^="data:"]').count()).toBe(0);
    // 链接文本仍可见
    const text = await editorDom(page).innerText();
    expect(text).toContain("danger js link");
    // 序列化不含危险 scheme
    const md = await getMarkdown(page);
    expect(md).not.toMatch(/javascript:/i);
    expect(md).not.toMatch(/data:text\/html/i);
  });
});

test.describe("L1 baseline: malicious html sanitization", () => {
  test("script/iframe/on* never enter DOM or serialized output", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { html: MALICIOUS_HTML, plain: "x" });

    expect(await editorDom(page).locator("script").count()).toBe(0);
    expect(await editorDom(page).locator("iframe").count()).toBe(0);
    expect(await editorDom(page).locator("[onclick]").count()).toBe(0);
    expect(await editorDom(page).locator("[onerror]").count()).toBe(0);
    const pwned = await page.evaluate(
      () => (window as unknown as { __pwned?: boolean }).__pwned,
    );
    expect(pwned).toBeUndefined();
    const text = await editorDom(page).innerText();
    expect(text).toContain("visible text");
    const md = await getMarkdown(page);
    expect(md).not.toContain("script");
  });
});

test.describe("L1 baseline: table / task list / image / footnote", () => {
  test("table html renders as table", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { html: TABLE_HTML, plain: "t" });
    await expect(editorDom(page).locator("table")).toHaveCount(1);
    await expect(editorDom(page).locator("th")).toHaveCount(2);
    await expect(editorDom(page).locator("td")).toHaveCount(4);
    const submitted = await getSubmitText(page);
    expect(submitted).toContain("| H1 | H2 |");
    expect(submitted).toContain("| c1 | c2 |");
    expect(submitted).not.toBe("t");
  });

  test("task list markdown keeps list structure and markers", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: TASK_LIST_MD });
    const text = await editorDom(page).innerText();
    expect(text).toContain("todo one");
    expect(text).toContain("done two");
    // 勾选标记以字面形态可见（[ ] / [x]）
    expect(text).toMatch(/\[\s?\]\s*todo one/);
    expect(text).toMatch(/\[x\]\s*done two/);
  });

  test("task list html keeps items visible", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { html: TASK_LIST_HTML, plain: "t" });
    const text = await editorDom(page).innerText();
    expect(text).toContain("todo item");
    expect(text).toContain("done item");
  });

  test("image markdown becomes visible link", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: IMAGE_MD });
    const text = await editorDom(page).innerText();
    expect(text).toContain("diagram");
    const md = await getMarkdown(page);
    expect(md).toContain("https://example.com/d.png");
  });

  test("image html becomes visible link", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { html: IMAGE_HTML, plain: "fig" });
    await expect(
      editorDom(page).locator('a[href="https://example.com/d.png"]'),
    ).toHaveCount(1);
    const text = await editorDom(page).innerText();
    expect(text).toContain("diagram");
  });

  test("footnote keeps literal text", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: FOOTNOTE_MD });
    const text = await editorDom(page).innerText();
    expect(text).toContain("[^1]");
    expect(text).toContain("The footnote body.");
  });
});

test.describe("L1 baseline: edge inputs", () => {
  test("unclosed fence does not crash, content visible", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: UNCLOSED_FENCE_MD });
    const text = await editorDom(page).innerText();
    expect(text).toContain("never_closed");
    const degraded = await page.evaluate(
      () => (window as unknown as InputSpikeWindow).__inputSpike!.lastDegraded,
    );
    console.log("UNCLOSED_FENCE_DEGRADED", degraded);
  });

  test("vector<T> angle brackets preserved", async ({ page }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { plain: VECTOR_MD });
    const text = await editorDom(page).innerText();
    expect(text).toContain("vector<T>");
    expect(text).toContain("unordered_map<K, V>");
  });
});

test.describe("L1 baseline: 30k+ long document", () => {
  test("long markdown paste completes without content loss", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await waitForHarnessReady(page);
    const longMd = makeLongMarkdown(30_000);
    await dispatchRealPaste(page, { plain: longMd });
    const submitted = await getSubmitText(page);
    // C1.4 粘贴保真路径命中：提交文本与原始粘贴一致（CRLF 归一后
    // byte-exact）。多变更批次由 100ms 静默窗口吸收（阶段 3 修复）。
    expect(submitted.replace(/\r\n/g, "\n")).toBe(longMd);
    // 内容完整性合同：所有结构单元不丢失（双保险）。
    const count = (s: string, sub: string) => s.split(sub).length - 1;
    expect(count(submitted, "## Section")).toBe(count(longMd, "## Section"));
    expect(count(submitted, "point one")).toBe(count(longMd, "point one"));
    expect(count(submitted, "quoted line")).toBe(count(longMd, "quoted line"));
    expect(submitted).toContain("# Long Document");
    // DOM 至少有内容渲染
    const text = await editorDom(page).innerText();
    expect(text).toContain("Long Document");
    expect(text).toContain("Section 0");
  });
});
