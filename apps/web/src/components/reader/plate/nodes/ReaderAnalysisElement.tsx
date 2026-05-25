"use client";

import { ChevronDown, MessageSquare } from "lucide-react";
import type { CSSProperties } from "react";
import type { RenderElement } from "platejs/react";
import type { ReaderAnalysisBlockNode } from "@/lib/reader-plate";
import { parseSentenceAnalysisContent } from "../../reader-entry-utils";
import { entryLabel } from "../shared";

function getCircleNumber(num: number): string {
  const circles = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];
  return circles[num - 1] || `(${num})`;
}

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
      return "border-context-blue/18";
    default:
      return "border-hairline";
  }
}

function entryLabelToneClass(entryType: ReaderAnalysisBlockNode["entryType"]) {
  switch (entryType) {
    case "sentence_analysis":
      return "text-structure-green";
    case "grammar_note":
      return "text-grammar-violet";
    case "term_note":
      return "text-vocab-amber";
    case "logic_note":
      return "text-lens-blue";
    case "interpretation_note":
    default:
      return "text-context-blue";
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
                    className="font-sans font-medium text-ink tracking-normal mx-[0.15em]"
                  >
                    {part}
                  </span>
                );
              }
              return (
                <span key={pIdx} className="font-sans">
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
  cueIndex?: number;
  onAsk?: () => void;
  onDelete?: () => void;
  onToggle?: () => void;
  onFocusChange?: (focused: boolean) => void;
}

export function ReaderAnalysisElement({
  active = false,
  expanded = false,
  cueIndex,
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
  const cardToneClass = analysisCardToneClass(element.entryType);
  const labelToneClass = entryLabelToneClass(element.entryType);
  const activeClass = active
    ? `reader-entry-note--active reader-entry-note--active-${element.entryType.replace("_", "-")}`
    : "";
  const headerCopy = `${category} · ${label}`;
  const cueCopy =
    element.entryType === "grammar_note" && typeof cueIndex === "number"
      ? getCircleNumber(cueIndex)
      : null;
  const supportsSourceLinkPreview = element.entryType === "grammar_note";

  const parsed = element.entryType === "sentence_analysis"
    ? parseSentenceAnalysisContent(element.content)
    : null;

  return (
    <section
      {...props.attributes}
      className={[
        "reader-entry-note group/analysis",
        `reader-entry-note--${element.entryType.replace("_", "-")}`,
        expanded ? "reader-entry-note--expanded" : "reader-entry-note--collapsed",
        activeClass,
      ]
        .filter(Boolean)
        .join(" ")}
      data-reader-node="analysis"
      data-entry-id={element.entryId}
      data-entry-type={element.entryType}
      data-entry-expanded={expanded ? "true" : "false"}
      onMouseEnter={supportsSourceLinkPreview ? () => onFocusChange?.(true) : undefined}
      onMouseLeave={supportsSourceLinkPreview ? () => onFocusChange?.(false) : undefined}
      onFocus={supportsSourceLinkPreview ? () => onFocusChange?.(true) : undefined}
      onBlur={supportsSourceLinkPreview ? () => onFocusChange?.(false) : undefined}
    >
      <div className="reader-entry-note-head flex items-center justify-between gap-3">
        <button
          type="button"
          className="reader-entry-note-trigger min-w-0 flex-1 text-left"
          onClick={(event) => {
            event.stopPropagation();
            onToggle?.();
          }}
          aria-expanded={expanded}
          aria-label={`${expanded ? "收起" : "展开"}${label}`}
        >
          <div className="flex min-w-0 items-center gap-2 select-none">
            <span
              className={`reader-entry-note-heading-copy truncate font-sans text-[0.88rem] font-bold tracking-wide ${labelToneClass}`}
              style={{ color: "var(--reader-entry-accent)" }}
            >
              {headerCopy}
            </span>
            {cueCopy ? (
              <span
                className={`reader-entry-note-heading-index shrink-0 font-sans text-[0.85rem] font-bold ${labelToneClass}`}
                style={{ color: "var(--reader-entry-accent)" }}
              >
                {cueCopy}
              </span>
            ) : null}
            {element.sourceKind === "ask_supplement" ? (
              <span className="ml-1 shrink-0 rounded bg-lens-blue/10 px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-[0.16em] text-lens-blue">
                AI 补充
              </span>
            ) : null}
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-1.5">
          {expanded && onAsk ? (
            <div className="flex shrink-0 items-center gap-1.5 opacity-0 transition-opacity group-hover/analysis:opacity-100 focus-within:opacity-100">
              {onDelete && element.deletable ? (
                <button
                  type="button"
                  className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-full border border-transparent bg-transparent text-muted transition-[border-color,color,background-color] hover:border-hairline hover:bg-surface hover:text-destructive"
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
                className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-full border border-transparent bg-transparent text-muted transition-[border-color,color,background-color] hover:border-hairline hover:bg-surface hover:text-lens-blue"
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
          <button
            type="button"
            className="reader-entry-note-toggle focus-ring flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted/55"
            onClick={(event) => {
              event.stopPropagation();
              onToggle?.();
            }}
            aria-expanded={expanded}
            aria-label={`${expanded ? "收起" : "展开"}${label}`}
          >
            <ChevronDown
              aria-hidden="true"
              className={`h-4 w-4 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
            />
          </button>
        </div>
      </div>

      <div
        className={`grid transition-[grid-template-rows,opacity,margin-top] duration-200 ease-[cubic-bezier(0.22,1,0.36,1)] ${
          expanded ? "mt-4 grid-rows-[1fr] opacity-100" : "mt-0 grid-rows-[0fr] opacity-0 pointer-events-none"
        }`}
        aria-hidden={!expanded}
      >
        <div className="overflow-hidden px-1 -mx-1">
          <div className="reader-entry-note-body border-t border-hairline/60 pt-4 mt-1">
            {element.entryType === "sentence_analysis" && parsed ? (
              <>
                {parsed.summary ? (
                  <p className="reader-entry-note-summary mb-5 whitespace-pre-line text-[0.95rem] leading-[1.75] text-ink">
                    <EnhancedText text={parsed.summary} />
                  </p>
                ) : null}
                {parsed.chunks.length > 0 ? (
                  <div className="reader-entry-analysis-list">
                    {parsed.chunks.map((chunk, index) => (
                      <div
                        key={`${element.entryId}-chunk-${index}`}
                        className="reader-entry-analysis-item reader-entry-analysis-item-tint group/chunk"
                        data-chunk-index={index + 1}
                        style={{ "--analysis-accent": `var(--reader-analysis-tone-${(index % 6) + 1})` } as CSSProperties}
                        onMouseEnter={(event) => {
                          const sentence = event.currentTarget.closest('[data-reader-node="sentence"]');
                          const atoms = sentence?.querySelectorAll(
                            `[data-analysis-entry-id="${element.entryId}"][data-analysis-index="${index + 1}"]`
                          );
                          atoms?.forEach((atom) => atom.classList.add("reader-analysis-atom--active"));
                        }}
                        onMouseLeave={(event) => {
                          const sentence = event.currentTarget.closest('[data-reader-node="sentence"]');
                          const atoms = sentence?.querySelectorAll(
                            `[data-analysis-entry-id="${element.entryId}"][data-analysis-index="${index + 1}"]`
                          );
                          atoms?.forEach((atom) => atom.classList.remove("reader-analysis-atom--active"));
                        }}
                      >
                        <div className={`reader-analysis-row-index reader-analysis-row-index--${(index % 6) + 1}`}>
                          {index + 1}
                        </div>
                        <div className="reader-entry-analysis-copy">
                          <div className="reader-entry-analysis-label">
                            {chunk.label}
                          </div>
                          <div className="reader-entry-analysis-text">
                            <EnhancedText text={chunk.text} />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="reader-entry-note-prose whitespace-pre-line text-[0.95rem] leading-[1.75] text-ink">
                    <EnhancedText text={element.content} />
                  </p>
                )}
              </>
            ) : (
              <p className="reader-entry-note-prose whitespace-pre-line text-[0.95rem] leading-[1.75] text-ink">
                <EnhancedText text={element.content} />
              </p>
            )}
          </div>
        </div>
      </div>

      <span className="hidden">{props.children}</span>
    </section>
  );
}
