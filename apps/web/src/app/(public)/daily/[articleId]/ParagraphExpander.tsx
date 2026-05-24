"use client";

import * as React from "react";
import { ChevronDown, Languages, Lightbulb } from "lucide-react";
import { cn } from "@/lib/cn";
import type { DailyReaderParagraph } from "@/types/view/DailyReaderVm";

interface ParagraphExpanderProps {
  translation?: string | null;
  readingNote?: DailyReaderParagraph["readingNote"] | null;
}

export function ParagraphExpander({ translation, readingNote }: ParagraphExpanderProps) {
  const [isOpen, setIsOpen] = React.useState(false);

  if (!translation && !readingNote) {
    return null;
  }

  return (
    <div className="mt-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setIsOpen((o) => !o)}
          className="group flex h-8 items-center gap-1.5 rounded-full border border-hairline bg-surface-warm px-3 text-xs font-medium text-muted transition-colors hover:border-muted hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue"
          aria-expanded={isOpen}
        >
          {readingNote ? <Lightbulb className="h-3.5 w-3.5" /> : <Languages className="h-3.5 w-3.5" />}
          <span>{isOpen ? "收起解析" : "展开解析"}</span>
          <ChevronDown
            className={cn("h-3.5 w-3.5 transition-transform duration-200", isOpen && "rotate-180")}
          />
        </button>
      </div>

      <div
        className={cn(
          "grid transition-all duration-200 ease-in-out",
          isOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        )}
      >
        <div className="overflow-hidden">
          <div className="mt-4 rounded-xl border border-hairline bg-surface-warm px-5 py-4 font-sans text-[0.95rem] leading-7 shadow-[0_4px_18px_rgba(17,17,17,0.02)]">
            {readingNote && (
              <div className="mb-4 space-y-2">
                {readingNote.focusQuestion && (
                  <p className="font-semibold text-ink">{readingNote.focusQuestion}</p>
                )}
                {readingNote.microSummary && (
                  <p className="text-ink-soft">{readingNote.microSummary}</p>
                )}
              </div>
            )}
            {translation && (
              <div className={cn("text-ink-soft", readingNote && "border-t border-hairline pt-3")}>
                <p>{translation}</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
