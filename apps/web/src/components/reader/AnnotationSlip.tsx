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
    <div className="mt-4 space-y-3">
      {noteAnnotations.map((item) => (
        <div
          key={item.id}
          className="relative overflow-hidden rounded-xl bg-surface-warm px-4 py-3 text-[0.9375rem] leading-[1.65] text-ink-soft shadow-surface-quiet border border-hairline/80"
        >
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-start gap-2.5 flex-1">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-vocab-amber/15 text-vocab-amber mt-0.5">
                <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
              </span>
              <p className="whitespace-pre-wrap">{item.note}</p>
            </div>
            <div className="flex items-center gap-3 shrink-0 pt-0.5">
              <span className="text-xs text-muted">
                {new Date(item.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
