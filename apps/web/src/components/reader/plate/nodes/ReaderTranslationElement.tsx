"use client";

import { MessageSquare } from "lucide-react";
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
      className={visible ? "group/translation mt-2" : "hidden"}
      data-reader-node="translation"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm leading-7 text-muted/95">{props.children}</p>
        {onAsk ? (
          <button
            type="button"
            className="focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-transparent bg-transparent text-muted opacity-0 transition-[opacity,border-color,color,background-color] hover:border-hairline hover:bg-surface hover:text-lens-blue focus-visible:opacity-100 group-hover/translation:opacity-100"
            onClick={(event) => {
              event.stopPropagation();
              onAsk();
            }}
            aria-label="带这句译文进入 Ask"
          >
            <MessageSquare aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    </div>
  );
}
