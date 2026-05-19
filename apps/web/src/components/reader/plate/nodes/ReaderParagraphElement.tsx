"use client";

import type { RenderElement } from "platejs/react";
import type { ReaderParagraphNode } from "@/lib/reader-plate";

interface ReaderParagraphElementProps {
  props: Parameters<RenderElement>[0];
  paragraphCount: number;
  paragraphIndex: number;
}

export function ReaderParagraphElement({
  paragraphCount,
  paragraphIndex,
  props,
}: ReaderParagraphElementProps) {
  const element = props.element as unknown as ReaderParagraphNode;

  return (
    <section
      {...props.attributes}
      className="reader-paragraph"
      data-reader-node="paragraph"
      data-paragraph-id={element.paragraphId}
    >
      <div className="reader-paragraph-index">
        <span>{String(paragraphIndex + 1).padStart(2, "0")}</span>
        <span aria-hidden="true">/</span>
        <span>{String(paragraphCount).padStart(2, "0")}</span>
      </div>
      <div className="min-w-0 space-y-6">{props.children}</div>
    </section>
  );
}
