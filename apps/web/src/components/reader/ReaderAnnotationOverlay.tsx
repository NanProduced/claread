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
  activeIndex?: number | null;
  children: ReactNode;
}

export function ReaderAnnotationOverlay({
  annotations,
  slipAnnotations,
  favoriteTargets = [],
  activeIndex,
  children,
}: ReaderAnnotationOverlayProps) {
  return (
    <>
      <AnnotationGutter annotations={annotations} favoriteCount={favoriteTargets.length} />
      {activeIndex ? (
        <span className="reader-active-dot" aria-hidden="true">
          {activeIndex}
        </span>
      ) : null}
      {children}
      <AnnotationSlip annotations={slipAnnotations ?? annotations} />
    </>
  );
}
