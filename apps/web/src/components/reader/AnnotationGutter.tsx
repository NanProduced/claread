import { useMemo, useState } from "react";
import { GripVertical, Highlighter, MessageSquare } from "lucide-react";

import type { WebAnnotationVm } from "@/types/api/annotations";
import { cn } from "@/lib/cn";

export interface AnnotationGutterProps {
  sentenceId?: string;
  annotations: WebAnnotationVm[];
  visible?: boolean;
  actionsActive?: boolean;
  hoveredTargetKey?: string | null;
  noteCount?: number;
  noteActive?: boolean;
  onOpenSentenceActions?: (sentenceId: string, triggerEl?: HTMLElement) => void;
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
  actionsActive = false,
  hoveredTargetKey = null,
  noteCount = 0,
  noteActive = false,
  onOpenSentenceActions,
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

  const showSentenceHandle = Boolean(sentenceId && onOpenSentenceActions);
  const hasAssets = visible && (highlightAnnotations.length > 0 || noteCount > 0);

  if (!showSentenceHandle && !hasAssets) {
    return null;
  }

  const active = highlightAnnotations.some((item) => item.targetKey === hoveredTargetKey);
  const hasMultipleHighlights = highlightAnnotations.length > 1;
  const primaryAnnotation = highlightAnnotations[0] ?? null;
  const railPersistent = actionsActive || hasAssets;

  return (
    <div
      className={`reader-sentence-rail absolute right-2 top-2 z-10 flex flex-col items-center gap-1.5 transition-[opacity,transform] duration-150 ${
        railPersistent
          ? "translate-x-0 opacity-100 pointer-events-auto"
          : "translate-x-1 opacity-0 pointer-events-none group-hover/sentence:translate-x-0 group-hover/sentence:opacity-100 group-hover/sentence:pointer-events-auto group-focus-within/sentence:translate-x-0 group-focus-within/sentence:opacity-100 group-focus-within/sentence:pointer-events-auto"
      }`}
      data-reader-sentence-rail="true"
    >
      <div className="flex flex-col items-center gap-1 rounded-full border border-border/40 bg-background/80 p-1 shadow-sm backdrop-blur-md transition-colors hover:bg-background/95">
      {showSentenceHandle ? (
        <button
          type="button"
          className={cn(
            "focus-ring inline-flex h-7 w-7 items-center justify-center rounded-full transition-colors",
            actionsActive ? "bg-muted/60 text-foreground" : "text-muted-foreground/60 hover:bg-muted/40 hover:text-foreground",
            !actionsActive && "hidden group-hover/sentence:inline-flex group-focus-within/sentence:inline-flex"
          )}
          data-reader-sentence-handle="true"
          aria-label="打开当前句操作"
          aria-expanded={actionsActive}
          onClick={(event) => {
            event.stopPropagation();
            setStripOpen(false);
            if (!sentenceId) {
              return;
            }
            onOpenSentenceActions?.(sentenceId, event.currentTarget);
          }}
        >
          <GripVertical aria-hidden="true" className="h-4 w-4" />
        </button>
      ) : null}

      {hasAssets ? (
        <>
          {noteCount > 0 ? (
            <button
              type="button"
              className={`focus-ring relative inline-flex h-7 w-7 items-center justify-center rounded-full transition-colors ${
                noteActive ? "bg-amber-500/20 text-amber-600 dark:text-amber-400" : "text-amber-500/80 hover:bg-amber-500/10 hover:text-amber-600 dark:hover:text-amber-400"
              }`}
              onClick={(event) => {
                event.stopPropagation();
                setStripOpen(false);
                if (!sentenceId) {
                  return;
                }
                onOpenNotes?.(sentenceId, event.currentTarget);
              }}
              aria-label={noteCount > 1 ? `打开当前句的 ${noteCount} 条笔记` : "打开当前句笔记"}
            >
              <MessageSquare className="h-[1.1rem] w-[1.1rem]" />
              {noteCount > 1 ? (
                <span className="absolute -right-0.5 -top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-amber-500 text-[0.6rem] font-bold text-white shadow-sm ring-1 ring-background" aria-hidden="true">
                  {noteCount}
                </span>
              ) : null}
            </button>
          ) : null}
          {highlightAnnotations.length > 0 ? (
            <button
              type="button"
              className={`focus-ring relative inline-flex h-7 w-7 items-center justify-center rounded-full transition-colors ${
                active ? "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400" : "text-emerald-500/80 hover:bg-emerald-500/10 hover:text-emerald-600 dark:hover:text-emerald-400"
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
                <span className="absolute -right-0.5 -top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-emerald-500 text-[0.6rem] font-bold text-white shadow-sm ring-1 ring-background" aria-hidden="true">
                  {highlightAnnotations.length}
                </span>
              ) : null}
            </button>
          ) : null}
        </>
      ) : null}
      </div>

      {hasAssets && hasMultipleHighlights && stripOpen ? (
        <div
          className="reader-annotation-gutter-strip absolute right-full top-0 mr-2 rounded-xl border border-border/40 bg-popover/95 p-1 shadow-md backdrop-blur-md"
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
                className={cn(
                  "flex w-full min-w-[8rem] items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm transition-colors",
                  isHovered ? "bg-muted/50 text-foreground" : "text-muted-foreground/80 hover:bg-muted/30 hover:text-foreground"
                )}
                onMouseEnter={() => onHoverTargetKeyChange?.(annotation.targetKey)}
                onFocus={() => onHoverTargetKeyChange?.(annotation.targetKey)}
                onBlur={() => onHoverTargetKeyChange?.(null)}
                onClick={(event) => {
                  event.stopPropagation();
                  setStripOpen(false);
                  onAnnotationJump?.(annotation, event.currentTarget, sentenceId);
                }}
              >
                <span className="text-[0.7rem] font-bold text-muted-foreground/50" aria-hidden="true">
                  {index + 1}
                </span>
                <span className="truncate max-w-[10rem]">{highlightChipLabel(annotation, sentenceId)}</span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
