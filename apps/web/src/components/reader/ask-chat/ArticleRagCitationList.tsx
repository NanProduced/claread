"use client";

import React from "react";
import { FileText } from "lucide-react";
import type { ReaderAskArticleRagCitationDto, ReaderAskArticleRagSidecarSafeDto } from "@/types/api/reader-ask";
import { cn } from "@/lib/cn";

type ArticleRagCitationListProps = {
  sidecar: ReaderAskArticleRagSidecarSafeDto;
  className?: string;
};

/**
 * Renders the article RAG citation list for an Ask completed message.
 *
 * Hard fail-soft contract:
 *  - Render only when `sidecar.status === "available"`, `sidecar.should_attach`
 *    is strictly `true`, and `citations.length > 0`.
 *  - Citations are I4A 9-key truth pointers into stable document facts.
 *    Display only stable identifiers (`block_ids` / `unit_ids` /
 *    `anchor_segment_ids` short tags). NEVER render `query`, `hash`,
 *    `failure_code`, `provider`, `chunk text`, `source_pack_hash`,
 *    `query_sha256`, `retryable`, or `fallback_allowed` — those are
 *    debug-only and have already been stripped by
 *    `mapAskArticleRagSidecar`, but this component still does not look
 *    them up.
 *  - No anchor scrolling / cross-surface jumps in this component — it is
 *    a read-only citation affordance.
 */
export function ArticleRagCitationList({ sidecar, className }: ArticleRagCitationListProps) {
  if (sidecar.status !== "available") return null;
  if (sidecar.should_attach !== true) return null;
  const citations = sidecar.citations;
  if (!Array.isArray(citations) || citations.length === 0) return null;

  return (
    <div
      className={cn(
        "rounded-lg border border-hairline/80 bg-reader-paper/40 px-3 py-2.5",
        "text-[12px] leading-5 text-ink-soft",
        className,
      )}
      data-testid="article-rag-citation-list"
    >
      <div className="mb-1.5 flex items-center gap-1.5 font-medium text-ink">
        <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>文章引用</span>
      </div>
      <ol className="space-y-1">
        {citations.map((citation, index) => (
          <ArticleRagCitationItem
            key={citationKey(citation, index)}
            citation={citation}
            index={index}
          />
        ))}
      </ol>
    </div>
  );
}

function ArticleRagCitationItem({
  citation,
  index,
}: {
  citation: ReaderAskArticleRagCitationDto;
  index: number;
}) {
  const truth = citation.citation;
  const stableIds = buildStableIdentifierShortTags(truth);
  return (
    <li
      className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5"
      data-testid="article-rag-citation-item"
    >
      <span className="font-medium text-ink">引用 {index + 1}</span>
      {stableIds.length > 0 ? (
        <span className="text-muted-foreground">{stableIds.join(" · ")}</span>
      ) : null}
    </li>
  );
}

/**
 * Build a short, user-readable stable identifier tag list from a 9-key truth
 * pointer. Picks at most one identifier from `block_ids`, `unit_ids`, and
 * `anchor_segment_ids` to keep the citation line short. Returns an empty
 * array when no stable identifier is available — the caller will render
 * just the "引用 N" label in that case.
 */
function buildStableIdentifierShortTags(
  truth: ReaderAskArticleRagCitationDto["citation"],
): string[] {
  const tags: string[] = [];
  if (Array.isArray(truth.block_ids) && truth.block_ids.length > 0) {
    tags.push(`block:${truth.block_ids[0]}`);
  }
  if (Array.isArray(truth.unit_ids) && truth.unit_ids.length > 0) {
    tags.push(`unit:${truth.unit_ids[0]}`);
  }
  if (Array.isArray(truth.anchor_segment_ids) && truth.anchor_segment_ids.length > 0) {
    tags.push(`seg:${truth.anchor_segment_ids[0]}`);
  }
  return tags;
}

function citationKey(citation: ReaderAskArticleRagCitationDto, index: number): string {
  const contextId =
    typeof citation.context_id === "string" && citation.context_id.length > 0
      ? citation.context_id
      : null;
  const chunkId =
    typeof citation.chunk_id === "string" && citation.chunk_id.length > 0
      ? citation.chunk_id
      : null;
  return contextId ?? chunkId ?? `article-rag-citation-${index}`;
}
