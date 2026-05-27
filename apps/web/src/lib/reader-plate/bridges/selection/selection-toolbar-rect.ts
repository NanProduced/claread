import {
  copyDomRect,
  firstUsableRangeRect,
  rectForTextOffsets,
} from "../../primitives";
import type { ReaderTextSelection } from "../../primitives";

function sentenceTextElementForSelection(
  articleElement: HTMLElement,
  sentenceId: string,
): HTMLElement | null {
  return articleElement.querySelector<HTMLElement>(
    `[data-reader-anchor="sentence"][data-sentence-id="${CSS.escape(sentenceId)}"] [data-reader-sentence-text="true"]`,
  );
}

export function selectionToolbarRectForReaderSelection(
  articleElement: HTMLElement | null,
  selection: ReaderTextSelection,
): DOMRect {
  if (selection.range) {
    const liveRangeRect = firstUsableRangeRect(selection.range);
    if (liveRangeRect) {
      return liveRangeRect;
    }
  }

  if (!articleElement) {
    return selection.rect;
  }

  const sentenceTextElement = sentenceTextElementForSelection(articleElement, selection.sentence.sentenceId);
  if (!sentenceTextElement) {
    return selection.rect;
  }

  if (selection.anchorType === "sentence") {
    return copyDomRect(sentenceTextElement.getBoundingClientRect());
  }

  if (selection.anchorType === "text_range") {
    const rangeRect = rectForTextOffsets(sentenceTextElement, selection.startOffset, selection.endOffset);
    if (rangeRect) {
      return rangeRect;
    }
  }

  return selection.rect;
}
