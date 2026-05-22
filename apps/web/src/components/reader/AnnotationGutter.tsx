import { useMemo, useState } from "react";
import { Highlighter, MessageSquare } from "lucide-react";

import type { WebAnnotationVm } from "@/types/api/annotations";

export interface AnnotationGutterProps {
  sentenceId?: string;
  annotations: WebAnnotationVm[];
  visible?: boolean;
  hoveredTargetKey?: string | null;
  noteCount?: number;
  noteActive?: boolean;
  onHoverTargetKeyChange?: (targetKey: string | null) => void;
  onAnnotationJump?: (annotation: WebAnnotationVm, triggerEl?: HTMLElement, sentenceId?: string) => void;
  onOpenNotes?: (sentenceId: string, triggerEl?: HTMLElement) => void;
}

function sentenceHighlightRange(annotation: WebAnnotationVm, sentenceId?: string) {
  if (!sentenceId) {
    return null;
  }

  if (annotation.anchorType === "sentence" && annotation.sentenceId === sentenceId) {
    return {
      sentenceId,
      selectedText: annotation.selectedText,
      startOffset: 0,
      endOffset: annotation.selectedText.length,
    };
  }

  if (annotation.anchorType === "text_range" && annotation.sentenceId === sentenceId) {
    return {
      sentenceId,
      selectedText: annotation.selectedText,
      startOffset: annotation.startOffset ?? 0,
      endOffset: annotation.endOffset ?? annotation.selectedText.length,
    };
  }

  if (annotation.anchorType === "multi_text") {
    const segment = annotation.segments.find((item) => item.sentenceId === sentenceId);
    if (!segment) {
      return null;
    }

    return {
      sentenceId,
      selectedText: segment.selectedText,
      startOffset: segment.startOffset,
      endOffset: segment.endOffset,
    };
  }

  return null;
}

function highlightChipLabel(annotation: WebAnnotationVm, sentenceId?: string) {
  const sentenceRange = sentenceHighlightRange(annotation, sentenceId);
  const text = sentenceRange?.selectedText?.trim() || annotation.selectedText.trim();
  if (!text) {
    return "高亮";
  }
  return text.length > 16 ? `${text.slice(0, 16)}...` : text;
}

export function AnnotationGutter({
  sentenceId,
  annotations,
  visible = true,
  hoveredTargetKey = null,
  noteCount = 0,
  noteActive = false,
  onHoverTargetKeyChange,
  onAnnotationJump,
  onOpenNotes,
}: AnnotationGutterProps) {
  const [stripOpen, setStripOpen] = useState(false);
  const highlightAnnotations = useMemo(
    () =>
      annotations
        .filter((item) => item.type === "highlight")
        .filter((item) => item.anchorType !== "multi_text" || item.segments[0]?.sentenceId === sentenceId)
        .filter((item) => Boolean(sentenceHighlightRange(item, sentenceId)))
        .sort((left, right) => {
          const leftRange = sentenceHighlightRange(left, sentenceId);
          const rightRange = sentenceHighlightRange(right, sentenceId);
          return (leftRange?.startOffset ?? 0) - (rightRange?.startOffset ?? 0);
        }),
    [annotations, sentenceId],
  );

  if (!visible || (highlightAnnotations.length === 0 && noteCount === 0)) {
    return null;
  }

  const active = highlightAnnotations.some((item) => item.targetKey === hoveredTargetKey);
  const hasMultipleHighlights = highlightAnnotations.length > 1;
  const primaryAnnotation = highlightAnnotations[0] ?? null;

  return (
    <div
      className="reader-annotation-gutter absolute -left-5 top-2.5 flex flex-col gap-1"
      aria-label="句子高亮与笔记锚点"
    >
      {noteCount > 0 ? (
        <button
          type="button"
          className={`reader-annotation-gutter-marker reader-annotation-gutter-marker--note relative ${
            noteActive ? "reader-annotation-gutter-marker--active" : ""
          }`}
          onClick={(event) => {
            event.stopPropagation();
            if (!sentenceId) {
              return;
            }
            onOpenNotes?.(sentenceId, event.currentTarget);
          }}
          aria-label={noteCount > 1 ? `打开当前句的 ${noteCount} 条笔记` : "打开当前句笔记"}
        >
          <MessageSquare className="h-4 w-4" />
          {noteCount > 1 ? (
            <span className="reader-annotation-gutter-count" aria-hidden="true">
              {noteCount}
            </span>
          ) : null}
        </button>
      ) : null}
      {highlightAnnotations.length > 0 ? (
        <button
          type="button"
          className={`reader-annotation-gutter-marker reader-annotation-gutter-marker--highlight relative text-structure-green drop-shadow-sm opacity-80 ${
            active ? "reader-annotation-gutter-marker--active" : ""
          }`}
          onMouseEnter={() => onHoverTargetKeyChange?.(primaryAnnotation?.targetKey ?? null)}
          onMouseLeave={() => onHoverTargetKeyChange?.(null)}
          onFocus={() => onHoverTargetKeyChange?.(primaryAnnotation?.targetKey ?? null)}
          onBlur={() => onHoverTargetKeyChange?.(null)}
          onClick={(event) => {
            event.stopPropagation();
            if (!primaryAnnotation) {
              return;
            }

            if (!hasMultipleHighlights) {
              onAnnotationJump?.(primaryAnnotation, event.currentTarget, sentenceId);
              return;
            }

            setStripOpen((current) => !current);
          }}
          aria-label={hasMultipleHighlights ? `查看本句 ${highlightAnnotations.length} 处高亮` : "打开本句高亮"}
          aria-expanded={hasMultipleHighlights ? stripOpen : undefined}
        >
          <Highlighter className="h-4 w-4" />
          {hasMultipleHighlights ? (
            <span className="reader-annotation-gutter-count" aria-hidden="true">
              {highlightAnnotations.length}
            </span>
          ) : null}
        </button>
      ) : null}
      {hasMultipleHighlights && stripOpen ? (
        <div
          className="reader-annotation-gutter-strip"
          role="listbox"
          aria-label="本句高亮列表"
          onMouseLeave={() => onHoverTargetKeyChange?.(null)}
        >
          {highlightAnnotations.map((annotation, index) => {
            const isHovered = hoveredTargetKey === annotation.targetKey;
            return (
              <button
                key={annotation.id}
                type="button"
                role="option"
                aria-selected={isHovered}
                className={`reader-annotation-gutter-strip-item ${
                  isHovered ? "reader-annotation-gutter-strip-item--active" : ""
                }`}
                onMouseEnter={() => onHoverTargetKeyChange?.(annotation.targetKey)}
                onFocus={() => onHoverTargetKeyChange?.(annotation.targetKey)}
                onBlur={() => onHoverTargetKeyChange?.(null)}
                onClick={(event) => {
                  event.stopPropagation();
                  setStripOpen(false);
                  onAnnotationJump?.(annotation, event.currentTarget, sentenceId);
                }}
              >
                <span className="reader-annotation-gutter-strip-index" aria-hidden="true">
                  {index + 1}
                </span>
                <span className="reader-annotation-gutter-strip-label">{highlightChipLabel(annotation, sentenceId)}</span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
