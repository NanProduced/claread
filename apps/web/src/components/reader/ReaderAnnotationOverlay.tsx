"use client";

import type { ReactNode } from "react";

import type { WebAnnotationVm } from "@/types/api/annotations";
import { AnnotationGutter } from "./AnnotationGutter";

interface ReaderAnnotationOverlayProps {
  annotations: WebAnnotationVm[];
  visible?: boolean;
  activeIndex?: number | null;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
  children: ReactNode;
}

export function ReaderAnnotationOverlay({
  annotations,
  visible = true,
  activeIndex,
  onAnnotationJump,
  children,
}: ReaderAnnotationOverlayProps) {
  return (
    <>
      <AnnotationGutter
        annotations={annotations}
        visible={visible}
        onAnnotationJump={onAnnotationJump}
      />
      {activeIndex ? (
        <span className="reader-active-dot" aria-hidden="true">
          {activeIndex}
        </span>
      ) : null}
      {children}
    </>
  );
}
