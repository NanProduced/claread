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
import type { SentenceAnalysisSegment } from "../reader-entry-utils";
import { readerMarkClassName } from "./shared";

interface ReaderMarkLeafProps {
  props: Parameters<RenderLeaf>[0];
  annotationVisibilityGroups: ReaderAnnotationVisibilityGroups;
  analysisSegmentsBySentence?: Map<string, SentenceAnalysisSegment[]>;
  annotationRangesBySentence?: Map<string, ReaderAssetRange[]>;
  jumpFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  noteFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  hoveredAnnotationTargetKey?: string | null;
  activeAnalysisEntryId?: string | null;
  sentenceTextBySentence?: Map<string, string>;
  sourceContextBySentence?: Map<string, string | undefined>;
  onHoverAnnotationTargetKeyChange?: (targetKey: string | null) => void;
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
  focusRangesBySentence: Map<string, ReaderJumpRangeSegment[]> | undefined,
) {
  if (!sentenceId || leafStartOffset === undefined || leafEndOffset === undefined || !focusRangesBySentence) {
    return [];
  }

  return (focusRangesBySentence.get(sentenceId) ?? [])
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
  jumpFocusedSegments: Array<{ startOffset: number; endOffset: number }>,
  noteFocusedSegments: Array<{ startOffset: number; endOffset: number }>,
  analysisSegments: Array<{
    startOffset: number;
    endOffset: number;
    label: string;
    index: number;
  }>,
  annotationSegments: Array<{
    startOffset: number;
    endOffset: number;
    annotations: ReaderAssetRange[];
  }>,
  hoveredAnnotationTargetKey: string | null,
  onHoverAnnotationTargetKeyChange?: (targetKey: string | null) => void,
): ReactNode {
  if (
    leafStartOffset === undefined ||
    (jumpFocusedSegments.length === 0 &&
      noteFocusedSegments.length === 0 &&
      annotationSegments.length === 0 &&
      analysisSegments.length === 0)
  ) {
    return text;
  }

  const leafEndOffset = leafStartOffset + text.length;
  const boundaries = new Set<number>([leafStartOffset, leafEndOffset]);
  jumpFocusedSegments.forEach((segment) => {
    boundaries.add(segment.startOffset);
    boundaries.add(segment.endOffset);
  });
  noteFocusedSegments.forEach((segment) => {
    boundaries.add(segment.startOffset);
    boundaries.add(segment.endOffset);
  });
  analysisSegments.forEach((segment) => {
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
    const overlappingJumpFocus = jumpFocusedSegments.some(
      (segment) => segment.startOffset < segmentEnd && segment.endOffset > segmentStart,
    );
    const overlappingNoteFocus = noteFocusedSegments.some(
      (segment) => segment.startOffset < segmentEnd && segment.endOffset > segmentStart,
    );
    const overlappingAnalysis = analysisSegments.find(
      (segment) => segment.startOffset < segmentEnd && segment.endOffset > segmentStart,
    );
    const overlappingAnnotations = annotationSegments.flatMap((segment) =>
      segment.startOffset < segmentEnd && segment.endOffset > segmentStart ? segment.annotations : [],
    );

    if (!overlappingJumpFocus && !overlappingNoteFocus && overlappingAnnotations.length === 0 && !overlappingAnalysis) {
      children.push(segmentText);
      continue;
    }

    const annotationClassName = classNameForRanges(overlappingAnnotations);
    const annotationTargetKeys = Array.from(
      new Set(overlappingAnnotations.map((annotation) => annotation.targetKey).filter(Boolean)),
    );
    const className = [
      annotationClassName,
      overlappingJumpFocus ? "reader-route-focus-range" : "",
      overlappingNoteFocus ? "reader-note-focus-range" : "",
      hoveredAnnotationTargetKey && annotationTargetKeys.includes(hoveredAnnotationTargetKey)
        ? "reader-user-range--hovered-group"
        : "",
    ]
      .filter(Boolean)
      .join(" ");

    let content: ReactNode = segmentText;
    if (overlappingAnalysis) {
      content = (
        <span
          className={`reader-analysis-atom reader-analysis-atom--${(overlappingAnalysis.index % 6) + 1}`}
          data-analysis-index={overlappingAnalysis.index + 1}
          data-analysis-label={overlappingAnalysis.label}
        >
          {content}
        </span>
      );
    }

    if (!className) {
      children.push(
        <span key={`${segmentStart}-${segmentEnd}`}>
          {content}
        </span>,
      );
      continue;
    }

    children.push(
      <span
        key={`${segmentStart}-${segmentEnd}`}
        className={className || undefined}
        data-reader-annotation-ids={
          overlappingAnnotations.length > 0
            ? overlappingAnnotations.map((annotation) => `${annotation.assetKind}:${annotation.assetId}`).join(",")
            : undefined
        }
        data-reader-annotation-count={
          overlappingAnnotations.length > 0
            ? String(new Set(overlappingAnnotations.map((annotation) => `${annotation.assetKind}:${annotation.assetId}`)).size)
            : undefined
        }
        data-reader-annotation-target-keys={annotationTargetKeys.length > 0 ? annotationTargetKeys.join(",") : undefined}
        onMouseEnter={() => {
          const targetKey = annotationTargetKeys[0] ?? null;
          if (targetKey) {
            onHoverAnnotationTargetKeyChange?.(targetKey);
          }
        }}
        onMouseLeave={() => {
          if (annotationTargetKeys.length > 0) {
            onHoverAnnotationTargetKeyChange?.(null);
          }
        }}
      >
        {content}
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

function classNameForRanges(ranges: ReaderAssetRange[]) {
  const uniqueRanges = Array.from(new Map(ranges.map((range) => [`${range.assetKind}:${range.assetId}`, range])).values());
  if (uniqueRanges.length === 0) {
    return "";
  }

  const primary = uniqueRanges[0];
  if (!primary) {
    return "";
  }

  if (uniqueRanges.length === 1) {
    return [
      "reader-user-range",
      annotationToneClass(primary.color ?? null),
    ]
      .filter(Boolean)
      .join(" ");
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

function analysisSegmentsForLeaf(
  sentenceId: string | undefined,
  leafStartOffset: number | undefined,
  leafEndOffset: number | undefined,
  analysisSegmentsBySentence: Map<string, SentenceAnalysisSegment[]> | undefined,
) {
  if (!sentenceId || leafStartOffset === undefined || leafEndOffset === undefined || !analysisSegmentsBySentence) {
    return [];
  }

  return (analysisSegmentsBySentence.get(sentenceId) ?? [])
    .map((segment) => ({
      startOffset: Math.max(leafStartOffset, segment.start),
      endOffset: Math.min(leafEndOffset, segment.end),
      label: segment.label,
      index: segment.index,
    }))
    .filter((segment) => segment.startOffset < segment.endOffset)
    .sort((left, right) => left.startOffset - right.startOffset);
}

export const ReaderMarkLeaf = memo(function ReaderMarkLeaf({
  activeAnalysisEntryId = null,
  analysisSegmentsBySentence,
  annotationRangesBySentence,
  annotationVisibilityGroups,
  hoveredAnnotationTargetKey = null,
  jumpFocusRangesBySentence,
  noteFocusRangesBySentence,
  onHoverAnnotationTargetKeyChange,
  onInspectIntent,
  onLookupIntent,
  props,
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
  const jumpFocusedSegments = useMemo(
    () =>
      routeFocusSegmentsForLeaf(
        leaf.readerSentenceId,
        leaf.readerTextStartOffset,
        leaf.readerTextEndOffset,
        jumpFocusRangesBySentence,
      ),
    [
      jumpFocusRangesBySentence,
      leaf.readerSentenceId,
      leaf.readerTextEndOffset,
      leaf.readerTextStartOffset,
    ],
  );
  const noteFocusedSegments = useMemo(
    () =>
      routeFocusSegmentsForLeaf(
        leaf.readerSentenceId,
        leaf.readerTextStartOffset,
        leaf.readerTextEndOffset,
        noteFocusRangesBySentence,
      ),
    [
      noteFocusRangesBySentence,
      leaf.readerSentenceId,
      leaf.readerTextStartOffset,
      leaf.readerTextEndOffset,
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
  const analysisSegments = useMemo(
    () =>
      analysisSegmentsForLeaf(
        leaf.readerSentenceId,
        leaf.readerTextStartOffset,
        leaf.readerTextEndOffset,
        analysisSegmentsBySentence,
      ),
    [
      analysisSegmentsBySentence,
      leaf.readerSentenceId,
      leaf.readerTextEndOffset,
      leaf.readerTextStartOffset,
    ],
  );
  const content = useMemo(
    () =>
      renderLeafContent(
        leaf.text,
        leaf.readerTextStartOffset,
        jumpFocusedSegments,
        noteFocusedSegments,
        analysisSegments,
        annotationSegments,
        hoveredAnnotationTargetKey,
        onHoverAnnotationTargetKeyChange,
      ),
    [
      analysisSegments,
      annotationSegments,
      hoveredAnnotationTargetKey,
      jumpFocusedSegments,
      leaf.text,
      leaf.readerTextStartOffset,
      noteFocusedSegments,
      onHoverAnnotationTargetKeyChange,
    ],
  );
  const hasDecoratedContent =
    jumpFocusedSegments.length > 0 ||
    noteFocusedSegments.length > 0 ||
    analysisSegments.length > 0 ||
    annotationSegments.length > 0;
  const visualTone = leaf.readerMarkVisualTone;
  if (!visualTone) {
    return <span {...props.attributes}>{hasDecoratedContent ? content : props.children}</span>;
  }

  const className = readerMarkClassName(visualTone, annotationVisibilityGroups);
  const isLinkedToActiveEntry =
    activeAnalysisEntryId &&
    (leaf.readerMarkParentId === activeAnalysisEntryId ||
      leaf.readerMarkId === activeAnalysisEntryId ||
      (leaf.readerMarkId?.startsWith("im_") &&
        activeAnalysisEntryId.startsWith("se_") &&
        leaf.readerMarkId.slice(3) === activeAnalysisEntryId.slice(3)));
  const entryActiveClass = isLinkedToActiveEntry ? "reader-mark--entry-active" : "";
  const isClickable = Boolean(className && leaf.readerMarkClickable && leaf.readerSentenceId);

  return (
    <span
      {...props.attributes}
      className={[className, isClickable ? "reader-mark--interactive" : "", entryActiveClass].filter(Boolean).join(" ") || undefined}
      data-reader-mark-id={leaf.readerMarkId}
      data-reader-mark-parent-id={leaf.readerMarkParentId}
      data-reader-mark-tone={visualTone}
      data-reader-mark-active={entryActiveClass ? "true" : undefined}
      tabIndex={isClickable ? -1 : undefined}
      onClick={(event) => {
        if (!isClickable || !leaf.readerSentenceId || leaf.readerTextStartOffset === undefined || leaf.readerTextEndOffset === undefined) {
          return;
        }

        const selection = window.getSelection();
        if (selection && !selection.isCollapsed && selection.toString().trim()) {
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
