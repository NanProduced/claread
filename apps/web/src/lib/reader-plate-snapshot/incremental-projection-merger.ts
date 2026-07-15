/**
 * T4.2a-PUX-R4-R2 / R2.1E: Interaction-stable incremental projection merger.
 *
 * Pure function that attempts a targeted Plate tree update for O4-legitimate
 * representation events (G1/G2/G3) and same-topology `layer_published`
 * revisions (R2.1E), avoiding `editor.tf.setValue()` full DOM rebuild. When
 * the merge is not safe, it returns `fallback_full_reload` so the caller can
 * fall back to the existing full-reload path.
 *
 * Design reference:
 * C:\tmp\TMP-t4.2a-pux-r4-r1-interaction-stable-projection-design-2026-07-13.md
 * C:\tmp\TMP-t4.2a-pux-r4-r2-1a-layer-incremental-audit-2026-07-14.md
 *
 * Key principles:
 * - Supports `projection_ops` / `record_state_changed` representation payloads
 *   that pass the O4-R2-D classifier (G1/G2/G3).
 * - R2.1E: supports `layer_published` events ONLY for same-topology revisions
 *   (changed-block-only replace). First-time publish, block insert/remove/
 *   reorder, identity missing, fence mismatch, mixed batch → all fallback.
 * - Unknown events, missing/invalid payload, generation/base fence mismatch,
 *   target not locatable, unsupported operation, path failure, projection
 *   structure change → all fallback. No speculative diff.
 * - Batch semantics: all events in the trigger batch must be representation
 *   events with matching fence, OR all must be `layer_published` events with
 *   matching fence. Mixed batches fallback. If any event fails, the whole
 *   batch fallbacks.
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
  type: "replace" | "remove" | "insert";
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
 * R2.1E: `layer_published` payload schema (v1).
 *
 * Backend writers (6发射点 in layer_publisher.py + grammar_window_publisher.py)
 * share these 7 base fields. grammar_window path adds source/plan_id/window_id
 * which are ignored by the merger (not needed for topology check).
 *
 * See: C:\tmp\TMP-t4.2a-pux-r4-r2-1a-layer-incremental-audit-2026-07-14.md §1.3
 */
type LayerPublishedLayerType =
  | "translation"
  | "vocabulary"
  | "grammar_note"
  | "sentence_analysis";

const ALLOWED_LAYER_PUBLISHED_LAYER_TYPES: ReadonlySet<LayerPublishedLayerType> =
  new Set<LayerPublishedLayerType>([
    "translation",
    "vocabulary",
    "grammar_note",
    "sentence_analysis",
  ]);

interface LayerPublishedPayload {
  record_id: string;
  base_id: string;
  layer_id: string;
  layer_type: LayerPublishedLayerType;
  target_scope: string;
  target_key: string;
  generation: number;
  operation?: string;
  insertions?: unknown;
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
// R2.1E: layer_published payload parsing and validation
// ---------------------------------------------------------------------------

/**
 * Parse a `layer_published` payload from an event.
 * Returns null if the payload is missing, not an object, or missing required
 * fields. Extra fields (source, plan_id, window_id from grammar_window path)
 * are tolerated and ignored.
 *
 * Note: target_scope is parsed as-is (not constrained to "unit"); the
 * `unsupported_target_scope` check is done by `validateLayerPublishedPayload`
 * so the caller gets a specific fallback reason.
 */
function parseLayerPublishedPayload(
  event: ReaderEventResponseDto,
): LayerPublishedPayload | null {
  const payload = event.payload;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const p = payload as Record<string, unknown>;
  if (
    typeof p.record_id !== "string" ||
    typeof p.base_id !== "string" ||
    typeof p.layer_id !== "string" ||
    typeof p.layer_type !== "string" ||
    typeof p.target_scope !== "string" ||
    typeof p.target_key !== "string" ||
    typeof p.generation !== "number"
  ) {
    return null;
  }
  if (!ALLOWED_LAYER_PUBLISHED_LAYER_TYPES.has(p.layer_type as LayerPublishedLayerType)) {
    return null;
  }
  if (p.target_key.length === 0) {
    return null;
  }
  if (!Number.isFinite(p.generation) || p.generation < 1) {
    return null;
  }
  if (p.base_id.length === 0) {
    return null;
  }
  if (p.record_id.length === 0) {
    return null;
  }
  return {
    record_id: p.record_id,
    base_id: p.base_id,
    layer_id: p.layer_id,
    layer_type: p.layer_type as LayerPublishedLayerType,
    target_scope: p.target_scope,
    target_key: p.target_key,
    generation: p.generation,
    operation: typeof p.operation === "string" ? p.operation : undefined,
    insertions: p.insertions,
  };
}

/**
 * Validate a `layer_published` payload against the R2.1E contract.
 * Returns an error reason string if invalid, or null if valid.
 *
 * Checks:
 * - target_scope must be "unit"
 * - record_id must match prevSnapshot.record_id and nextSnapshot.record_id
 * - generation and base_id must match snapshotFence (when fence is present)
 */
function validateLayerPublishedPayload(
  payload: LayerPublishedPayload,
  prevSnapshot: ReaderPlateSnapshotDto,
  nextSnapshot: ReaderPlateSnapshotDto,
  snapshotFence: SnapshotFenceContext | null,
): string | null {
  if (payload.target_scope !== "unit") {
    return "unsupported_target_scope";
  }
  // Record identity check (audit §4.5: record identity is part of the fence).
  if (
    payload.record_id !== prevSnapshot.record_id ||
    payload.record_id !== nextSnapshot.record_id
  ) {
    return "record_mismatch";
  }
  // Fence check.
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
  const inserts = operations
    .filter((operation) => operation.type === "insert")
    .sort((left, right) => comparePathsDescending(left.path, right.path));
  const removals = operations
    .filter((operation) => operation.type === "remove")
    .sort((left, right) => comparePathsDescending(left.path, right.path));
  return [...replacements, ...inserts, ...removals];
}

// ---------------------------------------------------------------------------
// R2.1E: layer_published changed-block-only apply
// ---------------------------------------------------------------------------

interface PlateBlockLike {
  id?: unknown;
  type?: unknown;
  variant?: unknown;
  icon?: unknown;
  children?: unknown;
  data?: unknown;
}

/**
 * Deterministic semantic equality check for two Plate blocks of the same
 * stable ID. Returns true only when the projected render-relevant fields are
 * identical: type, variant, icon, children (deep), data (deep).
 *
 * This is NOT a general-purpose tree diff. It only runs on same-ID blocks
 * within a single unit, after the topology check has confirmed both slices
 * have identical block_id sequences.
 *
 * We use JSON.stringify with sorted keys for determinism. This is safe because
 * the input is already a projected Plate block (no circular references, no
 * functions). The audit (§5.3) explicitly requires comparing children, marks,
 * variant, icon, data, and order — all of which are captured by stringify.
 */
function blocksAreSemanticallyEqual(
  prev: PlateBlockLike,
  next: PlateBlockLike,
): boolean {
  // Fast path: type / variant / icon short-circuit.
  if (prev.type !== next.type) return false;
  if (prev.variant !== next.variant) return false;
  if (prev.icon !== next.icon) return false;
  // Deep compare children + data via sorted-key JSON.
  // Sorted keys ensure deterministic comparison regardless of property order.
  return (
    stableJsonStringify(stripEditorGeneratedChildIds(prev.children)) ===
      stableJsonStringify(stripEditorGeneratedChildIds(next.children)) &&
    stableJsonStringify(prev.data) === stableJsonStringify(next.data)
  );
}

/** Plate assigns opaque ids to nested editor nodes during normalization. Root
 * block ids remain checked by topology; nested ids are editor-local. */
function stripEditorGeneratedChildIds(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripEditorGeneratedChildIds);
  if (value === null || typeof value !== "object") return value;
  const result: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (key !== "id") result[key] = stripEditorGeneratedChildIds(child);
  }
  return result;
}
/**
 * JSON.stringify with sorted object keys for deterministic comparison.
 * Falls back to JSON.stringify for non-object values. Handles arrays by
 * preserving order (array order is semantically significant in Plate children).
 */
function stableJsonStringify(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(stableJsonStringify).join(",")}]`;
  }
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  const pairs = keys.map((k) => `${JSON.stringify(k)}:${stableJsonStringify(obj[k])}`);
  return `{${pairs.join(",")}}`;
}

/**
 * Extract the `data.unitId` from a top-level Plate block. Returns null if the
 * block has no `data` object or no `unitId` string field.
 *
 * All projected Reader blocks (paragraph, blockquote, callout, sentence_analysis)
 * carry `data.unitId` — see reader-record-plate-document.ts L1038, L1083, L1123,
 * L1155, L1236.
 */
function extractUnitIdFromBlock(node: Descendant): string | null {
  const block = node as unknown as PlateBlockLike;
  if (!block.data || typeof block.data !== "object") {
    return null;
  }
  const data = block.data as { unitId?: unknown };
  if (typeof data.unitId !== "string" || data.unitId.length === 0) {
    return null;
  }
  return data.unitId;
}

/**
 * Extract the stable `id` from a top-level Plate block. Returns null if the
 * block has no string `id`. This is the contract every projected Reader block
 * must satisfy; missing id triggers fallback.
 */
function extractBlockId(node: Descendant): string | null {
  const block = node as unknown as PlateBlockLike;
  if (typeof block.id !== "string" || block.id.length === 0) {
    return null;
  }
  return block.id;
}

/**
 * Build a per-unit slice of top-level blocks from children.
 * Returns a map from unitId → array of { path, blockId (may be null), node }.
 *
 * Blocks with `data.unitId` but no stable `id` are INCLUDED with blockId=null
 * so the topology check can detect `unit_block_identity_missing`. Blocks
 * without `data.unitId` are skipped (they're not part of any unit, e.g.,
 * standalone content_summary).
 */
function buildUnitBlockSlices(
  children: Descendant[],
): Map<string, Array<{ path: number; blockId: string | null; node: Descendant }>> {
  const slices = new Map<string, Array<{ path: number; blockId: string | null; node: Descendant }>>();
  for (let i = 0; i < children.length; i++) {
    const node = children[i] as Descendant;
    const unitId = extractUnitIdFromBlock(node);
    // Skip blocks without unitId — they're not part of any unit.
    if (!unitId) continue;
    const blockId = extractBlockId(node);
    // Include blocks with null blockId so the topology check can detect
    // identity_missing fallback.
    const slice = slices.get(unitId) ?? [];
    slice.push({ path: i, blockId, node });
    slices.set(unitId, slice);
  }
  return slices;
}

interface GrammarInsertionDescriptor { unit_id: string; anchor_segment_id: string; kind: string; layer_id: string; item_ids: string[]; }

function extractGrammarItemIdsFromGroupChildren(children: unknown): string[] | null {
  if (!Array.isArray(children) || children.length === 0) return null;
  const ids: string[] = [];
  for (const child of children) {
    if (!child || typeof child !== "object") return null;
    const data = (child as { data?: unknown }).data;
    const itemId = data && typeof data === "object" ? (data as { itemId?: unknown }).itemId : undefined;
    if (typeof itemId !== "string" || itemId.length === 0) return null;
    ids.push(itemId);
  }
  return ids;
}

function normalizeParagraphWithoutDeclaredGrammarMarks(node: Descendant, expected: Set<string>): { node: Descendant; observed: Set<string> } | null {
  const block = node as unknown as PlateBlockLike;
  if (!Array.isArray(block.children)) return null;
  const observed = new Set<string>();
  const children: unknown[] = [];
  for (const child of block.children) {
    if (!child || typeof child !== "object") return null;
    const leaf = child as Record<string, unknown>;
    const data = leaf.grammar_data;
    if (leaf.grammar === true || data !== undefined) {
      const itemId = data && typeof data === "object" ? (data as { itemId?: unknown }).itemId : undefined;
      if (typeof itemId !== "string" || !expected.has(itemId)) return null;
      observed.add(itemId);
      const clean = { ...leaf }; delete clean.grammar; delete clean.grammar_data; children.push(clean);
    } else children.push(leaf);
  }
  const merged: unknown[] = [];
  for (const child of children) {
    const previous = merged[merged.length - 1];
    if (previous && child && typeof previous === "object" && typeof child === "object" && typeof (previous as { text?: unknown }).text === "string" && typeof (child as { text?: unknown }).text === "string") {
      const previousProps = { ...(previous as Record<string, unknown>) }; delete previousProps.text;
      const childProps = { ...(child as Record<string, unknown>) }; delete childProps.text;
      if (stableJsonStringify(previousProps) === stableJsonStringify(childProps)) {
        merged[merged.length - 1] = { ...(previous as Record<string, unknown>), text: (previous as { text: string }).text + (child as { text: string }).text };
        continue;
      }
    }
    merged.push(child);
  }
  return { node: { ...(block as Record<string, unknown>), children: merged } as unknown as Descendant, observed };
}

function parseGrammarInsertionDescriptors(payload: LayerPublishedPayload): GrammarInsertionDescriptor[] | null {
  if (payload.layer_type !== "grammar_note" || payload.operation !== "insert_after_anchor" || !Array.isArray(payload.insertions)) return null;
  const result: GrammarInsertionDescriptor[] = [];
  for (const raw of payload.insertions) {
    if (!raw || typeof raw !== "object") return null;
    const value = raw as Record<string, unknown>;
    if (typeof value.unit_id !== "string" || typeof value.anchor_segment_id !== "string" || value.kind !== "grammar_note" || typeof value.layer_id !== "string" || !Array.isArray(value.item_ids) || value.item_ids.length === 0 || value.item_ids.some((id) => typeof id !== "string" || id.length === 0)) return null;
    result.push({ unit_id: value.unit_id, anchor_segment_id: value.anchor_segment_id, kind: "grammar_note", layer_id: value.layer_id, item_ids: value.item_ids as string[] });
  }
  return result.length > 0 ? result : null;
}

function mergeGrammarFirstPublish(payload: LayerPublishedPayload, prevChildren: Descendant[], nextChildren: Descendant[]): IncrementalProjectionResult {
  const descriptors = parseGrammarInsertionDescriptors(payload);
  if (!descriptors) return { kind: "fallback_full_reload", reason: "grammar_first_publish_unsupported_layer_type" };
  const targetUnit = payload.target_key;
  const seen = new Set<string>(); const byParagraph = new Map<string, GrammarInsertionDescriptor>();
  const groupIds = new Set<string>(); const inserts: TargetedApplyOperation[] = [];
  for (const descriptor of descriptors) {
    if (descriptor.unit_id !== targetUnit) return { kind: "fallback_full_reload", reason: "grammar_first_publish_cross_unit" };
    if (descriptor.layer_id !== payload.layer_id) return { kind: "fallback_full_reload", reason: "grammar_first_publish_layer_id_mismatch" };
    if (seen.has(descriptor.anchor_segment_id)) return { kind: "fallback_full_reload", reason: "grammar_first_publish_duplicate_anchor_descriptor" };
    seen.add(descriptor.anchor_segment_id);
    const paragraphId = `paragraph:${descriptor.anchor_segment_id}`;
    const prevParagraph = findTopLevelBlockIndex(prevChildren, paragraphId); const nextParagraph = findTopLevelBlockIndex(nextChildren, paragraphId);
    if (prevParagraph < 0 || nextParagraph < 0) return { kind: "fallback_full_reload", reason: "grammar_first_publish_anchor_not_found" };
    const groupId = `callout-group:${descriptor.unit_id}:${descriptor.anchor_segment_id}`; const nextGroup = findTopLevelBlockIndex(nextChildren, groupId);
    if (nextGroup < 0) return { kind: "fallback_full_reload", reason: "grammar_first_publish_group_not_in_next" };
    if (findTopLevelBlockIndex(prevChildren, groupId) >= 0) return { kind: "fallback_full_reload", reason: "grammar_first_publish_group_already_in_prev" };
    if (nextGroup !== nextParagraph + 1) return { kind: "fallback_full_reload", reason: "grammar_first_publish_canonical_order_mismatch" };
    const group = nextChildren[nextGroup] as unknown as PlateBlockLike; const groupData = group.data as { unitId?: unknown; anchorSegmentId?: unknown } | undefined;
    if (!groupData || groupData.unitId !== descriptor.unit_id || groupData.anchorSegmentId !== descriptor.anchor_segment_id) return { kind: "fallback_full_reload", reason: "grammar_first_publish_group_identity_mismatch" };
    const itemIds = extractGrammarItemIdsFromGroupChildren(group.children); if (!itemIds) return { kind: "fallback_full_reload", reason: "grammar_first_publish_item_identity_missing" };
    const expected = new Set(descriptor.item_ids); const actual = new Set(itemIds);
    if (expected.size !== descriptor.item_ids.length || expected.size !== actual.size || descriptor.item_ids.some((id) => !actual.has(id))) return { kind: "fallback_full_reload", reason: "grammar_first_publish_item_ids_mismatch" };
    groupIds.add(groupId); byParagraph.set(paragraphId, descriptor); inserts.push({ path: [prevParagraph + 1], blockId: groupId, type: "insert", nodes: [nextChildren[nextGroup]!] });
  }
  const filtered = nextChildren.filter((node) => { const id = extractBlockId(node); return id === null || !groupIds.has(id); });
  if (filtered.length !== prevChildren.length) {
    return { kind: "fallback_full_reload", reason: "unrepresented_projection_change" };
  }
  const replaces: TargetedApplyOperation[] = [];
  for (let i = 0; i < prevChildren.length; i += 1) {
    const prev = prevChildren[i]!; const next = filtered[i]!; const prevId = extractBlockId(prev); const nextId = extractBlockId(next);
    if (!prevId || prevId !== nextId) {
      return { kind: "fallback_full_reload", reason: "unrepresented_projection_change" };
    }
    const descriptor = byParagraph.get(prevId);
    if (!descriptor) {
      if (!blocksAreSemanticallyEqual(prev as unknown as PlateBlockLike, next as unknown as PlateBlockLike)) {
        return { kind: "fallback_full_reload", reason: "unrepresented_projection_change" };
      }
      continue;
    }
    const normalized = normalizeParagraphWithoutDeclaredGrammarMarks(next, new Set(descriptor.item_ids));
    if (!normalized || !blocksAreSemanticallyEqual(prev as unknown as PlateBlockLike, normalized.node as unknown as PlateBlockLike)) return { kind: "fallback_full_reload", reason: "grammar_first_publish_paragraph_mutation_mismatch" };
    if (normalized.observed.size > 0) replaces.push({ path: [i], blockId: prevId, type: "replace", nodes: [next] });
  }
  return { kind: "targeted_apply", operations: orderOperationsForApplication([...replaces, ...inserts]), preservedInteraction: { preserveSelection: true, preserveScroll: true, preserveGrammarAccordion: true, preserveQuickPeek: true, preservePanels: true }, affectedTargetKeys: [targetUnit] };
}
/**
 * R2.1E: attempt a changed-block-only apply for `layer_published` events.
 *
 * Contract (audit §5.3 + P1-A unrepresented-change guard):
 * 1. P1-A: Verify the ENTIRE projection has no unrepresented changes outside
 *    the event's target units. If any non-target block differs (content,
 *    block_id, unitId, or projection length mismatch), fallback immediately.
 *    This prevents the cursor from advancing past an unrepresented change
 *    (e.g. unit B published between poll and snapshot fetch while the event
 *    only carries unit A).
 * 2. For each deduplicated (unit_id, layer_type), take all top-level blocks
 *    with `data.unitId === unit_id` from prevChildren and nextChildren.
 * 3. Both slices must be non-empty, and the stable `block_id` sequence must
 *    be identical element-by-element. Otherwise fallback.
 * 4. For each same-ID block pair, run deterministic semantic comparison. Only
 *    changed blocks produce a replaceNodes operation.
 * 5. All replacements preserve sibling path; apply in ascending path order.
 * 6. Any insert/remove/reorder/identity-missing/comparison-unsafe → fallback.
 *
 * Returns `targeted_apply` on success, or `fallback_full_reload` with a
 * specific reason on any failure.
 */
function mergeLayerPublishedChangedBlocks(
  prevChildren: Descendant[],
  nextChildren: Descendant[],
  targetUnitIds: string[],
): IncrementalProjectionResult {
  const targetUnitSet = new Set(targetUnitIds);

  // -------------------------------------------------------------------------
  // P1-A: Full projection unrepresented-change guard.
  //
  // If poll returns a unit A event, but unit B was published between poll and
  // snapshot fetch, the next snapshot already contains B's changes. Without
  // this guard, the merger would only replace A's blocks, accept the new
  // snapshot/cursor, and B's event would be skipped by the cursor — leaving
  // the UI permanently stuck on stale B.
  //
  // Guard: every block OUTSIDE the target units must be semantically
  // identical between prev and next. Any difference → fallback_full_reload.
  // -------------------------------------------------------------------------

  // P1-A.1: Projection length must match.
  if (prevChildren.length !== nextChildren.length) {
    // Distinguish: if a target unit's block count changed, preserve the
    // existing `unit_block_set_changed` reason for audit clarity. Otherwise
    // the length mismatch is an unrepresented structural change.
    const prevSlicesForLenCheck = buildUnitBlockSlices(prevChildren);
    const nextSlicesForLenCheck = buildUnitBlockSlices(nextChildren);
    for (const unitId of targetUnitIds) {
      const prevLen = prevSlicesForLenCheck.get(unitId)?.length ?? 0;
      const nextLen = nextSlicesForLenCheck.get(unitId)?.length ?? 0;
      if (prevLen !== nextLen) {
        return { kind: "fallback_full_reload", reason: "unit_block_set_changed" };
      }
    }
    return {
      kind: "fallback_full_reload",
      reason: "unrepresented_projection_change",
    };
  }

  // P1-A.2: Position-by-position verification of non-target blocks.
  for (let i = 0; i < prevChildren.length; i++) {
    const prevUnitId = extractUnitIdFromBlock(prevChildren[i]!);
    const nextUnitId = extractUnitIdFromBlock(nextChildren[i]!);
    const prevBlockId = extractBlockId(prevChildren[i]!);
    const nextBlockId = extractBlockId(nextChildren[i]!);

    // unitId must not change at any position.
    if (prevUnitId !== nextUnitId) {
      return {
        kind: "fallback_full_reload",
        reason: "unrepresented_projection_change",
      };
    }

    // blockId must not change at any position.
    if (prevBlockId !== nextBlockId) {
      const isTarget =
        prevUnitId !== null && targetUnitSet.has(prevUnitId);
      if (!isTarget) {
        // Non-target block_id changed → unrepresented change.
        return {
          kind: "fallback_full_reload",
          reason: "unrepresented_projection_change",
        };
      }
      // Target unit block_id change → handled by per-unit topology check
      // below (distinguishes set_changed vs order_changed).
    }

    // Non-target block content must be semantically identical.
    const isTargetBlock =
      prevUnitId !== null && targetUnitSet.has(prevUnitId);
    if (!isTargetBlock) {
      const prevNode = prevChildren[i]! as unknown as PlateBlockLike;
      const nextNode = nextChildren[i]! as unknown as PlateBlockLike;
      if (!blocksAreSemanticallyEqual(prevNode, nextNode)) {
        return {
          kind: "fallback_full_reload",
          reason: "unrepresented_change_in_non_target_block",
        };
      }
    }
  }

  // -------------------------------------------------------------------------
  // Per-unit topology check (existing logic, unchanged).
  // -------------------------------------------------------------------------
  const prevSlices = buildUnitBlockSlices(prevChildren);
  const nextSlices = buildUnitBlockSlices(nextChildren);

  const operations: TargetedApplyOperation[] = [];
  const plannedPaths = new Set<string>();

  for (const unitId of targetUnitIds) {
    const prevSlice = prevSlices.get(unitId);
    const nextSlice = nextSlices.get(unitId);

    // unit_not_found: no blocks in prev or next for this unit_id.
    if (!prevSlice || prevSlice.length === 0 || !nextSlice || nextSlice.length === 0) {
      return { kind: "fallback_full_reload", reason: "unit_not_found" };
    }

    // unit_block_set_changed: different number of blocks → set changed.
    if (prevSlice.length !== nextSlice.length) {
      return { kind: "fallback_full_reload", reason: "unit_block_set_changed" };
    }

    // First pass: check identity presence at every position.
    for (let i = 0; i < prevSlice.length; i++) {
      const prevBlock = prevSlice[i]!;
      const nextBlock = nextSlice[i]!;
      if (prevBlock.blockId === null || nextBlock.blockId === null) {
        return { kind: "fallback_full_reload", reason: "unit_block_identity_missing" };
      }
    }

    // Second pass: check block_id sequence equality.
    for (let i = 0; i < prevSlice.length; i++) {
      const prevBlock = prevSlice[i]!;
      const nextBlock = nextSlice[i]!;
      if (prevBlock.blockId !== nextBlock.blockId) {
        // Different block_id at the same position → could be reorder or set
        // change. Distinguish by checking set equality.
        const prevSet = new Set(prevSlice.map((b) => b.blockId));
        const nextSet = new Set(nextSlice.map((b) => b.blockId));
        if (prevSet.size !== nextSet.size) {
          return { kind: "fallback_full_reload", reason: "unit_block_set_changed" };
        }
        for (const id of prevSet) {
          if (!nextSet.has(id)) {
            return { kind: "fallback_full_reload", reason: "unit_block_set_changed" };
          }
        }
        // Same set, different order → reorder.
        return { kind: "fallback_full_reload", reason: "unit_block_order_changed" };
      }
    }

    // Same block_id sequence — compare each block semantically.
    for (let i = 0; i < prevSlice.length; i++) {
      const prevBlock = prevSlice[i]!;
      const nextBlock = nextSlice[i]!;
      const prevNode = prevBlock.node as unknown as PlateBlockLike;
      const nextNode = nextBlock.node as unknown as PlateBlockLike;
      const pathKey = `${prevBlock.path}`;
      if (plannedPaths.has(pathKey)) continue;

      const changed = !blocksAreSemanticallyEqual(prevNode, nextNode);
      if (changed) {
        operations.push({
          path: [prevBlock.path],
          blockId: prevBlock.blockId!,
          type: "replace",
          nodes: [nextBlock.node],
        });
        plannedPaths.add(pathKey);
      }
      // Unchanged block → no operation. Non-target DOM identity preserved.
    }
  }

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
    affectedTargetKeys: [...targetUnitIds],
  };
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

  // R2.1E: Detect whether the batch is all `layer_published` events.
  // If yes → use changed-block-only apply path.
  // If mixed (some layer_published + some representation events) → fallback.
  // If no layer_published events → use existing G1/G2/G3 representation path.
  const layerPublishedEvents = triggerEvents.filter(
    (event) => event.event_type === "layer_published",
  );
  const representationEvents = triggerEvents.filter(
    (event) =>
      event.event_type === "projection_ops" ||
      event.event_type === "record_state_changed",
  );
  const otherReliableReloadEvents = triggerEvents.filter(
    (event) =>
      event.event_type === "record_product_state_updated" ||
      event.event_type === "projection_reset_required",
  );
  const unsupportedEvents = triggerEvents.filter(
    (event) =>
      event.event_type !== "layer_published" &&
      event.event_type !== "projection_ops" &&
      event.event_type !== "record_state_changed" &&
      event.event_type !== "record_product_state_updated" &&
      event.event_type !== "projection_reset_required",
  );

  // Mixed batch: layer_published + representation events → fail-closed.
  if (layerPublishedEvents.length > 0 && representationEvents.length > 0) {
    return {
      kind: "fallback_full_reload",
      reason: "non_layer_published_in_batch",
    };
  }

  // Other reliable reload events (record_product_state_updated,
  // projection_reset_required) are not supported by either path.
  if (otherReliableReloadEvents.length > 0) {
    return {
      kind: "fallback_full_reload",
      reason: "layer_published_not_supported",
    };
  }

  // Unsupported event types (e.g., article_ready) → fail-closed.
  if (unsupportedEvents.length > 0) {
    return {
      kind: "fallback_full_reload",
      reason: "non_representation_event_in_batch",
    };
  }

  // R2.1E: layer_published-only batch → changed-block-only apply path.
  if (layerPublishedEvents.length > 0) {
    // Parse and validate each layer_published payload.
    const parsedLayerEvents: Array<{
      event: ReaderEventResponseDto;
      payload: LayerPublishedPayload;
    }> = [];
    for (const event of layerPublishedEvents) {
      const payload = parseLayerPublishedPayload(event);
      if (!payload) {
        return {
          kind: "fallback_full_reload",
          reason: "invalid_layer_published_payload",
        };
      }
      const validationError = validateLayerPublishedPayload(
        payload,
        prevSnapshot,
        nextSnapshot,
        snapshotFence,
      );
      if (validationError) {
        return { kind: "fallback_full_reload", reason: validationError };
      }
      parsedLayerEvents.push({ event, payload });
    }

    const extendedLayerEvents = parsedLayerEvents.filter(
      ({ payload }) => payload.operation !== undefined || payload.insertions !== undefined,
    );
    if (extendedLayerEvents.length > 0) {
      if (parsedLayerEvents.length !== 1) {
        return { kind: "fallback_full_reload", reason: "non_layer_published_in_batch" };
      }
      const grammarPayload = extendedLayerEvents[0]!.payload;
      if (grammarPayload.layer_type !== "grammar_note") {
        return { kind: "fallback_full_reload", reason: "grammar_first_publish_unsupported_layer_type" };
      }
      return mergeGrammarFirstPublish(grammarPayload, prevChildren, nextChildren);
    }

    // Deduplicate (unit_id, layer_type) pairs. Same unit may have multiple
    // layer_published events (e.g., translation + grammar_note); each unit
    // is processed once. Multiple events for the same (unit_id, layer_type)
    // also deduplicate to one operation set.
    const seenUnitIds = new Set<string>();
    const targetUnitIds: string[] = [];
    for (const { payload } of parsedLayerEvents) {
      const unitId = payload.target_key;
      if (!seenUnitIds.has(unitId)) {
        seenUnitIds.add(unitId);
        targetUnitIds.push(unitId);
      }
    }

    return mergeLayerPublishedChangedBlocks(
      prevChildren,
      nextChildren,
      targetUnitIds,
    );
  }

  // Representation events only → G1/G2/G3 path.
  const parsedEvents: ParsedEvent[] = [];

  for (const event of representationEvents) {
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
