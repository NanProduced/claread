import type { Descendant } from "platejs";

const SAFE_EMOJI_GRAPHEME_RE =
  /^(?:\p{Extended_Pictographic}|\p{Emoji_Presentation}|\p{Regional_Indicator}|\p{Emoji_Modifier}|\uFE0F|\u20E3|\u200D)+$/u;

type UnknownNode = {
  type?: unknown;
  text?: unknown;
  children?: unknown;
  displayIcon?: unknown;
};

function graphemes(value: string): string[] {
  const Segmenter = (
    Intl as typeof Intl & {
      Segmenter?: new (
        locales?: string | string[],
        options?: { granularity?: "grapheme" },
      ) => { segment(input: string): Iterable<{ segment: string }> };
    }
  ).Segmenter;

  if (Segmenter) {
    return Array.from(
      new Segmenter(undefined, { granularity: "grapheme" }).segment(value),
      (part) => part.segment,
    );
  }
  return Array.from(value);
}

/** Return true only for one safe Unicode emoji grapheme. */
export function isSafeCalloutEmoji(value: string): boolean {
  const trimmed = value.trim();
  return (
    trimmed === value &&
    graphemes(value).length === 1 &&
    SAFE_EMOJI_GRAPHEME_RE.test(value)
  );
}

function collectText(node: unknown): string {
  if (!node || typeof node !== "object") return "";
  const value = node as UnknownNode;
  if (typeof value.text === "string") return value.text;
  if (!Array.isArray(value.children)) return "";
  return value.children.map(collectText).join("");
}

function isParagraph(node: unknown): node is UnknownNode {
  if (!node || typeof node !== "object") return false;
  const type = (node as UnknownNode).type;
  return type === "p" || type === "paragraph";
}

export function extractCalloutDisplayIcon(children: Descendant[]): {
  displayIcon: string | null;
  children: Descendant[];
} {
  const first = children[0];
  if (!isParagraph(first)) {
    return { displayIcon: null, children };
  }

  const text = collectText(first);
  if (!isSafeCalloutEmoji(text)) {
    return { displayIcon: null, children };
  }

  return { displayIcon: text, children: children.slice(1) };
}

/** Normalize every source_callout in a Plate tree to icon metadata + body. */
export function normalizeCalloutDisplayIcons(nodes: Descendant[]): Descendant[] {
  return nodes.map((rawNode) => {
    if (!rawNode || typeof rawNode !== "object") return rawNode;

    const node = rawNode as UnknownNode;
    const nestedChildren = Array.isArray(node.children)
      ? normalizeCalloutDisplayIcons(node.children as Descendant[])
      : undefined;
    const withNested = nestedChildren
      ? ({ ...rawNode, children: nestedChildren } as UnknownNode)
      : (rawNode as UnknownNode);

    if (node.type !== "source_callout") return withNested as Descendant;

    const extracted = extractCalloutDisplayIcon(
      (withNested.children as Descendant[] | undefined) ?? [],
    );
    const existingIcon =
      typeof withNested.displayIcon === "string" &&
      isSafeCalloutEmoji(withNested.displayIcon)
        ? withNested.displayIcon
        : null;
    const displayIcon = existingIcon ?? extracted.displayIcon;
    return {
      ...withNested,
      ...(displayIcon ? { displayIcon } : {}),
      children: extracted.displayIcon
        ? extracted.children
        : ((withNested.children as Descendant[] | undefined) ?? []),
    } as Descendant;
  });
}
