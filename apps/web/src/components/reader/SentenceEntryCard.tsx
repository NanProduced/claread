import { BookOpen, Sparkles } from "lucide-react";
import type { KeyboardEvent } from "react";
import type { SentenceEntryModel } from "@/types/view/ReaderMockVm";
import { parseSentenceAnalysisContent } from "./reader-entry-utils";

interface SentenceEntryCardProps {
  entry: SentenceEntryModel;
  active?: boolean;
  onActivate?: (entry: SentenceEntryModel) => void;
}

const entryToneHeaderClass: Record<string, string> = {
  grammar_note: "text-grammar-violet bg-grammar-violet/10 ring-grammar-violet/15",
  sentence_analysis: "text-structure-green bg-structure-green/10 ring-structure-green/15",
  term_note: "text-vocab-amber bg-vocab-amber/10 ring-vocab-amber/15",
  logic_note: "text-lens-blue bg-lens-blue/10 ring-lens-blue/15",
  interpretation_note: "text-context-blue bg-context-blue/10 ring-context-blue/15",
  content_summary: "text-muted bg-muted/10",
};

const entryTypeLabel: Record<string, string> = {
  grammar_note: "语法旁注",
  sentence_analysis: "句子拆解",
  term_note: "术语说明",
  logic_note: "逻辑提示",
  interpretation_note: "理解提示",
  content_summary: "内容摘要",
};

function cardLabel(entry: SentenceEntryModel) {
  return entryTypeLabel[entry.entryType] ?? entry.label ?? "解析";
}

export function SentenceEntryCard({ entry, active = false, onActivate }: SentenceEntryCardProps) {
  const label = entry.title ?? entry.label ?? "解析";
  const iconClass = entryToneHeaderClass[entry.entryType] ?? entryToneHeaderClass.content_summary;
  const category = cardLabel(entry);
  const activate = () => onActivate?.(entry);
  const interactiveProps = {
    tabIndex: 0,
    "aria-current": active ? true : undefined,
    onClick: activate,
    onFocus: activate,
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    },
  };

  if (entry.entryType === "sentence_analysis") {
    const parsed = parseSentenceAnalysisContent(entry.content);

    return (
      <section
        className={`reader-entry-note border-structure-green/20 ${active ? "reader-entry-note--active reader-entry-note--active-analysis" : ""}`}
        {...interactiveProps}
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
          <span className="shrink-0 rounded-full border border-hairline bg-surface-warm px-2.5 py-1 text-[0.7rem] font-semibold text-muted">
            本句解析
          </span>
        </div>
        <div className="mt-4">
          {parsed.summary ? (
            <p className="mb-4 whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft">{parsed.summary}</p>
          ) : null}
          {parsed.chunks.length > 0 ? (
            <div className="overflow-hidden rounded-[10px] border border-hairline/80 bg-surface">
              {parsed.chunks.map((chunk, index) => (
                <div
                  key={`${entry.id}-chunk-${index}`}
                  data-analysis-id={entry.id}
                  data-analysis-index={index + 1}
                  data-analysis-order={chunk.order}
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
          ) : null}
        </div>
        <div className="mt-4 flex items-center justify-between border-t border-hairline/60 pt-3 text-xs text-muted">
          <span>结构拆解</span>
          <span>来源: 机器解析</span>
        </div>
      </section>
    );
  }

  return (
    <section
      className={`reader-entry-note border-grammar-violet/20 ${active ? "reader-entry-note--active reader-entry-note--active-grammar" : ""}`}
      {...interactiveProps}
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
        <span className="shrink-0 rounded-full border border-hairline bg-surface-warm px-2.5 py-1 text-[0.7rem] font-semibold text-muted">
          机器解析
        </span>
      </div>
      <div className="mt-4">
        <p className="whitespace-pre-line text-[0.9375rem] leading-[1.7] text-ink-soft">{entry.content}</p>
      </div>
      <div className="mt-4 flex items-center justify-between border-t border-hairline/60 pt-3 text-xs text-muted">
        <span>锚定本句</span>
        <span>来源: 机器解析</span>
      </div>
    </section>
  );
}
