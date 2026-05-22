"use client";

import type { ReactNode } from "react";

import type { WebAnnotationVm } from "@/types/api/annotations";
import { AnnotationGutter } from "./AnnotationGutter";

interface ReaderAnnotationOverlayProps {
  sentenceId?: string;
  annotations: WebAnnotationVm[];
  visible?: boolean;
  activeIndex?: number | null;
  hoveredTargetKey?: string | null;
  onHoverTargetKeyChange?: (targetKey: string | null) => void;
  onAnnotationJump?: (annotation: WebAnnotationVm, triggerEl?: HTMLElement, sentenceId?: string) => void;
  children: ReactNode;
}

export function ReaderAnnotationOverlay({
  sentenceId,
  annotations,
  visible = true,
  activeIndex,
  hoveredTargetKey,
  onHoverTargetKeyChange,
  onAnnotationJump,
  children,
}: ReaderAnnotationOverlayProps) {
  return (
    <>
      <AnnotationGutter
        sentenceId={sentenceId}
        annotations={annotations}
        visible={visible}
        hoveredTargetKey={hoveredTargetKey}
        onHoverTargetKeyChange={onHoverTargetKeyChange}
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
