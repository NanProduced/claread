"use client";

import { ChevronDown, Flag, Sparkles } from "lucide-react";
import type { CSSProperties } from "react";
import type { RenderElement } from "platejs/react";
import { readerInlineFocusRing, readerPanelItem, readerTransitionStandard } from "@/components/reader/interaction";
import { cn } from "@/lib/cn";
import type { ReaderAnalysisBlockNode } from "@/lib/reader-plate";
import { parseSentenceAnalysisContent } from "../../reader-entry-utils";
import { entryLabel } from "../shared";

function getCircleNumber(num: number): string {
  const circles = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];
  return circles[num - 1] || `(${num})`;
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

  // Split by newline to respect natural paragraph bounds and prevent trailing parenthesis orphan bugs
  const segments = text.split("\n");

  return (
    <>
      {segments.map((segment, sIdx) => {
        if (!segment.trim()) {
          // Render a spacer for empty lines to let paragraph blocks breathe
          return <div key={sIdx} className="h-2.5" />;
        }

        // Split into runs of English phrases vs Chinese/symbols
        const parts = segment.split(/([a-zA-Z]+(?:[\s'\-][a-zA-Z]+)*)/g);

        return (
          <span key={sIdx} className="reader-enhanced-line block text-[0.93rem] leading-[1.82] text-ink-soft mb-2.5 last:mb-0">
            {parts.map((part, pIdx) => {
              if (/[a-zA-Z]/.test(part)) {
                return (
                  <span
                    key={pIdx}
                    className="reader-enhanced-term font-sans font-semibold text-ink mx-[0.08em] tracking-normal"
                  >
                    {part}
                  </span>
                );
              }
              return (
                <span key={pIdx} className="reader-enhanced-copy font-sans font-normal">
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

function setSentenceAnalysisPreview(target: HTMLElement, entryId: string, chunkIndex: number, active: boolean) {
  const sentence = target.closest('[data-reader-node="sentence"]');
  const atoms = sentence?.querySelectorAll<HTMLElement>(
    `[data-analysis-entry-id="${entryId}"][data-analysis-index="${chunkIndex}"]`,
  );
  atoms?.forEach((atom) => atom.classList.toggle("reader-analysis-atom--active", active));
}

interface ReaderAnalysisElementProps {
  props: Parameters<RenderElement>[0];
  visible?: boolean;
  expanded?: boolean;
  active?: boolean;
  cueIndex?: number;
  onAsk?: () => void;
  onDelete?: () => void;
  onFeedback?: () => void;
  onToggle?: () => void;
  onFocusChange?: (focused: boolean) => void;
}

export function ReaderAnalysisElement({
  active = false,
  expanded = false,
  cueIndex,
  onAsk,
  onDelete,
  onFeedback,
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
    ? parseSentenceAnalysisContent(element.content, element.chunks)
    : null;
  const headerIconActionClassName = cn(
    "inline-flex h-7 w-7 items-center justify-center rounded-md text-muted/68 transition-[color,opacity] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)]",
    "hover:text-ink active:text-ink focus-visible:text-ink",
    "[&_svg]:stroke-[1.9] hover:[&_svg]:stroke-[2.35] focus-visible:[&_svg]:stroke-[2.35]",
  );

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
              <span className="ml-1 shrink-0 rounded bg-lens-blue/10 px-1.5 py-0.5 text-[0.6rem] font-semibold tracking-[0.16em] text-lens-blue">
                AI 补充
              </span>
            ) : null}
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-1 pl-2">
          {expanded && (onAsk || onFeedback) ? (
            <div className="flex shrink-0 items-center gap-1.5">
              {onAsk ? (
                <button
                  type="button"
                  className={cn(headerIconActionClassName, "text-lens-blue/72 hover:text-lens-blue focus-visible:text-lens-blue")}
                  onClick={(event) => {
                    event.stopPropagation();
                    onAsk();
                  }}
                  aria-label="带解析进入 Ask"
                  title="带解析进入 Ask"
                >
                  <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                </button>
              ) : null}
              {onFeedback ? (
                <button
                  type="button"
                  className={cn(headerIconActionClassName, "hover:text-lens-blue focus-visible:text-lens-blue")}
                  onClick={(event) => {
                    event.stopPropagation();
                    onFeedback();
                  }}
                  aria-label="反馈这条标注"
                  title="反馈这条标注"
                >
                  <Flag aria-hidden="true" className="h-3.5 w-3.5" />
                </button>
              ) : null}
              {onDelete && element.deletable ? (
                <button
                  type="button"
                  className={cn(headerIconActionClassName, "text-muted/62 hover:text-destructive focus-visible:text-destructive")}
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete();
                  }}
                  aria-label="删除 AI 补充"
                >
                  <span className="text-sm font-semibold">-</span>
                </button>
              ) : null}
            </div>
          ) : null}
          <button
            type="button"
            className={cn(
              readerInlineFocusRing,
              "reader-entry-note-toggle inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-none border-0 bg-transparent p-0 text-muted/62 transition-[color,opacity] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)] hover:bg-transparent hover:text-ink active:bg-transparent focus-visible:text-ink",
              "[&_svg]:stroke-[1.85] hover:[&_svg]:stroke-[2.3] focus-visible:[&_svg]:stroke-[2.3]",
            )}
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
        className={cn(
          "grid transition-[grid-template-rows,opacity,margin-top]",
          readerTransitionStandard,
          expanded ? "mt-4 grid-rows-[1fr] opacity-100" : "pointer-events-none mt-0 grid-rows-[0fr] opacity-0",
        )}
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
                        tabIndex={0}
                        aria-label={`定位句子结构：${chunk.label}`}
                        style={{ "--analysis-accent": `var(--reader-analysis-tone-${(index % 6) + 1})` } as CSSProperties}
                        onMouseEnter={(event) => {
                          setSentenceAnalysisPreview(event.currentTarget, element.entryId, index + 1, true);
                        }}
                        onMouseLeave={(event) => {
                          setSentenceAnalysisPreview(event.currentTarget, element.entryId, index + 1, false);
                        }}
                        onFocus={(event) => {
                          setSentenceAnalysisPreview(event.currentTarget, element.entryId, index + 1, true);
                        }}
                        onBlur={(event) => {
                          setSentenceAnalysisPreview(event.currentTarget, element.entryId, index + 1, false);
                        }}
                        onPointerDown={(event) => {
                          if (event.pointerType !== "mouse") {
                            setSentenceAnalysisPreview(event.currentTarget, element.entryId, index + 1, true);
                          }
                        }}
                        onPointerUp={(event) => {
                          if (event.pointerType !== "mouse") {
                            setSentenceAnalysisPreview(event.currentTarget, element.entryId, index + 1, false);
                          }
                        }}
                        onPointerCancel={(event) => {
                          setSentenceAnalysisPreview(event.currentTarget, element.entryId, index + 1, false);
                        }}
                      >
                        <div className="reader-entry-analysis-header">
                          <div className={`reader-analysis-row-index reader-analysis-row-index--${(index % 6) + 1}`}>
                            {index + 1}
                          </div>
                          <div className="reader-entry-analysis-label">
                            {chunk.label}
                          </div>
                        </div>
                        <div className="reader-entry-analysis-text">
                          <EnhancedText text={chunk.text} />
                        </div>
                      </div>
                    ))}
                  </div>
                ) : !parsed.summary ? (
                  <p className="reader-entry-note-prose whitespace-pre-line text-[0.95rem] leading-[1.75] text-ink">
                    <EnhancedText text={element.content} />
                  </p>
                ) : null}
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
