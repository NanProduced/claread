"use client";

import React from "react";
import { BookOpen, Library, Paperclip, type LucideIcon } from "lucide-react";
import type { ReaderAskCitationDto, ReaderAskCitationKindDto } from "@/types/api/reader-ask";
import { cn } from "@/lib/cn";

const kindIcon: Record<ReaderAskCitationKindDto, LucideIcon> = {
  anchor: Paperclip,
  vocabulary: Library,
  dictionary_entry: BookOpen,
  dictionary_ai: BookOpen,
};

type CitationListProps = {
  citations: ReaderAskCitationDto[];
  className?: string;
};

export function CitationList({ citations, className }: CitationListProps) {
  if (citations.length === 0) return null;

  return (
    <div className={cn("flex flex-row flex-wrap gap-1.5", className)}>
      {citations.map((citation, index) => {
        const Icon = kindIcon[citation.kind];
        return (
          <span
            key={citation.citation_id}
            className="inline-flex items-center text-xs rounded-full border px-2 py-0.5"
          >
            <Icon className="mr-1 h-3 w-3 shrink-0" aria-hidden="true" />
            <span>[{index + 1}]</span>
            <span className="ml-1">{citation.label}</span>
            {citation.source_article_title && (
              <span className="ml-1 text-muted-foreground">{citation.source_article_title}</span>
            )}
          </span>
        );
      })}
    </div>
  );
}
