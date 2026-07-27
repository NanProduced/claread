import type {
  ReaderAskAgenticCitationDto,
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
 *
 * Note: public completed DTOs no longer carry evidence; this projection is
 * retained only for restricted server-side or future adapter consumers.
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

/**
 * Public citation display item — no internal handles.
 * Consumed by InlineCitation hover cards (article citations) and
 * WebSources list (web citations).
 *
 * For `sourceKind === "web"`, `url` / `sourceTitle` / `description`
 * carry the provider-supplied web metadata (re-canonicalized by the
 * backend). `title` remains the stable Chinese label ("网络来源").
 */
export interface AgenticCitationDisplayItem {
  citationId: string;
  sourceKind: ReaderAskAgenticCitationDto["source_kind"];
  /** Stable Chinese label for the citation source type. */
  title: string;
  /** Safe original snippet; empty string when absent. */
  snippet: string;
  /** Web citation URL (only for source_kind === "web"); null otherwise. */
  url: string | null;
  /** Web page title from the source (only for source_kind === "web"); null otherwise. */
  sourceTitle: string | null;
  /** Web page description (only for source_kind === "web"); null otherwise. */
  description: string | null;
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
 * Public completed payloads no longer include evidence; prefer
 * {@link projectAgenticCitationsForDisplay}.
 */
export function projectAgenticEvidenceForDisplay(
  evidence: readonly ReaderAskAgenticEvidenceItemDto[],
): AgenticEvidenceDisplayItem[] {
  return evidence.map(projectOne);
}

/**
 * Project public citations for InlineCitation (article) and WebSources (web).
 * No handle join. Web citations carry url / sourceTitle / description;
 * article citations always have null for those fields.
 */
export function projectAgenticCitationsForDisplay(
  citations: readonly ReaderAskAgenticCitationDto[],
): AgenticCitationDisplayItem[] {
  return citations.map((citation) => {
    const isWeb = citation.source_kind === "web";
    const snippet =
      typeof citation.snippet === "string" && citation.snippet.length > 0
        ? citation.snippet
        : "";
    return {
      citationId: citation.citation_id,
      sourceKind: citation.source_kind,
      title: isWeb ? "网络来源" : "文章依据",
      snippet,
      url: isWeb && typeof citation.url === "string" ? citation.url : null,
      sourceTitle:
        isWeb && typeof citation.title === "string" ? citation.title : null,
      description:
        isWeb && typeof citation.description === "string"
          ? citation.description
          : null,
    };
  });
}
