"use client";

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
      <div>
        <p className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">{title}</p>
        <h3 className="mt-2 reader-serif text-[1.25rem] leading-tight text-ink">{intent.anchorText}</h3>
      </div>
      <div className="space-y-2">
        <p className="text-sm leading-6 text-ink-soft">{summary}</p>
        {intent.glossary?.reason ? (
          <p className="text-xs leading-5 text-muted">{intent.glossary.reason}</p>
        ) : null}
        {intent.contextSentence ? (
          <p className="rounded-[12px] bg-reader-paper/74 px-3 py-2 text-xs leading-5 text-muted">
            {intent.contextSentence}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        {onLookupPhrase ? (
          <button
            type="button"
            className="focus-ring reader-dictionary-secondary-button rounded-[10px] border px-3 py-1.5 text-[0.72rem] font-semibold"
            onClick={onLookupPhrase}
          >
            查短语
          </button>
        ) : null}
        {onAttachToAsk ? (
          <button
            type="button"
            className="focus-ring reader-dictionary-secondary-button rounded-[10px] border px-3 py-1.5 text-[0.72rem] font-semibold"
            onClick={onAttachToAsk}
          >
            带入 Ask
          </button>
        ) : null}
        {compact && onOpenDetail ? (
          <button
            type="button"
            className="focus-ring reader-dictionary-primary-button rounded-pill px-3.5 py-1.5 text-[0.72rem] font-semibold text-surface"
            onClick={onOpenDetail}
          >
            打开详情
          </button>
        ) : null}
      </div>
    </div>
  );
}
