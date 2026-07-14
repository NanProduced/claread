/**
 * T4.2a-PUX-R4-R2: Interaction-stable incremental projection merger.
 *
 * Pure function that attempts a targeted Plate tree update for O4-legitimate
 * representation events (G1/G2/G3), avoiding `editor.tf.setValue()` full DOM
 * rebuild. When the merge is not safe, it returns `fallback_full_reload` so
 * the caller can fall back to the existing full-reload path.
 *
 * Design reference:
 * C:\tmp\TMP-t4.2a-pux-r4-r1-interaction-stable-projection-design-2026-07-13.md
 *
 * Key principles:
 * - Only supports `projection_ops` / `record_state_changed` representation
 *   payloads that pass the O4-R2-D classifier.
 * - `layer_published`, unknown events, missing/invalid payload, generation/base
 *   fence mismatch, target not locatable, unsupported operation, path failure,
 *   projection structure change → all fallback. No speculative diff.
 * - Batch semantics: all events in the trigger batch must be representation
 *   events with matching fence. If any event fails, the whole batch fallbacks.
 * - This is "targeted application on full snapshot transport", NOT fragment
 *   transport or SSE.
 */

import type { Descendant } from "platejs";

import type {
  ReaderEventResponseDto,
  ReaderPlateSnapshotDto,
  ReaderTextRangeAnchorDto,
} from "@/types/api/reader-plate";
import {
  ALLOWED_METADATA_FIELDS,
  ALLOWED_OPERATIONS_BY_SECTION,
  ALLOWED_REPRESENTATION_SECTIONS,
  REPRESENTATION_PAYLOAD_SCHEMA_VERSION,
  type SnapshotFenceContext,
} from "@/lib/reader-plate-snapshot/representation-event-classifier";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface IncrementalProjectionMergerInput {
  /** The previously accepted snapshot (before reload). */
  prevSnapshot: ReaderPlateSnapshotDto;
  /** The newly fetched snapshot (after reload). */
  nextSnapshot: ReaderPlateSnapshotDto;
  /** All events from the poll response that triggered this reload. */
  triggerEvents: ReaderEventResponseDto[];
  /** Current Plate editor children (prev, already projected + filtered + grouped). */
  prevChildren: Descendant[];
  /** Next Plate value (already projected + filtered + grouped from nextSnapshot). */
  nextChildren: Descendant[];
  /** Snapshot fence for payload validation (generation + base_id). */
  snapshotFence: SnapshotFenceContext | null;
}

export interface TargetedApplyOperation {
  /** Path in prevChildren to replace or remove. */
  path: number[];
  /** Stable block ID for audit traceability. */
  blockId: string;
  /** Operation type: replace the subtree at `path` or remove it. */
  type: "replace" | "remove";
  /** Replacement node(s) from nextChildren (only for "replace"). */
  nodes?: Descendant[];
}

export interface InteractionPreservationInfo {
  /** Whether DOM selection should be preserved (path-validity check by caller). */
  preserveSelection: boolean;
  /** Whether scroll position should be preserved. */
  preserveScroll: boolean;
  /** Whether grammar accordion expand state should be preserved. */
  preserveGrammarAccordion: boolean;
  /** Whether Quick Peek panel state should be preserved. */
  preserveQuickPeek: boolean;
  /** Whether side panels (dictionary, ask) should be preserved. */
  preservePanels: boolean;
}

export type IncrementalProjectionResult =
  | {
      kind: "targeted_apply";
      /** Targeted replace/remove operations to apply via editor.tf.replaceNodes / removeNodes. */
      operations: TargetedApplyOperation[];
      /** Declares which interaction states the caller should preserve. */
      preservedInteraction: InteractionPreservationInfo;
      /** All target_keys from trigger events (for audit). */
      affectedTargetKeys: string[];
    }
  | {
      kind: "fallback_full_reload";
      /** Machine-readable reason for the fallback. */
      reason: string;
    };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

interface PlateNodeWithId {
  id?: unknown;
  type?: unknown;
  variant?: unknown;
}

/** Find the top-level index of a block by its stable `id` property. */
function findTopLevelBlockIndex(
  children: Descendant[],
  blockId: string,
): number {
  for (let i = 0; i < children.length; i++) {
    const node = children[i] as unknown as PlateNodeWithId;
    if (node.id === blockId) {
      return i;
    }
  }
  return -1;
}

/** Find a top-level block by its stable `id` property. */
function findTopLevelBlock(
  children: Descendant[],
  blockId: string,
): Descendant | null {
  const index = findTopLevelBlockIndex(children, blockId);
  return index >= 0 ? (children[index] ?? null) : null;
}

function isTextRangeAnchor(
  anchor: unknown,
): anchor is ReaderTextRangeAnchorDto {
  return (
    anchor !== null &&
    typeof anchor === "object" &&
    "anchor_type" in anchor &&
    (anchor as { anchor_type: unknown }).anchor_type === "text_range"
  );
}

/**
 * Find the anchor_segment_id for a user asset.
 * Searches both nextSnapshot (for upsert/merge) and prevSnapshot (for delete,
 * where the asset may have been removed from the array or marked deleted_at).
 */
function findUserAssetAnchorSegmentId(
  prevSnapshot: ReaderPlateSnapshotDto,
  nextSnapshot: ReaderPlateSnapshotDto,
  assetId: string,
): string | null {
  // Check next snapshot first (upsert/merge case).
  const nextAsset = nextSnapshot.user_assets.find(
    (a) => a.asset_id === assetId,
  );
  if (nextAsset && isTextRangeAnchor(nextAsset.anchor)) {
    return nextAsset.anchor.anchor_segment_id;
  }

  // Check prev snapshot (delete case — asset may be gone from next or marked deleted_at).
  const prevAsset = prevSnapshot.user_assets.find(
    (a) => a.asset_id === assetId,
  );
  if (prevAsset && isTextRangeAnchor(prevAsset.anchor)) {
    return prevAsset.anchor.anchor_segment_id;
  }

  return null;
}

// ---------------------------------------------------------------------------
// Payload validation
// ---------------------------------------------------------------------------

interface RepresentationPayload {
  schema_version: number;
  representation_section: string;
  operation: string;
  target_keys: string[];
  generation: number;
  base_id: string;
}

/**
 * Parse a representation payload from an event.
 * Returns null if the payload is missing or not a valid object with the
 * expected fields.
 */
function parseRepresentationPayload(
  event: ReaderEventResponseDto,
): RepresentationPayload | null {
  const payload = event.payload;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const p = payload as Record<string, unknown>;
  if (
    typeof p.schema_version !== "number" ||
    typeof p.representation_section !== "string" ||
    typeof p.operation !== "string" ||
    !Array.isArray(p.target_keys) ||
    typeof p.generation !== "number" ||
    typeof p.base_id !== "string"
  ) {
    return null;
  }
  return {
    schema_version: p.schema_version,
    representation_section: p.representation_section,
    operation: p.operation,
    target_keys: p.target_keys as string[],
    generation: p.generation,
    base_id: p.base_id,
  };
}

/**
 * Validate a representation payload against the O4-R2-D contract.
 * Returns an error reason string if invalid, or null if valid.
 */
function validateRepresentationPayload(
  payload: RepresentationPayload,
  snapshotFence: SnapshotFenceContext | null,
): string | null {
  if (payload.schema_version !== REPRESENTATION_PAYLOAD_SCHEMA_VERSION) {
    return `unknown_schema_version:${payload.schema_version}`;
  }
  if (!ALLOWED_REPRESENTATION_SECTIONS.has(payload.representation_section)) {
    return `unknown_section:${payload.representation_section}`;
  }
  const allowedOps =
    ALLOWED_OPERATIONS_BY_SECTION[payload.representation_section];
  if (!allowedOps.has(payload.operation)) {
    return `unknown_operation:${payload.operation}`;
  }
  if (payload.target_keys.length === 0) {
    return "missing_target_keys";
  }
  for (const key of payload.target_keys) {
    if (typeof key !== "string" || key.length === 0) {
      return "invalid_target_key";
    }
  }
  if (payload.representation_section === "record_metadata") {
    for (const key of payload.target_keys) {
      if (!ALLOWED_METADATA_FIELDS.has(key)) {
        return `unknown_metadata_field:${key}`;
      }
    }
  }
  if (!Number.isFinite(payload.generation) || payload.generation < 1) {
    return "invalid_generation";
  }
  if (payload.base_id.length === 0) {
    return "invalid_base_id";
  }
  if (
    snapshotFence !== null &&
    snapshotFence.generation !== null &&
    snapshotFence.baseId !== null
  ) {
    if (
      payload.generation !== snapshotFence.generation ||
      payload.base_id !== snapshotFence.baseId
    ) {
      return "fence_mismatch_in_batch";
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Target resolution
// ---------------------------------------------------------------------------

interface ParsedEvent {
  event: ReaderEventResponseDto;
  payload: RepresentationPayload;
}

interface ResolvedTarget {
  blockId: string;
  operation: "replace" | "remove";
}

/**
 * Resolve a G1 (user_assets) target to a block ID and operation type.
 * Returns null if the target cannot be resolved (fallback).
 */
function resolveUserAssetsTarget(
  prevSnapshot: ReaderPlateSnapshotDto,
  nextSnapshot: ReaderPlateSnapshotDto,
  prevChildren: Descendant[],
  nextChildren: Descendant[],
  assetId: string,
): ResolvedTarget | null {
  const anchorSegmentId = findUserAssetAnchorSegmentId(
    prevSnapshot,
    nextSnapshot,
    assetId,
  );
  if (!anchorSegmentId) {
    return null;
  }

  const blockId = `paragraph:${anchorSegmentId}`;
  const prevIndex = findTopLevelBlockIndex(prevChildren, blockId);
  if (prevIndex < 0) {
    return null;
  }

  // The paragraph block always exists (it contains source text, not the asset
  // itself). We replace it with the re-projected version from nextChildren.
  const nextBlock = findTopLevelBlock(nextChildren, blockId);
  if (!nextBlock) {
    return null;
  }

  return { blockId, operation: "replace" };
}

/**
 * Resolve a G2 (ask_supplements) target to a block ID and operation type.
 * Returns null if the target cannot be resolved (fallback).
 */
function resolveAskSupplementsTarget(
  prevChildren: Descendant[],
  nextChildren: Descendant[],
  supplementId: string,
  eventOperation: string,
): ResolvedTarget | null {
  const blockId = `callout:supplement:${supplementId}`;
  const prevIndex = findTopLevelBlockIndex(prevChildren, blockId);
  const nextBlock = findTopLevelBlock(nextChildren, blockId);

  if (eventOperation === "delete") {
    // Delete: target must exist in prev. If not in prev, fail-closed.
    if (prevIndex < 0) {
      return null;
    }
    return { blockId, operation: "remove" };
  }

  // Upsert or reactivate: target must exist in prev (we can't determine
  // insertion position without a full diff). If in prev but not in next,
  // treat as remove (the supplement was dismissed after the event).
  if (prevIndex < 0) {
    return null;
  }
  if (!nextBlock) {
    return { blockId, operation: "remove" };
  }
  return { blockId, operation: "replace" };
}

/**
 * Sort planned operations for safe sequential Slate application.
 *
 * Every path is resolved against the same prevChildren tree. Replacements do
 * not change sibling positions, but removals do; therefore apply all removes
 * from the highest path down so a prior removal cannot invalidate a later
 * path. Paths are currently top-level, while the full comparator keeps this
 * invariant correct if a future supported target is nested.
 */
function comparePathsDescending(left: number[], right: number[]): number {
  const sharedLength = Math.min(left.length, right.length);
  for (let index = 0; index < sharedLength; index += 1) {
    if (left[index] !== right[index]) {
      return (right[index] ?? 0) - (left[index] ?? 0);
    }
  }
  return right.length - left.length;
}

function orderOperationsForApplication(
  operations: TargetedApplyOperation[],
): TargetedApplyOperation[] {
  const replacements = operations.filter((operation) => operation.type === "replace");
  const removals = operations
    .filter((operation) => operation.type === "remove")
    .sort((left, right) => comparePathsDescending(left.path, right.path));
  return [...replacements, ...removals];
}
// ---------------------------------------------------------------------------
// Main applier
// ---------------------------------------------------------------------------

/**
 * Attempt a targeted incremental projection merge.
 *
 * This function is pure: it does not mutate any input and does not perform
 * any side effects. The caller is responsible for applying the returned
 * operations to the Plate editor.
 *
 * Returns `targeted_apply` if all trigger events can be safely resolved to
 * targeted replace/remove operations. Returns `fallback_full_reload` if any
 * validation fails or any target cannot be resolved.
 */
export function mergeIncrementalProjection(
  input: IncrementalProjectionMergerInput,
): IncrementalProjectionResult {
  const {
    prevSnapshot,
    nextSnapshot,
    triggerEvents,
    prevChildren,
    nextChildren,
    snapshotFence,
  } = input;

  // --- 1. Fence validation (prev vs next snapshot) ---

  if (prevSnapshot.record.generation !== nextSnapshot.record.generation) {
    return { kind: "fallback_full_reload", reason: "generation_changed" };
  }
  if (prevSnapshot.base.base_id !== nextSnapshot.base.base_id) {
    return { kind: "fallback_full_reload", reason: "base_changed" };
  }

  // --- 2. Event validation ---

  if (triggerEvents.length === 0) {
    return { kind: "fallback_full_reload", reason: "no_trigger_events" };
  }

  const parsedEvents: ParsedEvent[] = [];

  for (const event of triggerEvents) {
    // Reject reliable reload event types (layer_published etc.)
    if (
      event.event_type === "layer_published" ||
      event.event_type === "record_product_state_updated" ||
      event.event_type === "projection_reset_required"
    ) {
      return {
        kind: "fallback_full_reload",
        reason: "layer_published_not_supported",
      };
    }

    // Only representation-capable event types are supported.
    if (
      event.event_type !== "projection_ops" &&
      event.event_type !== "record_state_changed"
    ) {
      return {
        kind: "fallback_full_reload",
        reason: "non_representation_event_in_batch",
      };
    }

    // Parse and validate representation payload.
    const payload = parseRepresentationPayload(event);
    if (!payload) {
      return { kind: "fallback_full_reload", reason: "invalid_payload" };
    }

    const validationError = validateRepresentationPayload(
      payload,
      snapshotFence,
    );
    if (validationError) {
      return { kind: "fallback_full_reload", reason: validationError };
    }

    parsedEvents.push({ event, payload });
  }

  // --- 3. Target resolution and operation planning ---

  const operations: TargetedApplyOperation[] = [];
  const affectedTargetKeys: string[] = [];
  const plannedBlockIds = new Set<string>();

  for (const { payload } of parsedEvents) {
    const section = payload.representation_section;
    const operation = payload.operation;

    for (const targetKey of payload.target_keys) {
      affectedTargetKeys.push(targetKey);

      if (section === "user_assets") {
        const resolved = resolveUserAssetsTarget(
          prevSnapshot,
          nextSnapshot,
          prevChildren,
          nextChildren,
          targetKey,
        );
        if (!resolved) {
          return {
            kind: "fallback_full_reload",
            reason: "target_not_found",
          };
        }
        if (plannedBlockIds.has(resolved.blockId)) {
          continue;
        }
        plannedBlockIds.add(resolved.blockId);
        const prevIndex = findTopLevelBlockIndex(
          prevChildren,
          resolved.blockId,
        );
        const op: TargetedApplyOperation = {
          path: [prevIndex],
          blockId: resolved.blockId,
          type: resolved.operation,
        };
        if (resolved.operation === "replace") {
          const nextBlock = findTopLevelBlock(
            nextChildren,
            resolved.blockId,
          );
          if (!nextBlock) {
            return {
              kind: "fallback_full_reload",
              reason: "target_not_found",
            };
          }
          op.nodes = [nextBlock];
        }
        operations.push(op);
      } else if (section === "ask_supplements") {
        const resolved = resolveAskSupplementsTarget(
          prevChildren,
          nextChildren,
          targetKey,
          operation,
        );
        if (!resolved) {
          return {
            kind: "fallback_full_reload",
            reason:
              operation === "delete"
                ? "delete_target_missing"
                : "target_not_found",
          };
        }
        if (plannedBlockIds.has(resolved.blockId)) {
          continue;
        }
        plannedBlockIds.add(resolved.blockId);
        const prevIndex = findTopLevelBlockIndex(
          prevChildren,
          resolved.blockId,
        );
        const op: TargetedApplyOperation = {
          path: [prevIndex],
          blockId: resolved.blockId,
          type: resolved.operation,
        };
        if (resolved.operation === "replace") {
          const nextBlock = findTopLevelBlock(
            nextChildren,
            resolved.blockId,
          );
          if (!nextBlock) {
            return {
              kind: "fallback_full_reload",
              reason: "target_not_found",
            };
          }
          op.nodes = [nextBlock];
        }
        operations.push(op);
      } else if (section === "record_metadata") {
        // G3: no Plate tree update needed — metadata is rendered outside the
        // Plate editor (page header, etc.). We just preserve all interaction.
      }
    }
  }

  // --- 4. Return result ---

  // For G3-only updates (no Plate operations), return targeted_apply with
  // empty operations. The caller should skip editor.tf.setValue entirely.
  // For G1/G2 updates, return the targeted operations.
  // For mixed G1/G2/G3, return all operations (G3 contributes none).

  return {
    kind: "targeted_apply",
    operations: orderOperationsForApplication(operations),
    preservedInteraction: {
      preserveSelection: true,
      preserveScroll: true,
      preserveGrammarAccordion: true,
      preserveQuickPeek: true,
      preservePanels: true,
    },
    affectedTargetKeys,
  };
}
