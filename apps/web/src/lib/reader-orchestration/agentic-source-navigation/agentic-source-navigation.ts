/**
 * Reader-owned deep module: Agentic Ask source navigation.
 *
 * Ask-facing seam is only {@link NavigateAgenticSource}. Identity loading,
 * policy, candidate planning, and DOM scroll live behind
 * {@link createNavigateAgenticSource} construction dependencies.
 *
 * Product freeze (R3B0 + R3A):
 * - Missing evidenceScope → unavailable.legacy_scope_missing for ALL kinds
 *   that would navigate (including complete search_hit). No ragNavigation-only
 *   shortcut and no current-page identity fallback.
 * - envelope_fingerprint is never used for navigation.
 * - No snippet / body text search.
 */

import type { AgenticEvidenceRagNavigation } from "@/components/reader/ask/agentic-evidence";
import type { ReaderAskAgenticEvidenceScopeDto } from "@/types/api/reader-ask";

import {
  createReaderDomNavigationAdapter,
  type DomNavigationCandidate,
  type ReaderDomNavigationAdapter,
} from "./reader-dom-navigation-adapter";

// ---------------------------------------------------------------------------
// Ask-facing types (keep this surface small)
// ---------------------------------------------------------------------------

export type AgenticSourceKind =
  | "initial_anchor"
  | "read_range"
  | "search_hit"
  | "observation";

/**
 * Descriptor passed from Ask UI into the navigator.
 * No snippet, fingerprint, substrate, score, or DOM handles.
 */
export type AgenticSourceDescriptor = {
  handleId: string;
  kind: AgenticSourceKind;
  evidenceScope: ReaderAskAgenticEvidenceScopeDto | null;
  unitId?: string | null;
  anchorSegmentId?: string | null;
  ragNavigation: AgenticEvidenceRagNavigation | null;
};

export type SourceNavigationResult =
  | {
      status: "navigated";
      mode: "anchor_segment" | "unit";
      targetId: string;
    }
  | {
      status: "identity_mismatch";
      field: "reading_record" | "base" | "stable_document";
    }
  | {
      status: "stale_generation";
    }
  | {
      status: "target_not_found";
      attemptedModes: Array<"anchor_segment" | "unit">;
    }
  | {
      status: "unavailable";
      reason:
        | "no_locator"
        | "partial_citation"
        | "observation_only"
        | "canonical_range_unsupported"
        | "page_identity_incomplete"
        | "legacy_scope_missing";
    };

/**
 * Sole Ask-facing navigation callback.
 * Callers must not receive Element / Document / CurrentPageIdentity.
 */
export type NavigateAgenticSource = (
  source: AgenticSourceDescriptor,
) => Promise<SourceNavigationResult>;

// ---------------------------------------------------------------------------
// Reader-owned page identity (construction-time only)
// ---------------------------------------------------------------------------

export type PageStableDocumentStatus =
  | "loading"
  | "ready"
  | "not_ready"
  | "stale"
  | "failed";

export type CurrentPageIdentity = {
  readingRecordId: string;
  baseId: string;
  recordGeneration: number;
  stableDocument: {
    status: PageStableDocumentStatus;
    stableDocumentId: string | null;
  };
};

export type LoadCurrentPageIdentity = () =>
  | CurrentPageIdentity
  | Promise<CurrentPageIdentity>;

export type NavigateAgenticSourceDependencies = {
  loadCurrentPageIdentity: LoadCurrentPageIdentity;
  /**
   * Defaults to production plate-document adapter when omitted.
   * Tests inject an in-memory adapter; Ask never sees this type.
   */
  domAdapter?: ReaderDomNavigationAdapter;
};

// ---------------------------------------------------------------------------
// Internal planning (not part of Ask interface)
// ---------------------------------------------------------------------------

type NavigationFailure = Extract<
  SourceNavigationResult,
  { status: "unavailable" | "identity_mismatch" | "stale_generation" }
>;

type NavigationPlan = {
  candidates: DomNavigationCandidate[];
};

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/**
 * Deduplicate candidates while preserving first-seen order.
 */
function dedupeCandidates(
  items: readonly DomNavigationCandidate[],
): DomNavigationCandidate[] {
  const seen = new Set<string>();
  const out: DomNavigationCandidate[] = [];
  for (const item of items) {
    const key = `${item.mode}:${item.targetId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function hasCanonicalRangeOnly(nav: AgenticEvidenceRagNavigation): boolean {
  const noSeg = nav.anchorSegmentIds.length === 0;
  const noUnit = nav.unitIds.length === 0;
  const rangeOk =
    Number.isFinite(nav.canonicalTextStartUtf16) &&
    Number.isFinite(nav.canonicalTextEndUtf16) &&
    nav.canonicalTextEndUtf16 >= nav.canonicalTextStartUtf16;
  return noSeg && noUnit && rangeOk;
}

function buildNonRagCandidates(
  source: AgenticSourceDescriptor,
): DomNavigationCandidate[] {
  const items: DomNavigationCandidate[] = [];
  if (isNonEmptyString(source.anchorSegmentId)) {
    items.push({ mode: "anchor_segment", targetId: source.anchorSegmentId });
  }
  if (isNonEmptyString(source.unitId)) {
    items.push({ mode: "unit", targetId: source.unitId });
  }
  return dedupeCandidates(items);
}

function buildSearchHitCandidates(
  nav: AgenticEvidenceRagNavigation,
): DomNavigationCandidate[] {
  const items: DomNavigationCandidate[] = [];
  for (const id of nav.anchorSegmentIds) {
    if (isNonEmptyString(id)) {
      items.push({ mode: "anchor_segment", targetId: id });
    }
  }
  for (const id of nav.unitIds) {
    if (isNonEmptyString(id)) {
      items.push({ mode: "unit", targetId: id });
    }
  }
  return dedupeCandidates(items);
}

/**
 * Pure policy: decide whether to navigate and which candidates to try.
 * Does not touch DOM or loaders.
 */
function planSourceNavigation(
  source: AgenticSourceDescriptor,
  page: CurrentPageIdentity,
): NavigationPlan | NavigationFailure {
  // 1. observation — always display-only
  if (source.kind === "observation") {
    return { status: "unavailable", reason: "observation_only" };
  }

  // 2. missing scope — no DOM, no page fallback, no rag-only shortcut
  if (source.evidenceScope == null) {
    return { status: "unavailable", reason: "legacy_scope_missing" };
  }

  const scope = source.evidenceScope;

  // 3. locator legality by kind
  let candidates: DomNavigationCandidate[] = [];

  if (source.kind === "initial_anchor" || source.kind === "read_range") {
    candidates = buildNonRagCandidates(source);
    if (candidates.length === 0) {
      return { status: "unavailable", reason: "no_locator" };
    }
  } else if (source.kind === "search_hit") {
    if (source.ragNavigation == null) {
      return { status: "unavailable", reason: "partial_citation" };
    }
    const nav = source.ragNavigation;
    candidates = buildSearchHitCandidates(nav);
    if (candidates.length === 0) {
      if (hasCanonicalRangeOnly(nav)) {
        return { status: "unavailable", reason: "canonical_range_unsupported" };
      }
      return { status: "unavailable", reason: "no_locator" };
    }
  } else {
    return { status: "unavailable", reason: "no_locator" };
  }

  // 4. message scope vs current page (fixed order)
  if (scope.reading_record_id !== page.readingRecordId) {
    return { status: "identity_mismatch", field: "reading_record" };
  }
  if (scope.base_id !== page.baseId) {
    return { status: "identity_mismatch", field: "base" };
  }
  if (scope.record_generation !== page.recordGeneration) {
    return { status: "stale_generation" };
  }

  // 5. search_hit extra fence (after record/base/generation match)
  if (source.kind === "search_hit") {
    const nav = source.ragNavigation!;
    // rag must match message-level scope first
    if (nav.baseId !== scope.base_id) {
      return { status: "identity_mismatch", field: "base" };
    }
    if (nav.recordGeneration !== scope.record_generation) {
      return { status: "stale_generation" };
    }
    if (scope.stable_document_id == null || scope.stable_document_id.length < 1) {
      return { status: "unavailable", reason: "page_identity_incomplete" };
    }
    if (nav.stableDocumentId !== scope.stable_document_id) {
      return { status: "identity_mismatch", field: "stable_document" };
    }
    if (
      page.stableDocument.status !== "ready" ||
      page.stableDocument.stableDocumentId == null ||
      page.stableDocument.stableDocumentId.length < 1
    ) {
      return { status: "unavailable", reason: "page_identity_incomplete" };
    }
    if (page.stableDocument.stableDocumentId !== scope.stable_document_id) {
      return { status: "identity_mismatch", field: "stable_document" };
    }
  }

  // non-RAG: stable may be null on both sides; page stable need not be ready
  return { candidates };
}

function attemptedModesFromCandidates(
  candidates: readonly DomNavigationCandidate[],
): Array<"anchor_segment" | "unit"> {
  const modes: Array<"anchor_segment" | "unit"> = [];
  const seen = new Set<string>();
  for (const c of candidates) {
    if (seen.has(c.mode)) continue;
    seen.add(c.mode);
    modes.push(c.mode);
  }
  return modes;
}

// ---------------------------------------------------------------------------
// Factory — Reader construction seam
// ---------------------------------------------------------------------------

/**
 * Build the Ask-facing `NavigateAgenticSource` callback.
 * Workbench/R3C will call this; R3B does not wire UI.
 *
 * Default DOM adapter is **not** created at factory time (SSR-safe). It is
 * lazily constructed on the first navigation attempt that needs the DOM.
 * When `document` is undefined, the adapter fail-closes with no throw.
 */
export function createNavigateAgenticSource(
  dependencies: NavigateAgenticSourceDependencies,
): NavigateAgenticSource {
  let lazyDefaultAdapter: ReaderDomNavigationAdapter | null = null;

  function resolveDomAdapter(): ReaderDomNavigationAdapter {
    if (dependencies.domAdapter) {
      return dependencies.domAdapter;
    }
    if (lazyDefaultAdapter === null) {
      // Safe under Node/SSR: createReaderDomNavigationAdapter does not read
      // global document until resolveAndScroll.
      lazyDefaultAdapter = createReaderDomNavigationAdapter();
    }
    return lazyDefaultAdapter;
  }

  return async function navigateAgenticSource(
    source: AgenticSourceDescriptor,
  ): Promise<SourceNavigationResult> {
    // Early exits that must not load page identity or touch DOM
    if (source.kind === "observation") {
      return { status: "unavailable", reason: "observation_only" };
    }
    if (source.evidenceScope == null) {
      return { status: "unavailable", reason: "legacy_scope_missing" };
    }

    // Pre-check locators without page load when possible (saves loader calls)
    if (source.kind === "search_hit" && source.ragNavigation == null) {
      return { status: "unavailable", reason: "partial_citation" };
    }
    if (source.kind === "search_hit" && source.ragNavigation != null) {
      const nav = source.ragNavigation;
      const cands = buildSearchHitCandidates(nav);
      if (cands.length === 0 && hasCanonicalRangeOnly(nav)) {
        return { status: "unavailable", reason: "canonical_range_unsupported" };
      }
      if (cands.length === 0) {
        return { status: "unavailable", reason: "no_locator" };
      }
    }
    if (source.kind === "initial_anchor" || source.kind === "read_range") {
      if (buildNonRagCandidates(source).length === 0) {
        return { status: "unavailable", reason: "no_locator" };
      }
    }

    let page: CurrentPageIdentity;
    try {
      page = await dependencies.loadCurrentPageIdentity();
    } catch {
      // Network / stable-document loader failures → typed result only.
      // Do not leak exception text; do not call DOM.
      return { status: "unavailable", reason: "page_identity_incomplete" };
    }

    const planned = planSourceNavigation(source, page);
    if (!("candidates" in planned)) {
      return planned;
    }

    const hit = resolveDomAdapter().resolveAndScroll(planned.candidates);
    if (hit) {
      return {
        status: "navigated",
        mode: hit.mode,
        targetId: hit.targetId,
      };
    }

    return {
      status: "target_not_found",
      attemptedModes: attemptedModesFromCandidates(planned.candidates),
    };
  };
}
