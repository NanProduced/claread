import {
  buildMultiTextTargetKey,
  buildSentenceTargetKey,
  buildTextRangeTargetKey,
} from "@claread/contracts";

import type { WebAnnotationVm } from "@/types/api/annotations";
import type { ReaderTextSelection } from "./selection-types";

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

  if (annotation.anchorType === "sentence") {
    return annotation.sentenceId === selection.sentence.sentenceId;
  }

  return (
    annotation.anchorType === "text_range" &&
    annotation.sentenceId === selection.sentence.sentenceId &&
    typeof annotation.startOffset === "number" &&
    typeof annotation.endOffset === "number" &&
    annotation.startOffset <= selection.startOffset &&
    annotation.endOffset >= selection.endOffset
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
