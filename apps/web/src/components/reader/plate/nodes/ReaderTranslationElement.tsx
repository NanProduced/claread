"use client";

import type { RenderElement } from "platejs/react";

interface ReaderTranslationElementProps {
  props: Parameters<RenderElement>[0];
  className?: string;
  copyClassName?: string;
}

export function ReaderTranslationElement({
  className,
  copyClassName,
  props,
}: ReaderTranslationElementProps) {
  return (
    <div
      {...props.attributes}
      className={[
        "reader-translation-layer group/translation",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      data-reader-node="translation"
    >
      <div className="reader-translation-shell">
        <p
          className={["reader-translation-copy", copyClassName].filter(Boolean).join(" ")}
          data-reader-translation-text="true"
        >
          {props.children}
        </p>
      </div>
    </div>
  );
}
