"use client";

import { BookOpen, ChevronDown, MessageSquare, Sparkles } from "lucide-react";
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

function analysisCardToneClass(entryType: ReaderAnalysisBlockNode["entryType"]) {
  switch (entryType) {
    case "sentence_analysis":
      return "border-structure-green/22";
    case "grammar_note":
      return "border-grammar-violet/22";
    case "term_note":
      return "border-vocab-amber/18";
    case "logic_note":
      return "border-lens-blue/18";
    case "interpretation_note":
    default:
      return "border-hairline";
  }
}

interface ReaderAnalysisElementProps {
  props: Parameters<RenderElement>[0];
  visible?: boolean;
  expanded?: boolean;
  active?: boolean;
  onAsk?: () => void;
  onToggle?: () => void;
  onFocusChange?: (focused: boolean) => void;
}

export function ReaderAnalysisElement({
  active = false,
  expanded = false,
  onAsk,
  onFocusChange,
  onToggle,
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
  const cardToneClass = analysisCardToneClass(element.entryType);
  const activeClass =
    active && (element.entryType === "sentence_analysis"
      ? "reader-entry-note--active reader-entry-note--active-analysis"
      : "reader-entry-note--active reader-entry-note--active-grammar");

  const parsed = element.entryType === "sentence_analysis"
    ? parseSentenceAnalysisContent(element.content)
    : null;

  return (
    <section
      {...props.attributes}
      className={[
        "reader-entry-note group/analysis",
        expanded ? "reader-entry-note--expanded" : "reader-entry-note--collapsed",
        cardToneClass,
        activeClass,
      ]
        .filter(Boolean)
        .join(" ")}
      data-reader-node="analysis"
      data-entry-id={element.entryId}
      data-entry-type={element.entryType}
      data-entry-expanded={expanded ? "true" : "false"}
      onMouseEnter={() => onFocusChange?.(true)}
      onMouseLeave={() => onFocusChange?.(false)}
      onFocus={() => onFocusChange?.(true)}
      onBlur={() => onFocusChange?.(false)}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          className="min-w-0 flex-1 text-left"
          onClick={(event) => {
            event.stopPropagation();
            onToggle?.();
          }}
          aria-expanded={expanded}
          aria-label={`${expanded ? "收起" : "展开"}${label}`}
        >
          <div className="flex items-start gap-2.5">
            <span
              className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ring-1 ${iconClass}`}
            >
              {element.entryType === "sentence_analysis" ? (
                <BookOpen aria-hidden="true" className="h-3.5 w-3.5" />
              ) : (
                <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <p
                  className={`text-[0.72rem] font-semibold uppercase tracking-[0.08em] ${
                    element.entryType === "sentence_analysis" ? "text-structure-green" : "text-grammar-violet"
                  }`}
                >
                  {category}
                </p>
                <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-hairline/80 bg-white/92 text-muted">
                  <ChevronDown
                    aria-hidden="true"
                    className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
                  />
                </span>
              </div>
              <h3 className="mt-1 text-[0.95rem] font-semibold leading-6 text-ink">{label}</h3>
              {!expanded ? (
                <p className="mt-1 text-[0.78rem] leading-5 text-muted-foreground">
                  点击展开后查看完整解释，并回指原文锚点。
                </p>
              ) : null}
            </div>
          </div>
        </button>

        {onAsk ? (
          <button
            type="button"
            className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-transparent bg-transparent text-muted opacity-0 transition-[opacity,border-color,color,background-color] hover:border-hairline hover:bg-surface hover:text-lens-blue focus-visible:opacity-100 group-hover/analysis:opacity-100"
            onClick={(event) => {
              event.stopPropagation();
              onAsk();
            }}
            aria-label="带解析进入 Ask"
          >
            <MessageSquare aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>

      {expanded ? (
        <div className="mt-3 border-t border-hairline/80 pt-3">
          {parsed ? (
            <>
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
            </>
          ) : (
            <p className="whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft">{props.children}</p>
          )}
        </div>
      ) : null}

      <span className="hidden">{props.children}</span>
    </section>
  );
}
