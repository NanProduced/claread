import type { Descendant } from "platejs";

import { deserializeMarkdownToBlocksWithStatus } from "@/lib/reader-plate/markdown/deserialize";
import { normalizeCalloutDisplayIcons } from "@/lib/source-callout/source-callout-display-icon";
import {
  normalizeClipboardHref,
  prepareClipboardHtml,
} from "./prepare-clipboard-html";

export type ClipboardSourceKind = "html" | "plain" | "hybrid";

export interface ClipboardSourceNegotiationInput {
  html?: string | null;
  plain?: string | null;
}

export interface ClipboardSourceFingerprintDependencies {
  /** Existing Plate HTML deserializer, supplied by the mounted input editor. */
  deserializeHtml: (body: HTMLElement) => Descendant[];
  /** Existing Plate Markdown deserializer, supplied by the mounted input editor. */
  deserializeMarkdown: (markdown: string) => Descendant[];
}

export interface CalloutFusionLinkFingerprint {
  visibleText: string;
  sanitizedHref: string;
}

export interface CalloutFusionNodeFingerprint {
  type: string;
  visibleText: string;
  marks: string[];
  linkHref?: string | null;
  children: CalloutFusionNodeFingerprint[];
}

export interface CalloutFusionFingerprint {
  boundary: {
    open: "aside";
    close: "aside";
    block: true;
  };
  documentOrder: number;
  visibleText: string;
  blocks: CalloutFusionNodeFingerprint[];
  links: CalloutFusionLinkFingerprint[];
  linkCount: number;
  unsafeLinkCount: number;
}

export interface ClipboardAsideFusionMatch {
  documentOrder: number;
  plainAsideMarkdown: string;
  fingerprint: CalloutFusionFingerprint;
}

export interface ClipboardAsideFusionPlan {
  /** Every validated pair, in document order; all must validate before use. */
  matches: ClipboardAsideFusionMatch[];
  /** G1P-B-B：仅补 HTML 真正 missing 的 alt/title（非 URL 字段）。 */
  imageFieldMatches?: ClipboardImageFieldFusionMatch[];
  /** G1P-B-B：仅补 HTML 真正缺失的 fenced-code language。 */
  codeLanguageMatches?: ClipboardCodeLanguageFusionMatch[];
}

/**
 * 一个 image 字段融合对。URL 永不进 plan（htmlSrc 只是 HTML 自身的
 * structure truth，用于 fragment 应用前 identity 重验证）。
 */
export interface ClipboardImageFieldFusionMatch {
  /** 规范化段落路径（见 fieldKeyOf）。 */
  blockPath: number[];
  /** 段内 inline ordinal。 */
  ordinal: number;
  /** HTML 侧 src（safe URL truth），identity 重验证用，绝不作替换值。 */
  htmlSrc: string;
  /** 仅在 HTML alt 真正 missing 且 plain alt 非空时设置。 */
  alt?: string;
  /** 仅在 HTML title 真正 missing 且 plain title 非空时设置。 */
  title?: string;
}

export interface ClipboardCodeLanguageFusionMatch {
  blockPath: number[];
  /** code body 逐字原文（含内部空行），fragment 应用前重验证。 */
  body: string;
  language: string;
}

export interface ClipboardSourceNegotiationResult {
  kind: ClipboardSourceKind | null;
  /** Sanitized HTML when `kind === "html"` or `kind === "hybrid"`. */
  html: string;
  /** Original plain Markdown companion. */
  plain: string;
  /** A validated local merge plan when `kind === "hybrid"`. */
  fusion?: ClipboardAsideFusionPlan;
  reason:
    | "html_structured"
    | "plain_structured_aside"
    | "html_plain_aside_fused"
    | "html_plain_fields_fused"
    | "html_aside_fusion_declined"
    | "html_empty"
    | "plain_fallback"
    | "empty";
}

const ASIDE_OPEN_LINE_RE = /^<aside(?:\s+[^<>]*)?>$/i;
const ASIDE_CLOSE_LINE_RE = /^<\/aside>$/i;
const FENCE_LINE_RE = /^(`{3,}|~{3,})(?:.*)?$/;
const ESCAPED_ASIDE_LINE_RE = /^\\\s*<aside\b/i;

interface PlainAsideMatch {
  markdown: string;
  body: string;
  unsafeLinkCount: number;
}

// This is a security preflight only. Markdown structure still comes from the
// existing Plate Markdown deserializer; this scan ensures a URL that the
// parser sanitizes away cannot become an apparently matching missing link.
const MARKDOWN_LINK_DESTINATION_RE = /\]\(\s*(?:<([^>]+)>|([^\s)]+))/g;

function countUnsafeMarkdownLinks(markdown: string): number {
  let count = 0;
  for (const match of markdown.matchAll(MARKDOWN_LINK_DESTINATION_RE)) {
    const href = match[1] ?? match[2] ?? "";
    if (!normalizeClipboardHref(href)) count += 1;
  }
  return count;
}

/**
 * Find complete, block-shaped plain-text asides. The scanner deliberately
 * ignores escaped openings, inline examples, fenced code, and unclosed
 * openings. Complete pairs are returned in source order; the caller validates
 * every pair before any local replacement is allowed.
 */
function findPlainAsideMatches(markdown: string): PlainAsideMatch[] {
  if (!markdown.trim()) return [];

  const normalized = markdown.replace(/\r\n?/g, "\n");
  const lines = normalized.split("\n");
  let fenceChar: "`" | "~" | null = null;
  let openingIndex: number | null = null;
  const matches: PlainAsideMatch[] = [];

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    const trimmed = line.trim();
    const fence = trimmed.match(FENCE_LINE_RE);

    if (fence) {
      const nextFenceChar = fence[1]?.[0] as "`" | "~" | undefined;
      if (fenceChar === null) {
        fenceChar = nextFenceChar ?? null;
      } else if (nextFenceChar === fenceChar) {
        fenceChar = null;
      }
      continue;
    }
    if (fenceChar !== null) continue;

    if (openingIndex === null) {
      if (
        ESCAPED_ASIDE_LINE_RE.test(trimmed) ||
        trimmed.includes("`") ||
        !ASIDE_OPEN_LINE_RE.test(trimmed)
      ) {
        continue;
      }
      openingIndex = index;
      continue;
    }

    if (!ASIDE_CLOSE_LINE_RE.test(trimmed)) continue;

    const body = lines
      .slice(openingIndex + 1, index)
      .join("\n")
      .trim();
    if (body.length > 0) {
      matches.push({
        markdown: lines.slice(openingIndex, index + 1).join("\n"),
        body,
        unsafeLinkCount: countUnsafeMarkdownLinks(body),
      });
    }
    openingIndex = null;
  }

  return matches;
}

/**
 * Detect a complete block-shaped plain-text `<aside>` without treating prose
 * examples, inline code, fenced code, escaped tags, or an unclosed opening as
 * a source callout.
 */
export function hasHighConfidencePlainAside(markdown: string): boolean {
  return findPlainAsideMatches(markdown).length > 0;
}

const ESCAPED_ASIDE_BLOCK_TAGS = new Set([
  "ARTICLE",
  "DIV",
  "P",
  "SECTION",
]);
const HTML_ESCAPED_ASIDE_OPEN_RE = /^<aside(?:\s+[^<>]*)?>$/i;
const HTML_ESCAPED_ASIDE_CLOSE_RE = /^<\/aside>$/i;

export interface EscapedAsideDomRegion {
  parent: Node;
  start: ChildNode;
  end: ChildNode;
  bodyText: string;
}

function isWhitespaceTextNode(node: ChildNode): boolean {
  return node.nodeType === 3 && !(node.textContent ?? "").trim();
}

function isCodeLikeAncestor(element: Element): boolean {
  let current: Element | null = element;
  while (current) {
    if (["CODE", "PRE", "SCRIPT", "STYLE"].includes(current.tagName)) {
      return true;
    }
    current = current.parentElement;
  }
  return false;
}

function escapedAsideBoundary(
  node: ChildNode,
  kind: "open" | "close",
): node is HTMLElement {
  if (!(node instanceof HTMLElement)) return false;
  if (!ESCAPED_ASIDE_BLOCK_TAGS.has(node.tagName)) return false;
  if (node.children.length > 0 || isCodeLikeAncestor(node)) return false;
  const text = (node.textContent ?? "").trim();
  return kind === "open"
    ? HTML_ESCAPED_ASIDE_OPEN_RE.test(text)
    : HTML_ESCAPED_ASIDE_CLOSE_RE.test(text);
}

function domParents(root: ParentNode): ParentNode[] {
  return [root, ...Array.from(root.querySelectorAll("*"))];
}

export function findEscapedAsideDomRegions(root: ParentNode): EscapedAsideDomRegion[] {
  if (root.querySelector("aside")) return [];

  const regions: EscapedAsideDomRegion[] = [];
  for (const parent of domParents(root)) {
    const children = Array.from(parent.childNodes);
    for (let startIndex = 0; startIndex < children.length; startIndex += 1) {
      const start = children[startIndex];
      if (!start || !escapedAsideBoundary(start, "open")) continue;

      for (
        let endIndex = startIndex + 1;
        endIndex < children.length;
        endIndex += 1
      ) {
        const end = children[endIndex];
        if (!end) continue;
        if (isWhitespaceTextNode(end)) continue;
        if (!escapedAsideBoundary(end, "close")) continue;

        const middle = children.slice(startIndex + 1, endIndex);
        if (
          middle.some(
            (node) =>
              !isWhitespaceTextNode(node) && node.nodeType !== 1,
          )
        ) {
          break;
        }
        const bodyText = middle
          .map((node) => node.textContent ?? "")
          .join(" ");
        if (bodyText.trim()) {
          regions.push({
            parent: parent as Node,
            start,
            end,
            bodyText,
          });
        }
        break;
      }
    }
  }
  return regions.sort((left, right) => {
    if (left.start === right.start) return 0;
    const position = left.start.compareDocumentPosition(right.start);
    if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
}

/** Return the only escaped-aside DOM region, otherwise fail closed. */
export function locateEscapedAsideDomRegion(
  root: ParentNode,
): EscapedAsideDomRegion | null {
  const regions = findEscapedAsideDomRegions(root);
  return regions.length === 1 ? regions[0] ?? null : null;
}

type UnknownPlateNode = {
  type?: unknown;
  text?: unknown;
  children?: unknown;
  url?: unknown;
  href?: unknown;
  [key: string]: unknown;
};

function asPlateNode(value: unknown): UnknownPlateNode | null {
  return value && typeof value === "object"
    ? (value as UnknownPlateNode)
    : null;
}

function nodeChildren(value: UnknownPlateNode): UnknownPlateNode[] {
  return Array.isArray(value.children)
    ? value.children.map(asPlateNode).filter((node): node is UnknownPlateNode => node !== null)
    : [];
}

function collectPlateText(value: unknown): string {
  const node = asPlateNode(value);
  if (!node) return "";
  if (typeof node.text === "string") return node.text.replace(/\r\n?/g, "\n");
  return nodeChildren(node).map(collectPlateText).join("");
}

function normalizeFingerprintNodeType(value: unknown): string {
  const type = typeof value === "string" ? value : "text";
  if (type === "p" || type === "paragraph") return "paragraph";
  if (type === "ul") return "list:unordered";
  if (type === "ol") return "list:ordered";
  if (type === "li" || type === "list_item") return "list_item";
  if (type === "lic" || type === "list_item_content") {
    return "list_item_content";
  }
  if (type === "a" || type === "link") return "link";
  if (/^h[1-6]$/.test(type)) return `heading:${type}`;
  return type;
}

const FINGERPRINT_NODE_KEYS = new Set([
  "type",
  "text",
  "children",
  "url",
  "href",
  "id",
  "displayIcon",
  "kind",
]);

function fingerprintMarks(node: UnknownPlateNode): string[] {
  return Object.entries(node)
    .filter(
      ([key, value]) =>
        !FINGERPRINT_NODE_KEYS.has(key) && value === true,
    )
    .map(([key]) => key)
    .sort();
}

function nodeHref(node: UnknownPlateNode): string | null {
  for (const key of ["url", "href"]) {
    if (typeof node[key] === "string") return node[key] as string;
  }
  return null;
}

interface FingerprintLinkState {
  links: CalloutFusionLinkFingerprint[];
  unsafeLinkCount: number;
}

function buildFingerprintNode(
  value: unknown,
  state: FingerprintLinkState,
): CalloutFusionNodeFingerprint {
  const node = asPlateNode(value) ?? {};
  const type = normalizeFingerprintNodeType(node.type);
  const children = nodeChildren(node).map((child) =>
    buildFingerprintNode(child, state),
  );
  const visibleText = collectPlateText(node);
  const fingerprint: CalloutFusionNodeFingerprint = {
    type,
    visibleText,
    marks: fingerprintMarks(node),
    children,
  };

  if (type === "link") {
    const sanitizedHref = normalizeClipboardHref(nodeHref(node));
    if (!sanitizedHref) {
      state.unsafeLinkCount += 1;
      fingerprint.linkHref = null;
    } else {
      fingerprint.linkHref = sanitizedHref;
      state.links.push({
        visibleText,
        sanitizedHref,
      });
    }
  }

  return fingerprint;
}

/** Build the canonical structured comparison value for one callout body. */
export function buildCalloutFusionFingerprint(
  children: Descendant[],
  documentOrder: number,
  unsafeLinkCount = 0,
): CalloutFusionFingerprint {
  const state: FingerprintLinkState = {
    links: [],
    unsafeLinkCount,
  };
  const blocks = children.map((child) => buildFingerprintNode(child, state));
  return {
    boundary: { open: "aside", close: "aside", block: true },
    documentOrder,
    visibleText: blocks.map((block) => block.visibleText).join(""),
    blocks,
    links: state.links,
    linkCount: state.links.length,
    unsafeLinkCount: state.unsafeLinkCount,
  };
}

function findSourceCallout(nodes: Descendant[]): UnknownPlateNode | null {
  const matches = nodes
    .map(asPlateNode)
    .filter(
      (node): node is UnknownPlateNode => node?.type === "source_callout",
    );
  return matches.length === 1 ? matches[0] ?? null : null;
}

function extractCalloutBody(nodes: Descendant[]): Descendant[] | null {
  const callout = findSourceCallout(nodes);
  if (!callout) return null;
  const normalized = normalizeCalloutDisplayIcons([callout as Descendant]);
  const normalizedCallout = asPlateNode(normalized[0]);
  if (!normalizedCallout || !Array.isArray(normalizedCallout.children)) {
    return null;
  }
  return normalizedCallout.children as Descendant[];
}

function addDomMark(nodes: Descendant[], mark: string): Descendant[] {
  return nodes.map((rawNode) => {
    const node = asPlateNode(rawNode);
    if (!node) return rawNode;
    if (typeof node.text === "string") {
      return { ...node, [mark]: true } as Descendant;
    }
    return {
      ...node,
      children: Array.isArray(node.children)
        ? addDomMark(node.children as Descendant[], mark)
        : [],
    } as Descendant;
  });
}

function domInlineNode(node: Node): Descendant[] {
  if (node.nodeType === 3) {
    return [{ text: node.textContent ?? "" }];
  }
  if (!(node instanceof Element)) return [];

  const nested = domInlineNodes(node);
  switch (node.tagName) {
    case "STRONG":
    case "B":
      return addDomMark(nested, "bold");
    case "EM":
    case "I":
      return addDomMark(nested, "italic");
    case "CODE":
      return addDomMark(nested, "code");
    case "S":
    case "DEL":
      return addDomMark(nested, "strikethrough");
    case "A":
      return [
        {
          type: "a",
          url: node.getAttribute("href") ?? "",
          children: nested,
        } as Descendant,
      ];
    case "BR":
      return [{ text: "\n" }];
    default:
      return nested;
  }
}

function domInlineNodes(parent: Node): Descendant[] {
  return Array.from(parent.childNodes).flatMap((child) => domInlineNode(child));
}

function domBlockNode(element: Element): Descendant {
  const tag = element.tagName.toLowerCase();
  if (tag === "aside") {
    return { type: "source_callout", children: domBlockNodes(element) } as Descendant;
  }
  if (/^h[1-6]$/.test(tag)) {
    return { type: tag, children: domInlineNodes(element) } as Descendant;
  }
  if (tag === "p" || tag === "blockquote") {
    return { type: tag, children: domInlineNodes(element) } as Descendant;
  }
  if (tag === "ul" || tag === "ol") {
    return {
      type: tag,
      children: Array.from(element.children)
        .filter((child) => child.tagName === "LI")
        .map((child) => domBlockNode(child)),
    } as Descendant;
  }
  if (tag === "li") {
    const children: Descendant[] = [];
    let inlineChildren: Descendant[] = [];
    const flushListItemContent = () => {
      if (inlineChildren.length === 0) return;
      children.push({ type: "lic", children: inlineChildren } as Descendant);
      inlineChildren = [];
    };

    for (const child of Array.from(element.childNodes)) {
      if (child instanceof Element && ["UL", "OL"].includes(child.tagName)) {
        flushListItemContent();
        children.push(domBlockNode(child));
        continue;
      }
      inlineChildren.push(...domInlineNode(child));
    }
    flushListItemContent();
    return { type: "li", children } as Descendant;
  }
  if (tag === "table") {
    return {
      type: "table",
      children: Array.from(element.querySelectorAll(":scope > thead > tr, :scope > tbody > tr, :scope > tr")).map(
        (row) => domBlockNode(row),
      ),
    } as Descendant;
  }
  if (tag === "tr") {
    return {
      type: "tr",
      children: Array.from(element.children)
        .filter((child) => ["TD", "TH"].includes(child.tagName))
        .map((child) => domBlockNode(child)),
    } as Descendant;
  }
  if (tag === "td" || tag === "th") {
    return { type: tag, children: domInlineNodes(element) } as Descendant;
  }
  if (tag === "pre") {
    return {
      type: "code_block",
      children: [{ type: "code_line", children: [{ text: element.textContent ?? "" }] }],
    } as Descendant;
  }

  const hasBlockChild = Array.from(element.children).some((child) =>
    ["P", "UL", "OL", "TABLE", "BLOCKQUOTE", "PRE"].includes(child.tagName),
  );
  return hasBlockChild
    ? { type: tag, children: domBlockNodes(element) } as Descendant
    : { type: "p", children: domInlineNodes(element) } as Descendant;
}

function domBlockNodes(parent: Node): Descendant[] {
  const nodes: Descendant[] = [];
  for (const child of Array.from(parent.childNodes)) {
    if (child.nodeType === 3) {
      if ((child.textContent ?? "").trim()) {
        nodes.push({ type: "p", children: [{ text: child.textContent ?? "" }] });
      }
      continue;
    }
    if (child instanceof Element) nodes.push(domBlockNode(child));
  }
  return nodes;
}

function cloneEscapedAsideAsElement(
  region: EscapedAsideDomRegion,
): HTMLElement | null {
  const ownerDocument = region.start.ownerDocument;
  if (!ownerDocument) return null;
  const aside = ownerDocument.createElement("aside");
  const children = Array.from(region.parent.childNodes);
  const startIndex = children.indexOf(region.start);
  const endIndex = children.indexOf(region.end);
  if (startIndex < 0 || endIndex <= startIndex) return null;
  for (const child of children.slice(startIndex + 1, endIndex)) {
    if (child.nodeType === 1) aside.appendChild(child.cloneNode(true));
  }
  return aside;
}

function countUnsafeHrefAttributes(root: ParentNode): number {
  return Array.from(root.querySelectorAll("a[href]"))
    .map((anchor) => anchor.getAttribute("href"))
    .filter((href) => !normalizeClipboardHref(href)).length;
}

function defaultFingerprintDependencies(): ClipboardSourceFingerprintDependencies {
  return {
    deserializeHtml: (body) =>
      body.tagName === "ASIDE" ? [domBlockNode(body)] : domBlockNodes(body),
    deserializeMarkdown: (markdown) =>
      deserializeMarkdownToBlocksWithStatus(markdown).blocks,
  };
}

function htmlUnsafeLinkCounts(
  rawHtml: string,
  expectedCount: number,
): number[] {
  if (!rawHtml.trim()) return Array.from({ length: expectedCount }, () => 0);
  try {
    const document = new DOMParser().parseFromString(rawHtml, "text/html");
    const regions = findEscapedAsideDomRegions(document.body);
    if (regions.length !== expectedCount) {
      return Array.from({ length: expectedCount }, () => 0);
    }
    return regions.map((region) => {
      const aside = cloneEscapedAsideAsElement(region);
      return aside ? countUnsafeHrefAttributes(aside) : 0;
    });
  } catch {
    return Array.from({ length: expectedCount }, () => 0);
  }
}

function fingerprintsEqual(
  htmlFingerprint: CalloutFusionFingerprint,
  plainFingerprint: CalloutFusionFingerprint,
): boolean {
  if (htmlFingerprint.unsafeLinkCount > 0 || plainFingerprint.unsafeLinkCount > 0) {
    return false;
  }
  return JSON.stringify(htmlFingerprint) === JSON.stringify(plainFingerprint);
}

// ---------------------------------------------------------------------------
// G1P-B-B · bounded non-URL companion-field fusion（image alt/title、code lang）
//
// HTML 始终是结构 truth。只允许补齐真正 missing 的非 URL 字段：image alt、
// image title、fenced-code language。匹配规则（O-B1/O-B2/O-B3）：
// - 规范化段落路径 + inline ordinal + 邻接正文一致才构成候选；
// - 两侧 URL 同时存在时必须逐字相同（URL 冲突即不融合）；
// - HTML 无 src（含 sanitizer 摘除）不参与（无 URL identity 锚点）；
// - plain URL 只用于一致性检查，绝不作为 replacement 进入 plan；
// - alt missing 必须来自 sanitizer 后 DOM 的 hasAttribute 语义（反序列化
//   空 caption 同时覆盖 missing 与显式空值，不可作判定依据）；
// - 重复组（相同 url+alt 或相同 code body）无法按 path 唯一消歧时，
//   整个组不融合（不按“第 N 个”猜测）。
// ---------------------------------------------------------------------------

const FIELD_PARAGRAPH_SLOT_TYPES = new Set([
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "td",
  "th",
  "blockquote",
  "lic",
]);
const FIELD_CONTAINER_TYPES = new Set([
  "ul",
  "ol",
  "li",
  "table",
  "tr",
  "source_callout",
  "pre",
  "code_block",
  "hr",
]);
const FIELD_TRANSPARENT_TYPES = new Set(["div", "figure"]);

const DOM_PARAGRAPH_SLOT_TAGS = new Set([
  "P",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
  "TD",
  "TH",
  "BLOCKQUOTE",
]);
const DOM_CONTAINER_TAGS = new Set([
  "UL",
  "OL",
  "LI",
  "TABLE",
  "THEAD",
  "TBODY",
  "TFOOT",
  "TR",
  "ASIDE",
  "HR",
  "PRE",
]);
const DOM_TRANSPARENT_TAGS = new Set(["DIV", "FIGURE"]);

export interface ClipboardFieldImageScan {
  key: string;
  blockPath: number[];
  ordinal: number;
  realPath: number[];
  node: UnknownPlateNode;
  paragraphText: string;
}

export interface ClipboardFieldCodeScan {
  key: string;
  blockPath: number[];
  realPath: number[];
  node: UnknownPlateNode;
  body: string;
}

/** 规范化段落路径 key（image 带 ordinal，code 不带）。 */
export function fieldKeyOf(blockPath: number[], ordinal?: number): string {
  return ordinal === undefined
    ? blockPath.join(",")
    : `${blockPath.join(",")}:${ordinal}`;
}

export function imageCaptionText(node: UnknownPlateNode): string {
  if (!Array.isArray(node.caption)) return "";
  return node.caption
    .map((c) => {
      const text = asPlateNode(c)?.text;
      return typeof text === "string" ? text : "";
    })
    .join("");
}

/** code_block 逐字 body：code_line 文本按 \n 连接（含内部空行）。 */
export function codeBodyText(node: UnknownPlateNode): string {
  if (!Array.isArray(node.children)) return "";
  return nodeChildren(node)
    .map((line) => collectPlateText(line))
    .join("\n");
}

/**
 * 扫描 Plate fragment，产出规范化段落路径下的 img / code_block 记录。
 * p/div/figure 在 HTML deserializer 侧被解包为 inline run，plain 侧 p 是
 * 段落槽位——两种形态都归一到“段落槽位 + inline ordinal”，保证两侧 key
 * 一致（li 内 HTML 无 lic、plain 有 lic 同理）。
 */
export function scanClipboardFragment(
  nodes: unknown[],
): { images: ClipboardFieldImageScan[]; codes: ClipboardFieldCodeScan[] } {
  const images: ClipboardFieldImageScan[] = [];
  const codes: ClipboardFieldCodeScan[] = [];
  scanFragmentLevel(nodes, [], [], images, codes);
  return { images, codes };
}

function scanFragmentLevel(
  children: unknown[],
  prefix: number[],
  realPrefix: number[],
  images: ClipboardFieldImageScan[],
  codes: ClipboardFieldCodeScan[],
): void {
  let slotIndex = 0;
  let run: {
    slotIndex: number;
    items: Array<{ node: UnknownPlateNode; realPath: number[] }>;
  } | null = null;
  const closeRun = () => {
    if (!run) return;
    const paragraphText = run.items
      .map((item) => collectPlateText(item.node))
      .join("");
    let ordinal = 0;
    for (const item of run.items) {
      if (item.node.type === "img") {
        images.push({
          key: fieldKeyOf([...prefix, run.slotIndex], ordinal),
          blockPath: [...prefix, run.slotIndex],
          ordinal,
          realPath: item.realPath,
          node: item.node,
          paragraphText,
        });
      }
      ordinal += 1;
    }
    run = null;
  };

  const processNode = (node: UnknownPlateNode | null, realPath: number[]) => {
    if (!node) return;
    const type = typeof node.type === "string" ? node.type : "";
    if (FIELD_TRANSPARENT_TYPES.has(type)) {
      const kids = nodeChildren(node);
      for (let j = 0; j < kids.length; j += 1) {
        processNode(kids[j], [...realPath, j]);
      }
      return;
    }
    if (type === "code_block") {
      closeRun();
      codes.push({
        key: fieldKeyOf([...prefix, slotIndex]),
        blockPath: [...prefix, slotIndex],
        realPath,
        node,
        body: codeBodyText(node),
      });
      slotIndex += 1;
      return;
    }
    if (FIELD_PARAGRAPH_SLOT_TYPES.has(type)) {
      closeRun();
      scanInlineLevel(nodeChildren(node), [...prefix, slotIndex], realPath, images);
      slotIndex += 1;
      return;
    }
    if (FIELD_CONTAINER_TYPES.has(type)) {
      closeRun();
      scanFragmentLevel(
        nodeChildren(node),
        [...prefix, slotIndex],
        realPath,
        images,
        codes,
      );
      slotIndex += 1;
      return;
    }
    if (!run) {
      run = { slotIndex, items: [] };
      slotIndex += 1;
    }
    run.items.push({ node, realPath });
  };

  for (let i = 0; i < children.length; i += 1) {
    processNode(asPlateNode(children[i]), [...realPrefix, i]);
  }
  closeRun();
}

function scanInlineLevel(
  children: unknown[],
  prefix: number[],
  realPrefix: number[],
  images: ClipboardFieldImageScan[],
): void {
  const paragraphText = children.map(collectPlateText).join("");
  for (let ordinal = 0; ordinal < children.length; ordinal += 1) {
    const node = asPlateNode(children[ordinal]);
    if (node?.type !== "img") continue;
    images.push({
      key: fieldKeyOf(prefix, ordinal),
      blockPath: prefix,
      ordinal,
      realPath: [...realPrefix, ordinal],
      node,
      paragraphText,
    });
  }
}

/**
 * sanitizer 后 DOM 的 alt 缺失语义（hasAttribute）。key 与 fragment 扫描
 * 同一套规范化路径；任一 img 的 key 对不上（DOM/反序列化形态漂移）则
 * 该 key 缺失 → 调用方 fail closed。
 */
export function scanDomImageAltMissing(root: ParentNode): Map<string, boolean> {
  const altMissing = new Map<string, boolean>();
  scanDomLevel(Array.from(root.childNodes), [], [], altMissing);
  return altMissing;
}

function scanDomLevel(
  children: ChildNode[],
  prefix: number[],
  realPrefix: number[],
  altMissing: Map<string, boolean>,
): void {
  let slotIndex = 0;
  let run: { slotIndex: number; ordinal: number } | null = null;
  const closeRun = () => {
    run = null;
  };
  const processNode = (child: Node, realPath: number[]) => {
    if (!(child instanceof Element)) {
      if (!run) {
        run = { slotIndex, ordinal: 0 };
        slotIndex += 1;
      }
      run.ordinal += 1;
      return;
    }
    const tag = child.tagName;
    if (DOM_TRANSPARENT_TAGS.has(tag)) {
      for (let j = 0; j < child.childNodes.length; j += 1) {
        processNode(child.childNodes[j], [...realPath, j]);
      }
      return;
    }
    if (tag === "IMG") {
      if (!run) {
        run = { slotIndex, ordinal: 0 };
        slotIndex += 1;
      }
      altMissing.set(
        fieldKeyOf([...prefix, run.slotIndex], run.ordinal),
        !child.hasAttribute("alt"),
      );
      run.ordinal += 1;
      return;
    }
    if (tag === "PRE") {
      closeRun();
      slotIndex += 1;
      return;
    }
    if (DOM_PARAGRAPH_SLOT_TAGS.has(tag)) {
      closeRun();
      scanDomInline(child, [...prefix, slotIndex], realPath, altMissing);
      slotIndex += 1;
      return;
    }
    if (DOM_CONTAINER_TAGS.has(tag)) {
      closeRun();
      scanDomLevel(
        Array.from(child.childNodes),
        [...prefix, slotIndex],
        realPath,
        altMissing,
      );
      slotIndex += 1;
      return;
    }
    if (!run) {
      run = { slotIndex, ordinal: 0 };
      slotIndex += 1;
    }
    run.ordinal += 1;
  };
  for (let i = 0; i < children.length; i += 1) {
    processNode(children[i], [...realPrefix, i]);
  }
  closeRun();
}

function scanDomInline(
  element: Element,
  prefix: number[],
  realPrefix: number[],
  altMissing: Map<string, boolean>,
): void {
  const children = Array.from(element.childNodes);
  for (let ordinal = 0; ordinal < children.length; ordinal += 1) {
    const child = children[ordinal];
    if (!(child instanceof Element)) continue;
    if (child.tagName === "IMG") {
      altMissing.set(
        fieldKeyOf(prefix, ordinal),
        !child.hasAttribute("alt"),
      );
    }
  }
}

interface FieldImageGroup {
  members: ClipboardFieldImageScan[];
  pairs: Array<{
    html: ClipboardFieldImageScan;
    plain: ClipboardFieldImageScan;
    alt: string;
    title: string;
  }>;
}

interface FieldCodeGroup {
  members: ClipboardFieldCodeScan[];
  pairs: Array<{ html: ClipboardFieldCodeScan; plain: ClipboardFieldCodeScan }>;
}

function matchImageFields(
  htmlImages: ClipboardFieldImageScan[],
  plainImages: ClipboardFieldImageScan[],
  altMissingByKey: Map<string, boolean>,
): ClipboardImageFieldFusionMatch[] {
  const plainByKey = new Map<string, ClipboardFieldImageScan>();
  for (const plain of plainImages) {
    if (!plainByKey.has(plain.key)) plainByKey.set(plain.key, plain);
  }

  const groups = new Map<string, FieldImageGroup>();
  // 成员先按 (url, alt) 全量登记：重复组任一成员无法配对时整组不融合。
  for (const htmlImg of htmlImages) {
    const htmlUrl = typeof htmlImg.node.url === "string" ? htmlImg.node.url : "";
    if (!htmlUrl) continue;
    const groupKey = `${htmlUrl}|${imageCaptionText(htmlImg.node)}`;
    let group = groups.get(groupKey);
    if (!group) {
      group = { members: [], pairs: [] };
      groups.set(groupKey, group);
    }
    group.members.push(htmlImg);
  }

  for (const htmlImg of htmlImages) {
    const htmlUrl = typeof htmlImg.node.url === "string" ? htmlImg.node.url : "";
    if (!htmlUrl) continue;
    const plain = plainByKey.get(htmlImg.key);
    if (!plain) continue;
    // 邻接正文一致（HTML 反序列化会在 inline void 旁折叠空白，按空白
    // 不敏感比较）
    if (
      normalizedParagraphText(htmlImg.paragraphText) !==
      normalizedParagraphText(plain.paragraphText)
    ) {
      continue;
    }
    const plainUrl = typeof plain.node.url === "string" ? plain.node.url : "";
    // O-B2：plain URL 只作一致性检查；HTML 无 src 不参与（无 identity 锚点）
    if (!plainUrl) continue;
    if (htmlUrl !== plainUrl) continue;
    // O-B1：alt missing 必须来自 DOM hasAttribute；key 对不上即 fail closed
    const altMissing = altMissingByKey.get(htmlImg.key);
    if (altMissing === undefined) continue;
    const altCandidate = imageCaptionText(plain.node);
    const titleCandidate =
      typeof plain.node.title === "string" ? plain.node.title : "";
    const fillAlt = altMissing && altCandidate !== "";
    const fillTitle = typeof htmlImg.node.title !== "string" && titleCandidate !== "";
    if (!fillAlt && !fillTitle) continue;

    const group = groups.get(`${htmlUrl}|${imageCaptionText(htmlImg.node)}`);
    if (!group) continue;
    group.pairs.push({
      html: htmlImg,
      plain,
      alt: fillAlt ? altCandidate : "",
      title: fillTitle ? titleCandidate : "",
    });
  }

  const matches: ClipboardImageFieldFusionMatch[] = [];
  for (const group of groups.values()) {
    // O-B3：重复组无法唯一消歧（成员数 ≠ 配对数）→ 整组不融合
    if (group.members.length !== group.pairs.length) continue;
    for (const pair of group.pairs) {
      matches.push({
        blockPath: pair.html.blockPath,
        ordinal: pair.html.ordinal,
        htmlSrc: pair.html.node.url as string,
        ...(pair.alt ? { alt: pair.alt } : {}),
        ...(pair.title ? { title: pair.title } : {}),
      });
    }
  }
  return matches;
}

function matchCodeLanguages(
  htmlCodes: ClipboardFieldCodeScan[],
  plainCodes: ClipboardFieldCodeScan[],
): ClipboardCodeLanguageFusionMatch[] {
  const plainByKey = new Map<string, ClipboardFieldCodeScan>();
  for (const plain of plainCodes) {
    if (!plainByKey.has(plain.key)) plainByKey.set(plain.key, plain);
  }

  const groups = new Map<string, FieldCodeGroup>();
  // 成员按 body 全量登记（含已有 lang 的 HTML block：它们也是重复组的
  // 一部分，配对失败时整组不融合）。
  for (const htmlCode of htmlCodes) {
    let group = groups.get(htmlCode.body);
    if (!group) {
      group = { members: [], pairs: [] };
      groups.set(htmlCode.body, group);
    }
    group.members.push(htmlCode);
  }

  for (const htmlCode of htmlCodes) {
    if (typeof htmlCode.node.lang === "string") continue;
    const plain = plainByKey.get(htmlCode.key);
    if (!plain) continue;
    const language =
      typeof plain.node.lang === "string" ? plain.node.lang : "";
    if (!language) continue;
    // code body 逐字一致（含内部空行）
    if (htmlCode.body !== plain.body) continue;

    const group = groups.get(htmlCode.body);
    if (!group) continue;
    group.pairs.push({ html: htmlCode, plain });
  }

  const matches: ClipboardCodeLanguageFusionMatch[] = [];
  for (const group of groups.values()) {
    // O-B3：相同 body 重复且无法唯一消歧 → 整组不融合
    if (group.members.length !== group.pairs.length) continue;
    for (const pair of group.pairs) {
      matches.push({
        blockPath: pair.html.blockPath,
        body: pair.html.body,
        language: pair.plain.node.lang as string,
      });
    }
  }
  return matches;
}

/** 邻接正文比较用：空白不敏感（HTML 反序列化在 inline void 旁折叠空白）。 */
function normalizedParagraphText(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

interface ClipboardFieldFusionResult {
  imageFieldMatches: ClipboardImageFieldFusionMatch[];
  codeLanguageMatches: ClipboardCodeLanguageFusionMatch[];
}

/**
 * 计算 bounded field fusion plan（image alt/title、code language）。
 * 两侧都经 mounted input deserializer 走同一规范化路径；任何歧义、
 * 冲突、DOM 语义缺失都 fail closed（返回空列表，HTML 原样保留）。
 */
function findClipboardFieldFusion(
  html: string,
  plain: string,
  dependencies: ClipboardSourceFingerprintDependencies,
): ClipboardFieldFusionResult | null {
  if (!html.trim() || !plain.trim()) return null;

  const document = new DOMParser().parseFromString(html, "text/html");
  const htmlFragment = dependencies.deserializeHtml(document.body);
  // G1P-B-B-R1：plain candidate 解析固定走 input-aware deserializer
  // （{ preserveUnsupported: true }，产生 typed img）；不依赖调用方注入的
  // projection-only 形态。callout fingerprint/fusion 仍用注入的
  // dependencies.deserializeMarkdown，合同不变。
  const plainFragment = deserializeMarkdownToBlocksWithStatus(plain, {
    preserveUnsupported: true,
  }).blocks;
  const htmlScan = scanClipboardFragment(htmlFragment);
  const plainScan = scanClipboardFragment(plainFragment);
  const altMissingByKey = scanDomImageAltMissing(document.body);

  const imageFieldMatches = matchImageFields(
    htmlScan.images,
    plainScan.images,
    altMissingByKey,
  );
  const codeLanguageMatches = matchCodeLanguages(
    htmlScan.codes,
    plainScan.codes,
  );
  if (imageFieldMatches.length === 0 && codeLanguageMatches.length === 0) {
    return null;
  }
  return { imageFieldMatches, codeLanguageMatches };
}

/**
 * Validate every HTML/plain aside pair with a structured Plate fingerprint.
 * No pair is returned until counts, order, boundaries, block tree, visible
 * text, marks, and ordered sanitized links all match.
 */
export function findClipboardAsideFusion(
  html: string,
  plain: string,
  dependencies: ClipboardSourceFingerprintDependencies =
    defaultFingerprintDependencies(),
  rawHtml = html,
): ClipboardAsideFusionPlan | null {
  if (!html.trim() || !plain.trim()) return null;

  const plainMatches = findPlainAsideMatches(plain);
  const document = new DOMParser().parseFromString(html, "text/html");
  const htmlRegions = findEscapedAsideDomRegions(document.body);
  if (htmlRegions.length === 0 || htmlRegions.length !== plainMatches.length) {
    return null;
  }

  const rawUnsafeCounts = htmlUnsafeLinkCounts(rawHtml, htmlRegions.length);
  const matches: ClipboardAsideFusionMatch[] = [];

  try {
    for (let index = 0; index < htmlRegions.length; index += 1) {
      const htmlRegion = htmlRegions[index];
      const plainMatch = plainMatches[index];
      if (!htmlRegion || !plainMatch) return null;

      const htmlAside = cloneEscapedAsideAsElement(htmlRegion);
      if (!htmlAside) return null;

      const htmlContainer = htmlAside.ownerDocument.createElement("div");
      htmlContainer.appendChild(htmlAside);

      // Run the mounted HTML deserializer as the semantic gate. Its current
      // source_callout rule intentionally flattens direct paragraph children
      // into inline leaves, so retain the DOM block boundaries for the
      // structural side of the fingerprint instead of comparing two lossy
      // representations.
      if (!extractCalloutBody(dependencies.deserializeHtml(htmlContainer))) {
        return null;
      }
      const htmlBody = extractCalloutBody([
        {
          type: "source_callout",
          children: domBlockNodes(htmlAside),
        } as Descendant,
      ]);
      const plainBody = extractCalloutBody(
        dependencies.deserializeMarkdown(plainMatch.markdown),
      );
      if (!htmlBody || !plainBody) return null;

      const htmlFingerprint = buildCalloutFusionFingerprint(
        htmlBody,
        index,
        rawUnsafeCounts[index] ?? 0,
      );
      const plainFingerprint = buildCalloutFusionFingerprint(
        plainBody,
        index,
        plainMatch.unsafeLinkCount,
      );
      if (!fingerprintsEqual(htmlFingerprint, plainFingerprint)) return null;

      matches.push({
        documentOrder: index,
        plainAsideMarkdown: plainMatch.markdown,
        fingerprint: htmlFingerprint,
      });
    }
  } catch {
    return null;
  }

  return matches.length === htmlRegions.length ? { matches } : null;
}

/**
 * Select a clipboard representation for the input editor. HTML remains the
 * rich structural source. A validated escaped-aside region is represented as
 * a hybrid plan so the caller can replace only that region with the matching
 * plain Markdown callout.
 */
export function negotiateClipboardSource({
  html: rawHtml,
  plain: rawPlain,
}: ClipboardSourceNegotiationInput,
  dependencies: ClipboardSourceFingerprintDependencies =
    defaultFingerprintDependencies(),
): ClipboardSourceNegotiationResult {
  const html = rawHtml?.trim() ? prepareClipboardHtml(rawHtml) : "";
  const plain = rawPlain ?? "";

  if (html.trim()) {
    const fusion = findClipboardAsideFusion(
      html,
      plain,
      dependencies,
      rawHtml ?? html,
    );
    const fieldFusion = findClipboardFieldFusion(html, plain, dependencies);
    if (fusion || fieldFusion) {
      return {
        kind: "hybrid",
        html,
        plain,
        fusion: {
          matches: fusion?.matches ?? [],
          ...(fieldFusion && fieldFusion.imageFieldMatches.length > 0
            ? { imageFieldMatches: fieldFusion.imageFieldMatches }
            : {}),
          ...(fieldFusion && fieldFusion.codeLanguageMatches.length > 0
            ? { codeLanguageMatches: fieldFusion.codeLanguageMatches }
            : {}),
        },
        reason: fusion ? "html_plain_aside_fused" : "html_plain_fields_fused",
      };
    }

    const parsedHtml = new DOMParser().parseFromString(html, "text/html");
    const htmlHasEscapedAside =
      findEscapedAsideDomRegions(parsedHtml.body).length > 0;
    if (htmlHasEscapedAside && hasHighConfidencePlainAside(plain)) {
      return {
        kind: "html",
        html,
        plain,
        reason: "html_aside_fusion_declined",
      };
    }

    return {
      kind: "html",
      html,
      plain,
      reason: "html_structured",
    };
  }

  if (plain.trim()) {
    return {
      kind: "plain",
      html: "",
      plain,
      reason: rawHtml?.trim() ? "plain_fallback" : "html_empty",
    };
  }

  return { kind: null, html: "", plain: "", reason: "empty" };
}
