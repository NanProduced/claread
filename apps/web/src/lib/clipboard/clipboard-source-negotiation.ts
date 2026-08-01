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
    if (fusion) {
      return {
        kind: "hybrid",
        html,
        plain,
        fusion,
        reason: "html_plain_aside_fused",
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
