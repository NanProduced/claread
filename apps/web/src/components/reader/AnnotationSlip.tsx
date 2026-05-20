import { PenLine } from "lucide-react";
import type { WebAnnotationVm } from "@/types/api/annotations";

export interface AnnotationSlipProps {
  annotations: WebAnnotationVm[];
  visible?: boolean;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
  onAnnotationAsk?: (annotation: WebAnnotationVm) => void;
}

export function AnnotationSlip({
  annotations,
  visible = true,
  onAnnotationAsk,
  onAnnotationJump,
}: AnnotationSlipProps) {
  if (!visible) {
    return null;
  }

  const noteAnnotations = annotations.filter((item) => item.note);

  if (noteAnnotations.length === 0) {
    return null;
  }

  return (
    <div className="reader-annotation-slip-list mt-4 space-y-3">
      {noteAnnotations.map((item) => (
        <div
          key={item.id}
          className="reader-annotation-slip relative overflow-hidden rounded-xl border border-hairline/80 bg-surface-warm px-4 py-3 text-left text-[0.9375rem] leading-[1.65] text-ink-soft shadow-surface-quiet"
        >
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-start gap-2.5 flex-1">
              <button
                type="button"
                className="focus-ring flex min-h-11 min-w-0 flex-1 items-start gap-2.5 rounded-lg text-left transition-colors hover:bg-white/45"
                onClick={(event) => {
                  event.stopPropagation();
                  onAnnotationJump?.(item);
                }}
                aria-label="跳转到笔记锚点"
              >
                <span className="reader-annotation-slip-icon mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-vocab-amber/15 text-vocab-amber">
                  <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="text-xs font-semibold text-muted">
                    {item.anchorType === "text_range"
                      ? item.type === "highlight"
                        ? "选区高亮 + 笔记"
                        : "选区笔记"
                      : item.type === "highlight"
                        ? "整句高亮 + 笔记"
                        : "整句笔记"}
                  </span>
                  {item.anchorType === "text_range" ? (
                    <span className="mt-1 block line-clamp-2 text-xs text-muted">“{item.selectedText}”</span>
                  ) : null}
                  <span className="reader-annotation-slip-text mt-1 block whitespace-pre-wrap">{item.note}</span>
                </span>
              </button>
            </div>
            <div className="flex items-center gap-3 shrink-0 pt-0.5">
              {onAnnotationAsk ? (
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-10 items-center rounded-pill border border-hairline bg-surface px-2.5 py-1 text-[0.7rem] font-semibold text-lens-blue transition-colors hover:border-muted hover:text-ink"
                  onClick={(event) => {
                    event.stopPropagation();
                    onAnnotationAsk(item);
                  }}
                >
                  带入 Ask
                </button>
              ) : null}
              <span className="reader-annotation-slip-time text-xs text-muted">
                {new Date(item.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
