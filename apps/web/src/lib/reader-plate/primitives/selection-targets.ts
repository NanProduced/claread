import {
  buildMultiTextTargetKey,
  buildSentenceTargetKey,
  buildTextRangeTargetKey,
} from "@claread/contracts";

import type { WebAnnotationVm } from "@/types/api/annotations";
import type { ReaderTextSelection } from "./selection-types";

function rangeContains(
  outerStart: number | null | undefined,
  outerEnd: number | null | undefined,
  innerStart: number,
  innerEnd: number,
) {
  return typeof outerStart === "number" && typeof outerEnd === "number" && outerStart <= innerStart && outerEnd >= innerEnd;
}

function rangesOverlap(
  leftStart: number | null | undefined,
  leftEnd: number | null | undefined,
  rightStart: number,
  rightEnd: number,
) {
  return typeof leftStart === "number" && typeof leftEnd === "number" && leftStart < rightEnd && leftEnd > rightStart;
}

function multiTextContainsSelection(annotation: WebAnnotationVm, selection: ReaderTextSelection) {
  if (annotation.anchorType !== "multi_text") {
    return false;
  }

  return selection.segments.every((selectedSegment) =>
    annotation.segments.some(
      (segment) =>
        segment.sentenceId === selectedSegment.sentenceId &&
        rangeContains(segment.startOffset, segment.endOffset, selectedSegment.startOffset, selectedSegment.endOffset),
    ),
  );
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
    return selectionSegmentsEqual(annotation, selection) || multiTextContainsSelection(annotation, selection);
  }

  if (annotation.anchorType === "sentence") {
    return annotation.sentenceId === selection.sentence.sentenceId;
  }

  if (annotation.anchorType === "multi_text") {
    return multiTextContainsSelection(annotation, selection);
  }

  return (
    annotation.anchorType === "text_range" &&
    annotation.sentenceId === selection.sentence.sentenceId &&
    rangeContains(annotation.startOffset, annotation.endOffset, selection.startOffset, selection.endOffset)
  );
}

export function annotationOverlapsSelection(annotation: WebAnnotationVm, selection: ReaderTextSelection) {
  if (selection.anchorType === "sentence") {
    return annotation.sentenceId === selection.sentence.sentenceId;
  }

  if (selection.anchorType === "multi_text") {
    return selection.segments.some((selectedSegment) => {
      if (annotation.anchorType === "multi_text") {
        return annotation.segments.some(
          (segment) =>
            segment.sentenceId === selectedSegment.sentenceId &&
            rangesOverlap(segment.startOffset, segment.endOffset, selectedSegment.startOffset, selectedSegment.endOffset),
        );
      }

      if (annotation.anchorType === "sentence") {
        return annotation.sentenceId === selectedSegment.sentenceId;
      }

      return (
        annotation.sentenceId === selectedSegment.sentenceId &&
        rangesOverlap(annotation.startOffset, annotation.endOffset, selectedSegment.startOffset, selectedSegment.endOffset)
      );
    });
  }

  if (annotation.anchorType === "multi_text") {
    return annotation.segments.some(
      (segment) =>
        segment.sentenceId === selection.sentence.sentenceId &&
        rangesOverlap(segment.startOffset, segment.endOffset, selection.startOffset, selection.endOffset),
    );
  }

  if (annotation.anchorType === "sentence") {
    return annotation.sentenceId === selection.sentence.sentenceId;
  }

  return (
    annotation.sentenceId === selection.sentence.sentenceId &&
    rangesOverlap(annotation.startOffset, annotation.endOffset, selection.startOffset, selection.endOffset)
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
