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
 * apps/web/docs/reader-ia.md);
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
  /** 1-based depth; flat sources use 1. Drives mini-rail ticks (top level, with single-root fallback) + indent. */
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
  /**
   * Mini-rail ticks.
   * Semantic: depth-1 sections, or a single root's direct children.
   * Markdown: every `role="section"` heading, in panel order.
   */
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

/**
 * Mini-rail tick selection.
 *
 * Preferred ticks are the top-level (depth 1) sections in reading order. When
 * the outline collapses to a single root — e.g. one article node with every
 * real section nested beneath it — depth-1 filtering would render a lone,
 * useless tick. In that case fall back to the root's direct children so the
 * rail still reflects the actual section structure. A single root without
 * children keeps the lone tick (there is nothing else to show).
 */
function selectOutlineTickItems(panelItems: OutlineItem[]): OutlineItem[] {
  const topLevel = panelItems.filter((item) => item.depth === 1);
  if (topLevel.length !== 1) return topLevel;
  const root = topLevel[0]!;
  const children = panelItems.filter(
    (item) => item.depth === 2 && item.parentKey === root.key,
  );
  return children.length >= 2 ? children : topLevel;
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
    tickItems: selectOutlineTickItems(panelItems),
    orderedUnitIds: sem.orderedUnitIds,
    unitOrderById: sem.unitOrderById,
  };
}

/**
 * Markdown-heading outline source (B4).
 *
 * Projects heading structure from the stable snapshot into the source-agnostic
 * `ReaderOutlineViewModel`. A unit is a Markdown heading candidate when its
 * `unit_type === "heading"` (canonical, mirrors the backend semantic-outline
 * skip predicate in `job_bootstrap.py`) OR `stable_block_type === "heading"`
 * (defensive A5 payload mirror). `heading_level` is optional — when absent
 * (legacy heuristic path) the heading projects at level 1. Each selected
 * heading becomes an `OutlineItem` with:
 *
 *   - `depth = min(headingLevel, 3)` — the rail panel caps depth at 3 to match
 *     the semantic source (depths 1–3 only). Deeper headings are still visible
 *     in the reading surface; only the outline rail caps them.
 *   - `target = { unitId: heading.unit_id, anchorSegmentId: null }` — the rail
 *     scrolls to the heading unit; no anchor segment is needed because the
 *     heading itself is the section start.
 *   - `coverage = { startUnitId: this heading, endUnitId: next heading (same
 *     or higher level) − 1, or last unit }` — scroll-spy uses this interval to
 *     pick the currently active heading. The end is the unit *before* the next
 *     heading at level ≤ this heading's level, mirroring how Markdown section
 *     nesting works (a level-2 heading covers everything until the next level-1
 *     or level-2 heading).
 *   - `role = "section"` — every Markdown heading is independently navigable.
 *     Unlike the semantic source, Markdown never produces non-navigable groups:
 *     a heading is always a clickable landing point.
 *   - `key = "md:{unit_id}"` — namespaced by source kind so it cannot collide
 *     with semantic `nodeId`s when both sources are projected in tests.
 *   - `parentKey` — resolved by tracking the most recent heading at level
 *     `< this heading's level` (strictly shallower). A level-1 heading has no
 *     parent. This produces a true nesting tree without re-parsing Markdown.
 *
 * Fail-closed rules:
 *   - No `navigation.units` → unavailable (empty model).
 *   - Fewer than 2 heading units → unavailable. A single heading does not
 *     produce a useful rail (no sections to navigate between); fall through to
 *     the semantic source so the rail is not blanked out by one stray `#`.
 *   - A heading unit missing both `unit_type !== "heading"` and
 *     `stable_block_type !== "heading"` is skipped silently (defensive —
 *     should not happen when A5 is wired, but the projection never throws).
 *     `heading_level` missing is NOT a skip reason — it defaults to 1.
 *   - Coverage `endUnitId` falls back to the last unit when the heading is the
 *     final one or no subsequent same-or-shallower heading exists.
 *
 * Reference: apps/web/docs/design/component-system.md
 */
export function projectMarkdownOutlineView(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): ReaderOutlineViewModel {
  void plateDocument;

  const sourceIdentityKey = buildReaderRecordSourceIdentityKey(snapshot);
  const units = snapshot.navigation?.units ?? [];
  if (units.length === 0) {
    return emptyOutlineViewModel("markdown", sourceIdentityKey);
  }

  const sortedUnits = [...units].sort((a, b) => a.order_index - b.order_index);

  // Select heading units. The check mirrors the backend semantic-outline
  // skip predicate (`unit_type == "heading"` in job_bootstrap.py) so the
  // frontend Markdown outline and the backend skip decision stay aligned:
  // whenever the backend counts a unit as a heading, the frontend does too.
  //
  // Acceptance criteria (any one):
  //   - `unit_type === "heading"` (backend canonical; covers annotated
  //     snapshots AND legacy heuristic-classified snapshots)
  //   - `stable_block_type === "heading"` (defensive payload mirror;
  //     present whenever a StableBlockAnnotation matched, even if the
  //     snapshot was built before `unit_type` was overridden)
  //
  // `heading_level` resolution (legacy heuristic support):
  //   - `null` / `undefined` (not provided) → DEFAULT to 1. The backend
  //     heuristic `_classify_unit_type` detects headings without extracting
  //     a level, and legacy snapshots carry `unit_type === "heading"` with
  //     `heading_level === null`. These MUST still project at level 1 so the
  //     rail is not blanked after the backend skips semantic outline.
  //   - A finite number → clamp to [1, 6] (0 → 1, 7 → 6).
  //   - `NaN` / non-finite (truly broken payload) → SKIP defensively. A
  //     non-finite level cannot be placed in the hierarchy and projecting it
  //     at any value would distort coverage intervals. This is distinct from
  //     "not provided" — NaN means the payload explicitly carried garbage.
  interface HeadingPick {
    unitId: string;
    orderIndex: number;
    level: number; // 1-based, clamped to [1, 6]
    fallbackIndex: number;
  }
  const picks: HeadingPick[] = [];
  for (const unit of sortedUnits) {
    const isHeading =
      unit.unit_type === "heading" || unit.stable_block_type === "heading";
    if (!isHeading) continue;
    const rawLevel = unit.heading_level;
    // Missing/null → default to 1 (legacy heuristic path).
    if (rawLevel === null || rawLevel === undefined) {
      picks.push({
        unitId: unit.unit_id,
        orderIndex: unit.order_index,
        level: 1,
        fallbackIndex: picks.length,
      });
      continue;
    }
    // Non-finite number (NaN, Infinity, -Infinity) → skip defensively.
    if (typeof rawLevel !== "number" || !Number.isFinite(rawLevel)) {
      continue;
    }
    const level = Math.min(Math.max(Math.trunc(rawLevel), 1), 6);
    picks.push({
      unitId: unit.unit_id,
      orderIndex: unit.order_index,
      level,
      fallbackIndex: picks.length,
    });
  }

  // Fail-closed: fewer than 2 headings → not a useful Markdown outline.
  // Fall through to the semantic source instead of blanking the rail.
  if (picks.length < 2) {
    return emptyOutlineViewModel("markdown", sourceIdentityKey);
  }

  // Build a unit_id → order_index map for coverage resolution.
  const unitOrderById = new Map<string, number>();
  for (const unit of sortedUnits) {
    unitOrderById.set(unit.unit_id, unit.order_index);
  }
  const orderedUnitIds = sortedUnits.map((u) => u.unit_id);

  // Coverage resolution: for each heading, find the unit just before the next
  // heading at level <= this heading's level. If none, coverage extends to the
  // last unit.
  interface ResolvedHeading extends HeadingPick {
    endUnitId: string;
    parentLevel0Index: number | null; // index into picks[] of parent, or null
  }
  const resolved: ResolvedHeading[] = picks.map((pick, i) => {
    let endIndex = sortedUnits.length - 1;
    for (let j = i + 1; j < picks.length; j += 1) {
      if (picks[j]!.level <= pick.level) {
        // The next heading at same-or-shallower level: coverage ends at the
        // unit just before that heading. Find the unit just before picks[j].
        const nextHeadingOrder = picks[j]!.orderIndex;
        // sortedUnits is in order_index order; find the largest order_index
        // strictly less than nextHeadingOrder.
        let cursor = sortedUnits.length - 1;
        for (let k = sortedUnits.length - 1; k >= 0; k -= 1) {
          if (sortedUnits[k]!.order_index < nextHeadingOrder) {
            cursor = k;
            break;
          }
        }
        endIndex = cursor;
        break;
      }
    }
    const endUnitId = sortedUnits[endIndex]!.unit_id;

    // Parent resolution: most recent prior heading with strictly smaller level.
    let parentIndex: number | null = null;
    for (let p = i - 1; p >= 0; p -= 1) {
      if (picks[p]!.level < pick.level) {
        parentIndex = p;
        break;
      }
    }
    return { ...pick, endUnitId, parentLevel0Index: parentIndex };
  });

  // Project to OutlineItem[].
  const panelItems: OutlineItem[] = resolved.map((heading, i) => {
    const parentKey =
      heading.parentLevel0Index === null
        ? null
        : `md:${resolved[heading.parentLevel0Index]!.unitId}`;
    return {
      key: `md:${heading.unitId}`,
      parentKey,
      depth: Math.min(heading.level, 3),
      title: "", // Title is read from the snapshot unit label/text at render
      // time; the outline projection keeps it empty so the projection stays
      // pure w.r.t. text content (the renderer has the actual heading text via
      // the stable source preview / navigation unit label).
      target: { unitId: heading.unitId, anchorSegmentId: null },
      coverage: {
        startUnitId: heading.unitId,
        endUnitId: heading.endUnitId,
      },
      orderIndex: i,
      fallbackIndex: i,
      role: "section",
    };
  });

  // Fill titles from the navigation unit labels (defensive: label may be null
  // when the builder did not synthesize one — the renderer falls back to the
  // heading text from the stable source preview).
  const unitLabelById = new Map<string, string | null>();
  for (const unit of sortedUnits) {
    unitLabelById.set(unit.unit_id, unit.label ?? null);
  }
  for (let i = 0; i < panelItems.length; i += 1) {
    const item = panelItems[i]!;
    const label = unitLabelById.get(item.target.unitId);
    if (typeof label === "string" && label.length > 0) {
      panelItems[i] = { ...item, title: label };
    }
  }

  const tickItems = panelItems.filter((item) => item.role === "section");

  return {
    available: true,
    status: "ready",
    isPartial: false,
    identity: {
      sourceKind: "markdown",
      sourceIdentityKey,
      revision: null,
    },
    panelItems,
    tickItems,
    orderedUnitIds,
    unitOrderById,
  };
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
