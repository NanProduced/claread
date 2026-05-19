import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import {
  anchorPayloadFromAnnotation,
  anchorPayloadFromFavorite,
  anchorPayloadFromTargetRef,
} from "./adapters";
import type {
  ReaderAnchorPayload,
  ReaderJumpContext,
  ReaderJumpRangeSegment,
  ReaderJumpTarget,
  ReaderJumpTargetType,
  ReaderTargetRef,
} from "./types";

type ParsedTargetKey =
  | {
      anchorType: "sentence";
      targetKey: string;
      recordId: string;
      sentenceId: string;
    }
  | {
      anchorType: "text_range";
      targetKey: string;
      recordId: string;
      sentenceId: string;
      startOffset: number;
      endOffset: number;
      textHash: string;
    }
  | {
      anchorType: "multi_text";
      targetKey: string;
      recordId: string;
    };

function uniqueStrings(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.filter((value): value is string => Boolean(value))));
}

function normalizeRangeSegments(segments?: ReaderJumpRangeSegment[]): ReaderJumpRangeSegment[] {
  return (segments ?? []).filter((segment) => segment.sentenceId && segment.startOffset < segment.endOffset);
}

function jumpTargetFromRangeSegments(
  targetKey: string,
  targetType: ReaderAnchorPayload["anchorType"],
  segments: ReaderJumpRangeSegment[],
): ReaderJumpTarget | null {
  const normalizedSegments = normalizeRangeSegments(segments);
  if (normalizedSegments.length === 0) {
    return null;
  }

  return {
    targetType,
    targetKey,
    sentenceIds: uniqueStrings(normalizedSegments.map((segment) => segment.sentenceId)),
    paragraphIds: uniqueStrings(normalizedSegments.map((segment) => segment.paragraphId ?? null)),
    rangeSegments: normalizedSegments,
    primarySentenceId: normalizedSegments[0]?.sentenceId,
    highlightMode: targetType === "multi_text" ? "range_segments" : "range_segments",
    scrollStrategy: "center",
  };
}

function parsedTargetKeyToJumpTarget(parsed: ParsedTargetKey): ReaderJumpTarget | null {
  if (parsed.anchorType === "sentence") {
    return {
      targetType: "sentence",
      targetKey: parsed.targetKey,
      sentenceIds: [parsed.sentenceId],
      primarySentenceId: parsed.sentenceId,
      highlightMode: "sentence_frame",
      scrollStrategy: "center",
    };
  }

  if (parsed.anchorType === "text_range") {
    return jumpTargetFromRangeSegments(parsed.targetKey, "text_range", [
      {
        sentenceId: parsed.sentenceId,
        startOffset: parsed.startOffset,
        endOffset: parsed.endOffset,
        textHash: parsed.textHash,
      },
    ]);
  }

  return null;
}

function parseTargetKey(targetKey: string): ParsedTargetKey | null {
  const sentenceMatch = targetKey.match(/^record:([^:]+):sentence:([^:]+)$/);
  if (sentenceMatch?.[1] && sentenceMatch[2]) {
    return {
      anchorType: "sentence",
      targetKey,
      recordId: sentenceMatch[1],
      sentenceId: sentenceMatch[2],
    };
  }

  const rangeMatch = targetKey.match(/^record:([^:]+):range:([^:]+):(\d+):(\d+):([^:]+)$/);
  if (rangeMatch?.[1] && rangeMatch[2] && rangeMatch[3] && rangeMatch[4] && rangeMatch[5]) {
    return {
      anchorType: "text_range",
      targetKey,
      recordId: rangeMatch[1],
      sentenceId: rangeMatch[2],
      startOffset: Number(rangeMatch[3]),
      endOffset: Number(rangeMatch[4]),
      textHash: rangeMatch[5],
    };
  }

  const multiTextMatch = targetKey.match(/^record:([^:]+):multi_text:\d+:[^:]+$/);
  if (multiTextMatch?.[1]) {
    return {
      anchorType: "multi_text",
      targetKey,
      recordId: multiTextMatch[1],
    };
  }

  return null;
}

function jumpTargetFromPayload(payload: ReaderAnchorPayload): ReaderJumpTarget | null {
  if (payload.anchorType === "sentence") {
    if (!payload.sentenceId) {
      return null;
    }

    return {
      targetType: "sentence",
      targetKey: payload.targetKey,
      sentenceIds: [payload.sentenceId],
      paragraphIds: uniqueStrings([payload.paragraphId ?? null]),
      primarySentenceId: payload.sentenceId,
      highlightMode: "sentence_frame",
      scrollStrategy: "center",
    };
  }

  if (payload.anchorType === "text_range") {
    if (payload.segments && payload.segments.length > 0) {
      return jumpTargetFromRangeSegments(payload.targetKey, "text_range", payload.segments);
    }

    if (
      payload.sentenceId &&
      typeof payload.startOffset === "number" &&
      typeof payload.endOffset === "number" &&
      payload.startOffset < payload.endOffset
    ) {
      return jumpTargetFromRangeSegments(payload.targetKey, "text_range", [
        {
          paragraphId: payload.paragraphId ?? null,
          sentenceId: payload.sentenceId,
          selectedText: payload.selectedText,
          startOffset: payload.startOffset,
          endOffset: payload.endOffset,
          textHash: payload.textHash,
        },
      ]);
    }

    return null;
  }

  if (payload.segments && payload.segments.length > 0) {
    return jumpTargetFromRangeSegments(payload.targetKey, "multi_text", payload.segments);
  }

  if (payload.sentenceId) {
    return {
      targetType: "multi_text",
      targetKey: payload.targetKey,
      sentenceIds: [payload.sentenceId],
      paragraphIds: uniqueStrings([payload.paragraphId ?? null]),
      primarySentenceId: payload.sentenceId,
      highlightMode: "sentence_group",
      scrollStrategy: "center",
    };
  }

  return null;
}

function withTargetType(
  jumpTarget: ReaderJumpTarget | null,
  targetType: ReaderJumpTargetType,
): ReaderJumpTarget | null {
  if (!jumpTarget) {
    return null;
  }

  return {
    ...jumpTarget,
    targetType,
  };
}

function matchingAnnotation(targetKey: string, annotations: WebAnnotationVm[] | undefined) {
  return annotations?.find((annotation) => annotation.targetKey === targetKey) ?? null;
}

function matchingFavorite(targetKey: string, favoriteTargets: WebFavoriteTargetVm[] | undefined) {
  return favoriteTargets?.find((favorite) => favorite.targetKey === targetKey) ?? null;
}

export function jumpToTargetKey(targetKey: string, context: ReaderJumpContext = {}): ReaderJumpTarget | null {
  const annotation = matchingAnnotation(targetKey, context.annotations);
  if (annotation) {
    return withTargetType(jumpTargetFromPayload(anchorPayloadFromAnnotation(annotation)), "user_annotation");
  }

  const favorite = matchingFavorite(targetKey, context.favoriteTargets);
  if (favorite) {
    return withTargetType(jumpTargetFromPayload(anchorPayloadFromFavorite(favorite)), "favorite");
  }

  const parsed = parseTargetKey(targetKey);
  if (!parsed) {
    return null;
  }

  return parsedTargetKeyToJumpTarget(parsed);
}

export function jumpToAnchorPayload(payload: ReaderAnchorPayload): ReaderJumpTarget | null {
  return jumpTargetFromPayload(payload);
}

export function jumpToTargetRef(ref: ReaderTargetRef, context?: ReaderJumpContext): ReaderJumpTarget | null {
  if (ref.kind === "user_annotation") {
    return withTargetType(jumpTargetFromPayload(anchorPayloadFromAnnotation(ref.annotation)), "user_annotation");
  }

  if (ref.kind === "favorite") {
    return withTargetType(jumpTargetFromPayload(anchorPayloadFromFavorite(ref.favorite)), "favorite");
  }

  const payload = anchorPayloadFromTargetRef(ref);
  return jumpToAnchorPayload(payload) ?? (context && payload.targetKey ? jumpToTargetKey(payload.targetKey, context) : null);
}

export function resolveJumpTarget(
  input: string | ReaderAnchorPayload | ReaderTargetRef,
  context?: ReaderJumpContext,
): ReaderJumpTarget | null {
  if (typeof input === "string") {
    return jumpToTargetKey(input, context);
  }

  if ("kind" in input) {
    return jumpToTargetRef(input, context);
  }

  return jumpToAnchorPayload(input);
}
