import type { WebAnnotationVm } from "@/types/api/annotations";
import type {
  ProjectReaderAssetsInput,
  ReaderAssetProjection,
  ReaderAssetRange,
  ReaderSentenceAssetProjection,
  ReaderSentenceAssetSummary,
} from "./types";

function belongsToRecord(candidateRecordId: string | null | undefined, targetKey: string, recordId: string) {
  if (candidateRecordId === recordId) {
    return true;
  }

  if (candidateRecordId !== null) {
    return false;
  }

  return targetKey.startsWith(`record:${recordId}:`);
}

function filteredAnnotations(annotations: WebAnnotationVm[], recordId: string) {
  return annotations.filter((annotation) => belongsToRecord(annotation.recordId, annotation.targetKey, recordId));
}

function addToMapArray<T>(map: Map<string, T[]>, key: string | null | undefined, value: T) {
  if (!key) {
    return;
  }

  const current = map.get(key) ?? [];
  map.set(key, [...current, value]);
}

function rangeFromAnnotation(annotation: WebAnnotationVm): ReaderAssetRange[] {
  if (annotation.anchorType === "text_range") {
    if (
      !annotation.sentenceId ||
      typeof annotation.startOffset !== "number" ||
      typeof annotation.endOffset !== "number" ||
      annotation.startOffset >= annotation.endOffset
    ) {
      return [];
    }

    return [
      {
        assetKind: "annotation",
        assetId: annotation.id,
        targetKey: annotation.targetKey,
        paragraphId: annotation.paragraphId,
        sentenceId: annotation.sentenceId,
        selectedText: annotation.selectedText,
        startOffset: annotation.startOffset,
        endOffset: annotation.endOffset,
        textHash: annotation.textHash,
        color: annotation.color,
        annotationType: annotation.type,
      },
    ];
  }

  if (annotation.anchorType !== "multi_text") {
    return [];
  }

  return annotation.segments
    .filter((segment) => segment.sentenceId && segment.startOffset < segment.endOffset)
    .map((segment) => ({
      assetKind: "annotation" as const,
      assetId: annotation.id,
      targetKey: annotation.targetKey,
      paragraphId: segment.paragraphId ?? annotation.paragraphId,
      sentenceId: segment.sentenceId,
      selectedText: segment.selectedText,
      startOffset: segment.startOffset,
      endOffset: segment.endOffset,
      textHash: segment.textHash,
      color: annotation.color,
      annotationType: annotation.type,
    }));
}

function sentenceAssetSummary(annotations: WebAnnotationVm[]): ReaderSentenceAssetSummary {
  return {
    annotations,
    hasHighlight: annotations.some((annotation) => annotation.type === "highlight"),
  };
}

function sentenceAssetProjection(
  sentenceId: string,
  annotations: WebAnnotationVm[],
  annotationRanges: ReaderAssetRange[],
): ReaderSentenceAssetProjection {
  return {
    sentenceId,
    annotations,
    annotationRanges,
    hasHighlight: annotations.some((annotation) => annotation.type === "highlight"),
    primaryHighlightAnnotation: annotations.find((annotation) => annotation.type === "highlight") ?? null,
  };
}

export function projectReaderAssets({
  annotations,
  recordId,
}: ProjectReaderAssetsInput): ReaderAssetProjection {
  const annotationsBySentence = new Map<string, WebAnnotationVm[]>();
  const annotationRangesBySentence = new Map<string, ReaderAssetRange[]>();

  filteredAnnotations(annotations, recordId).forEach((annotation) => {
    const sentenceIds =
      annotation.anchorType === "multi_text" && annotation.segments.length > 0
        ? Array.from(new Set(annotation.segments.map((segment) => segment.sentenceId).filter(Boolean)))
        : annotation.sentenceId
          ? [annotation.sentenceId]
          : [];

    sentenceIds.forEach((sentenceId) => addToMapArray(annotationsBySentence, sentenceId, annotation));
    rangeFromAnnotation(annotation).forEach((range) => addToMapArray(annotationRangesBySentence, range.sentenceId, range));
  });

  const sentenceIds = Array.from(
    new Set([
      ...annotationsBySentence.keys(),
      ...annotationRangesBySentence.keys(),
    ]),
  );

  const sentenceAssetProjectionBySentence = new Map<string, ReaderSentenceAssetProjection>();
  const sentenceAssetSummaryBySentence = new Map<string, ReaderSentenceAssetSummary>();

  sentenceIds.forEach((sentenceId) => {
    const sentenceAnnotations = annotationsBySentence.get(sentenceId) ?? [];
    const sentenceAnnotationRanges = annotationRangesBySentence.get(sentenceId) ?? [];

    sentenceAssetSummaryBySentence.set(
      sentenceId,
      sentenceAssetSummary(sentenceAnnotations),
    );
    sentenceAssetProjectionBySentence.set(
      sentenceId,
      sentenceAssetProjection(
        sentenceId,
        sentenceAnnotations,
        sentenceAnnotationRanges,
      ),
    );
  });

  return {
    sentenceAssetProjectionBySentence,
    annotationRangesBySentence,
    sentenceAssetSummaryBySentence,
  };
}
