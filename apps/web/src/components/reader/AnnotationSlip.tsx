import { PenLine, Sparkles } from "lucide-react";
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
                  <span className="text-xs font-semibold text-muted block mb-0.5">
                    读书笔记
                  </span>
                  <span className="reader-annotation-slip-text block whitespace-pre-wrap">{item.note}</span>
                </span>
              </button>
            </div>
            <div className="flex items-center gap-3 shrink-0 pt-0.5">
              {onAnnotationAsk ? (
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-8 items-center gap-1.5 rounded-full bg-lens-blue/5 px-3 py-1 text-[0.725rem] font-semibold text-lens-blue transition-all duration-200 hover:bg-lens-blue hover:text-white active:scale-95"
                  onClick={(event) => {
                    event.stopPropagation();
                    onAnnotationAsk(item);
                  }}
                >
                  <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>带入 Ask</span>
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
