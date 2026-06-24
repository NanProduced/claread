import { computeUtf16FNV1a } from "@claread/contracts";

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import {
  anchorDraftsForSelection,
  type ReaderRecordAnchorDraft,
  type ReaderRecordAnchorDraftSelectionSegment,
} from "./reader-record-anchor-draft";

const ANCHOR_SEGMENT_SELECTOR = '[data-reader-record-node="anchor-segment"]';
const STABLE_SOURCE_LEAF_SELECTOR = '[data-reader-record-leaf="segment_text"]';

export interface ReaderRecordSelectionAnchorBridgeResult {
  drafts: ReaderRecordAnchorDraft[];
  selectedText: string;
  anchorType: "text_range" | "multi_text";
  segments: ReaderRecordAnchorDraftSelectionSegment[];
  sentenceId: string;
  contextSentence: string;
  supportedSingleRange: boolean;
}

function elementFromNode(node: Node): Element | null {
  return node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
}

function stableLeafForPoint(node: Node): HTMLElement | null {
  return (
    elementFromNode(node)?.closest<HTMLElement>(STABLE_SOURCE_LEAF_SELECTOR) ??
    null
  );
}

function isStableSourcePoint(segmentElement: HTMLElement, node: Node): boolean {
  const leaf = stableLeafForPoint(node);
  return Boolean(leaf && segmentElement.contains(leaf));
}

function stableTextNodes(segmentElement: HTMLElement): Text[] {
  const nodes: Text[] = [];
  const walker = document.createTreeWalker(segmentElement, NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();

  while (current) {
    const textNode = current as Text;
    const parentElement = textNode.parentElement;
    if (parentElement?.closest(STABLE_SOURCE_LEAF_SELECTOR)) {
      nodes.push(textNode);
    }
    current = walker.nextNode();
  }

  return nodes;
}

function stableText(segmentElement: HTMLElement): string {
  return stableTextNodes(segmentElement)
    .map((node) => node.textContent ?? "")
    .join("");
}

function offsetWithinStableText(
  segmentElement: HTMLElement,
  node: Node,
  offset: number,
): number | null {
  const textNodes = stableTextNodes(segmentElement);
  let cursor = 0;

  for (const textNode of textNodes) {
    const textLength = textNode.textContent?.length ?? 0;
    if (textNode === node) {
      return cursor + Math.max(0, Math.min(offset, textLength));
    }
    cursor += textLength;
  }

  if (node.nodeType !== Node.ELEMENT_NODE) {
    return null;
  }

  const boundary = document.createRange();
  try {
    boundary.setStart(node, offset);
    boundary.collapse(true);

    cursor = 0;
    for (const textNode of textNodes) {
      const textLength = textNode.textContent?.length ?? 0;
      const textStart = document.createRange();
      try {
        textStart.setStart(textNode, 0);
        textStart.collapse(true);
        if (
          boundary.compareBoundaryPoints(Range.START_TO_START, textStart) <= 0
        ) {
          return cursor;
        }
      } finally {
        textStart.detach();
      }
      cursor += textLength;
    }
    return cursor;
  } catch {
    return null;
  } finally {
    boundary.detach();
  }
}

function anchorSegmentElementsInRange(
  rootElement: HTMLElement,
  range: Range,
): HTMLElement[] {
  return Array.from(
    rootElement.querySelectorAll<HTMLElement>(ANCHOR_SEGMENT_SELECTOR),
  ).filter((element) => {
    try {
      return range.intersectsNode(element);
    } catch {
      return false;
    }
  });
}

function segmentFromElement(
  element: HTMLElement,
  selectedText: string,
  startOffset: number,
  endOffset: number,
): ReaderRecordAnchorDraftSelectionSegment | null {
  const unitId = element.dataset.unitId;
  const sentenceId = element.dataset.sentenceId;
  if (!unitId || !sentenceId || endOffset <= startOffset || !selectedText.trim()) {
    return null;
  }

  return {
    paragraphId: unitId,
    sentenceId,
    selectedText,
    startOffset,
    endOffset,
    textHash: computeUtf16FNV1a(selectedText),
  };
}

export function readReaderRecordSelectionAnchorDrafts(
  rootElement: HTMLElement | null,
  snapshot: ReaderPlateSnapshotDto,
): ReaderRecordSelectionAnchorBridgeResult | null {
  if (!rootElement) {
    return null;
  }

  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
    return null;
  }

  const range = selection.getRangeAt(0);
  const startSegmentElement = elementFromNode(
    range.startContainer,
  )?.closest<HTMLElement>(ANCHOR_SEGMENT_SELECTOR);
  const endSegmentElement = elementFromNode(
    range.endContainer,
  )?.closest<HTMLElement>(ANCHOR_SEGMENT_SELECTOR);

  if (
    !startSegmentElement ||
    !endSegmentElement ||
    !rootElement.contains(startSegmentElement) ||
    !rootElement.contains(endSegmentElement) ||
    !isStableSourcePoint(startSegmentElement, range.startContainer) ||
    !isStableSourcePoint(endSegmentElement, range.endContainer)
  ) {
    return null;
  }

  const segmentElements = anchorSegmentElementsInRange(rootElement, range);
  if (segmentElements.length === 0) {
    return null;
  }

  const segments: ReaderRecordAnchorDraftSelectionSegment[] = [];
  for (const segmentElement of segmentElements) {
    const text = stableText(segmentElement);
    const startOffset =
      segmentElement === startSegmentElement
        ? offsetWithinStableText(
            segmentElement,
            range.startContainer,
            range.startOffset,
          )
        : 0;
    const endOffset =
      segmentElement === endSegmentElement
        ? offsetWithinStableText(
            segmentElement,
            range.endContainer,
            range.endOffset,
          )
        : text.length;

    if (startOffset === null || endOffset === null || endOffset <= startOffset) {
      return null;
    }

    const segment = segmentFromElement(
      segmentElement,
      text.slice(startOffset, endOffset),
      startOffset,
      endOffset,
    );
    if (!segment) {
      return null;
    }
    segments.push(segment);
  }

  const drafts = anchorDraftsForSelection(snapshot, { segments });
  if (drafts.length !== segments.length) {
    return null;
  }

  const selectedText = segments
    .map((segment) => segment.selectedText.trim())
    .filter(Boolean)
    .join(" ")
    .trim();

  if (!selectedText) {
    return null;
  }

  return {
    drafts,
    selectedText,
    anchorType: segments.length === 1 ? "text_range" : "multi_text",
    segments,
    sentenceId: segments[0]?.sentenceId ?? "",
    contextSentence: stableText(segmentElements[0]),
    supportedSingleRange: drafts.length === 1,
  };
}
