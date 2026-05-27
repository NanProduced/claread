import type { WebAnnotationVm } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import type { ReaderTextSelection } from "../../primitives";
import {
  anchorPayloadFromAnnotation,
  anchorPayloadFromSelection,
  anchorPayloadFromSentence,
  anchorPayloadFromTargetRef,
} from "../assets";
import type { ReaderTargetRef } from "../assets";

export {
  anchorPayloadFromAnnotation,
  anchorPayloadFromSelection,
  anchorPayloadFromSentence,
  anchorPayloadFromTargetRef,
};

export function selectionToTargetRef(
  recordId: string,
  selection: ReaderTextSelection,
): ReaderTargetRef {
  if (selection.anchorType === "sentence") {
    return {
      kind: "sentence",
      recordId,
      sentence: selection.sentence,
    };
  }

  if (selection.anchorType === "multi_text") {
    return {
      kind: "multi_text",
      recordId,
      selection,
    };
  }

  return {
    kind: "text_range",
    recordId,
    selection,
  };
}

export function sentenceToTargetRef(recordId: string, sentence: SentenceModel): ReaderTargetRef {
  return {
    kind: "sentence",
    recordId,
    sentence,
  };
}

export function annotationToTargetRef(annotation: WebAnnotationVm): ReaderTargetRef {
  return {
    kind: "user_annotation",
    annotation,
  };
}
