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
