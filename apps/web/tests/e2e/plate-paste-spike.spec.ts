/**
 * L0 paste spike — 真实 Chromium ClipboardEvent 粘贴验证。
 *
 * 验证官方 @platejs 行为插件（basic-nodes / list-classic / link /
 * code-block / table）在真实浏览器里对以下输入的结构保真：
 *   A. 富结构 HTML（标题/段落/marks/链接/嵌套列表/blockquote/code/table），
 *      clipboard 同时带 text/html + text/plain 双格式。
 *   B. GFM task list HTML（<ul class="contains-task-list"> + checkbox input）。
 *   C. task list 裁决：candidate(BaseTaskListPlugin) vs todo(BaseTodoListPlugin)
 *      两种变体对 `- [ ]` Markdown 与 task list HTML 的反序列化 node shape。
 *
 * 使用真实 DataTransfer + dispatchEvent(new ClipboardEvent('paste'))，
 * 读取真实 clipboardData，不走 jsdom。
 */

import { expect, test, type Page } from "@playwright/test";

const HARNESS_URL = "/e2e-plate-paste-spike";

interface SpikeNode {
  type?: string;
  text?: string;
  url?: string;
  checked?: boolean;
  language?: string;
  children?: SpikeNode[];
  [key: string]: unknown;
}

interface PasteSpikeWindow {
  __pasteSpikeReady?: boolean;
  __pasteSpike?: {
    getChildren: () => SpikeNode[];
    deserializeHtml: (html: string, variant?: string) => SpikeNode[];
    deserializeMarkdown: (md: string, variant?: string) => SpikeNode[];
  };
}

async function waitForHarnessReady(page: Page) {
  await page.goto(HARNESS_URL);
  await page.waitForFunction(() => (window as unknown as PasteSpikeWindow).__pasteSpikeReady === true, undefined, {
    timeout: 30_000,
  });
}

/**
 * 真实粘贴路径：先经 navigator.clipboard 写入真实 ClipboardItem
 * （text/html + text/plain 双格式），click 建立受信 selection，
 * 再 keyboard Ctrl+V 触发 isTrusted 的浏览器原生 paste。
 *
 * 备注（spike 发现）：合成 ClipboardEvent + DataTransfer 路径下
 * Slate selection 始终为 null（headless 中 click/setSelection 都无法
 * 让 editor.selection 非空），insertData 被静默丢弃。真实 clipboard
 * + 键盘粘贴是唯一能驱动完整管线的路径。
 */
async function dispatchRealPaste(
  page: Page,
  payload: { html?: string; plain?: string },
) {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.evaluate(async ({ html, plain }) => {
    const items: Record<string, Blob> = {};
    if (html !== undefined)
      items["text/html"] = new Blob([html], { type: "text/html" });
    if (plain !== undefined)
      items["text/plain"] = new Blob([plain], { type: "text/plain" });
    await navigator.clipboard.write([
      new ClipboardItem(items, { presentationStyle: "unspecified" }),
    ]);
  }, payload);
  await page.locator("[data-slate-editor]").first().click();
  await page.keyboard.press("Control+V");
  // 等 Slate 完成插入
  await page.waitForTimeout(500);
  const debug = await page.evaluate(
    () =>
      (window as unknown as { __pasteSpikeDebug?: unknown[] })
        .__pasteSpikeDebug,
  );
  console.log("PASTE_DEBUG", JSON.stringify(debug));
}

async function getChildren(page: Page): Promise<SpikeNode[]> {
  return page.evaluate(() => (window as unknown as PasteSpikeWindow).__pasteSpike!.getChildren());
}

/** 收集树中所有 element type（含嵌套），便于结构断言。 */
function collectTypes(nodes: SpikeNode[]): string[] {
  const types: string[] = [];
  const walk = (ns: SpikeNode[]) => {
    for (const n of ns) {
      if (n.type) types.push(n.type);
      if (n.children) walk(n.children);
    }
  };
  walk(nodes);
  return types;
}

function collectText(nodes: SpikeNode[]): string {
  let out = "";
  const walk = (ns: SpikeNode[]) => {
    for (const n of ns) {
      if (typeof n.text === "string") out += n.text;
      if (n.children) walk(n.children);
    }
  };
  walk(nodes);
  return out;
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const RICH_HTML = `
<h1>Title One</h1>
<h2>Section Two</h2>
<p>Plain paragraph with <strong>bold</strong>, <em>italic</em>, <code>inline_code</code> and <a href="https://example.com/docs">a link</a>.</p>
<blockquote><p>quoted insight</p></blockquote>
<ul>
  <li>outer one
    <ul><li>nested alpha</li><li>nested beta</li></ul>
  </li>
  <li>outer two</li>
</ul>
<ol><li>first</li><li>second</li></ol>
<pre><code class="language-python">def f():\n    return 1</code></pre>
<table>
  <tr><th>Name</th><th>Value</th></tr>
  <tr><td>a</td><td>1</td></tr>
</table>
<hr>
<p>tail</p>
`.trim();

const RICH_PLAIN = `Title One\n\nSection Two\n\nPlain paragraph with bold, italic, inline_code and a link.\n\ntail`;

const TASK_LIST_HTML = `
<ul class="contains-task-list">
<li class="task-list-item"><input type="checkbox" disabled> todo item</li>
<li class="task-list-item"><input type="checkbox" checked disabled> done item</li>
</ul>
`.trim();

const TASK_LIST_MD = `- [ ] todo item\n- [x] done item\n`;

// ---------------------------------------------------------------------------
// A. 富结构 HTML + 双格式 clipboard paste
// ---------------------------------------------------------------------------

test.describe("A. rich HTML paste keeps structure", () => {
  test("headings/marks/link/nested list/blockquote/code/table survive real paste", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, { html: RICH_HTML, plain: RICH_PLAIN });

    const children = await getChildren(page);
    const types = collectTypes(children);
    console.log("PASTE_RESULT_TYPES", JSON.stringify(types));
    console.log("PASTE_RESULT_TREE", JSON.stringify(children));

    // 标题
    expect(types).toContain("h1");
    expect(types).toContain("h2");
    // blockquote
    expect(types).toContain("blockquote");
    // 列表（含嵌套）
    expect(types).toContain("ul");
    expect(types).toContain("ol");
    expect(types).toContain("li");
    expect(types.filter((t) => t === "ul").length).toBeGreaterThanOrEqual(2);
    // 代码块
    expect(types).toContain("code_block");
    // 表格
    expect(types).toContain("table");
    expect(types).toContain("tr");
    expect(types).toContain("td");
    // 分隔线
    expect(types).toContain("hr");
    // 链接 element + url
    const links: SpikeNode[] = [];
    const walk = (ns: SpikeNode[]) => {
      for (const n of ns) {
        if (n.type === "a") links.push(n);
        if (n.children) walk(n.children);
      }
    };
    walk(children);
    expect(links.length).toBe(1);
    expect(links[0].url).toBe("https://example.com/docs");
    // marks
    const text = collectText(children);
    expect(text).toContain("bold");
    expect(text).toContain("inline_code");
    const hasMark = (mark: string) =>
      JSON.stringify(children).includes(`"${mark}":true`);
    expect(hasMark("bold")).toBe(true);
    expect(hasMark("italic")).toBe(true);
    expect(hasMark("code")).toBe(true);
    // 代码块语言
    const codeBlocks: SpikeNode[] = [];
    const walkCb = (ns: SpikeNode[]) => {
      for (const n of ns) {
        if (n.type === "code_block") codeBlocks.push(n);
        if (n.children) walkCb(n.children);
      }
    };
    walkCb(children);
    console.log("CODE_BLOCK_NODE", JSON.stringify(codeBlocks[0]));
  });
});

// ---------------------------------------------------------------------------
// B. task list HTML paste
// ---------------------------------------------------------------------------

test.describe("B. task list HTML paste", () => {
  test("contains-task-list HTML real paste — record node shape", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    await dispatchRealPaste(page, {
      html: TASK_LIST_HTML,
      plain: "todo item\ndone item",
    });

    const children = await getChildren(page);
    console.log("TASKLIST_PASTE_TREE", JSON.stringify(children));
    const text = collectText(children);
    // 底线：文本不丢失
    expect(text).toContain("todo item");
    expect(text).toContain("done item");
    // 结构形态记录（不断言具体类型，裁决见 C/D）
    const types = collectTypes(children);
    console.log("TASKLIST_PASTE_TYPES", JSON.stringify(types));
    console.log(
      "TASKLIST_CHECKED_FLAGS",
      JSON.stringify(JSON.stringify(children).match(/"checked":(true|false|null)/g)),
    );
  });
});

// ---------------------------------------------------------------------------
// C/D. task list 裁决：candidate(taskList) vs todo(action_item)
// ---------------------------------------------------------------------------

test.describe("C. task list markdown deserialize", () => {
  test("candidate vs todo variant node shape for '- [ ]' markdown", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    const candidate = await page.evaluate(
      (md) => (window as unknown as PasteSpikeWindow).__pasteSpike!.deserializeMarkdown(md, "candidate"),
      TASK_LIST_MD,
    );
    const todo = await page.evaluate(
      (md) => (window as unknown as PasteSpikeWindow).__pasteSpike!.deserializeMarkdown(md, "todo"),
      TASK_LIST_MD,
    );
    console.log("MD_TASKLIST_CANDIDATE", JSON.stringify(candidate));
    console.log("MD_TASKLIST_TODO", JSON.stringify(todo));
    // 文本底线
    expect(collectText(candidate)).toContain("todo item");
    expect(collectText(todo)).toContain("todo item");
  });
});

test.describe("D. task list html deserialize", () => {
  test("candidate vs todo variant node shape for contains-task-list HTML", async ({
    page,
  }) => {
    await waitForHarnessReady(page);
    const candidate = await page.evaluate(
      (html) => (window as unknown as PasteSpikeWindow).__pasteSpike!.deserializeHtml(html, "candidate"),
      TASK_LIST_HTML,
    );
    const todo = await page.evaluate(
      (html) => (window as unknown as PasteSpikeWindow).__pasteSpike!.deserializeHtml(html, "todo"),
      TASK_LIST_HTML,
    );
    console.log("HTML_TASKLIST_CANDIDATE", JSON.stringify(candidate));
    console.log("HTML_TASKLIST_TODO", JSON.stringify(todo));
    expect(collectText(candidate)).toContain("todo item");
    expect(collectText(todo)).toContain("todo item");
  });
});
