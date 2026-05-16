import { PenLine } from "lucide-react";
import type { WebAnnotationVm } from "@/types/api/annotations";

export interface AnnotationSlipProps {
  annotations: WebAnnotationVm[];
}

export function AnnotationSlip({ annotations }: AnnotationSlipProps) {
  const noteAnnotations = annotations.filter((item) => item.note);

  if (noteAnnotations.length === 0) {
    return null;
  }

  return (
    <div className="reader-annotation-slip-list mt-4 space-y-3">
      {noteAnnotations.map((item) => (
        <div
          key={item.id}
          className="reader-annotation-slip relative overflow-hidden rounded-xl bg-surface-warm px-4 py-3 text-[0.9375rem] leading-[1.65] text-ink-soft shadow-surface-quiet border border-hairline/80"
        >
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-start gap-2.5 flex-1">
              <span className="reader-annotation-slip-icon flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-vocab-amber/15 text-vocab-amber mt-0.5">
                <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-muted">
                  {item.anchorType === "text_range"
                    ? item.type === "highlight"
                      ? "选区高亮 + 笔记"
                      : "选区笔记"
                    : item.type === "highlight"
                      ? "整句高亮 + 笔记"
                      : "整句笔记"}
                </p>
                {item.anchorType === "text_range" ? (
                  <p className="mt-1 line-clamp-2 text-xs text-muted">“{item.selectedText}”</p>
                ) : null}
                <p className="reader-annotation-slip-text mt-1 whitespace-pre-wrap">{item.note}</p>
              </div>
            </div>
            <div className="flex items-center gap-3 shrink-0 pt-0.5">
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
