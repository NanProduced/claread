"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";
import type { DailyReaderParagraph } from "@/types/view/DailyReaderVm";

interface ReadingNoteProps {
  note: DailyReaderParagraph["readingNote"];
}

export function ReadingNoteExpander({ note }: ReadingNoteProps) {
  const [isOpen, setIsOpen] = React.useState(false);

  if (!note) return null;

  return (
    <div className="mb-6">
      <button
        type="button"
        onClick={() => setIsOpen((o) => !o)}
        className="group flex w-full flex-col gap-1 text-left focus:outline-none"
        aria-expanded={isOpen}
      >
        <div className="flex w-full items-center gap-2 border-b border-hairline pb-2 transition-colors group-hover:border-ink/20">
          <span className="font-reading text-[1.1rem] font-medium italic text-ink-soft transition-colors group-hover:text-ink">
            {note.focusQuestion}
          </span>
          <div className="ml-auto flex shrink-0 items-center gap-1">
            <span className="font-sans text-[0.65rem] font-bold tracking-[0.1em] text-muted-foreground transition-colors group-hover:text-ink">
              {isOpen ? "Close" : "Expand"}
            </span>
            <ChevronDown
              className={cn(
                "h-3 w-3 text-muted-foreground transition-all duration-300 group-hover:text-ink",
                isOpen && "rotate-180"
              )}
            />
          </div>
        </div>
      </button>

      <div
        className={cn(
          "grid transition-all duration-300 ease-[cubic-bezier(0.19,1,0.22,1)]",
          isOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        )}
      >
        <div className="overflow-hidden">
          <div className="mt-4 rounded-md bg-surface-warm/60 px-5 py-4">
            <p className="font-sans text-[0.95rem] leading-[1.8] text-ink-soft">
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
}

export function TranslationExpander({ translation }: TranslationProps) {
  const [isOpen, setIsOpen] = React.useState(false);

  if (!translation) return null;

  return (
    <div className="mt-8 mb-4">
      <button
        type="button"
        onClick={() => setIsOpen((o) => !o)}
        className="group flex items-center gap-4 focus:outline-none"
        aria-expanded={isOpen}
      >
        <div className="h-px w-10 bg-hairline transition-all duration-300 group-hover:w-16 group-hover:bg-muted" />
        <span className="font-sans text-[0.65rem] font-bold tracking-[0.2em] text-muted-foreground transition-colors group-hover:text-ink">
          {isOpen ? "Hide Translation" : "Show Translation"}
        </span>
      </button>

      <div
        className={cn(
          "grid transition-all duration-300 ease-[cubic-bezier(0.19,1,0.22,1)]",
          isOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        )}
      >
        <div className="overflow-hidden">
          <div className="mt-5 font-sans text-[0.95rem] leading-[1.85] text-muted-foreground">
            <p>{translation}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
