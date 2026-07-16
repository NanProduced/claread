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
  analysisSegmentsBySentence?: Map<string, Array<SentenceAnalysisSegment & { entryId?: string }>>;
  annotationRangesBySentence?: Map<string, ReaderAssetRange[]>;
  selectionFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  contextFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  jumpFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  noteFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  hoveredAnnotationTargetKey?: string | null;
  hoveredInlineMarkKey?: string | null;
  focusedInlineMarkKey?: string | null;
  activeInlineMarkKey?: string | null;
  activeAnalysisEntryId?: string | null;
  expandedAnalysisEntryIds?: Set<string>;
  sentenceTextBySentence?: Map<string, string>;
  sourceContextBySentence?: Map<string, string | undefined>;
  onHoverAnnotationTargetKeyChange?: (targetKey: string | null) => void;
  onHoverInlineMarkKeyChange?: (markKey: string | null) => void;
  onFocusInlineMarkKeyChange?: (markKey: string | null) => void;
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
  lastLeafOffsetsByMarkKey?: Map<string, number>;
  grammarCueIndexByMarkKeyBySentence?: Map<string, Map<string, number>>;
  grammarEntryIdByMarkKeyBySentence?: Map<string, Map<string, string>>;
  onAnalysisToggle?: (entryId: string) => void;
}

function grammarMarkKey(
  leaf: {
    readerMarkId?: string;
    readerMarkParentId?: string;
  },
) {
  return leaf.readerMarkParentId ?? leaf.readerMarkId ?? null;
}

function circleNumber(num: number) {
  const circles = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];
  return circles[num - 1] ?? `(${num})`;
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
  selectionFocusedSegments: Array<{ startOffset: number; endOffset: number }>,
  contextFocusedSegments: Array<{ startOffset: number; endOffset: number }>,
  noteFocusedSegments: Array<{ startOffset: number; endOffset: number }>,
  analysisSegments: Array<{
    startOffset: number;
    endOffset: number;
    label: string;
    index: number;
    entryId?: string;
  }>,
  annotationSegments: Array<{
    startOffset: number;
    endOffset: number;
    annotations: ReaderAssetRange[];
  }>,
  hoveredAnnotationTargetKey: string | null,
  onHoverAnnotationTargetKeyChange?: (targetKey: string | null) => void,
  markSegmentClassName?: string | null,
): ReactNode {
  if (
    leafStartOffset === undefined ||
    (jumpFocusedSegments.length === 0 &&
      selectionFocusedSegments.length === 0 &&
      contextFocusedSegments.length === 0 &&
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
  selectionFocusedSegments.forEach((segment) => {
    boundaries.add(segment.startOffset);
    boundaries.add(segment.endOffset);
  });
  contextFocusedSegments.forEach((segment) => {
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
    const overlappingSelectionFocus = selectionFocusedSegments.some(
      (segment) => segment.startOffset < segmentEnd && segment.endOffset > segmentStart,
    );
    const overlappingContextFocus = contextFocusedSegments.some(
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

    if (
      !overlappingJumpFocus &&
      !overlappingSelectionFocus &&
      !overlappingContextFocus &&
      !overlappingNoteFocus &&
      overlappingAnnotations.length === 0 &&
      !overlappingAnalysis
    ) {
      children.push(segmentText);
      continue;
    }

    const annotationClassName = classNameForRanges(overlappingAnnotations);
    const annotationTargetKeys = Array.from(
      new Set(overlappingAnnotations.map((annotation) => annotation.targetKey).filter(Boolean)),
    );
    const className = [
      annotationClassName,
      overlappingSelectionFocus && overlappingAnnotations.length > 0 ? "reader-user-range--selection-muted" : "",
      overlappingContextFocus && overlappingAnnotations.length > 0 ? "reader-user-range--context-muted-foreground" : "",
      overlappingJumpFocus ? "reader-route-focus-range" : "",
      overlappingSelectionFocus ? "reader-selection-focus-range" : "",
      overlappingContextFocus ? "reader-context-focus-range" : "",
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
          className={`reader-analysis-atom reader-analysis-atom--${(overlappingAnalysis.index % 6) + 1} ${
            overlappingSelectionFocus ? "reader-analysis-atom--selection-muted" : ""
          } ${overlappingContextFocus ? "reader-analysis-atom--context-muted-foreground" : ""}`}
          data-analysis-index={overlappingAnalysis.index + 1}
          data-analysis-label={overlappingAnalysis.label}
          data-analysis-entry-id={overlappingAnalysis.entryId}
          onMouseEnter={(event) => {
            const sentence = event.currentTarget.closest('[data-reader-node="sentence"]');
            const row = sentence?.querySelector(
              `[data-entry-id="${overlappingAnalysis.entryId}"] [data-chunk-index="${overlappingAnalysis.index + 1}"]`
            );
            row?.classList.add("reader-entry-analysis-item--active");
          }}
          onMouseLeave={(event) => {
            const sentence = event.currentTarget.closest('[data-reader-node="sentence"]');
            const row = sentence?.querySelector(
              `[data-entry-id="${overlappingAnalysis.entryId}"] [data-chunk-index="${overlappingAnalysis.index + 1}"]`
            );
            row?.classList.remove("reader-entry-analysis-item--active");
          }}
        >
          {content}
        </span>
      );
    }

    if (markSegmentClassName) {
      content = (
        <span
          className={[
            markSegmentClassName,
            overlappingSelectionFocus ? "reader-mark--selection-muted" : "",
            overlappingContextFocus ? "reader-mark--context-muted-foreground" : "",
          ]
            .filter(Boolean)
            .join(" ")}
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
    case "warm_yellow":
      return "reader-user-range--warm-yellow";
    case "soft_mint":
      return "reader-user-range--soft-mint";
    case "soft_rose":
      return "reader-user-range--soft-rose";
    default:
      return "";
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
  analysisSegmentsBySentence: Map<string, Array<SentenceAnalysisSegment & { entryId?: string }>> | undefined,
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
      entryId: segment.entryId,
    }))
    .filter((segment) => segment.startOffset < segment.endOffset)
    .sort((left, right) => left.startOffset - right.startOffset);
}

export const ReaderMarkLeaf = memo(function ReaderMarkLeaf({
  activeAnalysisEntryId = null,
  activeInlineMarkKey = null,
  analysisSegmentsBySentence,
  annotationRangesBySentence,
  annotationVisibilityGroups,
  hoveredAnnotationTargetKey = null,
  hoveredInlineMarkKey = null,
  focusedInlineMarkKey = null,
  expandedAnalysisEntryIds,
  jumpFocusRangesBySentence,
  selectionFocusRangesBySentence,
  contextFocusRangesBySentence,
  noteFocusRangesBySentence,
  onHoverAnnotationTargetKeyChange,
  onHoverInlineMarkKeyChange,
  onFocusInlineMarkKeyChange,
  onInspectIntent,
  onLookupIntent,
  props,
  sentenceTextBySentence,
  sourceContextBySentence,
  lastLeafOffsetsByMarkKey,
  grammarCueIndexByMarkKeyBySentence,
  grammarEntryIdByMarkKeyBySentence,
  onAnalysisToggle,
}: ReaderMarkLeafProps) {
  const leaf = props.leaf as Parameters<RenderLeaf>[0]["leaf"] & {
    readerMarkAnnotationType?: ReaderLookupIntent["annotationType"];
    readerMarkAnchorText?: string;
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
    readerMarks?: Array<{
      annotationType: ReaderStructuredInspectIntent["annotationType"];
      anchorText: string;
      clickable: boolean;
      glossary?: ReaderLookupIntent["glossary"];
      id: string;
      parentId?: string;
      lookupKind?: ReaderStructuredInspectIntent["lookupKind"];
      lookupText?: string;
      renderType?: string;
      visualTone: ReaderStructuredInspectIntent["visualTone"];
    }>;
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
  const selectionFocusedSegments = useMemo(
    () =>
      routeFocusSegmentsForLeaf(
        leaf.readerSentenceId,
        leaf.readerTextStartOffset,
        leaf.readerTextEndOffset,
        selectionFocusRangesBySentence,
      ),
    [
      selectionFocusRangesBySentence,
      leaf.readerSentenceId,
      leaf.readerTextStartOffset,
      leaf.readerTextEndOffset,
    ],
  );
  const contextFocusedSegments = useMemo(
    () =>
      routeFocusSegmentsForLeaf(
        leaf.readerSentenceId,
        leaf.readerTextStartOffset,
        leaf.readerTextEndOffset,
        contextFocusRangesBySentence,
      ),
    [
      contextFocusRangesBySentence,
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
  const hasDecoratedContent =
    jumpFocusedSegments.length > 0 ||
    selectionFocusedSegments.length > 0 ||
    contextFocusedSegments.length > 0 ||
    noteFocusedSegments.length > 0 ||
    analysisSegments.length > 0 ||
    annotationSegments.length > 0;
  const marks = leaf.readerMarks && leaf.readerMarks.length > 0
    ? leaf.readerMarks
    : leaf.readerMarkVisualTone
      ? [
          {
            annotationType: leaf.readerMarkAnnotationType ?? "vocab_highlight",
            anchorText: leaf.readerMarkAnchorText ?? "",
            clickable: leaf.readerMarkClickable ?? false,
            glossary: leaf.readerMarkGlossary,
            id: leaf.readerMarkId ?? "",
            parentId: leaf.readerMarkParentId,
            lookupKind: leaf.readerMarkLookupKind,
            lookupText: leaf.readerMarkLookupText,
            renderType: leaf.readerMarkRenderType,
            visualTone: leaf.readerMarkVisualTone,
          },
        ]
      : [];

  const grammarMark = marks.find(m => m.visualTone === "grammar");
  let segmentedGrammarClassName: string | null = null;
  let hasSegmentedGrammar = false;

  if (grammarMark && hasDecoratedContent) {
    hasSegmentedGrammar = true;
    const markKey = grammarMark.parentId ?? grammarMark.id ?? null;
    const resolvedGrammarEntryId =
      leaf.readerSentenceId && markKey
        ? grammarEntryIdByMarkKeyBySentence?.get(leaf.readerSentenceId)?.get(markKey)
        : undefined;

    const isLinkedToEntryId = (entryId: string | null | undefined) => {
      if (!entryId) return false;
      return resolvedGrammarEntryId === entryId ||
        grammarMark.parentId === entryId ||
        grammarMark.id === entryId ||
        (grammarMark.id?.startsWith("im_") &&
          entryId.startsWith("se_") &&
          grammarMark.id.slice(3) === entryId.slice(3));
    };

    const isLinkedToActiveEntry = isLinkedToEntryId(activeAnalysisEntryId);
    const isLinkedToExpandedEntry = expandedAnalysisEntryIds
      ? Array.from(expandedAnalysisEntryIds).some(isLinkedToEntryId)
      : false;

    const grammarLinkStateClass = isLinkedToActiveEntry || isLinkedToExpandedEntry
      ? "reader-mark--grammar-linked"
      : "";
    const grammarPinnedStateClass = isLinkedToExpandedEntry
      ? "reader-mark--grammar-pinned"
      : "";

    segmentedGrammarClassName = [
      "reader-mark--grammar-segment",
      grammarLinkStateClass,
      grammarPinnedStateClass,
    ].filter(Boolean).join(" ");
  }

  const content = useMemo(
    () =>
      renderLeafContent(
        leaf.text,
        leaf.readerTextStartOffset,
        jumpFocusedSegments,
        selectionFocusedSegments,
        contextFocusedSegments,
        noteFocusedSegments,
        analysisSegments,
        annotationSegments,
        hoveredAnnotationTargetKey,
        onHoverAnnotationTargetKeyChange,
        segmentedGrammarClassName,
      ),
    [
      analysisSegments,
      annotationSegments,
      contextFocusedSegments,
      hoveredAnnotationTargetKey,
      jumpFocusedSegments,
      leaf.text,
      leaf.readerTextStartOffset,
      noteFocusedSegments,
      onHoverAnnotationTargetKeyChange,
      segmentedGrammarClassName,
      selectionFocusedSegments,
    ],
  );

  if (marks.length === 0) {
    return <span {...props.attributes}>{hasDecoratedContent ? content : props.children}</span>;
  }

  let wrappedContent = hasDecoratedContent ? content : props.children;
  const reversedMarks = [...marks].reverse();

  reversedMarks.forEach((mark, index) => {
    const isOutermost = index === reversedMarks.length - 1;
    const visualTone = mark.visualTone;
    const useSegmentedGrammarMark = visualTone === "grammar" && hasDecoratedContent;
    const markClassName = readerMarkClassName(visualTone, annotationVisibilityGroups);

    const markKey = mark.parentId ?? mark.id ?? null;
    const isInlineMarkGroupHovered = Boolean(markKey && hoveredInlineMarkKey === markKey);
    const isInlineMarkGroupFocused = Boolean(markKey && focusedInlineMarkKey === markKey);
    const isInlineMarkGroupActive = Boolean(markKey && activeInlineMarkKey === markKey);
    const resolvedGrammarEntryId =
      leaf.readerSentenceId && markKey
        ? grammarEntryIdByMarkKeyBySentence?.get(leaf.readerSentenceId)?.get(markKey)
        : undefined;

    const isLinkedToEntryId = (entryId: string | null | undefined) => {
      if (!entryId) return false;
      return resolvedGrammarEntryId === entryId ||
        mark.parentId === entryId ||
        mark.id === entryId ||
        (mark.id?.startsWith("im_") &&
          entryId.startsWith("se_") &&
          mark.id.slice(3) === entryId.slice(3));
    };

    const isLinkedToActiveEntry = isLinkedToEntryId(activeAnalysisEntryId);
    const isLinkedToExpandedEntry = expandedAnalysisEntryIds
      ? Array.from(expandedAnalysisEntryIds).some(isLinkedToEntryId)
      : false;

    const isGrammarLink = mark.annotationType === "grammar_note";
    const grammarLinkStateClass = isGrammarLink && (isLinkedToActiveEntry || isLinkedToExpandedEntry)
      ? "reader-mark--grammar-linked"
      : "";
    const grammarPinnedStateClass = isGrammarLink && isLinkedToExpandedEntry
      ? "reader-mark--grammar-pinned"
      : "";
    const entryActiveClass = !isGrammarLink && (isLinkedToActiveEntry || isLinkedToExpandedEntry)
      ? "reader-mark--entry-active"
      : "";

    const isClickable = Boolean(
      markClassName &&
      (mark.clickable || mark.annotationType === "grammar_note") &&
      leaf.readerSentenceId
    );

    const selectionMutedClass =
      !useSegmentedGrammarMark && selectionFocusedSegments.length > 0 ? "reader-mark--selection-muted" : "";
    const contextMutedClass =
      !useSegmentedGrammarMark && contextFocusedSegments.length > 0 ? "reader-mark--context-muted-foreground" : "";

    const grammarCueIndex =
      leaf.readerSentenceId && markKey
        ? grammarCueIndexByMarkKeyBySentence?.get(leaf.readerSentenceId)?.get(markKey)
        : undefined;

    const isLastLeafOfMark =
      mark.annotationType === "grammar_note" &&
      Boolean(markKey) &&
      typeof leaf.readerTextEndOffset === "number" &&
      leaf.readerTextEndOffset === lastLeafOffsetsByMarkKey?.get(markKey ?? "");

    wrappedContent = (
      <span
        {...(isOutermost ? props.attributes : {})}
        className={[
          useSegmentedGrammarMark ? "" : markClassName,
          isClickable ? "reader-mark--interactive" : "",
          isInlineMarkGroupHovered ? "reader-mark--group-hovered" : "",
          isInlineMarkGroupFocused ? "reader-mark--group-focused" : "",
          isInlineMarkGroupActive ? "reader-mark--group-active" : "",
          selectionMutedClass,
          contextMutedClass,
          entryActiveClass,
          useSegmentedGrammarMark ? "" : grammarLinkStateClass,
          useSegmentedGrammarMark ? "" : grammarPinnedStateClass,
        ].filter(Boolean).join(" ") || undefined}
        data-reader-mark-id={mark.id}
        data-reader-mark-parent-id={mark.parentId}
        data-reader-mark-tone={visualTone}
        data-reader-mark-active={entryActiveClass ? "true" : undefined}
        tabIndex={isClickable ? -1 : undefined}
        onMouseEnter={() => {
          if (isClickable && markKey) {
            onHoverInlineMarkKeyChange?.(markKey);
          }
        }}
        onMouseLeave={() => {
          if (isClickable && markKey) {
            onHoverInlineMarkKeyChange?.(null);
          }
        }}
        onFocus={() => {
          if (isClickable && markKey) {
            onFocusInlineMarkKeyChange?.(markKey);
          }
        }}
        onBlur={() => {
          if (isClickable && markKey) {
            onFocusInlineMarkKeyChange?.(null);
          }
        }}
        onClick={(event) => {
          if (!isClickable || !leaf.readerSentenceId || leaf.readerTextStartOffset === undefined || leaf.readerTextEndOffset === undefined) {
            return;
          }

          const selection = window.getSelection();
          if (selection && !selection.isCollapsed && selection.toString().trim()) {
            return;
          }

          if (mark.annotationType === "grammar_note") {
            const entryId = resolvedGrammarEntryId ?? mark.parentId ?? mark.id;
            if (entryId) {
              event.stopPropagation();
              onAnalysisToggle?.(entryId);
              return;
            }
          }

          const sentenceText = sentenceTextBySentence?.get(leaf.readerSentenceId) ?? "";
          if (!sentenceText) {
            return;
          }

          const sentence = {
            sentenceId: leaf.readerSentenceId,
            text: sentenceText,
          };
          const resolvedMark = {
            id: mark.id ?? `${leaf.readerSentenceId}:${leaf.readerTextStartOffset}:${leaf.readerTextEndOffset}`,
            annotationType: mark.annotationType ?? "vocab_highlight",
            visualTone,
            lookupKind: mark.lookupKind,
            lookupText: mark.lookupText,
            glossary: mark.glossary,
          };
          const anchor = resolveLookupPreviewAnchor(
            event.currentTarget.closest("[data-reader-sentence-text='true']") as HTMLElement ?? event.currentTarget,
            leaf.readerSentenceId,
            leaf.readerTextStartOffset,
            leaf.readerTextEndOffset,
          );
          const sourceContext = sourceContextBySentence?.get(leaf.readerSentenceId);
          const anchorText =
            typeof mark.anchorText === "string"
              ? mark.anchorText
              : typeof leaf.text === "string"
                ? leaf.text
                : "";
          const isStructured =
            mark.lookupKind === "phrase" ||
            /\s/.test(anchorText) ||
            mark.annotationType === "phrase_gloss" ||
            mark.annotationType === "context_gloss";

          event.stopPropagation();
          event.currentTarget.focus({ preventScroll: true });

          if (isStructured) {
            const intent = inspectIntentFromStructuredMark({
              mark: resolvedMark,
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
            mark: resolvedMark,
            sentence,
            anchorText,
            sourceContext,
            startOffset: leaf.readerTextStartOffset,
            endOffset: leaf.readerTextEndOffset,
          });
          onLookupIntent?.(intent, anchor, event.currentTarget);
        }}
      >
        {wrappedContent}
        {isOutermost && typeof grammarCueIndex === "number" && isLastLeafOfMark ? (
          <span className="reader-grammar-cue" aria-hidden="true">
            {circleNumber(grammarCueIndex)}
          </span>
        ) : null}
      </span>
    );
  });

  return <>{wrappedContent}</>;
});

ReaderMarkLeaf.displayName = "ReaderMarkLeaf";
