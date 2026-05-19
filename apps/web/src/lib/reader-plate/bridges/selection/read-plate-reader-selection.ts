import type { SentenceModel } from "@/types/view/ReaderMockVm";
import {
  firstUsableRangeRect,
  hashAnchorText,
  textOffsetWithinElement,
} from "../../primitives";
import type {
  ReaderSelectionSegment,
  ReaderTextSelection,
} from "../../primitives";

function elementFromNode(node: Node): Element | null {
  return node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
}

function sentenceTextElementsInRange(articleElement: HTMLElement, range: Range) {
  return Array.from(
    articleElement.querySelectorAll<HTMLElement>("[data-reader-sentence-text='true']"),
  ).filter((element) => range.intersectsNode(element));
}

function buildSelectionSegment(
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

export function readPlateReaderSelection(
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
  const startSentenceTextElement = elementFromNode(range.startContainer)?.closest<HTMLElement>(
    "[data-reader-sentence-text='true']",
  );
  const endSentenceTextElement = elementFromNode(range.endContainer)?.closest<HTMLElement>(
    "[data-reader-sentence-text='true']",
  );
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

  if (
    sentenceTextElement &&
    sentenceTextElement.contains(range.startContainer) &&
    sentenceTextElement.contains(range.endContainer)
  ) {
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

    const segment = buildSelectionSegment(sentence, startOffset, endOffset);
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

    const segment = buildSelectionSegment(sentence, startOffset, endOffset);
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
