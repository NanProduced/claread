import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
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

function filteredFavorites(favoriteTargets: WebFavoriteTargetVm[], recordId: string) {
  return favoriteTargets.filter((favorite) => belongsToRecord(favorite.recordId, favorite.targetKey, recordId));
}

function primarySentenceIdFromAnnotation(annotation: WebAnnotationVm) {
  return annotation.sentenceId ?? annotation.segments[0]?.sentenceId ?? null;
}

function primarySentenceIdFromFavorite(favorite: WebFavoriteTargetVm) {
  return favorite.sentenceId ?? favorite.segments[0]?.sentenceId ?? null;
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

function rangeFromFavorite(favorite: WebFavoriteTargetVm): ReaderAssetRange[] {
  if (favorite.anchorType === "text_range") {
    if (
      !favorite.sentenceId ||
      typeof favorite.startOffset !== "number" ||
      typeof favorite.endOffset !== "number" ||
      favorite.startOffset >= favorite.endOffset
    ) {
      return [];
    }

    return [
      {
        assetKind: "favorite",
        assetId: favorite.id,
        targetKey: favorite.targetKey,
        sentenceId: favorite.sentenceId,
        selectedText: favorite.selectedText,
        startOffset: favorite.startOffset,
        endOffset: favorite.endOffset,
        textHash: favorite.textHash,
      },
    ];
  }

  if (favorite.anchorType !== "multi_text") {
    return [];
  }

  return favorite.segments
    .filter((segment) => segment.sentenceId && segment.startOffset < segment.endOffset)
    .map((segment) => ({
      assetKind: "favorite" as const,
      assetId: favorite.id,
      targetKey: favorite.targetKey,
      paragraphId: segment.paragraphId,
      sentenceId: segment.sentenceId,
      selectedText: segment.selectedText,
      startOffset: segment.startOffset,
      endOffset: segment.endOffset,
      textHash: segment.textHash,
    }));
}

function sentenceAssetSummary(
  annotations: WebAnnotationVm[],
  favoriteTargets: WebFavoriteTargetVm[],
): ReaderSentenceAssetSummary {
  return {
    annotations,
    favoriteTargets,
    hasHighlight: annotations.some((annotation) => annotation.type === "highlight"),
    hasNote: annotations.some((annotation) => Boolean(annotation.note)),
    favoriteCount: favoriteTargets.length,
  };
}

function sentenceAssetProjection(
  sentenceId: string,
  annotations: WebAnnotationVm[],
  favoriteTargets: WebFavoriteTargetVm[],
  annotationRanges: ReaderAssetRange[],
  favoriteRanges: ReaderAssetRange[],
  slipAnnotations: WebAnnotationVm[],
): ReaderSentenceAssetProjection {
  return {
    sentenceId,
    annotations,
    favoriteTargets,
    annotationRanges,
    favoriteRanges,
    slipAnnotations,
    hasHighlight: annotations.some((annotation) => annotation.type === "highlight"),
    hasNote: annotations.some((annotation) => Boolean(annotation.note)),
    favoriteCount: favoriteTargets.length,
    primaryHighlightAnnotation: annotations.find((annotation) => annotation.type === "highlight") ?? null,
    primaryNoteAnnotation: annotations.find((annotation) => Boolean(annotation.note)) ?? null,
    primaryFavorite: favoriteTargets[0] ?? null,
  };
}

export function projectReaderAssets({
  annotations,
  favoriteTargets,
  recordId,
}: ProjectReaderAssetsInput): ReaderAssetProjection {
  const annotationsBySentence = new Map<string, WebAnnotationVm[]>();
  const favoriteTargetsBySentence = new Map<string, WebFavoriteTargetVm[]>();
  const annotationRangesBySentence = new Map<string, ReaderAssetRange[]>();
  const favoriteRangesBySentence = new Map<string, ReaderAssetRange[]>();
  const slipAnnotationsBySentence = new Map<string, WebAnnotationVm[]>();

  filteredAnnotations(annotations, recordId).forEach((annotation) => {
    const sentenceIds =
      annotation.anchorType === "multi_text" && annotation.segments.length > 0
        ? Array.from(new Set(annotation.segments.map((segment) => segment.sentenceId).filter(Boolean)))
        : annotation.sentenceId
          ? [annotation.sentenceId]
          : [];

    sentenceIds.forEach((sentenceId) => addToMapArray(annotationsBySentence, sentenceId, annotation));
    rangeFromAnnotation(annotation).forEach((range) => addToMapArray(annotationRangesBySentence, range.sentenceId, range));

    if (annotation.note) {
      addToMapArray(slipAnnotationsBySentence, primarySentenceIdFromAnnotation(annotation), annotation);
    }
  });

  filteredFavorites(favoriteTargets, recordId).forEach((favorite) => {
    const sentenceIds =
      favorite.anchorType === "multi_text" && favorite.segments.length > 0
        ? Array.from(new Set(favorite.segments.map((segment) => segment.sentenceId).filter(Boolean)))
        : favorite.sentenceId
          ? [favorite.sentenceId]
          : [];

    sentenceIds.forEach((sentenceId) => addToMapArray(favoriteTargetsBySentence, sentenceId, favorite));
    rangeFromFavorite(favorite).forEach((range) => addToMapArray(favoriteRangesBySentence, range.sentenceId, range));
  });

  const sentenceIds = Array.from(
    new Set([
      ...annotationsBySentence.keys(),
      ...favoriteTargetsBySentence.keys(),
      ...annotationRangesBySentence.keys(),
      ...favoriteRangesBySentence.keys(),
      ...slipAnnotationsBySentence.keys(),
    ]),
  );

  const sentenceAssetProjectionBySentence = new Map<string, ReaderSentenceAssetProjection>();
  const sentenceAssetSummaryBySentence = new Map<string, ReaderSentenceAssetSummary>();

  sentenceIds.forEach((sentenceId) => {
    const sentenceAnnotations = annotationsBySentence.get(sentenceId) ?? [];
    const sentenceFavorites = favoriteTargetsBySentence.get(sentenceId) ?? [];
    const sentenceAnnotationRanges = annotationRangesBySentence.get(sentenceId) ?? [];
    const sentenceFavoriteRanges = favoriteRangesBySentence.get(sentenceId) ?? [];
    const sentenceSlipAnnotations = slipAnnotationsBySentence.get(sentenceId) ?? [];

    sentenceAssetSummaryBySentence.set(
      sentenceId,
      sentenceAssetSummary(sentenceAnnotations, sentenceFavorites),
    );
    sentenceAssetProjectionBySentence.set(
      sentenceId,
      sentenceAssetProjection(
        sentenceId,
        sentenceAnnotations,
        sentenceFavorites,
        sentenceAnnotationRanges,
        sentenceFavoriteRanges,
        sentenceSlipAnnotations,
      ),
    );
  });

  return {
    sentenceAssetProjectionBySentence,
    annotationRangesBySentence,
    favoriteRangesBySentence,
    slipAnnotationsBySentence,
    sentenceAssetSummaryBySentence,
  };
}
