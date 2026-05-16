import {
  buildMultiTextTargetKey,
  buildSentenceTargetKey,
  buildTextRangeTargetKey,
  computeUtf16FNV1a,
} from "@claread/contracts";

import type { WebAnnotationVm } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";

export interface ReaderSelectionSegment {
  paragraphId: string;
  sentenceId: string;
  sentence: SentenceModel;
  selectedText: string;
  startOffset: number;
  endOffset: number;
  textHash: string;
}

export type ReaderTextSelection =
  | {
      anchorType: "sentence";
      sentence: SentenceModel;
      selectedText: string;
      rect: DOMRect;
      range?: Range;
      segments: [ReaderSelectionSegment];
      startOffset: number;
      endOffset: number;
      textHash: string;
    }
  | {
      anchorType: "text_range";
      sentence: SentenceModel;
      selectedText: string;
      rect: DOMRect;
      range?: Range;
      segments: [ReaderSelectionSegment];
      startOffset: number;
      endOffset: number;
      textHash: string;
    }
  | {
      anchorType: "multi_text";
      sentence: SentenceModel;
      selectedText: string;
      rect: DOMRect;
      range?: Range;
      segments: ReaderSelectionSegment[];
      startOffset: number;
      endOffset: number;
      textHash: string;
    };

export function hashAnchorText(text: string): string {
  return computeUtf16FNV1a(text);
}

export function copyDomRect(rect: DOMRect): DOMRect {
  return DOMRect.fromRect({
    x: rect.x,
    y: rect.y,
    width: rect.width,
    height: rect.height,
  });
}

function selectionSegmentsEqual(annotation: WebAnnotationVm, selection: ReaderTextSelection) {
  if (annotation.anchorType !== "multi_text" || annotation.segments.length !== selection.segments.length) {
    return false;
  }

  return annotation.segments.every((segment, index) => {
    const selectedSegment = selection.segments[index];
    return (
      selectedSegment !== undefined &&
      segment.sentenceId === selectedSegment.sentenceId &&
      segment.startOffset === selectedSegment.startOffset &&
      segment.endOffset === selectedSegment.endOffset &&
      segment.textHash === selectedSegment.textHash
    );
  });
}

export function annotationMatchesSelection(annotation: WebAnnotationVm, selection: ReaderTextSelection) {
  if (selection.anchorType === "sentence") {
    return annotation.anchorType === "sentence" && annotation.sentenceId === selection.sentence.sentenceId;
  }

  if (selection.anchorType === "multi_text") {
    return selectionSegmentsEqual(annotation, selection);
  }

  return (
    annotation.anchorType === "text_range" &&
    annotation.sentenceId === selection.sentence.sentenceId &&
    typeof annotation.startOffset === "number" &&
    typeof annotation.endOffset === "number" &&
    annotation.startOffset === selection.startOffset &&
    annotation.endOffset === selection.endOffset &&
    annotation.textHash === selection.textHash
  );
}

export function targetKeyForSelection(recordId: string, selection: ReaderTextSelection) {
  if (selection.anchorType === "sentence") {
    return buildSentenceTargetKey(recordId, selection.sentence.sentenceId);
  }
  if (selection.anchorType === "multi_text") {
    return buildMultiTextTargetKey(recordId, selection.segments);
  }
  return buildTextRangeTargetKey(
    recordId,
    selection.sentence.sentenceId,
    selection.startOffset,
    selection.endOffset,
    selection.textHash,
  );
}

export function favoriteTargetForSelection(
  recordId: string,
  selection: ReaderTextSelection,
  annotation?: WebAnnotationVm | null,
) {
  if (annotation && annotationMatchesSelection(annotation, selection)) {
    return {
      targetType:
        annotation.anchorType === "sentence"
          ? "sentence"
          : annotation.anchorType === "multi_text"
            ? "multi_text"
            : "text_range",
      targetKey: annotation.targetKey,
    } as const;
  }

  return {
    targetType:
      selection.anchorType === "sentence"
        ? "sentence"
        : selection.anchorType === "multi_text"
          ? "multi_text"
          : "text_range",
    targetKey: targetKeyForSelection(recordId, selection),
  } as const;
}

function elementFromNode(node: Node): Element | null {
  return node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
}

export function firstUsableRangeRect(range: Range): DOMRect | null {
  const rects = Array.from(range.getClientRects());
  const rect = rects.find((item) => item.width > 0 && item.height > 0) ?? range.getBoundingClientRect();

  if (rect.width === 0 && rect.height === 0) {
    return null;
  }

  return copyDomRect(rect);
}

export function textOffsetWithinElement(element: HTMLElement, node: Node, offset: number): number | null {
  if (!element.contains(node)) {
    return null;
  }

  const range = document.createRange();
  range.selectNodeContents(element);
  try {
    range.setEnd(node, offset);
    return range.toString().length;
  } catch {
    return null;
  } finally {
    range.detach();
  }
}

function textNodePointAtOffset(
  element: HTMLElement,
  targetOffset: number,
): { node: Node; offset: number } | null {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let remaining = targetOffset;
  let current = walker.nextNode();

  while (current) {
    const length = current.textContent?.length ?? 0;
    if (remaining <= length) {
      return { node: current, offset: remaining };
    }
    remaining -= length;
    current = walker.nextNode();
  }

  return null;
}

export function rectForTextOffsets(
  element: HTMLElement,
  startOffset: number,
  endOffset: number,
): DOMRect | null {
  if (startOffset < 0 || endOffset <= startOffset) {
    return null;
  }

  const startPoint = textNodePointAtOffset(element, startOffset);
  const endPoint = textNodePointAtOffset(element, endOffset);
  if (!startPoint || !endPoint) {
    return null;
  }

  const range = document.createRange();
  try {
    range.setStart(startPoint.node, startPoint.offset);
    range.setEnd(endPoint.node, endPoint.offset);
    return firstUsableRangeRect(range);
  } catch {
    return null;
  } finally {
    range.detach();
  }
}

function sentenceTextElementsInRange(articleElement: HTMLElement, range: Range) {
  return Array.from(
    articleElement.querySelectorAll<HTMLElement>("[data-reader-sentence-text='true']"),
  ).filter((element) => range.intersectsNode(element));
}

function buildSelectionSegment(
  sentenceTextElement: HTMLElement,
  sentence: SentenceModel,
  startOffset: number,
  endOffset: number,
): ReaderSelectionSegment | null {
  if (startOffset < 0 || endOffset <= startOffset || endOffset > sentence.text.length) {
    return null;
  }
  const selectedText = sentence.text.slice(startOffset, endOffset);
  if (!selectedText.trim()) {
    return null;
  }
  return {
    paragraphId: sentence.paragraphId,
    sentenceId: sentence.sentenceId,
    sentence,
    selectedText,
    startOffset,
    endOffset,
    textHash: hashAnchorText(selectedText),
  };
}

export function readReaderSelection(
  articleElement: HTMLElement | null,
  sentenceById: Map<string, SentenceModel>,
): ReaderTextSelection | null {
  if (!articleElement) {
    return null;
  }

  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
    return null;
  }

  const range = selection.getRangeAt(0);
  const commonElement = elementFromNode(range.commonAncestorContainer);
  const sentenceTextElement = commonElement?.closest<HTMLElement>("[data-reader-sentence-text='true']");
  const startSentenceTextElement = elementFromNode(range.startContainer)?.closest<HTMLElement>("[data-reader-sentence-text='true']");
  const endSentenceTextElement = elementFromNode(range.endContainer)?.closest<HTMLElement>("[data-reader-sentence-text='true']");
  if (!startSentenceTextElement || !endSentenceTextElement) {
    return null;
  }
  if (!articleElement.contains(startSentenceTextElement) || !articleElement.contains(endSentenceTextElement)) {
    return null;
  }

  const rect = firstUsableRangeRect(range);
  if (!rect) {
    return null;
  }

  if (sentenceTextElement && sentenceTextElement.contains(range.startContainer) && sentenceTextElement.contains(range.endContainer)) {
    const sentenceElement = sentenceTextElement.closest<HTMLElement>("[data-reader-anchor='sentence']");
    const sentenceId = sentenceElement?.dataset.sentenceId;
    if (!sentenceId) {
      return null;
    }

    const sentence = sentenceById.get(sentenceId);
    if (!sentence) {
      return null;
    }

    const startOffset = textOffsetWithinElement(sentenceTextElement, range.startContainer, range.startOffset);
    const endOffset = textOffsetWithinElement(sentenceTextElement, range.endContainer, range.endOffset);
    if (startOffset === null || endOffset === null || startOffset >= endOffset) {
      return null;
    }

    const segment = buildSelectionSegment(sentenceTextElement, sentence, startOffset, endOffset);
    if (!segment) {
      return null;
    }

    if (startOffset === 0 && endOffset === sentence.text.length) {
      return {
        anchorType: "sentence",
        sentence,
        selectedText: sentence.text,
        startOffset,
        endOffset,
        textHash: hashAnchorText(sentence.text),
        rect,
        segments: [
          {
            ...segment,
            selectedText: sentence.text,
            textHash: hashAnchorText(sentence.text),
          },
        ],
      };
    }

    return {
      anchorType: "text_range",
      sentence,
      selectedText: segment.selectedText,
      startOffset,
      endOffset,
      textHash: segment.textHash,
      rect,
      range: range.cloneRange(),
      segments: [segment],
    };
  }

  const sentenceElements = sentenceTextElementsInRange(articleElement, range);
  if (sentenceElements.length < 2) {
    return null;
  }

  const segments: ReaderSelectionSegment[] = [];
  sentenceElements.forEach((sentenceTextEl, index) => {
    const sentenceElement = sentenceTextEl.closest<HTMLElement>("[data-reader-anchor='sentence']");
    const sentenceId = sentenceElement?.dataset.sentenceId;
    if (!sentenceId) {
      return;
    }
    const sentence = sentenceById.get(sentenceId);
    if (!sentence) {
      return;
    }

    const startOffset =
      index === 0
        ? textOffsetWithinElement(sentenceTextEl, range.startContainer, range.startOffset)
        : 0;
    const endOffset =
      index === sentenceElements.length - 1
        ? textOffsetWithinElement(sentenceTextEl, range.endContainer, range.endOffset)
        : sentence.text.length;
    if (startOffset === null || endOffset === null) {
      return;
    }

    const segment = buildSelectionSegment(sentenceTextEl, sentence, startOffset, endOffset);
    if (segment) {
      segments.push(segment);
    }
  });

  if (segments.length < 2) {
    return null;
  }

  const selectedText = selection.toString().trim();
  if (!selectedText) {
    return null;
  }

  return {
    anchorType: "multi_text",
    sentence: segments[0].sentence,
    selectedText,
    startOffset: segments[0].startOffset,
    endOffset: segments[segments.length - 1].endOffset,
    textHash: hashAnchorText(selectedText),
    rect,
    range: range.cloneRange(),
    segments,
  };
}
