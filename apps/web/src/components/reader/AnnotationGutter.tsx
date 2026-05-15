import { Bookmark, Highlighter } from "lucide-react";
import type { WebAnnotationVm } from "@/types/api/annotations";

export interface AnnotationGutterProps {
  annotations: WebAnnotationVm[];
}

export function AnnotationGutter({ annotations }: AnnotationGutterProps) {
  if (annotations.length === 0) {
    return null;
  }

  const hasNote = annotations.some((item) => item.note);
  const hasHighlight = annotations.some((item) => item.type === "highlight");

  return (
    <div
      className="reader-annotation-gutter absolute -left-5 top-2.5 flex flex-col gap-1"
      aria-hidden="true"
    >
      {hasNote ? (
        <div className="reader-annotation-gutter-marker reader-annotation-gutter-marker--note relative text-vocab-amber drop-shadow-sm transition-transform hover:-translate-y-0.5">
          <Bookmark className="h-5 w-5 fill-current" />
        </div>
      ) : hasHighlight ? (
        <div className="reader-annotation-gutter-marker reader-annotation-gutter-marker--highlight relative text-structure-green drop-shadow-sm opacity-80">
          <Highlighter className="h-4 w-4" />
        </div>
      ) : null}
    </div>
  );
}
