"use client";

import type { LucideIcon } from "lucide-react";
import {
  ConversationEmptyState,
} from "@/components/ai-elements/conversation";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { ClareadAiMark } from "@/components/brand/ClareadAiMark";
import type { ReaderAskEntryActionDto } from "@/types/api/reader-ask";
import type { WebSearchModeDto } from "@/types/api/reader-ask";
import { cn } from "@/lib/cn";

type PromptSuggestionItem = {
  prompt: string;
  entryAction: ReaderAskEntryActionDto;
  icon: LucideIcon;
  iconClassName: string;
  badgeClassName: string;
  /**
   * R2.1 — when present, the host should enable web search for this single
   * send (used by the "查询相关资料" suggestion). Absent on article-only
   * suggestions.
   */
  webSearchOverride?: WebSearchModeDto;
};

type PromptSuggestionsProps = {
  title: string;
  description: string;
  suggestions: PromptSuggestionItem[];
  onPickPrompt: (
    prompt: string,
    entryAction: ReaderAskEntryActionDto,
    webSearchOverride?: WebSearchModeDto,
  ) => void;
  contextLabel?: string | null;
  contextPreview?: string | null;
};

export function PromptSuggestions({
  title,
  description,
  suggestions,
  onPickPrompt,
  contextLabel,
  contextPreview,
}: PromptSuggestionsProps) {
  return (
    <ConversationEmptyState className="h-full overflow-y-auto items-stretch justify-end px-4 pb-5 pt-8 sm:px-5">
      <div className="mx-auto flex w-full max-w-[31rem] flex-col items-start text-left">
        <div className="flex items-start gap-3">
          <ClareadAiMark
            size="sm"
            className="mt-0.5 shrink-0 shadow-none"
            badgeClassName="shadow-none"
          />
          <div className="min-w-0 space-y-1.5">
            <h3 className="text-[1.125rem] font-semibold leading-6 tracking-[-0.015em] text-ink">
              {title}
            </h3>
            <p className="max-w-[30rem] text-[13px] leading-5 text-muted-foreground">
              {description}
            </p>
          </div>
        </div>

        {contextPreview ? (
          <span className="mt-4 inline-flex max-w-full items-center rounded-md border border-border/65 bg-muted/25 px-2.5 py-1.5 text-xs leading-4 text-muted-foreground">
            <span className="truncate">
              {contextLabel ? `${contextLabel} · ` : ""}
              {contextPreview}
            </span>
          </span>
        ) : null}

        <Suggestions className="mt-5 w-full flex-col gap-1">
          {suggestions.map((suggestion) => (
            <Suggestion
              key={suggestion.prompt}
              suggestion={suggestion.prompt}
              className="h-auto w-full justify-start whitespace-normal rounded-md border border-transparent bg-transparent px-2 py-2.5 text-left text-[13px] leading-5 text-ink-soft transition-colors hover:bg-muted/50 hover:text-ink focus-visible:bg-muted/50 focus-visible:text-ink"
              onClick={() =>
                onPickPrompt(
                  suggestion.prompt,
                  suggestion.entryAction,
                  suggestion.webSearchOverride,
                )
              }
            >
              <span className="inline-flex items-start gap-2">
                <span
                  className={cn(
                    "mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
                    suggestion.badgeClassName,
                  )}
                >
                  <suggestion.icon className={cn("h-3.5 w-3.5", suggestion.iconClassName)} aria-hidden="true" />
                </span>
                <span>{suggestion.prompt}</span>
              </span>
            </Suggestion>
          ))}
        </Suggestions>
      </div>
    </ConversationEmptyState>
  );
}
