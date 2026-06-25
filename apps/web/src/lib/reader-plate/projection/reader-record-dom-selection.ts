import { computeUtf16FNV1a } from "@claread/contracts";

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import {
  anchorDraftsForSelection,
  type ReaderRecordAnchorDraft,
  type ReaderRecordAnchorDraftSelectionSegment,
} from "./reader-record-anchor-draft";

const STABLE_SOURCE_LEAF_SELECTOR = '[data-reader-record-leaf="segment_text"]';

export interface ReaderRecordSelectionAnchorBridgeResult {
  drafts: ReaderRecordAnchorDraft[];
  selectedText: string;
  anchorType: "text_range" | "multi_text";
  segments: ReaderRecordAnchorDraftSelectionSegment[];
  sentenceId: string;
  contextSentence: string;
  supportedSingleRange: boolean;
  rect: DOMRect | null;
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

function stableLeavesInRange(
  rootElement: HTMLElement,
  range: Range,
): HTMLElement[] {
  const leaves = Array.from(
    rootElement.querySelectorAll<HTMLElement>(STABLE_SOURCE_LEAF_SELECTOR),
  );
  return leaves.filter((leaf) => {
    try {
      return range.intersectsNode(leaf);
    } catch {
      return false;
    }
  });
}

function leafText(leaf: HTMLElement): string {
  return leaf.textContent ?? "";
}

function offsetWithinLeaf(
  leaf: HTMLElement,
  node: Node,
  offset: number,
): number | null {
  const textNodes: Text[] = [];
  const walker = document.createTreeWalker(leaf, NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();
  while (current) {
    textNodes.push(current as Text);
    current = walker.nextNode();
  }

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

function segmentStartOffset(leaf: HTMLElement): number {
  const raw = leaf.dataset.segmentStartUtf16;
  if (raw === undefined) {
    return 0;
  }
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function anchorSegmentId(leaf: HTMLElement): string | null {
  return leaf.dataset.anchorSegmentId ?? null;
}

function lookupSegmentMetadata(
  snapshot: ReaderPlateSnapshotDto,
  anchorSegmentId: string,
): { unitId: string; sentenceId: string } | null {
  const segment = snapshot.anchor_segments.find(
    (item) => item.anchor_segment_id === anchorSegmentId,
  );
  if (!segment) {
    return null;
  }
  return { unitId: segment.unit_id, sentenceId: segment.sentence_id };
}

function groupLeavesBySegment(
  leaves: HTMLElement[],
): Map<string, HTMLElement[]> {
  const groups = new Map<string, HTMLElement[]>();
  for (const leaf of leaves) {
    const segmentId = anchorSegmentId(leaf);
    if (!segmentId) {
      continue;
    }
    const list = groups.get(segmentId) ?? [];
    list.push(leaf);
    groups.set(segmentId, list);
  }
  return groups;
}

function buildSegmentFromGroup(
  leaves: HTMLElement[],
  startLeaf: HTMLElement,
  endLeaf: HTMLElement,
  range: Range,
  snapshot: ReaderPlateSnapshotDto,
): ReaderRecordAnchorDraftSelectionSegment | null {
  if (leaves.length === 0) {
    return null;
  }

  const segmentId = anchorSegmentId(startLeaf);
  if (!segmentId) {
    return null;
  }

  const metadata = lookupSegmentMetadata(snapshot, segmentId);
  if (!metadata) {
    return null;
  }

  const startLeafBase = segmentStartOffset(startLeaf);
  const endLeafBase = segmentStartOffset(endLeaf);

  const startOffsetInLeaf =
    startLeaf === range.startContainer.parentElement ||
    startLeaf.contains(range.startContainer)
      ? offsetWithinLeaf(startLeaf, range.startContainer, range.startOffset)
      : 0;
  const endOffsetInLeaf =
    endLeaf === range.endContainer.parentElement ||
    endLeaf.contains(range.endContainer)
      ? offsetWithinLeaf(endLeaf, range.endContainer, range.endOffset)
      : leafText(endLeaf).length;

  if (startOffsetInLeaf === null || endOffsetInLeaf === null) {
    return null;
  }

  const startOffset = startLeafBase + startOffsetInLeaf;
  const endOffset = endLeafBase + endOffsetInLeaf;

  if (endOffset <= startOffset) {
    return null;
  }

  const selectedText = leaves
    .map((leaf) => {
      const text = leafText(leaf);
      const base = segmentStartOffset(leaf);
      const leafStart = Math.max(startOffset, base) - base;
      const leafEnd = Math.min(endOffset, base + text.length) - base;
      if (leafEnd <= leafStart) {
        return "";
      }
      return text.slice(leafStart, leafEnd);
    })
    .join("");

  if (!selectedText.trim()) {
    return null;
  }

  return {
    paragraphId: metadata.unitId,
    sentenceId: metadata.sentenceId,
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
  const startLeaf = stableLeafForPoint(range.startContainer);
  const endLeaf = stableLeafForPoint(range.endContainer);

  if (
    !startLeaf ||
    !endLeaf ||
    !rootElement.contains(startLeaf) ||
    !rootElement.contains(endLeaf)
  ) {
    return null;
  }

  const leaves = stableLeavesInRange(rootElement, range);
  if (leaves.length === 0) {
    return null;
  }

  const groupedLeaves = groupLeavesBySegment(leaves);
  const segments: ReaderRecordAnchorDraftSelectionSegment[] = [];
  let contextSentenceText = "";

  for (const [segmentId, groupLeaves] of groupedLeaves) {
    const firstLeaf = groupLeaves[0];
    const lastLeaf = groupLeaves[groupLeaves.length - 1];

    const segment = buildSegmentFromGroup(
      groupLeaves,
      firstLeaf,
      lastLeaf,
      range,
      snapshot,
    );
    if (!segment) {
      continue;
    }
    segments.push(segment);

    if (segmentId === anchorSegmentId(startLeaf)) {
      contextSentenceText = groupLeaves.map(leafText).join("");
    }
  }

  if (segments.length === 0) {
    return null;
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

  const rect = range.getBoundingClientRect();

  return {
    drafts,
    selectedText,
    anchorType: segments.length === 1 ? "text_range" : "multi_text",
    segments,
    sentenceId: segments[0]?.sentenceId ?? "",
    contextSentence: contextSentenceText,
    supportedSingleRange: drafts.length === 1,
    rect,
  };
}
