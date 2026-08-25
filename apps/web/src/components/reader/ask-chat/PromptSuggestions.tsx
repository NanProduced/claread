"use client";

import {
  ConversationEmptyState,
} from "@/components/ai-elements/conversation";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { ClareadAiMark } from "@/components/brand/ClareadAiMark";
import type { ReaderAskEntryActionDto } from "@/types/api/reader-ask";
import type { WebSearchModeDto } from "@/types/api/reader-ask";

type PromptSuggestionItem = {
  prompt: string;
  entryAction: ReaderAskEntryActionDto;
  /**
   * When present, the host should enable web search for this single
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
};

export function PromptSuggestions({
  title,
  description,
  suggestions,
  onPickPrompt,
}: PromptSuggestionsProps) {
  return (
    <ConversationEmptyState className="h-full overflow-y-auto items-stretch justify-end px-4 pb-5 pt-8 sm:px-5">
      <div className="mx-auto flex w-full max-w-[31rem] flex-col items-start text-left">
        <div className="flex items-start gap-2.5">
          <ClareadAiMark
            size="sm"
            showBadge={false}
            className="mt-0.5 !size-5 shrink-0 border-0 bg-transparent shadow-none"
            markClassName="!size-4"
          />
          <div className="min-w-0 space-y-1">
            <h3 className="text-base font-medium leading-6 tracking-[-0.01em] text-ink">
              {title}
            </h3>
            <p className="max-w-[30rem] text-sm leading-5 text-muted-foreground">
              {description}
            </p>
          </div>
        </div>

        <Suggestions className="mt-4 w-full flex-col gap-0.5">
          {suggestions.map((suggestion) => (
            <Suggestion
              key={suggestion.prompt}
              suggestion={suggestion.prompt}
              className="h-auto w-full justify-start whitespace-normal rounded-md border-0 bg-transparent px-1.5 py-2 text-left text-sm font-normal leading-5 text-muted-foreground shadow-none transition-colors hover:bg-muted/40 hover:text-ink focus-visible:bg-muted/40 focus-visible:text-ink"
              onClick={() =>
                onPickPrompt(
                  suggestion.prompt,
                  suggestion.entryAction,
                  suggestion.webSearchOverride,
                )
              }
            >
              {suggestion.prompt}
            </Suggestion>
          ))}
        </Suggestions>
      </div>
    </ConversationEmptyState>
  );
}
