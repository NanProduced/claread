/**
 * Reader-owned deep module: Ask article-citation source navigation.
 *
 * Ownership split (secure citation navigation chain):
 * - The Ask panel owns the secure endpoint call. It submits only the
 *   record id + assistant message id + public citation id; the server
 *   loads the identity fence and returns a minimal typed location.
 * - This module owns everything after the server-verified typed location
 *   comes back: candidate order (anchor segment before unit) and DOM
 *   scroll/focus via the Reader DOM navigation adapter.
 *
 * Ask-facing seam is only {@link NavigateToArticleLocation}. Callers must
 * never pass Element / Document / page identity here, and never receive
 * them. No snippet, fingerprint, evidence scope, or client fence fields
 * cross this boundary.
 */

import {
  createReaderDomNavigationAdapter,
  type DomNavigationCandidate,
  type ReaderDomNavigationAdapter,
} from "./reader-dom-navigation-adapter";

// ---------------------------------------------------------------------------
// Ask-facing types (keep this surface small)
// ---------------------------------------------------------------------------

/**
 * Server-verified typed article location, as returned by the secure
 * citation navigate route. No handles, identity, or raw evidence.
 */
export type ArticleNavigationLocation = {
  unitId: string | null;
  anchorSegmentId: string | null;
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
 * Callers must not receive Element / Document / page identity.
 */
export type NavigateToArticleLocation = (
  location: ArticleNavigationLocation,
) => Promise<SourceNavigationResult>;

// ---------------------------------------------------------------------------
// Secure-response projection (feedback mapping stays single-owned)
// ---------------------------------------------------------------------------

/**
 * Project a secure citation-navigate response that did NOT yield a typed
 * location into the navigation result union, so user feedback formatting
 * stays owned by one formatter. `ok` responses with a location are handled
 * by the caller via {@link NavigateToArticleLocation} and never reach this.
 * Internal reason codes never surface to the user.
 */
export function sourceNavigationResultFromCitationNavigate(response: {
  status: string;
  reason?: string | null;
}): SourceNavigationResult {
  if (response.status === "identity_mismatch") {
    const field =
      response.reason === "base" || response.reason === "stable_document"
        ? response.reason
        : "reading_record";
    return { status: "identity_mismatch", field };
  }
  if (response.status === "stale_generation") {
    return { status: "stale_generation" };
  }
  const reason =
    response.reason === "legacy_scope_missing"
      ? "legacy_scope_missing"
      : response.reason === "record_fence_unavailable" ||
          response.reason === "live_stable_document_missing"
        ? "page_identity_incomplete"
        : "no_locator";
  return { status: "unavailable", reason };
}

// ---------------------------------------------------------------------------
// Factory — Reader construction seam
// ---------------------------------------------------------------------------

/**
 * Build the Ask-facing `NavigateToArticleLocation` callback.
 *
 * Default DOM adapter is **not** created at factory time (SSR-safe). It is
 * lazily constructed on the first navigation attempt. When `document` is
 * undefined, the adapter fail-closes with no throw.
 */
export function createArticleLocationNavigator(
  dependencies: { domAdapter?: ReaderDomNavigationAdapter } = {},
): NavigateToArticleLocation {
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

  return async function navigateToArticleLocation(
    location: ArticleNavigationLocation,
  ): Promise<SourceNavigationResult> {
    // Anchor segment is the finer-grained target; try it before the unit.
    const candidates: DomNavigationCandidate[] = [];
    if (
      typeof location.anchorSegmentId === "string" &&
      location.anchorSegmentId.length > 0
    ) {
      candidates.push({
        mode: "anchor_segment",
        targetId: location.anchorSegmentId,
      });
    }
    if (typeof location.unitId === "string" && location.unitId.length > 0) {
      candidates.push({ mode: "unit", targetId: location.unitId });
    }
    if (candidates.length === 0) {
      return { status: "unavailable", reason: "no_locator" };
    }

    const hit = resolveDomAdapter().resolveAndScroll(candidates);
    if (hit) {
      return {
        status: "navigated",
        mode: hit.mode,
        targetId: hit.targetId,
      };
    }

    const attemptedModes: Array<"anchor_segment" | "unit"> = [];
    for (const candidate of candidates) {
      if (!attemptedModes.includes(candidate.mode)) {
        attemptedModes.push(candidate.mode);
      }
    }
    return { status: "target_not_found", attemptedModes };
  };
}
