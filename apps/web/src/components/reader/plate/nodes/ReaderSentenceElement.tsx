"use client";

import type { RenderElement } from "platejs/react";
import type {
  ReaderSentenceAssetProjection,
  ReaderSentenceNode,
} from "@/lib/reader-plate";
import type { WebAnnotationVm } from "@/types/api/annotations";
import { ReaderAnnotationOverlay } from "../../ReaderAnnotationOverlay";
import type { ReaderAnnotationVisibilityGroups } from "../../settings";

interface ReaderSentenceElementProps {
  props: Parameters<RenderElement>[0];
  active?: boolean;
  sentenceActionsActive?: boolean;
  hasTranslation?: boolean;
  hasVisibleAnalysis?: boolean;
  analysisActive?: boolean;
  analysisExpanded?: boolean;
  routeFocused?: boolean;
  assetProjection?: ReaderSentenceAssetProjection | null;
  hoveredAnnotationTargetKey?: string | null;
  noteCount?: number;
  noteActive?: boolean;
  annotationVisibilityGroups: ReaderAnnotationVisibilityGroups;
  onOpenSentenceActions?: (sentenceId: string, anchorEl?: HTMLElement) => void;
  onOpenNotes?: (sentenceId: string, anchorEl?: HTMLElement) => void;
  onHoverAnnotationTargetKeyChange?: (targetKey: string | null) => void;
  onAnnotationJump?: (annotation: WebAnnotationVm, triggerEl?: HTMLElement, sentenceId?: string) => void;
}

export function ReaderSentenceElement({
  active = false,
  sentenceActionsActive = false,
  hasTranslation = false,
  hasVisibleAnalysis = false,
  analysisActive = false,
  analysisExpanded = false,
  assetProjection = null,
  hoveredAnnotationTargetKey = null,
  noteCount = 0,
  noteActive = false,
  annotationVisibilityGroups,
  onAnnotationJump,
  onOpenSentenceActions,
  onOpenNotes,
  onHoverAnnotationTargetKeyChange,
  props,
  routeFocused = false,
}: ReaderSentenceElementProps) {
  const element = props.element as unknown as ReaderSentenceNode;
  const frameClassName = [
    "group/sentence reader-sentence-shell relative scroll-mt-8 pr-10 sm:pr-12 rounded-[10px]",
    hasVisibleAnalysis ? "reader-sentence-shell--with-analysis" : hasTranslation ? "reader-sentence-shell--with-translation" : "reader-sentence-shell--plain",
    active ? "reader-sentence-shell--active" : "reader-sentence-shell--idle",
    analysisActive ? "reader-sentence--analysis-active reader-sentence--has-active-analysis" : "",
    analysisExpanded ? "reader-sentence--analysis-expanded" : "",
    annotationVisibilityGroups.userAssets && assetProjection?.hasHighlight ? "reader-sentence--user-highlight" : "",
    routeFocused ? "reader-route-focus-frame" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section
      {...props.attributes}
      id={`reader-sentence-${element.sentenceId}`}
      className={frameClassName}
      data-reader-anchor="sentence"
      data-reader-node="sentence"
      data-paragraph-id={element.paragraphId}
      data-sentence-id={element.sentenceId}
    >
      <ReaderAnnotationOverlay
        sentenceId={element.sentenceId}
        annotations={assetProjection?.annotations ?? []}
        visible={annotationVisibilityGroups.userAssets}
        actionsActive={sentenceActionsActive}
        hoveredTargetKey={hoveredAnnotationTargetKey}
        noteCount={noteCount}
        noteActive={noteActive}
        onOpenSentenceActions={onOpenSentenceActions}
        onHoverTargetKeyChange={onHoverAnnotationTargetKeyChange}
        onAnnotationJump={onAnnotationJump}
        onOpenNotes={onOpenNotes}
      >
        {props.children}
      </ReaderAnnotationOverlay>
    </section>
  );
}
