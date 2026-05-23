"use client";

import * as React from "react";
import * as Popover from "@radix-ui/react-popover";
import type { DailyReaderHighlight } from "@/types/view/DailyReaderVm";

const highlightClass: Record<DailyReaderHighlight["type"], string> = {
  vocab_highlight: "bg-amber-marker/25 text-ink ring-amber-marker/35",
  phrase_gloss: "bg-lavender-note/30 text-ink ring-lavender-note/40",
  context_gloss: "bg-context-blue/18 text-ink ring-context-blue/30",
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
          className={`cursor-pointer rounded-sm px-1 py-0.5 ring-1 transition-colors hover:ring-2 ${highlightClass[highlight.type]}`}
          aria-expanded={open}
          onClick={(e) => {
            e.preventDefault();
            setOpen((prev) => !prev);
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
