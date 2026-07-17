import type {
  ReaderAskAgenticEvidenceItemDto,
  ReaderAskAgenticEvidenceKindDto,
  ReaderAskAgenticRagCitationDto,
} from "@/types/api/reader-ask";

/**
 * UI-safe navigation payload derived from a complete agentic rag_citation.
 *
 * Intentionally excludes internal/debug fields such as rag_substrate_id,
 * index_run_id, plan/content hashes, score, and source_scope. Callers must
 * not invent missing navigation and must not format UTF-16 offsets as text.
 */
export interface AgenticEvidenceRagNavigation {
  stableDocumentId: string;
  baseId: string;
  recordGeneration: number;
  unitIds: string[];
  anchorSegmentIds: string[];
  canonicalTextStartUtf16: number;
  canonicalTextEndUtf16: number;
}

export interface AgenticEvidenceDisplayItem {
  handleId: string;
  kind: ReaderAskAgenticEvidenceKindDto;
  /** Stable Chinese label for the evidence kind. */
  title: string;
  /** Safe original snippet; empty string when absent. */
  snippet: string;
  sourceTool: string;
  /**
   * Present only for search_hit when rag_citation carries full identity +
   * UTF-16 range fields. Never guessed from partial data.
   */
  ragNavigation: AgenticEvidenceRagNavigation | null;
}

const EVIDENCE_KIND_TITLES: Record<ReaderAskAgenticEvidenceKindDto, string> = {
  initial_anchor: "初始选区",
  read_range: "阅读范围",
  search_hit: "文章检索",
  observation: "观察结果",
  article_seed: "文章原文",
};

function isCompleteRagCitationForNavigation(
  citation: ReaderAskAgenticRagCitationDto,
): boolean {
  return (
    typeof citation.stable_document_id === "string" &&
    citation.stable_document_id.length > 0 &&
    typeof citation.base_id === "string" &&
    citation.base_id.length > 0 &&
    typeof citation.record_generation === "number" &&
    Number.isFinite(citation.record_generation) &&
    citation.record_generation >= 1 &&
    Array.isArray(citation.unit_ids) &&
    Array.isArray(citation.anchor_segment_ids) &&
    typeof citation.canonical_text_start_utf16 === "number" &&
    Number.isFinite(citation.canonical_text_start_utf16) &&
    typeof citation.canonical_text_end_utf16 === "number" &&
    Number.isInteger(citation.canonical_text_start_utf16) &&
    citation.canonical_text_start_utf16 >= 0 &&
    Number.isFinite(citation.canonical_text_end_utf16) &&
    Number.isInteger(citation.canonical_text_end_utf16) &&
    citation.canonical_text_end_utf16 >= 0 &&
    citation.canonical_text_end_utf16 >= citation.canonical_text_start_utf16
  );
}

function projectRagNavigation(
  citation: ReaderAskAgenticRagCitationDto | null | undefined,
): AgenticEvidenceRagNavigation | null {
  if (!citation || !isCompleteRagCitationForNavigation(citation)) {
    return null;
  }
  // Copy arrays so callers cannot mutate the input DTO via the projection.
  return {
    stableDocumentId: citation.stable_document_id,
    baseId: citation.base_id,
    recordGeneration: citation.record_generation,
    unitIds: [...citation.unit_ids],
    anchorSegmentIds: [...citation.anchor_segment_ids],
    canonicalTextStartUtf16: citation.canonical_text_start_utf16,
    canonicalTextEndUtf16: citation.canonical_text_end_utf16,
  };
}

function projectOne(
  item: ReaderAskAgenticEvidenceItemDto,
): AgenticEvidenceDisplayItem {
  const kind = item.kind;
  const snippet =
    typeof item.snippet === "string" && item.snippet.length > 0
      ? item.snippet
      : "";

  // Navigation is only meaningful for search_hit with a complete citation.
  // Other kinds may carry unit/anchor ids but are not projected as ragNavigation.
  const ragNavigation =
    kind === "search_hit" ? projectRagNavigation(item.rag_citation) : null;

  return {
    handleId: item.handle_id,
    kind,
    title: EVIDENCE_KIND_TITLES[kind],
    snippet,
    sourceTool: item.source_tool,
    ragNavigation,
  };
}

/**
 * Project agentic completed evidence into UI-safe display items.
 *
 * Pure, order-preserving, non-mutating. Does **not** map into legacy
 * {@link ReaderAskEvidenceItemDto} or article_rag sidecar shapes.
 */
export function projectAgenticEvidenceForDisplay(
  evidence: readonly ReaderAskAgenticEvidenceItemDto[],
): AgenticEvidenceDisplayItem[] {
  return evidence.map(projectOne);
}
