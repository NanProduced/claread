"use client";

import {
  Source,
  SourceContent,
  SourceTrigger,
  WebSources,
  type WebSourceItem,
} from "@/components/prompt-kit/source";
import { cn } from "@/lib/cn";
import type { ReaderAskWebSearchSummaryDto } from "@/types/api/reader-ask";
import type { AgenticCitationDisplayItem } from "./agentic-evidence";

/**
 * Fixed Chinese outcome messages for non-completed web search outcomes.
 * Never interpolates raw data, provider names, queries, or error details.
 * `completed` maps to empty string because the sources list itself is the
 * positive signal — no extra notice is needed.
 */
const WEB_SEARCH_OUTCOME_MESSAGES: Record<
  ReaderAskWebSearchSummaryDto["outcome"],
  string
> = {
  completed: "",
  no_results: "未找到可用网页来源",
  unavailable: "网页搜索暂不可用",
  failed: "网页搜索未完成",
};

/**
 * Project agentic web citations into prompt-kit WebSourceItem entries.
 *
 * - Filters out non-web citations and web citations with missing/empty url.
 * - Deduplicates by canonical URL (first occurrence wins, order preserved).
 * - Title falls back to domain when `sourceTitle` is missing.
 *
 * Mirrors backend `PublicCitation` shape: url + title + optional description.
 * Never reads internal handles, rag_citation, or web_snapshot fields.
 */
function projectWebSources(
  citations: readonly AgenticCitationDisplayItem[],
): WebSourceItem[] {
  const seen = new Set<string>();
  const items: WebSourceItem[] = [];
  for (const citation of citations) {
    if (citation.sourceKind !== "web") {
      continue;
    }
    if (typeof citation.url !== "string" || citation.url.length === 0) {
      continue;
    }
    // Deduplicate by canonical URL — first occurrence wins, order preserved.
    if (seen.has(citation.url)) {
      continue;
    }
    seen.add(citation.url);
    items.push({
      citationId: citation.citationId,
      href: citation.url,
      title: citation.sourceTitle ?? citation.url,
      description: citation.description ?? undefined,
    });
  }
  return items;
}

/**
 * Answer-level web sources rendering for agentic Ask turns.
 *
 * Renders two things, in order:
 * 1. A fixed Chinese outcome notice when the web search outcome is not
 *    `completed` (no_results / unavailable / failed). This makes failed or
 *    empty searches visible instead of silently looking like "no search".
 * 2. A compact row of web source pills ("网页来源" + domain triggers) when
 *    web citations exist. Each pill opens the source URL and shows a hover
 *    card with the page title, description, and citation snippet.
 *
 * Returns `null` when there are no web citations and no non-completed
 * outcome to surface — matching the product rule "no sources ⇒ no entry".
 *
 * Design intent (TMP-ask-web-search-product-ux-2026-07-26 §5.3):
 * - Article citations stay in InlineCitation (not duplicated here).
 * - Web pills use prompt-kit Source primitives to avoid duplicating Radix
 *   HoverCard wiring and to keep visual consistency with the rest of the
 *   prompt-kit disclosure family.
 * - The list is order-preserving, non-mutating, and deduplicates by
 *   canonical URL (first occurrence wins).
 * - 360px sidebar: pills stay compact (max-w-40) so they never form a
 *   card wall.
 */
export function AgenticWebSources({
  citations,
  webSearchSummary,
  className,
}: {
  citations: readonly AgenticCitationDisplayItem[];
  webSearchSummary: ReaderAskWebSearchSummaryDto | null | undefined;
  className?: string;
}) {
  const webSources = projectWebSources(citations);

  // Only show the outcome notice for non-completed outcomes. `completed`
  // with 0 cited sources is a valid state (search finished, nothing cited)
  // and should not produce a misleading "no results" notice.
  const outcomeNotice =
    webSearchSummary != null && webSearchSummary.outcome !== "completed"
      ? WEB_SEARCH_OUTCOME_MESSAGES[webSearchSummary.outcome]
      : "";

  if (webSources.length === 0 && !outcomeNotice) {
    return null;
  }

  return (
    <div
      className={cn("mt-2 space-y-1.5", className)}
      data-testid="agentic-web-sources"
    >
      {outcomeNotice ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="web-search-outcome-notice"
        >
          {outcomeNotice}
        </p>
      ) : null}
      {webSources.length > 0 ? (
        <div data-testid="web-source-list">
          <WebSources sources={webSources} />
        </div>
      ) : null}
    </div>
  );
}

// Re-export prompt-kit primitives so call sites that want block-level
// Source composition (e.g. inside AgenticAnswerBlocks) can import them
// from a single agentic entry point without reaching into prompt-kit.
export { Source, SourceContent, SourceTrigger };
export type { WebSourceItem };
