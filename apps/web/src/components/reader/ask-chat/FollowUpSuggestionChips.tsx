"use client";

import { Sparkles } from "lucide-react";
import type { ReaderAskFollowUpSuggestionDto } from "@/types/api/reader-ask";
import { cn } from "@/lib/cn";

type FollowUpSuggestionChipsProps = {
  suggestions: ReaderAskFollowUpSuggestionDto[];
  onPickSuggestion: (prompt: string) => void;
  className?: string;
};

export function FollowUpSuggestionChips({
  suggestions,
  onPickSuggestion,
  className,
}: FollowUpSuggestionChipsProps) {
  if (suggestions.length === 0) return null;

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {suggestions.map((suggestion) => (
        <button
          key={suggestion.prompt}
          type="button"
          onClick={() => onPickSuggestion(suggestion.prompt)}
          className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
        >
          <Sparkles className="h-3 w-3" />
          <span>{suggestion.label}</span>
        </button>
      ))}
    </div>
  );
}
