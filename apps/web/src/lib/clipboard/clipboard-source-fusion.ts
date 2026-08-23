import type { Descendant } from "platejs";

import {
  type ClipboardAsideFusionPlan,
  codeBodyText,
  fieldKeyOf,
  findEscapedAsideDomRegions,
  imageCaptionText,
  scanClipboardFragment,
} from "./clipboard-source-negotiation";

const FUSION_SLOT_TEXT_PREFIX = "\uE000claread-source-callout-slot-";

function fusionSlotText(index: number): string {
  return `${FUSION_SLOT_TEXT_PREFIX}${index}\uE001`;
}

interface UnknownPlateNode {
  type?: unknown;
  text?: unknown;
  children?: unknown;
}

export interface ClipboardFragmentFusionDependencies {
  deserializeHtml: (body: HTMLElement) => Descendant[];
  deserializeMarkdown: (markdown: string) => Descendant[];
}

function collectNodeText(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  const node = value as UnknownPlateNode;
  if (typeof node.text === "string") return node.text;
  if (!Array.isArray(node.children)) return "";
  return node.children.map(collectNodeText).join("");
}

function isMarkerBlock(
  value: unknown,
  marker: string,
): value is UnknownPlateNode {
  if (!value || typeof value !== "object") return false;
  const node = value as UnknownPlateNode;
  return (
    (node.type === "p" || node.type === "paragraph") &&
    collectNodeText(node) === marker
  );
}

/**
 * HTML deserializers may coalesce adjacent paragraphs. When that happens the
 * private slot can share one paragraph with the following trailing text. Split
 * only that controlled slot leaf back into block siblings; this is not a
 * document-wide string replacement and preserves all non-slot rich nodes.
 */
function replaceMarkerInParagraph(
  value: Descendant,
  replacement: Descendant[],
  marker: string,
): Descendant[] | null {
  if (!value || typeof value !== "object") return null;
  const node = value as UnknownPlateNode;
  if (node.type !== "p" && node.type !== "paragraph") return null;
  if (!Array.isArray(node.children)) return null;

  const children = node.children as Descendant[];
  const markerIndex = children.findIndex((child) => {
    if (!child || typeof child !== "object") return false;
    const text = (child as UnknownPlateNode).text;
    return typeof text === "string" && text.includes(marker);
  });
  if (markerIndex < 0) return null;

  const markerChild = children[markerIndex] as UnknownPlateNode;
  const markerText = markerChild.text as string;
  const markerOffset = markerText.indexOf(marker);
  const prefixText = markerText.slice(0, markerOffset);
  const suffixText = markerText.slice(
    markerOffset + marker.length,
  );
  const prefixChildren = [
    ...children.slice(0, markerIndex),
    ...(prefixText
      ? [{ ...markerChild, text: prefixText } as Descendant]
      : []),
  ];
  const suffixChildren = [
    ...(suffixText
      ? [{ ...markerChild, text: suffixText } as Descendant]
      : []),
    ...children.slice(markerIndex + 1),
  ];
  const output: Descendant[] = [];
  if (prefixChildren.length > 0) {
    output.push({ ...value, children: prefixChildren } as Descendant);
  }
  output.push(...replacement);
  if (suffixChildren.length > 0) {
    output.push({ ...value, children: suffixChildren } as Descendant);
  }
  return output;
}

function replaceMarkerBlocks(
  nodes: Descendant[],
  replacement: Descendant[],
  marker: string,
): { nodes: Descendant[]; count: number } {
  let count = 0;
  const output: Descendant[] = [];

  for (const rawNode of nodes) {
    if (isMarkerBlock(rawNode, marker)) {
      output.push(...replacement);
      count += 1;
      continue;
    }

    const splitParagraph = replaceMarkerInParagraph(rawNode, replacement, marker);
    if (splitParagraph) {
      output.push(...splitParagraph);
      count += 1;
      continue;
    }

    if (!rawNode || typeof rawNode !== "object") {
      output.push(rawNode);
      continue;
    }

    const node = rawNode as UnknownPlateNode;
    if (!Array.isArray(node.children)) {
      output.push(rawNode);
      continue;
    }

    const nested = replaceMarkerBlocks(
      node.children as Descendant[],
      replacement,
      marker,
    );
    count += nested.count;
    output.push({
      ...(rawNode as unknown as Record<string, unknown>),
      children: nested.nodes,
    } as Descendant);
  }

  return { nodes: output, count };
}

function replaceDomRegionWithSlot(
  region: NonNullable<ReturnType<typeof findEscapedAsideDomRegions>>[number],
  marker: string,
): void {
  const ownerDocument = region.start.ownerDocument;
  if (!ownerDocument) return;

  const slot = ownerDocument.createElement("p");
  slot.textContent = marker;

  const afterRegion = region.end.nextSibling;
  let current: ChildNode | null = region.start;
  while (current) {
    const next: ChildNode | null = current.nextSibling;
    current.remove();
    if (current === region.end) break;
    current = next;
  }
  region.parent.insertBefore(slot, afterRegion);
}

/**
 * G1P-B-B：fragment 应用前按 plan 重新验证 path/type/既有字段/identity；
 * 任一失败返回 null，调用方保留 sanitized HTML。只改 caption/title/lang
 * 字段，不动节点数量、path、parent/order、正文、marks 或 URL。
 */
function applyFieldPatches(
  nodes: Descendant[],
  fusion: ClipboardAsideFusionPlan,
): Descendant[] | null {
  const { images, codes } = scanClipboardFragment(nodes);
  const imageByKey = new Map(images.map((img) => [img.key, img]));
  const codeByKey = new Map(codes.map((code) => [code.key, code]));

  const patches: Array<{ realPath: number[]; replacement: Descendant }> = [];
  for (const match of fusion.imageFieldMatches ?? []) {
    const scan = imageByKey.get(fieldKeyOf(match.blockPath, match.ordinal));
    if (!scan) return null;
    if (scan.node.type !== "img") return null;
    // identity 重验证：HTML src 仍是唯一 URL truth
    if (scan.node.url !== match.htmlSrc) return null;
    if (match.alt !== undefined) {
      // 既有字段重验证：alt 必须仍是 missing（caption 为空）
      if (imageCaptionText(scan.node) !== "") return null;
    }
    if (match.title !== undefined) {
      // 既有字段重验证：title 必须仍是 missing
      if (typeof scan.node.title === "string") return null;
    }
    patches.push({
      realPath: scan.realPath,
      replacement: {
        ...scan.node,
        ...(match.alt !== undefined ? { caption: [{ text: match.alt }] } : {}),
        ...(match.title !== undefined ? { title: match.title } : {}),
      } as Descendant,
    });
  }

  for (const match of fusion.codeLanguageMatches ?? []) {
    const scan = codeByKey.get(fieldKeyOf(match.blockPath));
    if (!scan) return null;
    if (scan.node.type !== "code_block") return null;
    // body 逐字守恒重验证
    if (codeBodyText(scan.node) !== match.body) return null;
    // lang 必须仍是 missing（HTML 胜出语义不变）
    if (typeof scan.node.lang === "string") return null;
    patches.push({
      realPath: scan.realPath,
      replacement: { ...scan.node, lang: match.language } as Descendant,
    });
  }

  let output = nodes;
  for (const patch of patches) {
    const patched = replaceAtPath(output, patch.realPath, patch.replacement);
    if (!patched) return null;
    output = patched;
  }
  return output;
}

function replaceAtPath(
  nodes: Descendant[],
  path: number[],
  replacement: Descendant,
): Descendant[] | null {
  const head = path[0];
  if (head === undefined) return null;
  const rest = path.slice(1);
  if (rest.length === 0) {
    return nodes.map((node, index) => (index === head ? replacement : node));
  }
  const target = nodes[head];
  if (!target || typeof target !== "object") return null;
  const children = (target as UnknownPlateNode).children;
  if (!Array.isArray(children)) return null;
  const patchedChildren = replaceAtPath(
    children as Descendant[],
    rest,
    replacement,
  );
  if (!patchedChildren) return null;
  return nodes.map((node, index) =>
    index === head
      ? ({ ...node, children: patchedChildren } as Descendant)
      : node,
  );
}

/**
 * Merge every validated Notion escaped-aside region at the Plate fragment
 * seam. Rich HTML is deserialized as one document; each DOM region is replaced
 * by an indexed slot, and its matching plain slice is parsed through the
 * existing Markdown/SourceCallout parser. No partial or full-document
 * HTML/plain fallback exists. G1P-B-B field matches are re-validated against
 * the fragment before any field is patched.
 */
export function deserializeHybridClipboardFragment(
  html: string,
  fusion: ClipboardAsideFusionPlan,
  dependencies: ClipboardFragmentFusionDependencies,
): Descendant[] | null {
  if (!html.trim()) return null;
  const hasCallouts = fusion.matches.length > 0;
  const hasFields =
    (fusion.imageFieldMatches?.length ?? 0) > 0 ||
    (fusion.codeLanguageMatches?.length ?? 0) > 0;
  if (!hasCallouts && !hasFields) return null;
  if (html.includes(FUSION_SLOT_TEXT_PREFIX)) return null;

  const document = new DOMParser().parseFromString(html, "text/html");
  const callouts: Descendant[][] = [];
  if (hasCallouts) {
    const regions = findEscapedAsideDomRegions(document.body);
    if (regions.length !== fusion.matches.length) return null;

    for (let index = 0; index < fusion.matches.length; index += 1) {
      const region = regions[index];
      const match = fusion.matches[index];
      if (!region || !match) return null;
      replaceDomRegionWithSlot(region, fusionSlotText(index));

      const callout = dependencies.deserializeMarkdown(match.plainAsideMarkdown);
      if (
        callout.length === 0 ||
        callout.filter(
          (node) =>
            Boolean(node) &&
            typeof node === "object" &&
            (node as UnknownPlateNode).type === "source_callout",
        ).length !== 1
      ) {
        return null;
      }
      callouts.push(callout);
    }
  }

  const rich = dependencies.deserializeHtml(document.body);
  let nodes = rich;
  for (let index = 0; index < callouts.length; index += 1) {
    const replacement = callouts[index];
    if (!replacement) return null;
    const replaced = replaceMarkerBlocks(
      nodes,
      replacement,
      fusionSlotText(index),
    );
    if (replaced.count !== 1) return null;
    nodes = replaced.nodes;
  }

  if (hasFields) {
    return applyFieldPatches(nodes, fusion);
  }
  return nodes;
}
