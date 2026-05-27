import { TEXT_RANGE_HASH_ALGORITHM, TEXT_RANGE_OFFSET_UNIT } from "@claread/contracts";

import type { WebAnnotationCreateRequest, WebAnnotationVm } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import type { ReaderTextSelection } from "../../primitives";
import { hashAnchorText, targetKeyForSelection } from "../../primitives";
import type {
  AnnotationRequestFromAnchorPayloadOptions,
  ReaderAnchorPayload,
  ReaderAnchorSegment,
  ReaderTargetRef,
} from "./types";

function recordIdFromTargetKey(targetKey: string): string {
  const match = targetKey.match(/^record:([^:]+):/);
  return match?.[1] ?? "";
}

function validRangeSegment(segment: {
  sentenceId: string;
  startOffset: number;
  endOffset: number;
}): boolean {
  return Boolean(segment.sentenceId) && segment.startOffset < segment.endOffset;
}

function normalizeRangeSegments(
  segments: Array<{
    paragraphId?: string | null;
    sentenceId: string;
    selectedText?: string | null;
    startOffset: number;
    endOffset: number;
    textHash?: string | null;
  }>,
): ReaderAnchorSegment[] {
  return segments.filter(validRangeSegment).map((segment) => ({
    paragraphId: segment.paragraphId ?? null,
    sentenceId: segment.sentenceId,
    selectedText: segment.selectedText ?? null,
    startOffset: segment.startOffset,
    endOffset: segment.endOffset,
    textHash: segment.textHash ?? null,
  }));
}

function supportedAnnotationAnchorType(annotation: WebAnnotationVm): ReaderAnchorPayload["anchorType"] | null {
  if (annotation.anchorType === "sentence" || annotation.anchorType === "text_range" || annotation.anchorType === "multi_text") {
    return annotation.anchorType;
  }

  if (annotation.anchorType === "paragraph" && annotation.sentenceId) {
    return "sentence";
  }

  return null;
}

function payloadJsonForTextRange(
  payload: ReaderAnchorPayload,
  sentenceTextById: ReadonlyMap<string, string> | undefined,
  translationBySentence: ReadonlyMap<string, { sentenceId: string; translationZh: string }> | undefined,
) {
  if (
    !payload.sentenceId ||
    typeof payload.startOffset !== "number" ||
    typeof payload.endOffset !== "number"
  ) {
    return undefined;
  }

  const sentenceText = sentenceTextById?.get(payload.sentenceId);
  return {
    offset_unit: TEXT_RANGE_OFFSET_UNIT,
    text_hash_algorithm: TEXT_RANGE_HASH_ALGORITHM,
    selected_text_hash: payload.textHash ?? null,
    sentence_text_hash: sentenceText ? hashAnchorText(sentenceText) : null,
    prefix: sentenceText ? sentenceText.slice(0, payload.startOffset).slice(-32) : "",
    suffix: sentenceText ? sentenceText.slice(payload.endOffset, payload.endOffset + 32) : "",
    created_client: "web",
    translation: payload.sentenceId ? translationBySentence?.get(payload.sentenceId) ?? null : null,
  };
}

function payloadJsonForMultiText(
  payload: ReaderAnchorPayload,
  translationBySentence: ReadonlyMap<string, { sentenceId: string; translationZh: string }> | undefined,
) {
  return {
    offset_unit: TEXT_RANGE_OFFSET_UNIT,
    text_hash_algorithm: TEXT_RANGE_HASH_ALGORITHM,
    created_client: "web",
    translation: payload.sentenceId ? translationBySentence?.get(payload.sentenceId) ?? null : null,
    segments: (payload.segments ?? []).map((segment) => ({
      paragraph_id: segment.paragraphId ?? null,
      sentence_id: segment.sentenceId,
      selected_text: segment.selectedText ?? "",
      start_offset: segment.startOffset,
      end_offset: segment.endOffset,
      text_hash: segment.textHash ?? null,
    })),
  };
}

export function anchorPayloadFromSelection(
  recordId: string,
  selection: ReaderTextSelection,
): ReaderAnchorPayload {
  return {
    anchorType: selection.anchorType,
    targetKey: targetKeyForSelection(recordId, selection),
    recordId,
    paragraphId: selection.sentence.paragraphId,
    sentenceId: selection.sentence.sentenceId,
    selectedText: selection.selectedText,
    startOffset: selection.anchorType === "text_range" ? selection.startOffset : null,
    endOffset: selection.anchorType === "text_range" ? selection.endOffset : null,
    textHash: selection.anchorType === "text_range" ? selection.textHash : null,
    segments:
      selection.anchorType === "multi_text"
        ? normalizeRangeSegments(selection.segments)
        : selection.anchorType === "text_range"
          ? normalizeRangeSegments([
              {
                paragraphId: selection.sentence.paragraphId,
                sentenceId: selection.sentence.sentenceId,
                selectedText: selection.selectedText,
                startOffset: selection.startOffset,
                endOffset: selection.endOffset,
                textHash: selection.textHash,
              },
            ])
          : undefined,
    metadata: {
      offsetUnit: TEXT_RANGE_OFFSET_UNIT,
      textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
      source: "reader_selection",
      originType: selection.anchorType === "sentence" ? "sentence" : selection.anchorType,
    },
  };
}

export function anchorPayloadFromSentence(recordId: string, sentence: SentenceModel): ReaderAnchorPayload {
  return {
    anchorType: "sentence",
    targetKey: `record:${recordId}:sentence:${sentence.sentenceId}`,
    recordId,
    paragraphId: sentence.paragraphId,
    sentenceId: sentence.sentenceId,
    selectedText: sentence.text,
    metadata: {
      offsetUnit: TEXT_RANGE_OFFSET_UNIT,
      textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
      source: "sentence_ref",
      originType: "sentence",
    },
  };
}

export function anchorPayloadFromAnnotation(annotation: WebAnnotationVm): ReaderAnchorPayload {
  const anchorType = supportedAnnotationAnchorType(annotation) ?? "sentence";
  return {
    anchorType,
    targetKey: annotation.targetKey,
    recordId: annotation.recordId ?? recordIdFromTargetKey(annotation.targetKey),
    paragraphId: annotation.paragraphId,
    sentenceId: annotation.sentenceId,
    selectedText: annotation.selectedText,
    startOffset: anchorType === "text_range" ? annotation.startOffset : null,
    endOffset: anchorType === "text_range" ? annotation.endOffset : null,
    textHash: anchorType === "text_range" ? annotation.textHash : null,
    segments:
      anchorType === "multi_text"
        ? normalizeRangeSegments(annotation.segments)
        : anchorType === "text_range" &&
            annotation.sentenceId &&
            typeof annotation.startOffset === "number" &&
            typeof annotation.endOffset === "number"
          ? normalizeRangeSegments([
              {
                paragraphId: annotation.paragraphId,
                sentenceId: annotation.sentenceId,
                selectedText: annotation.selectedText,
                startOffset: annotation.startOffset,
                endOffset: annotation.endOffset,
                textHash: annotation.textHash,
              },
            ])
          : undefined,
    metadata: {
      offsetUnit: TEXT_RANGE_OFFSET_UNIT,
      textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
      source: "loaded_annotation",
      originType: "user_annotation",
    },
  };
}

export function annotationRequestFromAnchorPayload(
  payload: ReaderAnchorPayload,
  options: AnnotationRequestFromAnchorPayloadOptions = {},
): WebAnnotationCreateRequest {
  return {
    recordId: payload.recordId,
    paragraphId: payload.paragraphId ?? undefined,
    sentenceId: payload.sentenceId ?? undefined,
    selectedText: payload.selectedText,
    anchorType: payload.anchorType,
    startOffset: payload.anchorType === "text_range" ? payload.startOffset ?? null : null,
    endOffset: payload.anchorType === "text_range" ? payload.endOffset ?? null : null,
    textHash: payload.anchorType === "text_range" ? payload.textHash ?? null : null,
    segments:
      payload.anchorType === "multi_text"
        ? (payload.segments ?? []).map((segment) => ({
            paragraphId: segment.paragraphId ?? undefined,
            sentenceId: segment.sentenceId,
            selectedText: segment.selectedText ?? "",
            startOffset: segment.startOffset,
            endOffset: segment.endOffset,
            textHash: segment.textHash ?? "",
          }))
        : undefined,
    color: options.color,
    payloadJson:
      payload.anchorType === "text_range"
        ? payloadJsonForTextRange(payload, options.sentenceTextById, options.translationBySentence)
        : payload.anchorType === "multi_text"
          ? payloadJsonForMultiText(payload, options.translationBySentence)
          : undefined,
  };
}

export function anchorPayloadFromTargetRef(ref: ReaderTargetRef): ReaderAnchorPayload {
  if (ref.kind === "sentence") {
    return anchorPayloadFromSentence(ref.recordId, ref.sentence);
  }

  if (ref.kind === "text_range" || ref.kind === "multi_text") {
    return anchorPayloadFromSelection(ref.recordId, ref.selection);
  }

  if (ref.kind === "user_annotation") {
    return anchorPayloadFromAnnotation(ref.annotation);
  }

  const exhaustive: never = ref;
  return exhaustive;
}
