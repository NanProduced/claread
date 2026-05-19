"use client";

import { BookOpen, Sparkles } from "lucide-react";
import type { RenderElement } from "platejs/react";
import type { ReaderAnalysisBlockNode } from "@/lib/reader-plate";
import { parseSentenceAnalysisContent } from "../../reader-entry-utils";
import { entryLabel } from "../shared";

const entryToneHeaderClass: Record<ReaderAnalysisBlockNode["entryType"], string> = {
  grammar_note: "text-grammar-violet bg-grammar-violet/10 ring-grammar-violet/15",
  sentence_analysis: "text-structure-green bg-structure-green/10 ring-structure-green/15",
  term_note: "text-vocab-amber bg-vocab-amber/10 ring-vocab-amber/15",
  logic_note: "text-lens-blue bg-lens-blue/10 ring-lens-blue/15",
  interpretation_note: "text-context-blue bg-context-blue/10 ring-context-blue/15",
};

interface ReaderAnalysisElementProps {
  props: Parameters<RenderElement>[0];
  visible?: boolean;
  onAsk?: () => void;
}

export function ReaderAnalysisElement({
  onAsk,
  props,
  visible = true,
}: ReaderAnalysisElementProps) {
  const element = props.element as unknown as ReaderAnalysisBlockNode;
  if (!visible) {
    return (
      <section {...props.attributes} className="hidden" data-reader-node="analysis" data-entry-type={element.entryType}>
        <span className="hidden">{props.children}</span>
      </section>
    );
  }

  const category = entryLabel(element);
  const label = element.title ?? element.label ?? "解析";
  const iconClass = entryToneHeaderClass[element.entryType];

  if (element.entryType === "sentence_analysis") {
    const parsed = parseSentenceAnalysisContent(element.content);

    return (
      <section
        {...props.attributes}
        className="reader-entry-note border-structure-green/20"
        data-reader-node="analysis"
        data-entry-type={element.entryType}
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-center gap-2">
            <span className={`flex h-8 w-8 items-center justify-center rounded-full ring-1 ${iconClass}`}>
              <BookOpen aria-hidden="true" className="h-4 w-4" />
            </span>
            <div>
              <p className="text-xs font-semibold text-structure-green">{category}</p>
              <h3 className="mt-0.5 text-[0.95rem] font-semibold leading-6 text-ink">{label}</h3>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="rounded-full border border-hairline bg-surface-warm px-2.5 py-1 text-[0.7rem] font-semibold text-muted">
              本句解析
            </span>
            {onAsk ? (
              <button
                type="button"
                className="focus-ring rounded-pill border border-hairline bg-surface px-2.5 py-1 text-[0.7rem] font-semibold text-lens-blue transition-colors hover:border-muted hover:text-ink"
                onClick={(event) => {
                  event.stopPropagation();
                  onAsk();
                }}
              >
                带入 Ask
              </button>
            ) : null}
          </div>
        </div>
        <div className="mt-4">
          {parsed.summary ? (
            <p className="mb-4 whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft">{parsed.summary}</p>
          ) : null}
          {parsed.chunks.length > 0 ? (
            <div className="overflow-hidden rounded-[10px] border border-hairline/80 bg-surface">
              {parsed.chunks.map((chunk, index) => (
                <div
                  key={`${element.entryId}-chunk-${index}`}
                  className={`grid grid-cols-[2.25rem_1fr] text-[0.9375rem] leading-[1.65] sm:grid-cols-[2.25rem_minmax(7rem,0.45fr)_1fr] ${
                    index > 0 ? "border-t border-hairline/80" : ""
                  }`}
                >
                  <div className={`reader-analysis-row-index reader-analysis-row-index--${(index % 6) + 1}`}>
                    {index + 1}
                  </div>
                  <div className="flex items-center px-3 py-2.5 text-sm font-semibold text-ink sm:border-r sm:border-hairline/80">
                    {chunk.label}
                  </div>
                  <div className="col-span-2 flex flex-1 items-center border-t border-hairline/60 bg-surface px-4 py-2.5 text-ink-soft sm:col-span-1 sm:border-t-0">
                    {chunk.text}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft">{element.content}</p>
          )}
        </div>
        <span className="hidden">{props.children}</span>
      </section>
    );
  }

  return (
    <section
      {...props.attributes}
      className="reader-entry-note border-grammar-violet/20"
      data-reader-node="analysis"
      data-entry-type={element.entryType}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-center gap-2">
          <span className={`flex h-8 w-8 items-center justify-center rounded-full ring-1 ${iconClass}`}>
            <Sparkles aria-hidden="true" className="h-4 w-4" />
          </span>
          <div>
            <p className="text-xs font-semibold text-grammar-violet">{category}</p>
            <h3 className="mt-0.5 text-[0.95rem] font-semibold leading-6 text-ink">{label}</h3>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="rounded-full border border-hairline bg-surface-warm px-2.5 py-1 text-[0.7rem] font-semibold text-muted">
            机器解析
          </span>
          {onAsk ? (
            <button
              type="button"
              className="focus-ring rounded-pill border border-hairline bg-surface px-2.5 py-1 text-[0.7rem] font-semibold text-lens-blue transition-colors hover:border-muted hover:text-ink"
              onClick={(event) => {
                event.stopPropagation();
                onAsk();
              }}
            >
              带入 Ask
            </button>
          ) : null}
        </div>
      </div>
      <div className="mt-4">
        <p className="whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft">{props.children}</p>
      </div>
    </section>
  );
}
