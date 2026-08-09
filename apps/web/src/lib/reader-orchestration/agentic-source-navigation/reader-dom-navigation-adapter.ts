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

import {
  READER_RECORD_ANCHOR_SEGMENT_ATTR,
  READER_RECORD_ANCHOR_SEGMENT_SELECTOR,
  READER_RECORD_NAVIGABLE_NODE_SELECTOR,
  READER_RECORD_PLATE_DOCUMENT_SELECTOR,
  READER_RECORD_UNIT_ID_ATTR,
  READER_RECORD_UNIT_START_ATTR,
} from "@/lib/reader-plate/reader-record-dom-contract";

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

/**
 * Find an anchor-segment element by exact attribute match inside the plate body.
 * Iterates `[data-anchor-segment-id]` nodes and compares attribute values —
 * never interpolates `targetId` into an unescaped CSS selector.
 */
function findAnchorSegmentInPlateDocument(
  root: ParentNode,
  targetId: string,
): HTMLElement | null {
  const nodes = root.querySelectorAll<HTMLElement>(
    READER_RECORD_ANCHOR_SEGMENT_SELECTOR,
  );
  for (const node of nodes) {
    if (node.getAttribute(READER_RECORD_ANCHOR_SEGMENT_ATTR) === targetId) {
      return node;
    }
  }
  return null;
}

/**
 * Find a unit scroll target inside the plate body.
 *
 * Source-agnostic — shared contract with ReaderRecordNavigationRail via
 * reader-record-dom-contract: any navigable node (a paragraph today; a Markdown
 * heading / source block later) carrying the unit id, preferring the unit-start
 * node, else the first matching node.
 */
function findUnitInPlateDocument(
  root: ParentNode,
  unitId: string,
): HTMLElement | null {
  const nodes = root.querySelectorAll<HTMLElement>(
    READER_RECORD_NAVIGABLE_NODE_SELECTOR,
  );
  let fallback: HTMLElement | null = null;
  for (const node of nodes) {
    if (node.getAttribute(READER_RECORD_UNIT_ID_ATTR) !== unitId) continue;
    if (node.getAttribute(READER_RECORD_UNIT_START_ATTR) === "true") {
      return node;
    }
    if (fallback === null) {
      fallback = node;
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

/**
 * Focus owner for a navigation hit. Scroll targets the most precise element
 * (an anchor segment may be an inline span), but keyboard focus lands on the
 * nearest navigable Reader block so assistive technology sees a meaningful
 * block, not an anonymous inline node.
 */
function resolveFocusOwner(element: HTMLElement): HTMLElement {
  if (element.matches(READER_RECORD_NAVIGABLE_NODE_SELECTOR)) {
    return element;
  }
  const block = element.closest<HTMLElement>(
    READER_RECORD_NAVIGABLE_NODE_SELECTOR,
  );
  return block ?? element;
}

/**
 * Make a Reader block programmatically focusable without joining the tab
 * order. `tabIndex` reads 0 for natively focusable elements and -1 for the
 * rest, so only blocks that could not otherwise take focus get the explicit
 * attribute. `tabindex="-1"` never affects tab order and never renders a
 * visible control; programmatic focus does not match `:focus-visible`.
 */
function ensureProgrammaticallyFocusable(element: HTMLElement): void {
  if (element.tabIndex < 0 && !element.hasAttribute("tabindex")) {
    element.setAttribute("tabindex", "-1");
  }
}

function scrollAndFocus(
  element: HTMLElement,
  options?: DomNavigationScrollOptions,
): void {
  const behavior = options?.behavior ?? "smooth";
  const block = options?.block ?? "center";
  element.scrollIntoView({ behavior, block });
  const focusOwner = resolveFocusOwner(element);
  ensureProgrammaticallyFocusable(focusOwner);
  if (typeof focusOwner.focus === "function") {
    try {
      focusOwner.focus({ preventScroll: true });
    } catch {
      // Focus is best-effort; scroll alone still completes the navigation.
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

      const root = doc.querySelector<HTMLElement>(
        READER_RECORD_PLATE_DOCUMENT_SELECTOR,
      );
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
