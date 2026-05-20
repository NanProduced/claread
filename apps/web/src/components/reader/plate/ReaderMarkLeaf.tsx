"use client";

import { memo, useMemo } from "react";
import type { ReactNode } from "react";
import type { RenderLeaf } from "platejs/react";
import {
  inspectIntentFromStructuredMark,
  lookupIntentFromMark,
  resolveLookupPreviewAnchor,
  type ReaderAssetRange,
  type ReaderJumpRangeSegment,
  type ReaderLookupIntent,
  type ReaderLookupPreviewAnchor,
  type ReaderStructuredInspectIntent,
} from "../../../lib/reader-plate";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { ReaderAnnotationVisibilityGroups } from "../settings";
import { readerMarkClassName } from "./shared";

interface ReaderMarkLeafProps {
  props: Parameters<RenderLeaf>[0];
  annotationVisibilityGroups: ReaderAnnotationVisibilityGroups;
  annotationRangesBySentence?: Map<string, ReaderAssetRange[]>;
  routeFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  activeAnalysisEntryId?: string | null;
  sentenceTextBySentence?: Map<string, string>;
  sourceContextBySentence?: Map<string, string | undefined>;
  onLookupIntent?: (
    intent: ReaderLookupIntent,
    anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) => void;
  onInspectIntent?: (
    intent: ReaderStructuredInspectIntent,
    anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) => void;
}

function routeFocusSegmentsForLeaf(
  sentenceId: string | undefined,
  leafStartOffset: number | undefined,
  leafEndOffset: number | undefined,
  routeFocusRangesBySentence: Map<string, ReaderJumpRangeSegment[]> | undefined,
) {
  if (!sentenceId || leafStartOffset === undefined || leafEndOffset === undefined || !routeFocusRangesBySentence) {
    return [];
  }

  return (routeFocusRangesBySentence.get(sentenceId) ?? [])
    .map((segment) => ({
      startOffset: Math.max(leafStartOffset, segment.startOffset),
      endOffset: Math.min(leafEndOffset, segment.endOffset),
    }))
    .filter((segment) => segment.startOffset < segment.endOffset)
    .sort((left, right) => left.startOffset - right.startOffset);
}

function renderLeafContent(
  text: string,
  leafStartOffset: number | undefined,
  focusedSegments: Array<{ startOffset: number; endOffset: number }>,
  annotationSegments: Array<{
    startOffset: number;
    endOffset: number;
    annotations: ReaderAssetRange[];
  }>,
): ReactNode {
  if (leafStartOffset === undefined || (focusedSegments.length === 0 && annotationSegments.length === 0)) {
    return text;
  }

  const leafEndOffset = leafStartOffset + text.length;
  const boundaries = new Set<number>([leafStartOffset, leafEndOffset]);
  focusedSegments.forEach((segment) => {
    boundaries.add(segment.startOffset);
    boundaries.add(segment.endOffset);
  });
  annotationSegments.forEach((segment) => {
    boundaries.add(segment.startOffset);
    boundaries.add(segment.endOffset);
  });

  const orderedBoundaries = Array.from(boundaries).sort((left, right) => left - right);
  const children: ReactNode[] = [];
  for (let index = 0; index < orderedBoundaries.length - 1; index += 1) {
    const segmentStart = orderedBoundaries[index];
    const segmentEnd = orderedBoundaries[index + 1];
    if (segmentStart === undefined || segmentEnd === undefined || segmentStart >= segmentEnd) {
      continue;
    }

    const segmentText = text.slice(segmentStart - leafStartOffset, segmentEnd - leafStartOffset);
    const overlappingFocus = focusedSegments.some(
      (segment) => segment.startOffset < segmentEnd && segment.endOffset > segmentStart,
    );
    const overlappingAnnotations = annotationSegments.flatMap((segment) =>
      segment.startOffset < segmentEnd && segment.endOffset > segmentStart ? segment.annotations : [],
    );

    if (!overlappingFocus && overlappingAnnotations.length === 0) {
      children.push(segmentText);
      continue;
    }

    const annotationClassName = classNameForAnnotations(overlappingAnnotations);
    const className = [annotationClassName, overlappingFocus ? "reader-route-focus-range" : ""]
      .filter(Boolean)
      .join(" ");
    children.push(
      <span
        key={`${segmentStart}-${segmentEnd}`}
        className={className || undefined}
        data-reader-annotation-ids={
          overlappingAnnotations.length > 0
            ? overlappingAnnotations.map((annotation) => annotation.assetId).join(",")
            : undefined
        }
        data-reader-annotation-count={
          overlappingAnnotations.length > 0 ? String(new Set(overlappingAnnotations.map((annotation) => annotation.assetId)).size) : undefined
        }
      >
        {segmentText}
      </span>,
    );
  }

  return children;
}

function annotationToneClass(color: WebAnnotationVm["color"] | null | undefined) {
  switch (color) {
    case "soft_green":
    case "sage_green":
      return "reader-user-range--soft-green";
    case "soft_blue":
      return "reader-user-range--soft-blue";
    case "soft_purple":
      return "reader-user-range--soft-purple";
    case "warm_yellow":
    default:
      return "reader-user-range--warm-yellow";
  }
}

function classNameForAnnotations(annotations: ReaderAssetRange[]) {
  const uniqueAnnotations = Array.from(new Map(annotations.map((annotation) => [annotation.assetId, annotation])).values());
  if (uniqueAnnotations.length === 0) {
    return "";
  }

  if (uniqueAnnotations.length === 1) {
    return `reader-user-range ${annotationToneClass(uniqueAnnotations[0]?.color ?? null)}`;
  }

  return "reader-user-range reader-user-range--stacked";
}

function annotationSegmentsForLeaf(
  sentenceId: string | undefined,
  leafStartOffset: number | undefined,
  leafEndOffset: number | undefined,
  annotationRangesBySentence: Map<string, ReaderAssetRange[]> | undefined,
) {
  if (!sentenceId || leafStartOffset === undefined || leafEndOffset === undefined || !annotationRangesBySentence) {
    return [];
  }

  return (annotationRangesBySentence.get(sentenceId) ?? [])
    .map((range) => ({
      startOffset: Math.max(leafStartOffset, range.startOffset),
      endOffset: Math.min(leafEndOffset, range.endOffset),
      annotations: [range],
    }))
    .filter((segment) => segment.startOffset < segment.endOffset)
    .sort((left, right) => left.startOffset - right.startOffset);
}

export const ReaderMarkLeaf = memo(function ReaderMarkLeaf({
  activeAnalysisEntryId = null,
  annotationRangesBySentence,
  annotationVisibilityGroups,
  onInspectIntent,
  onLookupIntent,
  props,
  routeFocusRangesBySentence,
  sentenceTextBySentence,
  sourceContextBySentence,
}: ReaderMarkLeafProps) {
  const leaf = props.leaf as Parameters<RenderLeaf>[0]["leaf"] & {
    readerMarkAnnotationType?: ReaderLookupIntent["annotationType"];
    readerMarkClickable?: boolean;
    readerMarkGlossary?: ReaderLookupIntent["glossary"];
    readerMarkId?: string;
    readerMarkLookupKind?: ReaderStructuredInspectIntent["lookupKind"];
    readerMarkLookupText?: string;
    readerMarkParentId?: string;
    readerMarkRenderType?: string;
    readerSentenceId?: string;
    readerTextStartOffset?: number;
    readerTextEndOffset?: number;
    readerMarkVisualTone?: Parameters<typeof readerMarkClassName>[0];
  };
  const focusedSegments = useMemo(
    () =>
      routeFocusSegmentsForLeaf(
        leaf.readerSentenceId,
        leaf.readerTextStartOffset,
        leaf.readerTextEndOffset,
        routeFocusRangesBySentence,
      ),
    [
      leaf.readerSentenceId,
      leaf.readerTextStartOffset,
      leaf.readerTextEndOffset,
      routeFocusRangesBySentence,
    ],
  );
  const annotationSegments = useMemo(
    () =>
      annotationSegmentsForLeaf(
        leaf.readerSentenceId,
        leaf.readerTextStartOffset,
        leaf.readerTextEndOffset,
        annotationRangesBySentence,
      ),
    [
      leaf.readerSentenceId,
      leaf.readerTextStartOffset,
      leaf.readerTextEndOffset,
      annotationRangesBySentence,
    ],
  );
  const content = useMemo(
    () =>
      renderLeafContent(
        leaf.text,
        leaf.readerTextStartOffset,
        focusedSegments,
        annotationSegments,
      ),
    [annotationSegments, focusedSegments, leaf.text, leaf.readerTextStartOffset],
  );
  const hasDecoratedContent = focusedSegments.length > 0 || annotationSegments.length > 0;
  const visualTone = leaf.readerMarkVisualTone;
  if (!visualTone) {
    return <span {...props.attributes}>{hasDecoratedContent ? content : props.children}</span>;
  }

  const className = readerMarkClassName(visualTone, annotationVisibilityGroups);
  const entryActiveClass =
    activeAnalysisEntryId && leaf.readerMarkParentId === activeAnalysisEntryId ? "reader-mark--entry-active" : "";
  const isClickable = Boolean(className && leaf.readerMarkClickable && leaf.readerSentenceId);

  return (
    <span
      {...props.attributes}
      className={[className, entryActiveClass].filter(Boolean).join(" ") || undefined}
      data-reader-mark-id={leaf.readerMarkId}
      data-reader-mark-parent-id={leaf.readerMarkParentId}
      data-reader-mark-tone={visualTone}
      data-reader-mark-active={entryActiveClass ? "true" : undefined}
      tabIndex={isClickable ? -1 : undefined}
      onClick={(event) => {
        if (!isClickable || !leaf.readerSentenceId || leaf.readerTextStartOffset === undefined || leaf.readerTextEndOffset === undefined) {
          return;
        }

        const sentenceText = sentenceTextBySentence?.get(leaf.readerSentenceId) ?? "";
        if (!sentenceText) {
          return;
        }

        const sentence = {
          sentenceId: leaf.readerSentenceId,
          text: sentenceText,
        };
        const mark = {
          id: leaf.readerMarkId ?? `${leaf.readerSentenceId}:${leaf.readerTextStartOffset}:${leaf.readerTextEndOffset}`,
          annotationType: leaf.readerMarkAnnotationType ?? "vocab_highlight",
          visualTone,
          lookupKind: leaf.readerMarkLookupKind,
          lookupText: leaf.readerMarkLookupText,
          glossary: leaf.readerMarkGlossary,
        };
        const anchor = resolveLookupPreviewAnchor(
          event.currentTarget.closest("[data-reader-sentence-text='true']") as HTMLElement ?? event.currentTarget,
          leaf.readerSentenceId,
          leaf.readerTextStartOffset,
          leaf.readerTextEndOffset,
        );
        const sourceContext = sourceContextBySentence?.get(leaf.readerSentenceId);
        const anchorText =
          typeof leaf.readerMarkAnchorText === "string"
            ? leaf.readerMarkAnchorText
            : typeof leaf.text === "string"
              ? leaf.text
              : "";
        const isStructured =
          leaf.readerMarkLookupKind === "phrase" ||
          /\s/.test(anchorText) ||
          leaf.readerMarkAnnotationType === "phrase_gloss" ||
          leaf.readerMarkAnnotationType === "context_gloss";

        event.stopPropagation();
        event.currentTarget.focus({ preventScroll: true });

        if (isStructured) {
          const intent = inspectIntentFromStructuredMark({
            mark,
            sentence,
            anchorText,
            sourceContext,
            startOffset: leaf.readerTextStartOffset,
            endOffset: leaf.readerTextEndOffset,
          });
          onInspectIntent?.(intent, anchor, event.currentTarget);
          return;
        }

        const intent = lookupIntentFromMark({
          mark,
          sentence,
          anchorText,
          sourceContext,
          startOffset: leaf.readerTextStartOffset,
          endOffset: leaf.readerTextEndOffset,
        });
        onLookupIntent?.(intent, anchor, event.currentTarget);
      }}
    >
      {hasDecoratedContent ? content : props.children}
    </span>
  );
});

ReaderMarkLeaf.displayName = "ReaderMarkLeaf";
