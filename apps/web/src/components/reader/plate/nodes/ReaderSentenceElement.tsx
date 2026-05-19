"use client";

import type { RenderElement } from "platejs/react";
import type {
  ReaderSentenceAssetProjection,
  ReaderSentenceNode,
} from "@/lib/reader-plate";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import { ReaderAnnotationOverlay } from "../../ReaderAnnotationOverlay";
import type { ReaderAnnotationVisibilityGroups } from "../../settings";

interface ReaderSentenceElementProps {
  props: Parameters<RenderElement>[0];
  active?: boolean;
  routeFocused?: boolean;
  assetProjection?: ReaderSentenceAssetProjection | null;
  annotationVisibilityGroups: ReaderAnnotationVisibilityGroups;
  onActivate?: (sentenceId: string) => void;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
  onAnnotationAsk?: (annotation: WebAnnotationVm) => void;
  onFavoriteJump?: (favorite: WebFavoriteTargetVm) => void;
}

export function ReaderSentenceElement({
  active = false,
  assetProjection = null,
  annotationVisibilityGroups,
  onAnnotationAsk,
  onAnnotationJump,
  onActivate,
  onFavoriteJump,
  props,
  routeFocused = false,
}: ReaderSentenceElementProps) {
  const element = props.element as unknown as ReaderSentenceNode;
  const frameClassName = [
    "group/sentence relative scroll-mt-8 px-2 py-2 transition-colors rounded-[8px]",
    active ? "bg-surface/42" : "hover:bg-surface/28",
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
      tabIndex={0}
      aria-label={`句子 ${element.sentenceId}`}
      onClick={() => onActivate?.(element.sentenceId)}
      onFocus={(event) => {
        if (event.target === event.currentTarget) {
          onActivate?.(element.sentenceId);
        }
      }}
    >
      <ReaderAnnotationOverlay
        annotations={assetProjection?.annotations ?? []}
        slipAnnotations={assetProjection?.slipAnnotations ?? []}
        favoriteTargets={assetProjection?.favoriteTargets ?? []}
        visible={annotationVisibilityGroups.userAssets}
        onAnnotationAsk={onAnnotationAsk}
        onAnnotationJump={onAnnotationJump}
        onFavoriteJump={onFavoriteJump}
      >
        {props.children}
      </ReaderAnnotationOverlay>
    </section>
  );
}
