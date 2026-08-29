/**
 * Markdown 输入端预警 lint（非阻断提示）。
 *
 * 目标：在用户粘贴/输入 Markdown 时，前端实时检测**可能**触发
 * `candidate_document_required` 的内容，显示**非阻断**警告 badge。
 *
 * 与后端关系（重要，阶段 3 固化）：
 *   - 前端 lint 是纯启发式正则，**不与后端判定做强制一致承诺**。
 *   - 本模块不拥有安全真相：后端 `markdown_source_parser.py` +
 *     `input_suitability_gate.py` 是安全判定与清洗的单一真相源
 *     （三级分类 silent / adaptation_notice / content_check）。
 *     前端不做任何提交阻断——粘贴与上传入口都只是提示。
 *   - 已知差异：代码块内 `<tag>` 前端正则假阳性、未闭合围栏处理细节等
 *     都属于启发式边界，后端会做权威判定。
 *   - 不再新增规则副本：本文件保持当前 4 类检测，后续若后端规则扩展，
 *     通过文案层弱化提示而非同步复制规则。
 *
 * 启发式参考（仅用于解释当前正则来源，非一致性承诺）：
 *   - Raw HTML: services/api/.../markdown_source_parser.py
 *     - html_block (行 566)
 *     - html_inline (行 860-866)
 *   - Unsafe link: _is_safe_link (行 101-112)
 *     - SAFE_LINK_PROTOCOLS = {"http", "https", "mailto"} (行 54)
 *   - Footnote ref: _has_footnote_ref (行 428-432, 调用点行 878-880)
 *   - Unclosed fence: normalized.count("```") % 2 != 0 (行 534-536)
 *
 * 不变式：
 *   - lint 是纯启发式，不改变 sourceType，不阻塞提交。
 *   - 文案为"含可能进入审核的内容"风格，弱化为提示而非警告。
 *   - `hasDangerousContent` 仅驱动 badge 展示，不得用于提交阻断。
 */

export type MarkdownLintWarningKind =
  | "raw_html"
  | "unsafe_link"
  | "footnote"
  | "unclosed_fence";

export interface MarkdownLintWarning {
  kind: MarkdownLintWarningKind;
  /** 中文消息，与现有 UI 风格一致 */
  message: string;
  /** 出现次数 */
  count: number;
}

export interface MarkdownLintResult {
  warnings: MarkdownLintWarning[];
  hasDangerousContent: boolean;
}

/**
 * 与后端 SAFE_LINK_PROTOCOLS = frozenset({"http", "https", "mailto"}) 对齐。
 * 后端 _is_safe_link 允许无 scheme（相对链接/锚点）。
 */
const SAFE_LINK_SCHEMES = new Set(["http", "https", "mailto"]);

/**
 * 提取 [text](href) 中的 href，返回 { text, href } 数组。
 *
 * 后端 _is_safe_link 用 urlparse(href).scheme 判断协议；前端用相同思路。
 * 不使用全局正则匹配嵌套结构（Markdown 链接语法不允许嵌套括号），
 * 简单匹配第一个 `]` 到对应 `)`。
 */
const LINK_PATTERN = /\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;

/**
 * Footnote reference: `[^id]`，与后端 _has_footnote_ref 检测 footnote_ref
 * token 对齐（markdown-it-footnote 插件语法）。
 */
const FOOTNOTE_REF_PATTERN = /\[\^[^\]]+\]/g;

/**
 * Raw HTML 检测：
 *   - 块级 HTML：行首 <tag...>（与后端 html_block token 对齐）
 *   - Inline HTML：任意位置的 <tag> 或 <tag/>（与后端 html_inline token 对齐）
 *
 * 不匹配 HTML comment / CDATA / declaration（这些 markdown-it 也归为 html_block，
 * 但用户输入场景中极少出现，且不影响 candidate_review 路由判定）。
 */
const BLOCK_HTML_PATTERN = /^[ \t]*<[a-zA-Z][^>]*>/gm;
const INLINE_HTML_PATTERN = /<[a-zA-Z][^>]*>/g;

/**
 * 检测单个 href 是否为不安全协议。
 * 与后端 _is_safe_link 行为一致：
 *   - 空 href → 安全（False，即不报警）
 *   - 无 scheme（相对路径/锚点）→ 安全
 *   - scheme 不在白名单 → 不安全
 */
export function isUnsafeHref(href: string): boolean {
  if (!href) return false;
  // 与后端 urlparse(href).scheme 行为对齐：取 scheme 部分（不区分大小写）。
  // 后端允许无 scheme（相对链接/锚点）；前端用相同判断。
  const schemeMatch = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(href);
  if (!schemeMatch) return false; // 相对路径/锚点，安全
  const scheme = schemeMatch[1]!.toLowerCase();
  return !SAFE_LINK_SCHEMES.has(scheme);
}

/**
 * 启发式检测 Markdown 输入中的危险内容。
 *
 * @param text 用户输入的 Markdown 字符串（来自 MarkdownTextInput.serialize()）
 * @returns warnings 数组 + hasDangerousContent 标志
 */
export function lintMarkdownInput(text: string): MarkdownLintResult {
  const warnings: MarkdownLintWarning[] = [];

  if (!text || text.length === 0) {
    return { warnings, hasDangerousContent: false };
  }

  // 1. Raw HTML（块级 + inline 合并计数，与后端 has_raw_html || has_inline_html 一致）
  const blockHtmlMatches = text.match(BLOCK_HTML_PATTERN) ?? [];
  // 临时移除已识别的块级 HTML 行，避免 inline 正则重复计数同一段
  const textWithoutBlockHtml = text.replace(BLOCK_HTML_PATTERN, "");
  const inlineHtmlMatches =
    textWithoutBlockHtml.match(INLINE_HTML_PATTERN) ?? [];
  const totalHtmlCount = blockHtmlMatches.length + inlineHtmlMatches.length;
  if (totalHtmlCount > 0) {
    warnings.push({
      kind: "raw_html",
      message: `检测到 ${totalHtmlCount} 处原始 HTML 标签`,
      count: totalHtmlCount,
    });
  }

  // 2. Unsafe link（协议不在白名单）
  let unsafeLinkCount = 0;
  let linkMatch: RegExpExecArray | null;
  // 重置 lastIndex（全局正则在 match 后已重置，但 exec 循环需要显式重置）
  LINK_PATTERN.lastIndex = 0;
  while ((linkMatch = LINK_PATTERN.exec(text)) !== null) {
    const href = linkMatch[2] ?? "";
    if (isUnsafeHref(href)) {
      unsafeLinkCount += 1;
    }
  }
  if (unsafeLinkCount > 0) {
    warnings.push({
      kind: "unsafe_link",
      message: `检测到 ${unsafeLinkCount} 个不安全协议链接（javascript/data/vbscript 等）`,
      count: unsafeLinkCount,
    });
  }

  // 3. Footnote reference
  const footnoteMatches = text.match(FOOTNOTE_REF_PATTERN) ?? [];
  if (footnoteMatches.length > 0) {
    warnings.push({
      kind: "footnote",
      message: `检测到 ${footnoteMatches.length} 处脚注引用（[^id]）`,
      count: footnoteMatches.length,
    });
  }

  // 4. Unclosed fence（``` 出现次数为奇数）
  const fenceCount = (text.match(/```/g) ?? []).length;
  if (fenceCount % 2 !== 0) {
    warnings.push({
      kind: "unclosed_fence",
      message: "检测到未闭合的代码围栏（``` 出现奇数次）",
      count: 1,
    });
  }

  return {
    warnings,
    // Footnotes are structurally lossy today, but they are not unsafe.
    // Keep the warning visible and let the backend route the document to
    // Candidate Review. Only diagnostics that must never be submitted are
    // blocking at the Web boundary.
    hasDangerousContent: warnings.some(
      (warning) => warning.kind !== "footnote",
    ),
  };
}

/**
 * 把 warnings 数组渲染成单条中文摘要文案（用于警告 badge）。
 *
 * 纯提示文案从"提交后将进入审核流程"（阻塞式语气）
 * 改为"含可能进入审核的内容"（提示式语气），强调前端只是预警、
 * 后端才是 fail-closed 单一真相源。
 *
 * 例："[raw_html: 2, unsafe_link: 1]" →
 *     "检测到 2 处原始 HTML 标签、检测到 1 个不安全协议链接（javascript/data/vbscript 等），含可能进入审核的内容"
 */
export function summarizeLintWarnings(warnings: MarkdownLintWarning[]): string {
  if (warnings.length === 0) return "";
  const parts = warnings.map((w) => w.message);
  return `${parts.join("、")}，含可能进入审核的内容`;
}
