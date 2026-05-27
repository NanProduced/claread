"use client";

import type { ReactNode } from "react";

import type { WebAnnotationVm } from "@/types/api/annotations";
import { AnnotationGutter } from "./AnnotationGutter";

interface ReaderAnnotationOverlayProps {
  sentenceId?: string;
  annotations: WebAnnotationVm[];
  visible?: boolean;
  activeIndex?: number | null;
  actionsActive?: boolean;
  hoveredTargetKey?: string | null;
  noteCount?: number;
  noteActive?: boolean;
  onOpenSentenceActions?: (sentenceId: string, triggerEl?: HTMLElement) => void;
  onHoverTargetKeyChange?: (targetKey: string | null) => void;
  onAnnotationJump?: (annotation: WebAnnotationVm, triggerEl?: HTMLElement, sentenceId?: string) => void;
  onOpenNotes?: (sentenceId: string, triggerEl?: HTMLElement) => void;
  children: ReactNode;
}

export function ReaderAnnotationOverlay({
  sentenceId,
  annotations,
  visible = true,
  activeIndex,
  actionsActive = false,
  hoveredTargetKey,
  noteCount = 0,
  noteActive = false,
  onOpenSentenceActions,
  onHoverTargetKeyChange,
  onAnnotationJump,
  onOpenNotes,
  children,
}: ReaderAnnotationOverlayProps) {
  return (
    <>
      <AnnotationGutter
        sentenceId={sentenceId}
        annotations={annotations}
        visible={visible}
        actionsActive={actionsActive}
        hoveredTargetKey={hoveredTargetKey}
        noteCount={noteCount}
        noteActive={noteActive}
        onOpenSentenceActions={onOpenSentenceActions}
        onHoverTargetKeyChange={onHoverTargetKeyChange}
        onAnnotationJump={onAnnotationJump}
        onOpenNotes={onOpenNotes}
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
