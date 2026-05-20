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

function EnhancedText({ text }: { text: string }) {
  if (!text) return null;

  // Split by sentence punctuation: 。 and ； (retaining them)
  const segments = text.split(/(?<=[。；])/g);

  return (
    <>
      {segments.map((segment, sIdx) => {
        if (!segment.trim()) return null;

        // Split into runs of English phrases vs Chinese/symbols
        const parts = segment.split(/([a-zA-Z]+(?:[\s'\-][a-zA-Z]+)*)/g);

        return (
          <span key={sIdx} className="block mt-1 first:mt-0">
            {parts.map((part, pIdx) => {
              if (/[a-zA-Z]/.test(part)) {
                return (
                  <span
                    key={pIdx}
                    className="font-serif font-semibold text-ink antialiased tracking-normal mx-0.5"
                  >
                    {part}
                  </span>
                );
              }
              return (
                <span key={pIdx} className="font-sans text-ink-soft">
                  {part}
                </span>
              );
            })}
          </span>
        );
      })}
    </>
  );
}

interface ReaderAnalysisElementProps {
  props: Parameters<RenderElement>[0];
  visible?: boolean;
  expanded?: boolean;
  active?: boolean;
  onAsk?: () => void;
  onDelete?: () => void;
  onToggle?: () => void;
  onFocusChange?: (focused: boolean) => void;
}

export function ReaderAnalysisElement({
  active = false,
  expanded = false,
  onAsk,
  onDelete,
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
      onMouseEnter={() => expanded && onFocusChange?.(true)}
      onMouseLeave={() => expanded && onFocusChange?.(false)}
      onFocus={() => expanded && onFocusChange?.(true)}
      onBlur={() => expanded && onFocusChange?.(false)}
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
                {element.sourceKind === "ask_supplement" ? (
                  <span className="rounded-full border border-lens-blue/15 bg-lens-blue/10 px-2 py-0.5 text-[0.64rem] font-semibold uppercase tracking-[0.08em] text-lens-blue">
                    AI 补充
                  </span>
                ) : null}
                <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-hairline/80 bg-white/92 text-muted">
                  <ChevronDown
                    aria-hidden="true"
                    className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
                  />
                </span>
              </div>
              <h3 className="mt-1 text-[0.95rem] font-semibold leading-6 text-ink">{label}</h3>
            </div>
          </div>
        </button>

        {onAsk ? (
          <div className="flex shrink-0 items-center gap-2 opacity-0 transition-opacity group-hover/analysis:opacity-100 focus-within:opacity-100">
            {onDelete && element.deletable ? (
              <button
                type="button"
                className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-full border border-transparent bg-transparent text-muted transition-[border-color,color,background-color] hover:border-hairline hover:bg-surface hover:text-destructive"
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete();
                }}
                aria-label="删除 AI 补充"
              >
                <span className="text-sm font-semibold">-</span>
              </button>
            ) : null}
            <button
              type="button"
              className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-full border border-transparent bg-transparent text-muted transition-[border-color,color,background-color] hover:border-hairline hover:bg-surface hover:text-lens-blue"
              onClick={(event) => {
                event.stopPropagation();
                onAsk();
              }}
              aria-label="带解析进入 Ask"
            >
              <MessageSquare aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : null}
      </div>

      {expanded ? (
        <div className="mt-3 border-t border-hairline/80 pt-3">
          {parsed ? (
            <>
              {parsed.summary ? (
                <p className="mb-4 whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft"><EnhancedText text={parsed.summary} /></p>
              ) : null}
              {parsed.chunks.length > 0 ? (
                <div className="overflow-hidden rounded-[10px] border border-hairline/50 bg-surface">
                  {parsed.chunks.map((chunk, index) => (
                    <div
                      key={`${element.entryId}-chunk-${index}`}
                      className={`grid grid-cols-[2.25rem_1fr] text-[0.9375rem] leading-[1.65] sm:grid-cols-[2.25rem_minmax(7rem,0.45fr)_1fr] ${
                        index > 0 ? "border-t border-hairline/50" : ""
                      }`}
                    >
                      <div className={`reader-analysis-row-index reader-analysis-row-index--${(index % 6) + 1}`}>
                        {index + 1}
                      </div>
                      <div className="flex items-center px-3 py-2.5 text-sm font-semibold text-ink sm:border-r sm:border-hairline/50">
                        {chunk.label}
                      </div>
                      <div className="col-span-2 flex flex-1 items-center border-t border-hairline/45 bg-surface px-4 py-2.5 text-ink-soft sm:col-span-1 sm:border-t-0">
                        <div className="w-full"><EnhancedText text={chunk.text} /></div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft"><EnhancedText text={element.content} /></p>
              )}
            </>
          ) : (
            <p className="whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft"><EnhancedText text={element.content} /></p>
          )}
        </div>
      ) : null}

      <span className="hidden">{props.children}</span>
    </section>
  );
}
