import { Highlighter } from "lucide-react";

import type { WebAnnotationVm } from "@/types/api/annotations";

export interface AnnotationGutterProps {
  sentenceId?: string;
  annotations: WebAnnotationVm[];
  visible?: boolean;
  hoveredTargetKey?: string | null;
  onHoverTargetKeyChange?: (targetKey: string | null) => void;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
}

export function AnnotationGutter({
  sentenceId,
  annotations,
  visible = true,
  hoveredTargetKey = null,
  onHoverTargetKeyChange,
  onAnnotationJump,
}: AnnotationGutterProps) {
  if (!visible) {
    return null;
  }

  const highlightAnnotation = annotations.find((item) => item.type === "highlight") ?? null;
  if (!highlightAnnotation) {
    return null;
  }

  const isFirstSentence =
    !sentenceId ||
    (highlightAnnotation.anchorType === "multi_text"
      ? highlightAnnotation.segments?.[0]?.sentenceId === sentenceId
      : true);

  if (!isFirstSentence) {
    return null;
  }

  return (
    <div
      className="reader-annotation-gutter absolute -left-5 top-2.5 flex flex-col gap-1"
      aria-label="句子高亮锚点"
    >
      <button
        type="button"
        className={`reader-annotation-gutter-marker reader-annotation-gutter-marker--highlight relative text-structure-green drop-shadow-sm opacity-80 ${
          hoveredTargetKey === highlightAnnotation.targetKey ? "reader-annotation-gutter-marker--active" : ""
        }`}
        onMouseEnter={() => onHoverTargetKeyChange?.(highlightAnnotation.targetKey)}
        onMouseLeave={() => onHoverTargetKeyChange?.(null)}
        onFocus={() => onHoverTargetKeyChange?.(highlightAnnotation.targetKey)}
        onBlur={() => onHoverTargetKeyChange?.(null)}
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
