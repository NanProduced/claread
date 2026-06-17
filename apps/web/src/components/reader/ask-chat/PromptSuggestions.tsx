"use client";

import type { LucideIcon } from "lucide-react";
import { Sparkles } from "lucide-react";
import {
  ConversationEmptyState,
} from "@/components/ai-elements/conversation";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import type { ReaderAskEntryActionDto } from "@/types/api/reader-ask";
import { cn } from "@/lib/cn";

type PromptSuggestionItem = {
  prompt: string;
  entryAction: ReaderAskEntryActionDto;
  icon: LucideIcon;
  iconClassName: string;
  badgeClassName: string;
};

type PromptSuggestionsProps = {
  title: string;
  description: string;
  suggestions: PromptSuggestionItem[];
  onPickPrompt: (prompt: string, entryAction: ReaderAskEntryActionDto) => void;
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
    <ConversationEmptyState className="h-full overflow-y-auto items-stretch justify-center gap-6 px-4 py-6">
      <div className="mx-auto flex w-full max-w-[30rem] flex-col items-center text-center">
        <div className="space-y-4">
          <img
            src="/brand/ask-claread/empty-state-illustration.png"
            alt=""
            aria-hidden="true"
            className="mx-auto w-full max-w-[10rem] object-contain sm:max-w-[12rem]"
          />

          <div className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" />
            <span>Ask Claread</span>
          </div>

          <div className="space-y-2">
            <h3 className="text-2xl font-semibold tracking-tight text-foreground">
              {title}
            </h3>
            <p className="mx-auto max-w-[28rem] text-sm leading-6 text-muted-foreground">
              {description}
            </p>
          </div>

          {contextPreview ? (
            <span className="inline-flex max-w-full items-center rounded-full border px-3 py-1.5 text-xs text-muted-foreground">
              <span className="truncate">
                {contextLabel ? `${contextLabel} · ` : ""}
                {contextPreview}
              </span>
            </span>
          ) : null}
        </div>

        <Suggestions className="w-full flex-col gap-2">
          {suggestions.map((suggestion) => (
            <Suggestion
              key={suggestion.prompt}
              suggestion={suggestion.prompt}
              className="h-auto w-full justify-start whitespace-normal rounded-md px-3 py-2 text-left text-sm"
              onClick={() => onPickPrompt(suggestion.prompt, suggestion.entryAction)}
            >
              <span className="inline-flex items-start gap-2">
                <span
                  className={cn(
                    "mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
                    suggestion.badgeClassName,
                  )}
                >
                  <suggestion.icon className={cn("h-3.5 w-3.5", suggestion.iconClassName)} />
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
