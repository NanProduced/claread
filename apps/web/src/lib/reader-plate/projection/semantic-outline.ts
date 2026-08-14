/** Backend-owned semantic-outline wire consumer and pure validator. */

export type SemanticOutlineStatus =
  | "unavailable"
  | "pending"
  | "partial"
  | "ready"
  | "failed"
  | "stale";

export type ReaderSemanticOutlineSourceIdentityDto = {
  base_id: string;
  generation: number;
};
export type ReaderSemanticOutlinePublicationDto = {
  outline_revision: string;
  layer_id?: string | null;
  published_at?: string | null;
};
export type ReaderSemanticOutlineProvenanceDto = {
  kind: "llm" | "hybrid" | "deterministic";
  builder?: string | null;
  model?: string | null;
};
export type ReaderSemanticOutlineNodeDto = {
  node_id: string;
  parent_node_id: string | null;
  depth: number;
  title: string;
  start_unit_id: string;
  end_unit_id: string;
  start_anchor_segment_id: string | null;
  end_anchor_segment_id: string | null;
  order_index: number;
};
export type ReaderSemanticOutlineDropDto = {
  node_id: string | null;
  reason_code: string;
};
export type ReaderSemanticOutlineDiagnosticsDto = {
  drops: ReaderSemanticOutlineDropDto[];
  skipped_node_count: number;
};
export type ReaderSemanticOutlineProjectionDto = {
  schema_kind: "reader_semantic_outline";
  schema_version: 1;
  status: SemanticOutlineStatus;
  source_identity: ReaderSemanticOutlineSourceIdentityDto;
  publication: ReaderSemanticOutlinePublicationDto;
  provenance: ReaderSemanticOutlineProvenanceDto;
  nodes: ReaderSemanticOutlineNodeDto[];
  diagnostics: ReaderSemanticOutlineDiagnosticsDto;
};

export type SemanticOutlineSourceIdentity = ReaderSemanticOutlineSourceIdentityDto;
export type SemanticOutlineValidationContext = {
  source_identity: SemanticOutlineSourceIdentity;
  units: Array<{ unit_id: string; order_index: number }>;
  anchors: Array<{ anchor_segment_id: string; unit_id: string }>;
};
type RawSemanticOutlineNode = {
  node_id: string | null;
  parent_node_id: string | null;
  depth: unknown;
  title: unknown;
  start_unit_id: string | null;
  end_unit_id: string | null;
  start_anchor_segment_id: string | null;
  end_anchor_segment_id: string | null;
};
export type SemanticOutlineValidationInput = {
  field_present: boolean;
  requested: boolean;
  in_flight: boolean;
  worker_failure: boolean;
  projection_source_identity: SemanticOutlineSourceIdentity;
  attempted_nodes: RawSemanticOutlineNode[];
};
export type ValidatedSemanticOutlineNode = ReaderSemanticOutlineNodeDto;
export type SemanticOutlineDrop = ReaderSemanticOutlineDropDto;
export type SemanticOutlineValidationResult = {
  status: SemanticOutlineStatus;
  nodes: ValidatedSemanticOutlineNode[];
  diagnostics: ReaderSemanticOutlineDiagnosticsDto;
};

const MAX_DEPTH = 3;
const TITLE_MAX_CODE_POINTS = 80;

function mapping(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function sourceIdentity(value: unknown): SemanticOutlineSourceIdentity {
  const raw = mapping(value);
  return {
    base_id: optionalString(raw.base_id) ?? "",
    generation:
      typeof raw.generation === "number" && Number.isInteger(raw.generation)
        ? raw.generation
        : 0,
  };
}

function sameSourceIdentity(
  left: SemanticOutlineSourceIdentity,
  right: SemanticOutlineSourceIdentity,
): boolean {
  return left.base_id === right.base_id && left.generation === right.generation;
}

export function semanticOutlineValidationContextFromUnknown(
  value: unknown,
): SemanticOutlineValidationContext {
  const raw = mapping(value);
  const units: SemanticOutlineValidationContext["units"] = [];
  for (const item of Array.isArray(raw.units) ? raw.units : []) {
    const unit = mapping(item);
    const unit_id = optionalString(unit.unit_id);
    if (
      unit_id !== null &&
      typeof unit.order_index === "number" &&
      Number.isInteger(unit.order_index) &&
      unit.order_index >= 1
    ) {
      units.push({ unit_id, order_index: unit.order_index });
    }
  }
  const anchors: SemanticOutlineValidationContext["anchors"] = [];
  for (const item of Array.isArray(raw.anchors) ? raw.anchors : []) {
    const anchor = mapping(item);
    const anchor_segment_id = optionalString(anchor.anchor_segment_id);
    const unit_id = optionalString(anchor.unit_id);
    if (anchor_segment_id !== null && unit_id !== null) {
      anchors.push({ anchor_segment_id, unit_id });
    }
  }
  return { source_identity: sourceIdentity(raw.source_identity), units, anchors };
}

export function semanticOutlineValidationInputFromUnknown(
  value: unknown,
): SemanticOutlineValidationInput {
  const raw = mapping(value);
  const attempted_nodes: RawSemanticOutlineNode[] = [];
  for (const item of Array.isArray(raw.attempted_nodes) ? raw.attempted_nodes : []) {
    const node = mapping(item);
    attempted_nodes.push({
      node_id: optionalString(node.node_id),
      parent_node_id: optionalString(node.parent_node_id),
      depth: node.depth,
      title: node.title,
      start_unit_id: optionalString(node.start_unit_id),
      end_unit_id: optionalString(node.end_unit_id),
      start_anchor_segment_id: optionalString(node.start_anchor_segment_id),
      end_anchor_segment_id: optionalString(node.end_anchor_segment_id),
    });
  }
  return {
    field_present: raw.field_present === true,
    requested: raw.requested === true,
    in_flight: raw.in_flight === true,
    worker_failure: raw.worker_failure === true,
    projection_source_identity: sourceIdentity(raw.projection_source_identity),
    attempted_nodes,
  };
}

export function validateSemanticOutlineProjection(
  context: SemanticOutlineValidationContext,
  input: SemanticOutlineValidationInput,
): SemanticOutlineValidationResult {
  const terminal = (
    status: SemanticOutlineStatus,
    reason?: string,
  ): SemanticOutlineValidationResult => ({
    status,
    nodes: [],
    diagnostics: {
      drops: reason === undefined ? [] : [{ node_id: null, reason_code: reason }],
      skipped_node_count: 0,
    },
  });
  if (input.worker_failure) return terminal("failed", "worker_failure");
  if (!input.field_present || (!input.requested && input.attempted_nodes.length === 0)) {
    return terminal("unavailable");
  }
  if (input.in_flight && input.attempted_nodes.length === 0) return terminal("pending");
  if (!sameSourceIdentity(input.projection_source_identity, context.source_identity)) {
    return terminal("stale", "source_mismatch");
  }
  if (input.attempted_nodes.length === 0) return terminal("failed", "empty_attempt");

  const unitOrder = new Map(context.units.map((unit) => [unit.unit_id, unit.order_index]));
  const anchorUnits = new Map(
    context.anchors.map((anchor) => [anchor.anchor_segment_id, anchor.unit_id]),
  );
  const accepted: ValidatedSemanticOutlineNode[] = [];
  const acceptedById = new Map<string, ValidatedSemanticOutlineNode>();
  const droppedIds = new Set<string>();
  const seenIds = new Set<string>();
  const drops: SemanticOutlineDrop[] = [];
  const drop = (
    node: RawSemanticOutlineNode,
    reason_code: string,
    markDropped = true,
  ) => {
    drops.push({ node_id: node.node_id, reason_code });
    if (markDropped && node.node_id !== null) droppedIds.add(node.node_id);
  };

  for (const raw of input.attempted_nodes) {
    if (raw.node_id === null) {
      drop(raw, "missing_node_id");
      continue;
    }
    if (seenIds.has(raw.node_id)) {
      drop(raw, "duplicate_node_id", false);
      continue;
    }
    seenIds.add(raw.node_id);
    const parent = raw.parent_node_id;
    let parentNode: ValidatedSemanticOutlineNode | undefined;
    if (parent !== null) {
      if (droppedIds.has(parent)) {
        drop(raw, "parent_dropped");
        continue;
      }
      parentNode = acceptedById.get(parent);
      if (parentNode === undefined) {
        drop(raw, "invalid_parent");
        continue;
      }
      if (raw.depth !== parentNode.depth + 1) {
        drop(raw, "depth_parent_mismatch");
        continue;
      }
    }
    if (
      typeof raw.depth !== "number" ||
      !Number.isInteger(raw.depth) ||
      raw.depth < 1 ||
      raw.depth > MAX_DEPTH
    ) {
      drop(raw, "depth_out_of_range");
      continue;
    }
    if (parent === null && raw.depth !== 1) {
      drop(raw, "invalid_root_depth");
      continue;
    }
    if (typeof raw.title !== "string") {
      drop(raw, "empty_title");
      continue;
    }
    const title = raw.title.trim();
    if (title.length === 0) {
      drop(raw, "empty_title");
      continue;
    }
    if ([...title].length > TITLE_MAX_CODE_POINTS) {
      drop(raw, "title_too_long");
      continue;
    }
    if (
      raw.start_unit_id === null ||
      raw.end_unit_id === null ||
      !unitOrder.has(raw.start_unit_id) ||
      !unitOrder.has(raw.end_unit_id)
    ) {
      drop(raw, "missing_unit");
      continue;
    }
    const startOrder = unitOrder.get(raw.start_unit_id)!;
    const endOrder = unitOrder.get(raw.end_unit_id)!;
    if (startOrder > endOrder) {
      drop(raw, "inverted_range");
      continue;
    }
    if (parentNode !== undefined) {
      const parentStart = unitOrder.get(parentNode.start_unit_id)!;
      const parentEnd = unitOrder.get(parentNode.end_unit_id)!;
      if (startOrder < parentStart || endOrder > parentEnd) {
        drop(raw, "range_not_nested");
        continue;
      }
    }
    const overlapsSibling = accepted.some(
      (node) =>
        node.parent_node_id === parent &&
        !(
          endOrder < unitOrder.get(node.start_unit_id)! ||
          startOrder > unitOrder.get(node.end_unit_id)!
        ),
    );
    if (overlapsSibling) {
      drop(raw, "range_overlap");
      continue;
    }
    let startAnchor = raw.start_anchor_segment_id;
    let endAnchor = raw.end_anchor_segment_id;
    let invalidAnchor = false;
    if (startAnchor !== null && anchorUnits.get(startAnchor) !== raw.start_unit_id) {
      startAnchor = null;
      invalidAnchor = true;
    }
    if (endAnchor !== null && anchorUnits.get(endAnchor) !== raw.end_unit_id) {
      endAnchor = null;
      invalidAnchor = true;
    }
    if (invalidAnchor) drops.push({ node_id: raw.node_id, reason_code: "invalid_anchor" });
    const node: ValidatedSemanticOutlineNode = {
      node_id: raw.node_id,
      parent_node_id: parent,
      depth: raw.depth,
      title,
      start_unit_id: raw.start_unit_id,
      end_unit_id: raw.end_unit_id,
      start_anchor_segment_id: startAnchor,
      end_anchor_segment_id: endAnchor,
      order_index: accepted.length + 1,
    };
    accepted.push(node);
    acceptedById.set(node.node_id, node);
  }

  const attempted = input.attempted_nodes.length;
  const valid = accepted.length;
  const status: SemanticOutlineStatus =
    attempted === 0 || valid === 0 ? "failed" : valid === attempted ? "ready" : "partial";
  return {
    status,
    nodes: accepted,
    diagnostics: { drops, skipped_node_count: attempted - valid },
  };
}

// ---------------------------------------------------------------------------
// Snapshot wire consumer helpers (null | undefined | object)
// ---------------------------------------------------------------------------

/**
 * Trusted product projection only when a non-null object with ready|partial.
 * `undefined` (legacy omit) and `null` (new backend) are equivalent: no outline.
 * Other status objects are defensively ignored (must not throw or drive UI).
 */
export function hasTrustedSemanticOutline(
  value: ReaderSemanticOutlineProjectionDto | null | undefined,
): value is ReaderSemanticOutlineProjectionDto {
  if (value === null || value === undefined) {
    return false;
  }
  if (typeof value !== "object") {
    return false;
  }
  return value.status === "ready" || value.status === "partial";
}

/**
 * Source-identity key for any future L2 outline cache (same formula as L0/L1).
 * Pure helper only — it does not implement L2 state.
 */
export function buildOutlineSourceIdentityKey(
  baseId: string,
  generation: number,
): string {
  return `${baseId}:${generation}`;
}
