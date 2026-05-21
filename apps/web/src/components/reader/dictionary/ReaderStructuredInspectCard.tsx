"use client";

import { Sparkles } from "lucide-react";
import type { ReaderStructuredInspectIntent } from "@/lib/reader-plate";
import { contextualGlossaryText, structuredInspectLabel } from "./shared";

interface ReaderStructuredInspectCardProps {
  intent: ReaderStructuredInspectIntent;
  variant?: "peek" | "rail";
  onLookupPhrase?: () => void;
  onOpenDetail?: () => void;
  onAttachToAsk?: () => void;
}

export function ReaderStructuredInspectCard({
  onAttachToAsk,
  intent,
  onLookupPhrase,
  onOpenDetail,
  variant = "peek",
}: ReaderStructuredInspectCardProps) {
  const title = structuredInspectLabel(intent.annotationType, intent.glossary?.phraseType);
  const summary =
    contextualGlossaryText(intent.glossary) ||
    intent.lookupText ||
    "该标注更适合先查看结构化解释，再决定是否继续查词。";
  const compact = variant === "peek";

  return (
    <div
      className={
        compact
          ? "space-y-3"
          : "rounded-[16px] border border-hairline/85 bg-surface/80 px-4 py-4 shadow-[0_1px_2px_rgba(17,17,17,0.04)]"
      }
    >
      {!compact ? (
        <div>
          <p className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">{title}</p>
          <h3 className="mt-2 reader-serif text-[1.25rem] leading-tight text-ink">{intent.anchorText}</h3>
        </div>
      ) : null}
      <div className="space-y-2">
        <p className="text-sm leading-6 text-ink-soft">{summary}</p>
        {intent.glossary?.reason ? (
          <p className="text-xs leading-5 text-muted">{intent.glossary.reason}</p>
        ) : null}
      </div>
      {variant === "rail" && (onLookupPhrase || onAttachToAsk) ? (
        <div className="mt-3 flex items-center gap-2 border-t border-hairline/60 pt-3">
          {onLookupPhrase ? (
            <button
              type="button"
              className="focus-ring inline-flex h-8 items-center justify-center rounded-md px-2.5 text-[0.72rem] font-semibold text-muted transition-colors hover:bg-surface hover:text-ink"
              onClick={onLookupPhrase}
            >
              查短语
            </button>
          ) : null}
          {onAttachToAsk ? (
            <button
              type="button"
              className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface hover:text-ink"
              onClick={onAttachToAsk}
              title="带入 Ask"
              aria-label="带入 Ask"
            >
              <Sparkles className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
