"use client";

import { Quote } from "lucide-react";
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
  onActivate?: (sentenceId: string, anchorEl: HTMLElement) => void;
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
    >
      {onActivate ? (
        <button
          type="button"
          className="focus-ring absolute top-2 right-2 z-10 inline-flex h-10 w-10 items-center justify-center rounded-full border border-hairline/70 bg-white/88 text-muted opacity-0 shadow-[0_8px_20px_rgba(17,17,17,0.05)] transition-[opacity,border-color,color,background-color] hover:border-muted hover:text-ink focus-visible:opacity-100 group-hover/sentence:opacity-100 group-focus-within/sentence:opacity-100"
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
