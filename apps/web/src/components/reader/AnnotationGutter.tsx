import { Highlighter } from "lucide-react";

import type { WebAnnotationVm } from "@/types/api/annotations";

export interface AnnotationGutterProps {
  annotations: WebAnnotationVm[];
  visible?: boolean;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
}

export function AnnotationGutter({
  annotations,
  visible = true,
  onAnnotationJump,
}: AnnotationGutterProps) {
  if (!visible) {
    return null;
  }

  const highlightAnnotation = annotations.find((item) => item.type === "highlight") ?? null;
  if (!highlightAnnotation) {
    return null;
  }

  return (
    <div
      className="reader-annotation-gutter absolute -left-5 top-2.5 flex flex-col gap-1"
      aria-label="句子高亮锚点"
    >
      <button
        type="button"
        className="reader-annotation-gutter-marker reader-annotation-gutter-marker--highlight relative text-structure-green drop-shadow-sm opacity-80"
        onClick={(event) => {
          event.stopPropagation();
          onAnnotationJump?.(highlightAnnotation);
        }}
        aria-label="跳转到高亮锚点"
      >
        <Highlighter className="h-4 w-4" />
      </button>
    </div>
  );
}
