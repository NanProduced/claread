import { isSafeCalloutEmoji } from "./source-callout-display-icon";

/**
 * Source Callout 共享适配器 — 受限、安全的 `<aside>` 识别与归一化。
 *
 * 纯 Markdown 路径（remark plugin）与富 HTML 剪贴板路径（Plate HTML
 * deserializer）都经过本模块，确保两条路径产生相同的 `source_callout`
 * 语义结构，不各自发明不同的数据表示。
 *
 * 安全合同：
 * - 仅识别完整、块级、成对的 `<aside>...</aside>`；不完整 aside、转义
 *   `\<aside>`、普通 `<div>` / `<script>` 不被匹配。
 * - 不执行、不透传 event handler / style / script / iframe / 危险 URL
 *   等属性。仅提取 `class` 用于 callout kind 推断，其余属性全部丢弃。
 * - callout 内基础 inline/block 格式（粗体、斜体、链接、段落等）由
 *   remark-parse / Plate HTML deserializer 正常解析保留。
 *
 * Canonical Markdown 表达：
 * - 序列化方向：`<aside>\n{inner markdown}\n</aside>`（块级 raw HTML）
 * - 反序列化方向：remark-parse 将 `<aside>` 识别为 mdast `html` 节点，
 *   remark-source-callout 插件将其转换为带 `data.hName="aside"` 标记的
 *   blockquote，最终由 Plate rule 归一为 `source_callout` element。
 */

/** 已知 callout kind 与对应的 GFM alert marker / 图标。 */
export const CALLOUT_KINDS = [
  "note",
  "tip",
  "important",
  "warning",
  "abstract",
  "info",
] as const;

export type CalloutKind = (typeof CALLOUT_KINDS)[number];

export const DEFAULT_CALLOUT_KIND: CalloutKind = "note";

/** callout kind → 图标 emoji（Notion 风格）。 */
export const CALLOUT_ICONS: Record<CalloutKind, string> = {
  note: "💡",
  tip: "💡",
  important: "❗",
  warning: "⚠️",
  abstract: "📝",
  info: "ℹ️",
};

/**
 * callout kind → Notion 风格 CSS classes。
 * 淡色表面、明确边界、非斜体正文。
 */
export const CALLOUT_CSS_CLASSES: Record<CalloutKind, string> = {
  note: "border-amber-200/60 bg-amber-50/80 dark:border-amber-400/20 dark:bg-amber-950/30",
  tip: "border-emerald-200/60 bg-emerald-50/80 dark:border-emerald-400/20 dark:bg-emerald-950/30",
  important: "border-rose-200/60 bg-rose-50/80 dark:border-rose-400/20 dark:bg-rose-950/30",
  warning: "border-orange-200/60 bg-orange-50/80 dark:border-orange-400/20 dark:bg-orange-950/30",
  abstract: "border-violet-200/60 bg-violet-50/80 dark:border-violet-400/20 dark:bg-violet-950/30",
  info: "border-sky-200/60 bg-sky-50/80 dark:border-sky-400/20 dark:bg-sky-950/30",
};

/** callout kind → 图标颜色。 */
export const CALLOUT_ICON_COLORS: Record<CalloutKind, string> = {
  note: "text-amber-600 dark:text-amber-400",
  tip: "text-emerald-600 dark:text-emerald-400",
  important: "text-rose-600 dark:text-rose-400",
  warning: "text-orange-600 dark:text-orange-400",
  abstract: "text-violet-600 dark:text-violet-400",
  info: "text-sky-600 dark:text-sky-400",
};

/**
 * GFM alert marker → callout kind 映射。
 * 用于从 `> [!NOTE]` 等 GFM alert 标记推断 callout kind。
 */
const GFM_ALERT_TO_KIND: Record<string, CalloutKind> = {
  NOTE: "note",
  TIP: "tip",
  IMPORTANT: "important",
  WARNING: "warning",
  CAUTION: "warning",
  ABSTRACT: "abstract",
  INFO: "info",
};

/**
 * class 名 → callout kind 推断。
 * 支持 `callout-warning` / `notion-callout_tip` / `warning` 等变体。
 */
const CLASS_KIND_PATTERNS: ReadonlyArray<readonly [RegExp, CalloutKind]> = [
  [/warning|caution|danger/i, "warning"],
  [/tip|hint|succeed/i, "tip"],
  [/important|critical/i, "important"],
  [/abstract|summary|tl;?dr/i, "abstract"],
  [/info/i, "info"],
  [/note/i, "note"],
];

/**
 * 匹配完整块级 `<aside>...</aside>` 的正则。
 *
 * 要求：
 * - `<aside` 后跟可选属性（仅用于 kind 推断，不透传），然后 `>`
 * - 任意内容（非贪婪）
 * - `</aside>` 结束
 * - `</aside>` 后可跟任意尾随文本（captured as group 3）
 *
 * 不匹配：
 * - `\<aside>` (转义)
 * - `<aside>` 无闭合
 * - `<div>` / `<script>` 等其他标签
 * - 行内 `<aside>text</aside>` 混在段落中间（由 remark-parse 保证块级 HTML 独立成 html 节点）
 *
 * R-Aside-1R A1: 移除结尾 `\s*$` 锚定，允许 `</aside>` 后紧接正文。
 * 尾随正文由 matchAsideBlock 返回 trailingContent 字段，由 remarkMergeAsideHtml
 * 拆分为独立段落节点。
 */
const ASIDE_BLOCK_RE = /^<aside(\s+[^>]*)?>([\s\S]*?)<\/aside>([\s\S]*)$/i;

/**
 * 从 `<aside>` 标签的属性字符串中提取 class 值。
 * 仅提取 class（或 React 风格 className，mdast/Plate 处理可能转换），
 * 其余属性全部丢弃（安全降级）。
 */
function extractClassFromAttrs(attrs: string | undefined): string {
  if (!attrs) return "";
  const match = attrs.match(/class(?:Name)?\s*=\s*["']([^"']*)["']/i);
  return match ? match[1] : "";
}

/**
 * 从 class 名推断 callout kind。
 * 未知 class 默认为 `note`。
 */
export function classifyCalloutKind(className: string): CalloutKind {
  for (const [pattern, kind] of CLASS_KIND_PATTERNS) {
    if (pattern.test(className)) {
      return kind;
    }
  }
  return DEFAULT_CALLOUT_KIND;
}

/**
 * 从 GFM alert marker（如 `[!NOTE]`）推断 callout kind。
 * 未知 marker 默认为 `note`。
 */
export function classifyCalloutKindFromGfmMarker(marker: string): CalloutKind {
  const normalized = marker.trim().toUpperCase();
  return GFM_ALERT_TO_KIND[normalized] ?? DEFAULT_CALLOUT_KIND;
}

/**
 * 检查 GFM alert marker 正则（与后端 `_GFM_ALERT_MARKER_RE` 对齐）。
 * 严格匹配：整个文本就是 marker 本身（允许前后空白）。
 */
export const GFM_ALERT_MARKER_RE = /^\s*\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|ABSTRACT|INFO)\]\s*$/i;

/**
 * 从文本开头提取 GFM alert marker。
 *
 * 与 `GFM_ALERT_MARKER_RE` 的区别：本函数只要求 marker 出现在文本开头
 * （允许后跟空白或换行再接其他内容），因为 remark-parse 可能把 marker
 * 行和后续内容合并到同一个 text 节点（如 `[!NOTE]\nContent`）。
 *
 * 输入如 `[!NOTE]` 或 `[!NOTE]\nContent`，返回 `NOTE`。
 * 不匹配（如 `[!NOTA]`、`regular text`）返回 null。
 *
 * 安全约束：marker 后必须紧跟空白或字符串结束，避免匹配 `[!NOTE]text`
 * 这类伪 marker。
 */
export function extractGfmAlertMarker(text: string): string | null {
  const match = text.match(
    /^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION|ABSTRACT|INFO)\](?=\s|$)/i,
  );
  return match ? match[1].toUpperCase() : null;
}

/**
 * 从文本中移除开头的 GFM alert marker（含尾部空白）。
 * 返回移除 marker 后的剩余文本。如果不包含 marker，返回原文本。
 */
export function stripGfmAlertMarker(text: string): string {
  return text.replace(
    /^\s*\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|ABSTRACT|INFO)\]\s*/,
    "",
  );
}

/**
 * 尝试将一个 mdast `html` 节点的 value 匹配为 `<aside>...</aside>` 块。
 *
 * 匹配成功返回 `{ kind, innerContent, trailingContent }`：
 * - `kind`: callout kind（从 class 推断，默认 note）
 * - `innerContent`: `<aside>` 标签内的原始文本（需后续解析为 markdown）
 * - `trailingContent`: `</aside>` 后的尾随正文（已 trim；空字符串表示无尾随）
 *
 * 不匹配返回 `null`（包括非 aside 标签、不完整 aside、转义 aside）。
 *
 * R-Aside-1R A1: 返回 trailingContent 以支持 `</aside>Peer discussion...` 这类
 * 真实输入。remarkMergeAsideHtml 会在 remark 阶段将尾随文本拆分为独立段落节点，
 * 确保 html deserialize 规则只看到纯净的 `<aside>...</aside>`。
 */
export function matchAsideBlock(
  htmlValue: string,
): { kind: CalloutKind; innerContent: string; trailingContent: string } | null {
  const match = htmlValue.match(ASIDE_BLOCK_RE);
  if (!match) return null;
  const [, attrs, innerContent, trailing] = match;
  const className = extractClassFromAttrs(attrs);
  const kind = classifyCalloutKind(className);
  return {
    kind,
    innerContent: innerContent.trim(),
    trailingContent: (trailing ?? "").trim(),
  };
}

/**
 * 构建 canonical `<aside>` Markdown 字符串。
 *
 * 序列化方向使用：将 callout 的内部 markdown 内容包裹在 `<aside>` 标签中。
 * 输出格式：`<aside>\n{innerMarkdown}\n</aside>`
 *
 * 这是 source callout 的唯一 canonical Markdown 表达，确保：
 * - Confirmed Source 中存储的是此格式
 * - 后端 markdown_source_parser 识别为 `html_aside` hint
 * - 重新解析时 remark-source-callout 再次识别为 source_callout
 * - 可逆 round-trip
 */
export function buildCanonicalAsideMarkdown(
  innerMarkdown: string,
  kind?: CalloutKind,
  displayIcon?: string | null,
): string {
  void kind;
  const trimmed = innerMarkdown.trim();
  const safeIcon =
    typeof displayIcon === "string" && isSafeCalloutEmoji(displayIcon)
      ? displayIcon
      : null;
  const canonicalInner = safeIcon
    ? trimmed
      ? `${safeIcon}\n\n${trimmed}`
      : safeIcon
    : trimmed;
  // 注意：canonical 表达不携带 kind 属性。
  // kind 在输入侧由 class/GFM marker 推断后保留在 Plate element data 中，
  // 但 canonical markdown 只用裸 `<aside>` 标签，不序列化 kind。
  // 这确保 canonical 表达最小化、稳定、可逆。
  // 后端 classifier 对所有 `<aside>` 统一分类为 source_callout（T-only），
  // 不因 kind 不同而产生不同 policy。
  if (!canonicalInner) {
    return "<aside>\n\n</aside>";
  }
  return `<aside>\n${canonicalInner}\n</aside>`;
}

/**
 * 安全属性白名单：仅允许 class（用于 kind 推断）。
 * 其他所有属性（onclick、style、href、src 等）一律丢弃。
 */
export const SAFE_ASIDE_ATTRS = new Set(["class"]);

/**
 * 清洗 `<aside>` DOM 元素的属性，仅保留 class。
 * 返回 sanitized class 字符串（可能为空）。
 */
export function sanitizeAsideElement(el: Element): string {
  const className = el.getAttribute("class") ?? "";
  // 移除所有属性
  for (const attr of Array.from(el.attributes)) {
    el.removeAttribute(attr.name);
  }
  return className;
}

// ---------------------------------------------------------------------------
// remarkMergeAsideHtml — 合并被 commonmark 空行拆分的 <aside> html 节点
// ---------------------------------------------------------------------------

/**
 * mdast 节点的最小类型（仅用于 remark 插件内部）。
 *
 * R-Aside-1R A2: 新增 `_asideChildren` 自定义属性，用于携带 aside 内部的
 * 原始 mdast 子节点。这避免了将子节点序列化为 markdown 字符串再重新解析
 * 的损失性 round-trip（旧实现使用 `serializeMdastNodeToMarkdown`，会压平
 * 表格、嵌套列表、复杂 inline marks 等结构）。
 */
interface MdastNode {
  type: string;
  value?: string;
  url?: string;
  alt?: string | null;
  children?: MdastNode[];
  ordered?: boolean;
  start?: number | null;
  depth?: number;
  checked?: boolean | null;
  /**
   * aside 合并专用：携带 aside 内部的原始 mdast 子节点。
   * html deserialize 规则优先使用此属性，避免重新解析 markdown 字符串。
   * 仅由 remarkMergeAsideHtml 设置，不参与 mdast 规范。
   */
  _asideChildren?: MdastNode[];
}

/**
 * 判断 html 节点的 value 是否包含未闭合的 `<aside>` 开标签。
 * 匹配 `<aside>` 或 `<aside ...>`，但不匹配已包含 `</aside>` 的节点。
 */
function isOpeningAsideHtml(node: MdastNode): boolean {
  if (node.type !== "html") return false;
  const value = node.value ?? "";
  return /<aside[\s>]/i.test(value) && !/<\/aside>/i.test(value);
}

/**
 * 判断 html 节点的 value 是否同时包含 `<aside>` 开标签和 `</aside>` 闭标签。
 * 用于检测单个 html 节点中完整的 aside（可能带尾随正文）。
 */
function isCompleteAsideHtml(node: MdastNode): boolean {
  if (node.type !== "html") return false;
  const value = node.value ?? "";
  return /<aside[\s>]/i.test(value) && /<\/aside>/i.test(value);
}

/**
 * 从包含 `</aside>` 的 html 值中拆分闭标签和尾随正文。
 *
 * 输入 `</aside>Peer discussion...` → `{ closePart: "</aside>", trailingText: "Peer discussion..." }`
 * 输入 `</aside>` → `{ closePart: "</aside>", trailingText: "" }`
 *
 * R-Aside-1R A1: 闭标签后紧接的同一行正文必须成为后续普通段落，绝不能吞入 callout。
 */
function splitClosingAside(value: string): { closePart: string; trailingText: string } {
  const match = value.match(/^([\s\S]*<\/aside>)([\s\S]*)$/i);
  if (!match) {
    return { closePart: value, trailingText: "" };
  }
  return { closePart: match[1], trailingText: match[2].trim() };
}

/**
 * 从单个包含完整 `<aside>...</aside>` 的 html 节点中拆分 aside 部分和尾随正文。
 *
 * 输入 `<aside>content</aside>trailing` → `{ asideNode: html("<aside>content</aside>"), trailingNode: paragraph("trailing") }`
 * 输入 `<aside>content</aside>` → `{ asideNode: html("<aside>content</aside>"), trailingNode: null }`
 *
 * 仅当存在尾随正文时才拆分；无尾随正文时返回 null（节点保持原样）。
 */
function splitCompleteAsideWithTrailing(
  node: MdastNode,
): { asideNode: MdastNode; trailingNode: MdastNode | null } | null {
  const value = node.value ?? "";
  const match = value.match(/^(<aside[\s\S]*?<\/aside>)([\s\S]+)$/i);
  if (!match) return null;
  const asidePart = match[1];
  const trailingText = match[2].trim();
  if (!trailingText) return null;

  const asideNode: MdastNode = { type: "html", value: asidePart };
  const trailingNode: MdastNode = {
    type: "paragraph",
    children: [{ type: "text", value: trailingText }],
  };
  return { asideNode, trailingNode };
}

/**
 * 尝试从 `opening` 节点开始，向后扫描 siblings，合并直到 `</aside>` 的
 * 所有节点为一个 html 节点。
 *
 * R-Aside-1R A2: 中间节点（paragraph / list / blockquote / code / heading）
 * 直接作为 mdast 子节点携带在 `_asideChildren` 中，不再序列化为 markdown
 * 字符串。这保留了所有 inline marks（bold/italic/link）、嵌套结构（表格、
 * 嵌套列表）和代码块语法，消除了旧 `serializeMdastNodeToMarkdown` 的
 * 结构压平问题。
 *
 * R-Aside-1R A1: 闭合 `</aside>` 后的尾随正文被拆分为独立 paragraph 节点，
 * 绝不被吞入 callout。
 *
 * 返回 null 表示未找到闭合 `</aside>`，调用方应保留原节点不合并。
 */
function tryMergeAsideFrom(
  opening: MdastNode,
  siblings: MdastNode[],
  startIndex: number,
): { node: MdastNode; nextIndex: number; trailingNode: MdastNode | null } | null {
  const asideChildren: MdastNode[] = [];
  let closingValue = "";
  let trailingText = "";
  let j = startIndex + 1;
  let foundClose = false;

  while (j < siblings.length) {
    const next = siblings[j];
    if (next.type === "html") {
      const value = next.value ?? "";
      if (/<\/aside>/i.test(value)) {
        // 找到闭合 </aside>，拆分尾随正文
        const { closePart, trailingText: tt } = splitClosingAside(value);
        closingValue = closePart;
        trailingText = tt;
        foundClose = true;
        j++;
        break;
      }
      // html 节点但无 </aside> — 作为中间子节点携带
      mergeAsideInNode(next);
      asideChildren.push(next);
      j++;
      continue;
    }
    if (
      next.type === "paragraph" ||
      next.type === "list" ||
      next.type === "blockquote" ||
      next.type === "code" ||
      next.type === "heading" ||
      next.type === "table" ||
      next.type === "thematicBreak"
    ) {
      // R-Aside-1R A2: 直接携带 mdast 子节点，不序列化
      mergeAsideInNode(next);
      asideChildren.push(next);
      j++;
      continue;
    }
    // 遇到不支持的节点类型，中止合并（安全降级）
    return null;
  }

  if (!foundClose) return null;

  // 构建合并后的 html 节点 value：opening + closing
  // opening value 可能包含 <aside> 开标签和部分内部文本（如 "<aside>\n**Alignment**: ..."）
  // closing value 是 </aside> 闭标签
  // 内部的 mdast 子节点（middle nodes）通过 _asideChildren 携带，不序列化进 value
  const mergedValue = `${opening.value ?? ""}\n${closingValue}`;
  const mergedNode: MdastNode = {
    type: "html",
    value: mergedValue,
  };
  if (asideChildren.length > 0) {
    mergedNode._asideChildren = asideChildren;
  }

  // R-Aside-1R A1: 尾随正文成为独立段落节点
  let trailingNode: MdastNode | null = null;
  if (trailingText) {
    trailingNode = {
      type: "paragraph",
      children: [{ type: "text", value: trailingText }],
    };
  }

  return { node: mergedNode, nextIndex: j, trailingNode };
}

/**
 * 递归处理 mdast 节点的 children：
 * 1. 合并被拆分的 `<aside>` html 序列（多节点 → 单节点 + _asideChildren）
 * 2. 拆分单个 `<aside>...</aside>trailing` html 节点（单节点 → aside + trailing paragraph）
 */
function mergeAsideInNode(node: MdastNode): void {
  if (!node.children) return;

  const newChildren: MdastNode[] = [];
  let i = 0;
  while (i < node.children.length) {
    const child = node.children[i];

    // Case 1: 开标签 html 节点（无 </aside>）— 尝试与后续 siblings 合并
    if (isOpeningAsideHtml(child)) {
      const result = tryMergeAsideFrom(child, node.children, i);
      if (result) {
        newChildren.push(result.node);
        if (result.trailingNode) {
          newChildren.push(result.trailingNode);
        }
        i = result.nextIndex;
        continue;
      }
    }

    // Case 2: 完整 aside html 节点带尾随正文 — 拆分为 aside + trailing paragraph
    if (isCompleteAsideHtml(child)) {
      const split = splitCompleteAsideWithTrailing(child);
      if (split) {
        newChildren.push(split.asideNode);
        newChildren.push(split.trailingNode!);
        i++;
        continue;
      }
    }

    // Default: 递归处理子节点后保留
    mergeAsideInNode(child);
    newChildren.push(child);
    i++;
  }
  node.children = newChildren;
}

/**
 * remark 插件：合并被 commonmark 空行拆分的 `<aside>...</aside>` html 节点，
 * 并拆分闭标签后的尾随正文为独立段落。
 *
 * Commonmark HTML block type 6（包含 `<aside>`）在遇到空行时终止。
 * 当 `<aside>` 块内包含段落分隔（空行）时，remark-parse 会把它拆分为
 * 多个节点：
 *
 *   html: "<aside>\nFirst paragraph."
 *   paragraph: "Second paragraph."
 *   html: "</aside>Peer discussion..."
 *
 * 本插件将这样的序列重新合并为单个 html 节点（携带原始 mdast 子节点
 * 在 `_asideChildren` 中），并将 `</aside>` 后的尾随正文拆分为独立段落。
 *
 * R-Aside-1R A1: `</aside>Peer discussion...` 中的尾随正文不再被吞入 callout。
 * R-Aside-1R A2: 内部 marks/结构通过 `_asideChildren` 原样保留，不再压平。
 *
 * 安全约束：
 * - 仅合并以 `<aside` 开标签起始、以 `</aside>` 闭标签结束的序列
 * - 中间节点作为 mdast 子节点携带，不执行原始 HTML
 * - 不匹配的 html 节点（如 `<div>`、`<script>`）不受影响
 * - 未找到闭合 `</aside>` 时不合并（保留原始开标签作为降级文本）
 */
export function remarkMergeAsideHtml() {
  return (tree: MdastNode) => {
    mergeAsideInNode(tree);
  };
}
