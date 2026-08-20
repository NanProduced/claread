"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";
import type { DailyReaderParagraph } from "@/types/view/DailyReaderVm";

interface ReadingNoteProps {
  note: DailyReaderParagraph["readingNote"];
  paragraphNumber: number;
}

export function ReadingNoteExpander({ note, paragraphNumber }: ReadingNoteProps) {
  const [isOpen, setIsOpen] = React.useState(false);
  const contentId = React.useId();

  if (!note) return null;

  const actionLabel = `${isOpen ? "收起" : "展开"}第 ${paragraphNumber} 段导读`;

  return (
    <div className="mb-6 border-t border-[color:var(--dr-rule)] pt-3">
      <button
        type="button"
        onClick={() => setIsOpen((o) => !o)}
        className="group flex min-h-11 w-full items-start gap-4 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[color:var(--dr-accent)]"
        aria-expanded={isOpen}
        aria-controls={contentId}
        aria-label={actionLabel}
      >
        <span className="dr-font-mono mt-1 shrink-0 text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-meta)]">
          段落 {String(paragraphNumber).padStart(2, "0")}
        </span>
        <span className="flex min-w-0 flex-1 items-start justify-between gap-3">
          <span className="dr-font-zh text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)] transition-colors group-hover:text-[color:var(--dr-ink)]">
            {note.focusQuestion}
          </span>
          <span className="flex min-h-11 shrink-0 items-center gap-1 self-center text-[length:var(--dr-type-caption-size)] font-semibold text-[color:var(--dr-ink)]">
            <span>{isOpen ? "收起导读" : "展开导读"}</span>
            <ChevronDown
              aria-hidden="true"
              className={cn(
                "h-4 w-4 transition-transform duration-200 motion-reduce:transition-none",
                isOpen && "rotate-180",
              )}
            />
          </span>
        </span>
      </button>

      <div
        id={contentId}
        aria-hidden={!isOpen}
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none",
          isOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
        )}
      >
        <div className="overflow-hidden">
          <div className="mt-3 border-t border-[color:var(--dr-rule)] bg-[var(--dr-paper-raised)] px-4 py-3">
            <p className="dr-font-zh text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
              {note.microSummary}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

interface TranslationProps {
  translation: string | null;
  paragraphNumber: number;
}

export function TranslationExpander({ translation, paragraphNumber }: TranslationProps) {
  const [isOpen, setIsOpen] = React.useState(false);
  const contentId = React.useId();

  if (!translation) return null;

  const actionLabel = `${isOpen ? "收起" : "显示"}第 ${paragraphNumber} 段译文`;

  return (
    <div className="mt-6">
      <button
        type="button"
        onClick={() => setIsOpen((o) => !o)}
        className="group flex min-h-11 items-center gap-3 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[color:var(--dr-accent)]"
        aria-expanded={isOpen}
        aria-controls={contentId}
        aria-label={actionLabel}
      >
        <span aria-hidden="true" className="h-px w-8 bg-[var(--dr-rule)]" />
        <span className="text-[length:var(--dr-type-caption-size)] font-semibold text-[color:var(--dr-ink)]">
          {isOpen ? "收起译文" : "显示译文"}
        </span>
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "h-4 w-4 transition-transform duration-200 motion-reduce:transition-none",
            isOpen && "rotate-180",
          )}
        />
      </button>

      <div
        id={contentId}
        aria-hidden={!isOpen}
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none",
          isOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
        )}
      >
        <div className="overflow-hidden">
          <div className="dr-font-zh mt-3 border-t border-[color:var(--dr-rule)] pt-4 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
            <p>{translation}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
