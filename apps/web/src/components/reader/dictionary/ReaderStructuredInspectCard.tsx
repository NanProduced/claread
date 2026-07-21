"use client";

import { Sparkles } from "lucide-react";
import { readerCommandControl, readerIconAction } from "@/components/reader/interaction";
import { cn } from "@/lib/cn";
import type { ReaderStructuredInspectIntent } from "@/lib/reader-plate";
import { LearningNoteMarkdown } from "./LearningNoteMarkdown";
import {
  contextualGlossaryExample,
  contextualGlossaryExampleTranslation,
  contextualGlossaryLearningNote,
  contextualGlossaryReason,
  contextualGlossaryText,
  phraseGlossarySubtypeLabel,
  structuredInspectCategoryLabel,
  structuredInspectToneClass,
} from "./shared";

interface ReaderStructuredInspectCardProps {
  intent: ReaderStructuredInspectIntent;
  variant?: "peek" | "rail";
  onLookupPhrase?: () => void;
  onAttachToAsk?: () => void;
  onFeedback?: () => void;
}

export function ReaderStructuredInspectCard({
  onAttachToAsk,
  intent,
  onLookupPhrase,
  onFeedback,
  variant = "peek",
}: ReaderStructuredInspectCardProps) {
  const category = structuredInspectCategoryLabel(intent.annotationType);
  const subtype = phraseGlossarySubtypeLabel(intent.glossary);
  const toneClassName = structuredInspectToneClass(intent.annotationType);
  const displayAnchorText = intent.lookupText ?? intent.anchorText;
  const gloss =
    contextualGlossaryText(intent.glossary) ||
    intent.lookupText ||
    "该标注更适合先查看结构化解释，再决定是否继续查词。";
  const learningNote = contextualGlossaryLearningNote(intent.glossary);
  const example = contextualGlossaryExample(intent.glossary);
  const exampleTranslation = contextualGlossaryExampleTranslation(intent.glossary);
  const reason = contextualGlossaryReason(intent.glossary);
  const compact = variant === "peek";
  const showLookupPhrase =
    Boolean(onLookupPhrase) &&
    intent.annotationType !== "phrase_gloss" &&
    intent.annotationType !== "context_gloss";
  const isPhraseGloss = intent.annotationType === "phrase_gloss";

  return (
    <div
      className={
        compact
          ? "space-y-3"
          : "rounded-[16px] border border-hairline/85 bg-surface/80 px-4 py-4 shadow-[var(--app-panel-shadow-quiet)]"
      }
    >
      {!compact ? (
        <div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`text-[0.7rem] font-semibold tracking-[0.08em] ${toneClassName}`}>
              {category}
            </span>
            {subtype ? (
              <span className="rounded-[5px] bg-ink/[0.04] px-1.5 py-0.5 text-[0.66rem] font-semibold leading-none text-muted-foreground">
                {subtype}
              </span>
            ) : null}
          </div>
          <h3 className="mt-2 reader-serif text-[1.25rem] leading-tight text-ink">{displayAnchorText}</h3>
        </div>
      ) : null}
      <div className="max-h-[min(50vh,22rem)] space-y-2 overflow-y-auto overscroll-contain pr-0.5">
        {/* gloss is always the primary scannable content */}
        <p className="text-sm leading-6 text-ink-soft">{gloss}</p>
        {isPhraseGloss && learningNote ? (
          <LearningNoteMarkdown markdown={learningNote} />
        ) : null}
        {example ? (
          <div className="rounded-[7px] border border-hairline/60 bg-ink/[0.012] px-2.5 py-2">
            <p className="text-[0.68rem] font-semibold text-muted-foreground">例句</p>
            <p className="mt-1 text-xs leading-5 text-ink-soft">{example}</p>
            {exampleTranslation ? (
              <p className="mt-0.5 text-[0.72rem] leading-5 text-muted-foreground">{exampleTranslation}</p>
            ) : null}
          </div>
        ) : null}
        {reason ? (
          <p className="text-xs leading-5 text-muted-foreground">
            {reason}
          </p>
        ) : null}
      </div>
      {variant === "rail" && (showLookupPhrase || onAttachToAsk || onFeedback) ? (
        <div className="mt-3 flex items-center gap-2 border-t border-hairline/60 pt-3">
          {showLookupPhrase ? (
            <button
              type="button"
              className={cn(readerCommandControl, "h-8 rounded-md px-2.5 text-[0.72rem] font-semibold")}
              onClick={onLookupPhrase}
            >
              查词典释义
            </button>
          ) : null}
          {onFeedback ? (
            <button
              type="button"
              className={cn(readerCommandControl, "h-8 rounded-md px-2.5 text-[0.72rem] font-semibold")}
              onClick={onFeedback}
            >
              反馈
            </button>
          ) : null}
          {onAttachToAsk ? (
            <button
              type="button"
              className={cn(readerIconAction, "h-8 w-8 rounded-[0.7rem]")}
              onClick={onAttachToAsk}
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
