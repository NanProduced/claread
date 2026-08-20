"use client";

import type { DailyReaderHighlight } from "@/types/view/DailyReaderVm";

const highlightClass: Record<DailyReaderHighlight["type"], string> = {
  vocab_highlight: "decoration-solid",
  phrase_gloss: "decoration-dotted",
  context_gloss: "decoration-double",
};

interface InteractiveHighlightProps {
  highlight: DailyReaderHighlight;
  isActive: boolean;
  noteId: string;
  onActivate: (highlight: DailyReaderHighlight) => void;
  children: React.ReactNode;
}

export function InteractiveHighlight({
  highlight,
  isActive,
  noteId,
  onActivate,
  children,
}: InteractiveHighlightProps) {
  return (
    <button
      type="button"
      className={`cursor-pointer underline decoration-[0.08em] underline-offset-[0.18em] transition-[text-decoration-thickness,color] hover:decoration-[0.12em] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[color:var(--dr-accent)] ${highlightClass[highlight.type]}`}
      aria-label={`查看“${highlight.text}”注释`}
      aria-controls={noteId}
      aria-expanded={isActive}
      onClick={() => onActivate(highlight)}
    >
      {children}
    </button>
  );
}
