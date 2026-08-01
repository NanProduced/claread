/**
 * L1 输入端 baseline fixtures — 粘贴/输入入口的可执行 fixture 集。
 *
 * 覆盖：raw Markdown、text/html+text/plain 双格式 clipboard、Notion
 * callout HTML、Word 风格 HTML、普通/危险链接、aside、table、task
 * list、image、footnote、未闭合 fence、vector<T>、30k+ 长文（程序生成）。
 *
 * 仅供 e2e spec import；不进生产 bundle。
 */

// ---------------------------------------------------------------------------
// Raw Markdown
// ---------------------------------------------------------------------------

export const RICH_MARKDOWN = `# Quarterly Report

Opening paragraph with **bold**, *italic*, \`inline_code\` and a [link](https://example.com/r).

> A quoted insight.

- outer one
  - nested alpha
  - nested beta
- outer two

1. first
2. second

\`\`\`python
def f():
    return 1
\`\`\`

| Name | Value |
| ---- | ----- |
| a    | 1     |

---

tail paragraph
`;

// ---------------------------------------------------------------------------
// text/html + text/plain 双格式 clipboard
// ---------------------------------------------------------------------------

export const RICH_HTML = `
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

export const RICH_HTML_PLAIN =
  "Title One\n\nSection Two\n\nPlain paragraph with bold, italic, inline_code and a link.\n\ntail";

// ---------------------------------------------------------------------------
// Notion callout（<aside> 与 Notion 导出两种表示）
// ---------------------------------------------------------------------------

export const NOTION_CALLOUT_ASIDE_HTML = `
<p>before</p>
<aside>
  <div>💡</div>
  <div>callout body with <strong>bold</strong> content</div>
</aside>
<p>after</p>
`.trim();

export const NOTION_CALLOUT_EXPORT_HTML = `
<div class="notion-callout">
  <div>📌</div>
  <div>exported callout content</div>
</div>
`.trim();

/**
 * Complete Notion dual-MIME payload. HTML carries the surrounding rich
 * structure but exposes the callout delimiters as escaped block text; the
 * companion plain representation carries the same article and a paired
 * Markdown aside.
 */
export const NOTION_CALLOUT_DUAL_MIME_HTML = `
<h2>Reader Goals</h2>
<p>Opening with <strong>strong</strong> and <em>em</em> structure.</p>
<ul><li>first point</li><li>second point</li></ul>
<p>Read the <a href="https://example.com/guide">safe link</a>.</p>
<h3>Reference list</h3>
<ol><li><a href="https://example.com/reference">Reference A</a></li><li>Reference B</li></ol>
<table><thead><tr><th>Source</th><th>Meaning</th></tr></thead><tbody><tr><td>article</td><td>rich structure</td></tr><tr><td>callout</td><td>plain semantic fallback</td></tr></tbody></table>
<p>&lt;aside&gt;</p>
<p>🎯</p>
<p><strong>Alignment</strong>: preserve the <a href="https://example.com/first">first callout link</a>.</p>
<ul><li>Read the <a href="https://example.com/first-list">first list link</a><ul><li>Nested first detail</li></ul></li><li><em>Keep</em> the first list explicit.</li></ul>
<p>&lt;/aside&gt;</p>
<p>&lt;aside&gt;</p>
<p>⚠️</p>
<p><em>Warning</em>: preserve the <a href="https://example.com/second">second callout link</a>.</p>
<ol><li>First warning item<ul><li>Nested warning detail</li></ul></li><li><strong>Second warning item</strong></li></ol>
<p>&lt;/aside&gt;</p>
<p>Trailing paragraph remains independent.</p>
`.trim();

export const NOTION_CALLOUT_DUAL_MIME_PLAIN = `## Reader Goals

Opening with **strong** and *em* structure.

- first point
- second point

Read the [safe link](https://example.com/guide).

### Reference list

1. [Reference A](https://example.com/reference)
2. Reference B

| Source | Meaning |
| --- | --- |
| article | rich structure |
| callout | plain semantic fallback |

<aside>
🎯

**Alignment**: preserve the [first callout link](https://example.com/first).

- Read the [first list link](https://example.com/first-list)
  - Nested first detail
- *Keep* the first list explicit.
</aside>

<aside>
⚠️

*Warning*: preserve the [second callout link](https://example.com/second).

1. First warning item
   - Nested warning detail
2. **Second warning item**

</aside>

Trailing paragraph remains independent.`;

// ---------------------------------------------------------------------------
// Word 风格 HTML（mso 样式 + 条件注释残渣）
// ---------------------------------------------------------------------------

export const WORD_HTML = `
<p class="MsoNormal" style="mso-margin-top-alt:auto"><b>Word bold heading</b></p>
<p class="MsoListParagraph" style="text-indent:-18.0pt">· Word bullet one</p>
<p class="MsoNormal"><i>word italic</i> and <u>underline</u></p>
`.trim();

// ---------------------------------------------------------------------------
// 链接：普通 + 危险
// ---------------------------------------------------------------------------

export const LINKS_HTML = `
<p><a href="https://example.com/safe">safe link</a></p>
<p><a href="javascript:alert(document.cookie)">danger js link</a></p>
<p><a href="data:text/html;base64,PHNjcmlwdD4=">danger data link</a></p>
`.trim();

// ---------------------------------------------------------------------------
// 危险 HTML（script/iframe/on*）
// ---------------------------------------------------------------------------

export const MALICIOUS_HTML = `
<p onclick="steal()">visible text</p>
<script>window.__pwned = true</script>
<iframe src="https://evil.example/x"></iframe>
<img src="x" onerror="boom()">
`.trim();

// ---------------------------------------------------------------------------
// aside / table / task list / image / footnote
// ---------------------------------------------------------------------------

export const ASIDE_HTML = `<aside><p>plain aside note</p></aside>`;

// ---------------------------------------------------------------------------
// Source Callout — 纯 Markdown <aside>、带 class 的 <aside>、GFM alert
// ---------------------------------------------------------------------------

/** 纯 Markdown text/plain 粘贴的 <aside> 块（canonical 表达）。 */
export const ASIDE_MARKDOWN_PLAIN = `<aside>
This is a source callout with **bold** text.
</aside>`;

/** 纯 Markdown 多段落 <aside>（含空行，测试 remarkMergeAsideHtml）。 */
export const ASIDE_MARKDOWN_MULTIPARA = `<aside>
First paragraph in callout.

Second paragraph in callout.
</aside>`;

/** 带 class 的 <aside> HTML（剪贴板 text/html 粘贴，kind=warning）。 */
export const ASIDE_HTML_WITH_CLASS = `<aside class="callout-warning"><p>Warning callout body</p></aside>`;

/** GFM alert Markdown（text/plain 粘贴 `> [!NOTE]`）。 */
export const GFM_ALERT_NOTE_MD = `> [!NOTE]
> This is a note callout
> with multiple lines`;

/** GFM alert WARNING Markdown。 */
export const GFM_ALERT_WARNING_MD = `> [!WARNING]
> Be careful with this approach`;

/** 转义的 \\<aside> Markdown（历史数据回归）。 */
export const ESCAPED_ASIDE_MD = `\\<aside>This is literal\\</aside>`;

/** 不完整 <aside>（无闭合标签）。 */
export const UNCLOSED_ASIDE_MD = `<aside>No closing tag here`;

/** R-Aside-1R A1: `</aside>` 后紧接正文 — callout 与后续段落边界测试。 */
export const ASIDE_WITH_TRAILING_TEXT_MD = `<aside>
**Alignment**: this is a callout body.

Second paragraph inside callout.
</aside>Peer discussion continues here`;

/** 危险 <aside>（带 event handler 属性，测试安全降级）。 */
export const DANGEROUS_ASIDE_HTML = `<aside onclick="steal()" class="callout-note"><p>safe content</p></aside>`;

export const TABLE_HTML = `
<table>
  <tr><th>H1</th><th>H2</th></tr>
  <tr><td>c1</td><td>c2</td></tr>
  <tr><td>c3</td><td>c4</td></tr>
</table>
`.trim();

export const TASK_LIST_MD = `- [ ] todo one\n- [x] done two\n`;

export const TASK_LIST_HTML = `
<ul class="contains-task-list">
<li class="task-list-item"><input type="checkbox" disabled> todo item</li>
<li class="task-list-item"><input type="checkbox" checked disabled> done item</li>
</ul>
`.trim();

export const IMAGE_MD = `![diagram](https://example.com/d.png)`;

export const IMAGE_HTML = `<p>fig:</p><img src="https://example.com/d.png" alt="diagram">`;

export const FOOTNOTE_MD = `Body text.[^1]\n\n[^1]: The footnote body.\n`;

// ---------------------------------------------------------------------------
// 边界输入
// ---------------------------------------------------------------------------

export const UNCLOSED_FENCE_MD = `# Doc

\`\`\`python
def never_closed():
    return "fence stays open"
`;

export const VECTOR_MD = `Use std::vector<T> and std::unordered_map<K, V> carefully.`;

// ---------------------------------------------------------------------------
// 30k+ 长文（程序生成）
// ---------------------------------------------------------------------------

export function makeLongMarkdown(minLength = 30_000): string {
  const para =
    "Reading at scale requires structure: headings, lists, tables, code, and quotes all carry meaning that a plain textarea silently destroys. ";
  const block = `## Section\n\n${para}${para}\n\n- point one\n- point two\n\n> quoted line\n\n`;
  const parts: string[] = ["# Long Document\n"];
  let length = parts[0].length;
  let i = 0;
  while (length < minLength) {
    const b = block.replace("## Section", `## Section ${i}`);
    parts.push(b);
    length += b.length;
    i += 1;
  }
  return parts.join("\n");
}
