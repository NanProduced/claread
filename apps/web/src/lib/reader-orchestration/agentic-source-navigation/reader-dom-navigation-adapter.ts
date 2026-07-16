/**
 * Reader-owned DOM adapter for agentic source navigation.
 *
 * Search is scoped exclusively to `.reader-record-plate-document`.
 * Never uses global `[data-unit-id]`, snippet text search, Plate paths,
 * Slate ops, or raw CSS selector injection of external ids.
 *
 * Public surface: types + {@link createReaderDomNavigationAdapter} only.
 * Find helpers stay private implementation details.
 */

export type DomNavigationTargetMode = "anchor_segment" | "unit";

export type DomNavigationCandidate = {
  mode: DomNavigationTargetMode;
  targetId: string;
};

export type DomNavigationHit = {
  mode: DomNavigationTargetMode;
  targetId: string;
};

export type DomNavigationScrollOptions = {
  behavior?: ScrollBehavior;
  block?: ScrollLogicalPosition;
};

/**
 * Production adapter: find + scroll/focus inside the plate document root.
 * Injected into the navigation module via construction deps (not Ask-facing).
 */
export type ReaderDomNavigationAdapter = {
  resolveAndScroll(
    candidates: readonly DomNavigationCandidate[],
    options?: DomNavigationScrollOptions,
  ): DomNavigationHit | null;
};

const PLATE_DOCUMENT_ROOT = ".reader-record-plate-document";

/**
 * Find an anchor-segment element by exact attribute match inside the plate body.
 * Iterates `[data-anchor-segment-id]` nodes and compares attribute values —
 * never interpolates `targetId` into an unescaped CSS selector.
 */
function findAnchorSegmentInPlateDocument(
  root: ParentNode,
  targetId: string,
): HTMLElement | null {
  const nodes = root.querySelectorAll<HTMLElement>("[data-anchor-segment-id]");
  for (const node of nodes) {
    if (node.getAttribute("data-anchor-segment-id") === targetId) {
      return node;
    }
  }
  return null;
}

/**
 * Find a unit scroll target inside the plate body.
 *
 * Semantics aligned with ReaderRecordNavigationRail (without importing it):
 * 1. paragraphs with `data-reader-record-node="paragraph"`
 * 2. `data-unit-id` exact attribute match
 * 3. prefer `data-reader-record-unit-start="true"`
 * 4. else first matching paragraph
 */
function findUnitInPlateDocument(
  root: ParentNode,
  unitId: string,
): HTMLElement | null {
  const paragraphs = root.querySelectorAll<HTMLElement>(
    '[data-reader-record-node="paragraph"]',
  );
  let fallback: HTMLElement | null = null;
  for (const paragraph of paragraphs) {
    if (paragraph.getAttribute("data-unit-id") !== unitId) continue;
    if (paragraph.getAttribute("data-reader-record-unit-start") === "true") {
      return paragraph;
    }
    if (fallback === null) {
      fallback = paragraph;
    }
  }
  return fallback;
}

function resolveDocumentRef(
  documentRef: Document | null | undefined,
): Document | null {
  if (documentRef != null) {
    return documentRef;
  }
  // Fail-closed under SSR / Node — never throw ReferenceError on `document`.
  if (typeof document === "undefined") {
    return null;
  }
  return document;
}

function scrollAndFocus(
  element: HTMLElement,
  options?: DomNavigationScrollOptions,
): void {
  const behavior = options?.behavior ?? "smooth";
  const block = options?.block ?? "center";
  element.scrollIntoView({ behavior, block });
  if (typeof element.focus === "function") {
    try {
      element.focus({ preventScroll: true });
    } catch {
      // Some elements are not focusable; ignore.
    }
  }
}

/**
 * Create the production DOM adapter.
 *
 * Does **not** touch `document` at construction time. The global document is
 * only resolved on {@link ReaderDomNavigationAdapter.resolveAndScroll}.
 * When no Document is available (SSR/Node), resolveAndScroll returns null
 * without throwing.
 *
 * @param documentRef Optional explicit Document (tests). Omitted → lazy global.
 */
export function createReaderDomNavigationAdapter(
  documentRef?: Document | null,
): ReaderDomNavigationAdapter {
  return {
    resolveAndScroll(candidates, options) {
      const doc = resolveDocumentRef(documentRef);
      if (!doc) return null;

      const root = doc.querySelector<HTMLElement>(PLATE_DOCUMENT_ROOT);
      if (!root) return null;

      for (const candidate of candidates) {
        const el =
          candidate.mode === "anchor_segment"
            ? findAnchorSegmentInPlateDocument(root, candidate.targetId)
            : findUnitInPlateDocument(root, candidate.targetId);
        if (el) {
          scrollAndFocus(el, options);
          return { mode: candidate.mode, targetId: candidate.targetId };
        }
      }
      return null;
    },
  };
}
