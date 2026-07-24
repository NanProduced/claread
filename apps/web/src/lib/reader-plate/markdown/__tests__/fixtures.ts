/**
 * Phase 2 / P1: 双 Parser Round-trip 一致性测试 fixtures。
 *
 * 从后端 services/api/tests/fixtures/markdown_structured_source/ 同步而来
 * （手动复制，避免引入跨语言构建依赖）。后端 fixtures 是 G0 frozen 只读
 * 真值源，本文件仅提炼前端可对比的关键维度（顶层 block type 序列 +
 * heading level + code_block language + 顶层 block 数量）。
 *
 * 后端 block_type → 前端 Plate type 映射：
 *   paragraph       → p
 *   heading         → h1~h6 (level in payload_json)
 *   blockquote      → blockquote
 *   list (ordered=F) → ul
 *   list (ordered=T) → ol
 *   code_block      → code_block
 *   table           → table
 *   thematic_break  → hr
 *   footnote        → 无对应（前端 Plate 不支持 footnote 插件，会作为 paragraph）
 *
 * 已知差异（不 hard fail，记录为 soft-skip）：
 *   - footnote: 后端产出 block_type="footnote"，前端无 footnote 插件
 *     会作为 paragraph 处理（[^1] 保留为文本）
 *   - raw_html: 后端 html_block/html_inline 不作为 first-class block type
 *     （归入 paragraph.text_content + 诊断警告），前端 Plate 行为依赖
 *     MarkdownKit 配置，可能保留为文本或剥离标签
 *   - unsafe_link: 后端剥离 unsafe 协议链接保留 link text，
 *     前端 Plate 行为依赖 remarkGfm 配置
 *   - unclosed_fence: 后端捕获为 code_block（closed=False），
 *     前端 Plate 可能将剩余内容全部作为 code_block 或 paragraph
 *
 * 这些差异是启发式双 parser 架构的已知边界，本测试目标是建立可持续运行
 * 的对比基线，不是一次性修复所有差异。
 */

export interface ExpectedTopLevelBlock {
  /** 后端 block_type（用于溯源），前端映射后用 expectedPlateType 对比 */
  backendBlockType: string;
  /** 期望前端 Plate 的 type 字段（已做映射） */
  expectedPlateType: string;
  /** heading level（仅 heading 有） */
  level?: number;
  /** code_block language（仅 code_block 有） */
  language?: string;
}

export interface ParityFixture {
  /** fixture 名称（与后端目录名对齐） */
  name: string;
  /** fixture 描述（来自后端 expected_blocks.json description） */
  description: string;
  /** Markdown 输入（同步自后端 input.md） */
  input: string;
  /** 期望的顶层 block 序列（按 order_index 排序，仅 parent_block_id=null） */
  expectedTopLevel: ExpectedTopLevelBlock[];
  /**
   * 是否跳过该 fixture 的硬断言（true 时只 console.warn 差异，不 fail）。
   * 已知 parser 差异的 fixture 设为 true。
   */
  softSkip?: boolean;
  /** 跳过原因（softSkip=true 时必填） */
  skipReason?: string;
}

export const PARITY_FIXTURES: ParityFixture[] = [
  {
    name: "simple_paragraph",
    description: "Baseline single-paragraph fixture — the simplest stable-document-ready input.",
    input: "This is a simple paragraph for baseline testing.\n",
    expectedTopLevel: [
      { backendBlockType: "paragraph", expectedPlateType: "p" },
    ],
  },
  {
    name: "r14_complex",
    description:
      "Complex article: multi-level heading, nested ordered+unordered list, GFM table, fenced code, blockquote, thematic break, emphasis/strong/strikethrough/inline code/link mixed.",
    input: `# Article Title

## Introduction

The article covers **multiple** topics including \`inline code\` and [a link](https://example.com).

### Subsection

- First item
- Second item with *emphasis*
  1. Nested ordered
  2. Another nested item

| Col A | Col B |
|-------|:-----:|
| 1     | 2     |

\`\`\`python
def hello():
    print("hi")
\`\`\`

> A blockquote with ~~strikethrough~~.

---

Final paragraph with \`code\` and **bold**.
`,
    expectedTopLevel: [
      { backendBlockType: "heading", expectedPlateType: "h1", level: 1 },
      { backendBlockType: "heading", expectedPlateType: "h2", level: 2 },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
      { backendBlockType: "heading", expectedPlateType: "h3", level: 3 },
      { backendBlockType: "list", expectedPlateType: "ul" },
      { backendBlockType: "table", expectedPlateType: "table" },
      { backendBlockType: "code_block", expectedPlateType: "code_block", language: "python" },
      { backendBlockType: "blockquote", expectedPlateType: "blockquote" },
      { backendBlockType: "thematic_break", expectedPlateType: "hr" },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
    ],
  },
  {
    name: "code_mermaid",
    description: "Fenced code blocks including a mermaid diagram block (static, not executed).",
    input: `# Diagram

Some intro text.

\`\`\`mermaid
graph TD
    A --> B
    B --> C
\`\`\`

\`\`\`python
x = 1
\`\`\`

Final paragraph.
`,
    expectedTopLevel: [
      { backendBlockType: "heading", expectedPlateType: "h1", level: 1 },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
      { backendBlockType: "code_block", expectedPlateType: "code_block", language: "mermaid" },
      { backendBlockType: "code_block", expectedPlateType: "code_block", language: "python" },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
    ],
  },
  {
    name: "footnote",
    description: "Footnote definition and reference — first phase routes to candidate with warning.",
    input: `# Document

This has a footnote[^1] reference.

[^1]: This is the footnote definition.
`,
    expectedTopLevel: [
      { backendBlockType: "heading", expectedPlateType: "h1", level: 1 },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
      // 后端 block_type="footnote"；前端 Plate 无 footnote 插件，会作为 paragraph 处理
      { backendBlockType: "footnote", expectedPlateType: "p" },
    ],
    softSkip: true,
    skipReason:
      "前端 Plate 无 footnote 插件，[^1] 引用会保留为文本，[^1]: 定义会被解析为 paragraph；后端产出 block_type=footnote。这是已知双 parser 差异。",
  },
  {
    name: "gfm_table",
    description: "Standard GFM table with alignment separator row (left, center, right).",
    input: `| Name  | Age | City   |
|:------|:---:|-------:|
| Bob   | 30  | NYC    |
| Anna  | 25  | London |
`,
    expectedTopLevel: [
      { backendBlockType: "table", expectedPlateType: "table" },
    ],
  },
  {
    name: "nested_list",
    description: "3-level nested ordered + unordered list exercising parent_block_id depth chain.",
    input: `- Level 1 unordered item A
  - Level 2 unordered item B
    - Level 3 unordered item C
  1. Level 2 ordered item D
      1. Level 3 ordered item E
- Another top level item F
`,
    expectedTopLevel: [
      { backendBlockType: "list", expectedPlateType: "ul" },
    ],
  },
  {
    name: "raw_html",
    description: "Raw HTML block + inline HTML — first phase extracts text, routes to candidate.",
    input: `<div class="note">

This is inside HTML.

</div>

A paragraph with <span style="color:red">inline HTML</span>.

<b>Bold via HTML</b>
`,
    // 后端把 html_block 归入 paragraph.text_content（剥离标签），inline html 也归入 paragraph
    // 前端 Plate 行为依赖 MarkdownKit 配置；测试记录差异但不硬断言
    expectedTopLevel: [
      // 后端实际产出：3 个 paragraph（html_block 剥离后为空 text 不产出 block，
      // "This is inside HTML." 作为 paragraph，inline html paragraph，<b> 剥离后 "Bold via HTML" paragraph）
      { backendBlockType: "paragraph", expectedPlateType: "p" },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
    ],
    softSkip: true,
    skipReason:
      "前端 Plate 对 raw HTML 的处理与后端 markdown-it 不同（依赖 MarkdownKit 配置）；后端剥离标签归入 paragraph.text_content。这是已知双 parser 差异。",
  },
  {
    name: "real_list_wrapper",
    description: "Article with intro paragraph + unordered list + ordered list + closing paragraph.",
    input: `# Real List Wrapper Article

This article opens with a short paragraph before the first list.

- First unordered item
- Second unordered item
- Third unordered item

1. First ordered item
2. Second ordered item
3. Third ordered item

Closing paragraph that follows the lists.
`,
    expectedTopLevel: [
      { backendBlockType: "heading", expectedPlateType: "h1", level: 1 },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
      { backendBlockType: "list", expectedPlateType: "ul" },
      { backendBlockType: "list", expectedPlateType: "ol" },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
    ],
  },
  {
    name: "reject_empty",
    description: "Code-only input — code-dominant with no narrative blocks; rejected from stable document freeze.",
    input: `\`\`\`python
def foo():
    pass

class Bar:
    def baz(self):
        return 42

if __name__ == "__main__":
    foo()
\`\`\`
`,
    expectedTopLevel: [
      { backendBlockType: "code_block", expectedPlateType: "code_block", language: "python" },
    ],
  },
  {
    name: "unclosed_fence",
    description: "Unclosed fenced code block — captured as code_block but requires candidate review.",
    input: `# Unclosed Fence

Some text.

\`\`\`python
def unclosed():
    return "no closing fence"
`,
    expectedTopLevel: [
      { backendBlockType: "heading", expectedPlateType: "h1", level: 1 },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
      // 后端产出 code_block (closed=False)；前端 Plate 可能将剩余内容全部作为 code_block
      { backendBlockType: "code_block", expectedPlateType: "code_block", language: "python" },
    ],
    softSkip: true,
    skipReason:
      "前端 Plate 对未闭合 fence 的处理与后端不同；后端捕获为 code_block (closed=False)，前端可能将剩余内容全部作为 code_block 或拆分为多个 block。这是已知双 parser 差异。",
  },
  {
    name: "unsafe_link",
    description: "Unsafe-protocol links (javascript/data/vbscript) — stripped, link text preserved.",
    input: `# Unsafe Links

A [javascript link](javascript:alert(1)) and a [data link](data:text/html,<script>alert(1)</script>) and a [vbscript link](vbscript:msgbox(1)).

A safe [https link](https://example.com) and [mailto](mailto:test@example.com).
`,
    expectedTopLevel: [
      { backendBlockType: "heading", expectedPlateType: "h1", level: 1 },
      // 后端剥离 unsafe link 协议保留 link text，归入 paragraph
      { backendBlockType: "paragraph", expectedPlateType: "p" },
      { backendBlockType: "paragraph", expectedPlateType: "p" },
    ],
    softSkip: true,
    skipReason:
      "前端 Plate 对 unsafe 协议链接的处理与后端不同（依赖 remarkGfm 配置）；后端剥离协议保留 link text。这是已知双 parser 差异。",
  },
];
