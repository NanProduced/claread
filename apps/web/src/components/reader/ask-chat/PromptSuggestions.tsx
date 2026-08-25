"use client";

import Image from "next/image";
import type { LucideIcon } from "lucide-react";
import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import type { ReaderAskEntryActionDto } from "@/types/api/reader-ask";
import type { WebSearchModeDto } from "@/types/api/reader-ask";

type PromptSuggestionItem = {
  prompt: string;
  icon: LucideIcon;
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
    <ConversationEmptyState className="h-full items-stretch justify-end overflow-y-auto px-4 pb-8 pt-8 sm:px-5">
      <div className="mx-auto flex w-full max-w-[31rem] flex-col items-start text-left">
        <Image
          src="/brand/ask-claread/empty-state-illustration-v2.png"
          width={104}
          height={104}
          alt="Ask Claread 阅读助手"
          className="mb-3 size-32 object-contain"
          priority
        />
        <div className="space-y-1">
          <h3 className="text-base font-semibold leading-6 tracking-[-0.01em] text-ink">
            {title}
          </h3>
          <p className="max-w-[30rem] text-sm leading-5 text-muted-foreground">
            {description}
          </p>
        </div>

        <Suggestions className="mt-4 w-full flex-col gap-0.5">
          {suggestions.map((suggestion) => {
            const Icon = suggestion.icon;
            return (
              <Suggestion
                key={suggestion.prompt}
                suggestion={suggestion.prompt}
                className="group h-auto w-full cursor-pointer justify-start gap-2.5 whitespace-normal rounded-md border-0 bg-transparent px-2 py-2 text-left text-sm font-normal leading-5 text-ink shadow-none transition-colors hover:bg-muted/55 focus-visible:bg-muted/55 focus-visible:text-ink"
                onClick={() =>
                  onPickPrompt(
                    suggestion.prompt,
                    suggestion.entryAction,
                    suggestion.webSearchOverride,
                  )
                }
              >
                <Icon
                  aria-hidden="true"
                  className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-ink"
                />
                <span>{suggestion.prompt}</span>
              </Suggestion>
            );
          })}
        </Suggestions>
      </div>
    </ConversationEmptyState>
  );
}
