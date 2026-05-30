"use client";

import * as React from "react";
import * as Popover from "@radix-ui/react-popover";
import type { DailyReaderHighlight } from "@/types/view/DailyReaderVm";

const highlightClass: Record<DailyReaderHighlight["type"], string> = {
  vocab_highlight:
    "font-semibold decoration-amber-marker/75 hover:bg-amber-marker/12 focus-visible:bg-amber-marker/14",
  phrase_gloss:
    "font-medium italic decoration-lavender-note/80 hover:bg-lavender-note/14 focus-visible:bg-lavender-note/16",
  context_gloss:
    "font-semibold decoration-context-blue/70 hover:bg-context-blue/10 focus-visible:bg-context-blue/12",
};

interface InteractiveHighlightProps {
  highlight: DailyReaderHighlight;
  children: React.ReactNode;
}

export function InteractiveHighlight({ highlight, children }: InteractiveHighlightProps) {
  const [open, setOpen] = React.useState(false);

  // Apply the custom floating note shadow from DESIGN.md
  const floatingNoteShadow = "0 14px 44px rgba(28, 24, 18, 0.12)";

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <span
          role="button"
          tabIndex={0}
          className={`cursor-pointer rounded-[2px] underline decoration-[0.075em] underline-offset-[0.16em] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lens-blue/45 ${highlightClass[highlight.type]}`}
          aria-expanded={open}
          onClick={(e) => {
            e.preventDefault();
            setOpen((prev) => !prev);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setOpen((prev) => !prev);
            }
          }}
        >
          {children}
        </span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          className="z-50 w-[280px] rounded-lg border border-hairline bg-surface p-4 text-ink outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 sm:w-[320px]"
          style={{ boxShadow: floatingNoteShadow }}
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <div className="flex flex-col gap-2">
            {highlight.detail?.phonetic || highlight.detail?.pos ? (
              <div className="flex items-center gap-2 text-xs text-muted">
                {highlight.detail?.phonetic && (
                  <span className="font-sans font-medium">{highlight.detail.phonetic}</span>
                )}
                {highlight.detail?.pos && (
                  <span className="rounded-sm bg-surface-warm px-1.5 py-0.5 font-sans font-semibold">
                    {highlight.detail.pos}
                  </span>
                )}
              </div>
            ) : null}
            <p className="font-sans text-[0.95rem] font-medium leading-relaxed text-ink">
              {highlight.gloss}
            </p>
            {highlight.detail?.contextExplanation && (
              <div className="mt-1 border-t border-hairline pt-2">
                <p className="font-sans text-sm leading-relaxed text-ink-soft">
                  {highlight.detail.contextExplanation}
                </p>
              </div>
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
