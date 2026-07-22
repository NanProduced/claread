/**
 * Source-agnostic Reader outline view model.
 *
 * This is the ONLY outline contract the navigation rail consumes. The UI never
 * sees a "semantic" or "deterministic" outline concept directly: every source is
 * projected into the same `ReaderOutlineViewModel` shape here, and a pure
 * priority combinator (`pickReaderOutlineSource`) chooses which source wins.
 *
 * Priority rule (product spec): valid Markdown headings → semantic outline → hide.
 * Today only the semantic source is implemented; `projectMarkdownOutlineView` is
 * an honest "unavailable" stub (no parsed headings, no faked data), so the
 * combinator currently always falls through to semantic. A future Markdown source
 * plugs in by replacing `projectMarkdownOutlineView` only — the rail and the
 * combinator stay untouched.
 *
 * Rationale: the backend stable document model is the source of truth and the
 * Plate/value layer is a web projection (see
 * docs/tmp/reader-orchestration/research/TMP-plate-v53-markdown-readonly-capability-research-2026-07-16.md);
 * binding the UI to a source-agnostic view model — rather than to either
 * `semantic_outline` or a Markdown AST — keeps that boundary clean.
 */

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";
import type { ReaderRecordPlateDocument } from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  projectReaderSemanticOutlineNav,
  type ReaderSemanticOutlineNavProjection,
  type ReaderSemanticOutlineNavItem,
} from "@/lib/reader-plate/projection/semantic-outline-nav";
import { buildReaderRecordSourceIdentityKey } from "@/lib/reader-plate/projection/reader-record-navigation";

export type OutlineSourceKind = "semantic" | "markdown";

export interface OutlineSourceIdentity {
  /** The source that produced (or, when unavailable, attempted) this model. */
  sourceKind: OutlineSourceKind;
  /** `${base_id}:${generation}` — present even when the outline is unavailable. */
  sourceIdentityKey: string;
  /** Source revision (e.g. outline revision); null when none / unavailable. */
  revision: string | null;
}

export interface OutlineItemTarget {
  /** Scroll target: the unit the item begins at. */
  unitId: string;
  /** Optional anchor segment to prefer within the unit; null when absent. */
  anchorSegmentId: string | null;
}

export interface OutlineItemCoverage {
  /** Reading-order unit interval this item spans (used by scroll-spy). */
  startUnitId: string;
  endUnitId: string;
}

export interface OutlineItem {
  /** Stable, unique-within-source row key (semantic nodeId / future heading id). */
  key: string;
  parentKey: string | null;
  /** 1-based depth; flat sources use 1. Drives mini-rail ticks (depth===1) + indent. */
  depth: number;
  title: string;
  /** Only the *start* target — the rail only needs it for click-to-scroll. */
  target: OutlineItemTarget;
  coverage: OutlineItemCoverage;
  orderIndex: number;
  /** 0-based preorder index among panel rows. */
  fallbackIndex: number;
  /**
   * Display/interaction role — the single source of truth for interactivity.
   * A `group` is a meaningful parent topic with no independent navigation
   * landing point (it shares its start with its first child): rendered for
   * hierarchy, NOT clickable/focusable. A `section` is a normal navigable row.
   * Navigability is derived as `role === "section"` — there is intentionally no
   * separate `navigable` field, so the two cannot drift. Decided per source: the
   * semantic adapter sets it today; a Markdown adapter maps headings to `section`.
   */
  role: "section" | "group";
}

export interface ReaderOutlineViewModel {
  /** True only when a source produced a usable (ready / partial) outline. */
  available: boolean;
  status: "ready" | "partial" | null;
  isPartial: boolean;
  identity: OutlineSourceIdentity;
  /** Preorder depth 1–3 rows for the expanded panel. */
  panelItems: OutlineItem[];
  /** depth===1 roots only (mini-rail ticks). */
  tickItems: OutlineItem[];
  /** Reading-order unit ids (for scroll-spy current-unit resolution). */
  orderedUnitIds: string[];
  /** unit_id → order_index (for coverage / scroll-spy). */
  unitOrderById: Map<string, number>;
}

/**
 * Full isolation identity for the rail: `${sourceKind}:${sourceIdentityKey}`.
 * A semantic and a future Markdown source may share the same base_id:generation,
 * so the rail's state, DOM cache, scroll lock and scroll-spy fence must key on
 * this — never on sourceIdentityKey alone.
 */
export function buildOutlineScopeKey(identity: OutlineSourceIdentity): string {
  return `${identity.sourceKind}:${identity.sourceIdentityKey}`;
}

function emptyOutlineViewModel(
  sourceKind: OutlineSourceKind,
  sourceIdentityKey: string,
): ReaderOutlineViewModel {
  return {
    available: false,
    status: null,
    isPartial: false,
    identity: { sourceKind, sourceIdentityKey, revision: null },
    panelItems: [],
    tickItems: [],
    orderedUnitIds: [],
    unitOrderById: new Map(),
  };
}

function toOutlineItem(item: ReaderSemanticOutlineNavItem): OutlineItem {
  return {
    key: item.nodeId,
    parentKey: item.parentNodeId,
    depth: item.depth,
    title: item.title,
    target: {
      unitId: item.startUnitId,
      anchorSegmentId: item.startAnchorSegmentId,
    },
    coverage: {
      startUnitId: item.startUnitId,
      endUnitId: item.endUnitId,
    },
    orderIndex: item.orderIndex,
    fallbackIndex: item.fallbackIndex,
    role: "section",
  };
}

/**
 * Semantic-source role resolution: a parent with no independent navigation
 * landing point — i.e. it has at least one direct child whose start target
 * equals its own — becomes a non-navigable `group`, kept for hierarchy/topic
 * (never flattened or deleted). Everything else is a navigable `section`. This
 * is a semantic-source decision; source-agnostic layers and other adapters
 * (Markdown) never apply it.
 */
export function applySemanticOutlineRoles(
  items: OutlineItem[],
): OutlineItem[] {
  const childrenOf = new Map<string, OutlineItem[]>();
  for (const item of items) {
    if (!item.parentKey) continue;
    const list = childrenOf.get(item.parentKey);
    if (list) list.push(item);
    else childrenOf.set(item.parentKey, [item]);
  }
  return items.map((item) => {
    const kids = childrenOf.get(item.key);
    const isGroup =
      !!kids &&
      kids.length > 0 &&
      (() => {
        const firstChild = kids.reduce(
          (min, kid) => (kid.orderIndex < min.orderIndex ? kid : min),
          kids[0]!,
        );
        return (
          item.target.unitId === firstChild.target.unitId &&
          item.target.anchorSegmentId === firstChild.target.anchorSegmentId
        );
      })();
    return isGroup ? { ...item, role: "group" as const } : item;
  });
}

function semanticToOutlineViewModel(
  sem: ReaderSemanticOutlineNavProjection,
): ReaderOutlineViewModel {
  const panelItems = applySemanticOutlineRoles(
    sem.panelItems.map(toOutlineItem),
  );
  return {
    available: sem.available,
    status: sem.status,
    isPartial: sem.isPartial,
    identity: {
      sourceKind: "semantic",
      sourceIdentityKey: sem.sourceIdentityKey,
      revision: sem.outlineRevision,
    },
    panelItems,
    tickItems: panelItems.filter((item) => item.depth === 1),
    orderedUnitIds: sem.orderedUnitIds,
    unitOrderById: sem.unitOrderById,
  };
}

/**
 * Markdown-heading outline source. NOT implemented this round.
 *
 * Returns an honest unavailable model (no items, no faked headings) so the
 * priority combinator falls through to the semantic source. A real implementation
 * parses heading structure (block type heading + depth/heading_level) from the
 * stable source document — see
 * docs/tmp/reader-orchestration/TMP-reader-markdown-rich-input-deep-research-2026-07-16.md §7.
 */
export function projectMarkdownOutlineView(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): ReaderOutlineViewModel {
  // TODO(markdown-outline): parse headings from the stable source document and
  // project them into OutlineItem[] (depth from heading level; coverage heading →
  // next heading; target from the heading's unit/anchor). Until then, no items.
  void plateDocument;
  return emptyOutlineViewModel(
    "markdown",
    buildReaderRecordSourceIdentityKey(snapshot),
  );
}

/**
 * Pure priority rule: a usable Markdown outline wins; otherwise the semantic
 * outline (which may itself be unavailable). This is the unit-testable seam — a
 * future source changes `projectMarkdownOutlineView`, never this function or the UI.
 */
export function pickReaderOutlineSource(
  markdown: ReaderOutlineViewModel,
  semantic: ReaderOutlineViewModel,
): ReaderOutlineViewModel {
  return markdown.available ? markdown : semantic;
}

/**
 * The only builder the UI calls. Composes the per-source projections through the
 * priority combinator. Role/navigable resolution happens inside each source
 * adapter (see {@link applySemanticOutlineRoles}); there is no source-agnostic
 * "delete duplicate" layer — parents without an independent landing point are
 * kept as non-navigable groups, never removed. Never throws (each source
 * projection is fail-closed).
 */
export function projectReaderOutlineView(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): ReaderOutlineViewModel {
  const semantic = semanticToOutlineViewModel(
    projectReaderSemanticOutlineNav(snapshot, plateDocument),
  );
  const markdown = projectMarkdownOutlineView(snapshot, plateDocument);
  return pickReaderOutlineSource(markdown, semantic);
}

/**
 * Pick the most specific outline item covering `currentUnitId`.
 * Among covering items: max depth, then max orderIndex. Returns the item key.
 * Generalized from the semantic-only selector; source-agnostic over `OutlineItem`.
 */
export function selectMostSpecificCoveringNode(
  panelItems: OutlineItem[],
  unitOrderById: Map<string, number>,
  currentUnitId: string | null,
): string | null {
  if (currentUnitId === null) return null;
  const currentOrder = unitOrderById.get(currentUnitId);
  if (currentOrder === undefined) return null;

  let best: OutlineItem | null = null;
  for (const item of panelItems) {
    const startO = unitOrderById.get(item.coverage.startUnitId);
    const endO = unitOrderById.get(item.coverage.endUnitId);
    if (startO === undefined || endO === undefined) continue;
    if (currentOrder < startO || currentOrder > endO) continue;
    if (
      best === null ||
      item.depth > best.depth ||
      (item.depth === best.depth && item.orderIndex > best.orderIndex)
    ) {
      best = item;
    }
  }
  return best?.key ?? null;
}
