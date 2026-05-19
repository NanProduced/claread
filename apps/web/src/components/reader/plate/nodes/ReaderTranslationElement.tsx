"use client";

import type { RenderElement } from "platejs/react";

interface ReaderTranslationElementProps {
  props: Parameters<RenderElement>[0];
  visible: boolean;
  onAsk?: () => void;
}

export function ReaderTranslationElement({
  onAsk,
  props,
  visible,
}: ReaderTranslationElementProps) {
  return (
    <div
      {...props.attributes}
      className={visible ? "mt-2" : "hidden"}
      data-reader-node="translation"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm leading-7 text-muted/95">{props.children}</p>
        {onAsk ? (
          <button
            type="button"
            className="focus-ring shrink-0 rounded-pill border border-hairline bg-surface px-2.5 py-1 text-[0.7rem] font-semibold text-lens-blue transition-colors hover:border-muted hover:text-ink"
            onClick={(event) => {
              event.stopPropagation();
              onAsk();
            }}
          >
            追问译文
          </button>
        ) : null}
      </div>
    </div>
  );
}
