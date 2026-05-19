"use client";

import type { ReactNode } from "react";

import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import { AnnotationGutter } from "./AnnotationGutter";
import { AnnotationSlip } from "./AnnotationSlip";

interface ReaderAnnotationOverlayProps {
  annotations: WebAnnotationVm[];
  slipAnnotations?: WebAnnotationVm[];
  favoriteTargets?: WebFavoriteTargetVm[];
  visible?: boolean;
  activeIndex?: number | null;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
  onAnnotationAsk?: (annotation: WebAnnotationVm) => void;
  onFavoriteJump?: (favorite: WebFavoriteTargetVm) => void;
  children: ReactNode;
}

export function ReaderAnnotationOverlay({
  annotations,
  slipAnnotations,
  favoriteTargets = [],
  visible = true,
  activeIndex,
  onAnnotationAsk,
  onAnnotationJump,
  onFavoriteJump,
  children,
}: ReaderAnnotationOverlayProps) {
  return (
    <>
      <AnnotationGutter
        annotations={annotations}
        favoriteTargets={favoriteTargets}
        visible={visible}
        onAnnotationJump={onAnnotationJump}
        onFavoriteJump={onFavoriteJump}
      />
      {activeIndex ? (
        <span className="reader-active-dot" aria-hidden="true">
          {activeIndex}
        </span>
      ) : null}
      {children}
      <AnnotationSlip
        annotations={slipAnnotations ?? annotations}
        visible={visible}
        onAnnotationAsk={onAnnotationAsk}
        onAnnotationJump={onAnnotationJump}
      />
    </>
  );
}
