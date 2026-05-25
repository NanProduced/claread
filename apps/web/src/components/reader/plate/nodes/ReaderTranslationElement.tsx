"use client";

import type { RenderElement } from "platejs/react";

interface ReaderTranslationElementProps {
  props: Parameters<RenderElement>[0];
}

export function ReaderTranslationElement({
  props,
}: ReaderTranslationElementProps) {
  return (
    <div
      {...props.attributes}
      className="reader-translation-layer group/translation"
      data-reader-node="translation"
    >
      <div className="reader-translation-shell">
        <p className="reader-translation-copy" data-reader-translation-text="true">
          {props.children}
        </p>
      </div>
    </div>
  );
}
