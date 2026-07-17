/**
 * T5.5a — pure L2 semantic-outline navigation projection.
 *
 * Product gate (Phase 0 A) is stricter than T5.4b hasTrustedSemanticOutline:
 * ready|partial + non-empty nodes + source identity match + every start_unit_id
 * present in the deterministic unit universe.
 */

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";
import type { ReaderRecordPlateDocument } from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  buildOutlineSourceIdentityKey,
  type ReaderSemanticOutlineNodeDto,
  type ReaderSemanticOutlineProjectionDto,
} from "@/lib/reader-plate/projection/semantic-outline";
import { buildReaderRecordSourceIdentityKey } from "@/lib/reader-plate/projection/reader-record-navigation";

export type ReaderOutlineSurface = "deterministic" | "semantic";

export interface ReaderSemanticOutlineNavItem {
  nodeId: string;
  parentNodeId: string | null;
  depth: number;
  title: string;
  startUnitId: string;
  endUnitId: string;
  startAnchorSegmentId: string | null;
  endAnchorSegmentId: string | null;
  orderIndex: number;
  /** 0-based preorder index among all panel rows. */
  fallbackIndex: number;
}

export interface ReaderSemanticOutlineNavProjection {
  /** True only when the full L2 UI gate passes. */
  available: boolean;
  status: "ready" | "partial" | null;
  isPartial: boolean;
  outlineRevision: string | null;
  sourceIdentityKey: string;
  /** Preorder depth 1–3 rows for the expanded panel. */
  panelItems: ReaderSemanticOutlineNavItem[];
  /** Fixed: depth===1 roots only (mini ticks). */
  tickItems: ReaderSemanticOutlineNavItem[];
  /** unit_id → order_index for coverage/spy. */
  unitOrderById: Map<string, number>;
  /** Reading-order unit ids (deterministic universe). */
  orderedUnitIds: string[];
}

const EMPTY_PROJECTION = (
  sourceIdentityKey: string,
): ReaderSemanticOutlineNavProjection => ({
  available: false,
  status: null,
  isPartial: false,
  outlineRevision: null,
  sourceIdentityKey,
  panelItems: [],
  tickItems: [],
  unitOrderById: new Map(),
  orderedUnitIds: [],
});

/**
 * Deterministic unit universe for L2 gate + coverage.
 * Prefer snapshot.navigation.units; fall back to plate document unit ids.
 */
export function collectDeterministicUnitUniverse(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): { orderedUnitIds: string[]; unitOrderById: Map<string, number> } {
  const units = [...(snapshot.navigation?.units ?? [])].sort(
    (a, b) => a.order_index - b.order_index,
  );

  if (units.length > 0) {
    const orderedUnitIds = units.map((u) => u.unit_id);
    const unitOrderById = new Map(
      units.map((u) => [u.unit_id, u.order_index] as const),
    );
    return { orderedUnitIds, unitOrderById };
  }

  // Document fallback (same universe idea as L0 when units empty).
  const seen = new Set<string>();
  const orderedUnitIds: string[] = [];
  for (const block of plateDocument.children) {
    if (block.type !== "paragraph") continue;
    const unitId = block.data.unitId;
    if (!unitId || seen.has(unitId)) continue;
    seen.add(unitId);
    orderedUnitIds.push(unitId);
  }
  const unitOrderById = new Map(
    orderedUnitIds.map((id, index) => [id, index + 1] as const),
  );
  return { orderedUnitIds, unitOrderById };
}

function isOutlineObject(
  value: unknown,
): value is ReaderSemanticOutlineProjectionDto {
  return value !== null && typeof value === "object";
}

/**
 * Full L2 product gate. Never throws.
 */
export function isSemanticOutlineNavAvailable(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): boolean {
  return projectReaderSemanticOutlineNav(snapshot, plateDocument).available;
}

/**
 * Project trusted L2 navigation rows for the rail.
 * Fail-closed: available=false and empty lists when gate fails.
 */
export function projectReaderSemanticOutlineNav(
  snapshot: ReaderPlateSnapshotDto,
  plateDocument: ReaderRecordPlateDocument,
): ReaderSemanticOutlineNavProjection {
  const sourceIdentityKey = buildReaderRecordSourceIdentityKey(snapshot);
  const { orderedUnitIds, unitOrderById } = collectDeterministicUnitUniverse(
    snapshot,
    plateDocument,
  );

  try {
    const outline = snapshot.semantic_outline;
    if (!isOutlineObject(outline)) {
      return EMPTY_PROJECTION(sourceIdentityKey);
    }
    if (outline.status !== "ready" && outline.status !== "partial") {
      return EMPTY_PROJECTION(sourceIdentityKey);
    }
    const nodes = Array.isArray(outline.nodes) ? outline.nodes : [];
    if (nodes.length === 0) {
      return EMPTY_PROJECTION(sourceIdentityKey);
    }

    const outlineKey = buildOutlineSourceIdentityKey(
      outline.source_identity?.base_id ?? "",
      outline.source_identity?.generation ?? 0,
    );
    if (outlineKey !== sourceIdentityKey) {
      return EMPTY_PROJECTION(sourceIdentityKey);
    }

    // Full fail-closed gate: every node must have start+end in the universe
    // with a non-inverted reading-order range. One bad node → no L2 entry.
    for (const node of nodes) {
      if (
        !node ||
        typeof node.start_unit_id !== "string" ||
        !unitOrderById.has(node.start_unit_id)
      ) {
        return EMPTY_PROJECTION(sourceIdentityKey);
      }
      if (
        typeof node.end_unit_id !== "string" ||
        !unitOrderById.has(node.end_unit_id)
      ) {
        return EMPTY_PROJECTION(sourceIdentityKey);
      }
      const startOrder = unitOrderById.get(node.start_unit_id)!;
      const endOrder = unitOrderById.get(node.end_unit_id)!;
      if (startOrder > endOrder) {
        return EMPTY_PROJECTION(sourceIdentityKey);
      }
    }

    const panelItems = nodes.map((node, fallbackIndex) =>
      toNavItem(node, fallbackIndex),
    );
    // Fixed rule B: ticks = depth===1 only (preserve input order among roots).
    const tickItems = panelItems.filter((item) => item.depth === 1);

    if (tickItems.length === 0) {
      // No roots → unusable tree for mini rail; fail closed.
      return EMPTY_PROJECTION(sourceIdentityKey);
    }

    return {
      available: true,
      status: outline.status,
      isPartial: outline.status === "partial",
      outlineRevision: outline.publication?.outline_revision ?? null,
      sourceIdentityKey,
      panelItems,
      tickItems,
      unitOrderById,
      orderedUnitIds,
    };
  } catch {
    return EMPTY_PROJECTION(sourceIdentityKey);
  }
}

function toNavItem(
  node: ReaderSemanticOutlineNodeDto,
  fallbackIndex: number,
): ReaderSemanticOutlineNavItem {
  return {
    nodeId: node.node_id,
    parentNodeId: node.parent_node_id,
    depth: node.depth,
    title: node.title,
    startUnitId: node.start_unit_id,
    endUnitId: node.end_unit_id,
    startAnchorSegmentId: node.start_anchor_segment_id,
    endAnchorSegmentId: node.end_anchor_segment_id,
    orderIndex: node.order_index,
    fallbackIndex,
  };
}

/**
 * Pick the most specific outline node covering `currentUnitId`.
 * Among covering nodes: max depth, then max orderIndex.
 */
export function selectMostSpecificCoveringNode(
  panelItems: ReaderSemanticOutlineNavItem[],
  unitOrderById: Map<string, number>,
  currentUnitId: string | null,
): string | null {
  if (currentUnitId === null) return null;
  const currentOrder = unitOrderById.get(currentUnitId);
  if (currentOrder === undefined) return null;

  let best: ReaderSemanticOutlineNavItem | null = null;
  for (const item of panelItems) {
    const startO = unitOrderById.get(item.startUnitId);
    const endO = unitOrderById.get(item.endUnitId);
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
  return best?.nodeId ?? null;
}
