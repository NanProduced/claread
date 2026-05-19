import { Bookmark, Highlighter, PenLine } from "lucide-react";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";

export interface AnnotationGutterProps {
  annotations: WebAnnotationVm[];
  favoriteTargets?: WebFavoriteTargetVm[];
  visible?: boolean;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
  onFavoriteJump?: (favorite: WebFavoriteTargetVm) => void;
}

export function AnnotationGutter({
  annotations,
  favoriteTargets = [],
  visible = true,
  onAnnotationJump,
  onFavoriteJump,
}: AnnotationGutterProps) {
  if (!visible) {
    return null;
  }

  const favoriteCount = favoriteTargets.length;
  if (annotations.length === 0 && favoriteCount === 0) {
    return null;
  }

  const hasNote = annotations.some((item) => item.note);
  const hasHighlight = annotations.some((item) => item.type === "highlight");
  const hasFavorite = favoriteCount > 0;
  const noteAnnotation = annotations.find((item) => item.note) ?? null;
  const highlightAnnotation = annotations.find((item) => item.type === "highlight") ?? null;
  const favoriteTarget = favoriteTargets[0] ?? null;

  return (
    <div
      className="reader-annotation-gutter absolute -left-5 top-2.5 flex flex-col gap-1"
      aria-label="句子资产锚点"
    >
      {hasNote ? (
        <button
          type="button"
          className="reader-annotation-gutter-marker reader-annotation-gutter-marker--note relative text-vocab-amber drop-shadow-sm transition-transform hover:-translate-y-0.5"
          onClick={(event) => {
            event.stopPropagation();
            noteAnnotation && onAnnotationJump?.(noteAnnotation);
          }}
          aria-label="跳转到笔记锚点"
        >
          <PenLine className="h-5 w-5" />
        </button>
      ) : null}
      {hasFavorite ? (
        <button
          type="button"
          className="reader-annotation-gutter-marker reader-annotation-gutter-marker--favorite relative text-vocab-amber drop-shadow-sm transition-transform hover:-translate-y-0.5"
          onClick={(event) => {
            event.stopPropagation();
            favoriteTarget && onFavoriteJump?.(favoriteTarget);
          }}
          aria-label="跳转到收藏锚点"
        >
          <Bookmark className="h-5 w-5 fill-current" />
        </button>
      ) : null}
      {hasHighlight ? (
        <button
          type="button"
          className="reader-annotation-gutter-marker reader-annotation-gutter-marker--highlight relative text-structure-green drop-shadow-sm opacity-80"
          onClick={(event) => {
            event.stopPropagation();
            highlightAnnotation && onAnnotationJump?.(highlightAnnotation);
          }}
          aria-label="跳转到高亮锚点"
        >
          <Highlighter className="h-4 w-4" />
        </button>
      ) : null}
    </div>
  );
}
