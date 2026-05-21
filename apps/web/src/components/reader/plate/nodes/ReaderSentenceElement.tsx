"use client";

import { MessageSquare, Quote } from "lucide-react";
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
  analysisActive?: boolean;
  analysisExpanded?: boolean;
  routeFocused?: boolean;
  assetProjection?: ReaderSentenceAssetProjection | null;
  hoveredAnnotationTargetKey?: string | null;
  noteCount?: number;
  noteActive?: boolean;
  annotationVisibilityGroups: ReaderAnnotationVisibilityGroups;
  onActivate?: (sentenceId: string, anchorEl: HTMLElement) => void;
  onOpenNotes?: (sentenceId: string, anchorEl: HTMLElement) => void;
  onHoverAnnotationTargetKeyChange?: (targetKey: string | null) => void;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
}

export function ReaderSentenceElement({
  active = false,
  analysisActive = false,
  analysisExpanded = false,
  assetProjection = null,
  hoveredAnnotationTargetKey = null,
  noteCount = 0,
  noteActive = false,
  annotationVisibilityGroups,
  onAnnotationJump,
  onActivate,
  onOpenNotes,
  onHoverAnnotationTargetKeyChange,
  props,
  routeFocused = false,
}: ReaderSentenceElementProps) {
  const element = props.element as unknown as ReaderSentenceNode;
  const frameClassName = [
    "group/sentence relative scroll-mt-8 pl-2 pr-12 py-2 transition-colors rounded-[8px]",
    active ? "bg-surface/42" : "hover:bg-surface/28",
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
      {onActivate ? (
        <button
          type="button"
          className="focus-ring absolute top-2 right-2 z-10 inline-flex h-11 w-11 items-center justify-center rounded-full border border-hairline/70 bg-white/88 text-muted opacity-0 shadow-[0_8px_20px_rgba(17,17,17,0.05)] transition-[opacity,border-color,color,background-color] hover:border-muted hover:text-ink focus-visible:opacity-100 group-hover/sentence:opacity-100 group-focus-within/sentence:opacity-100"
          aria-label="打开当前句操作"
          aria-haspopup="dialog"
          data-reader-sentence-handle="true"
          onClick={(event) => {
            event.stopPropagation();
            onActivate(element.sentenceId, event.currentTarget);
          }}
        >
          <Quote aria-hidden="true" className="h-4 w-4" />
        </button>
      ) : null}
      {noteCount > 0 ? (
        <button
          type="button"
          className={`focus-ring absolute top-[3.25rem] right-2 z-10 inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-full border bg-white/95 px-2 text-muted shadow-[0_8px_20px_rgba(17,17,17,0.05)] transition-[border-color,color,background-color,box-shadow,transform] hover:-translate-y-[1px] hover:border-muted hover:text-ink ${
            noteActive
              ? "border-amber-300 text-amber-800 shadow-[0_10px_24px_rgba(177,135,77,0.12)]"
              : "border-hairline/70"
          }`}
          aria-label={noteCount > 1 ? `打开当前句的 ${noteCount} 条笔记` : "打开当前句笔记"}
          data-reader-sentence-note-handle="true"
          onClick={(event) => {
            event.stopPropagation();
            onOpenNotes?.(element.sentenceId, event.currentTarget);
          }}
        >
          <MessageSquare aria-hidden="true" className="h-4 w-4" />
          {noteCount > 1 ? (
            <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[0.68rem] font-semibold leading-none text-amber-900">
              {noteCount}
            </span>
          ) : null}
        </button>
      ) : null}
      <ReaderAnnotationOverlay
        sentenceId={element.sentenceId}
        annotations={assetProjection?.annotations ?? []}
        visible={annotationVisibilityGroups.userAssets}
        hoveredTargetKey={hoveredAnnotationTargetKey}
        onHoverTargetKeyChange={onHoverAnnotationTargetKeyChange}
        onAnnotationJump={onAnnotationJump}
      >
        {props.children}
      </ReaderAnnotationOverlay>
    </section>
  );
}
