/**
 * Payload-aware Reader Event Classifier.
 *
 * Pure function that classifies a Reader Event as:
 * - ``reload_snapshot``: event changes snapshot representation; reload required
 * - ``cursor_only``: event does not affect current snapshot; advance cursor
 * - ``reload_or_reset``: event indicates fence failure / unknown schema;
 *   reload or full reset required
 *
 * This replaces the previous static ``RELOAD_TRIGGER_EVENT_TYPES`` check
 * (which only inspected ``event_type``) with a single, tested, payload-aware
 * classifier. Representation events (G1/G2/G3) published by the backend
 * writers now reliably trigger snapshot reload instead of being silently
 * consumed as cursor-only.
 *
 * Contract:
 * docs/architecture/reader-orchestration.md
 */

import type { ReaderEventResponseDto, ReaderEventType } from "@/types/api/reader-plate";

// ---------------------------------------------------------------------------
// Constants mirroring backend representation_event_payload.py (payload v1)
// ---------------------------------------------------------------------------

export const REPRESENTATION_PAYLOAD_SCHEMA_VERSION = 1;

export const ALLOWED_REPRESENTATION_SECTIONS: ReadonlySet<string> = new Set([
  "user_assets",
  "ask_supplements",
  "record_metadata",
]);

export const ALLOWED_OPERATIONS_BY_SECTION: Readonly<Record<string, ReadonlySet<string>>> = {
  user_assets: new Set(["upsert", "delete", "merge"]),
  ask_supplements: new Set(["upsert", "delete", "reactivate"]),
  record_metadata: new Set(["status_changed"]),
};

export const ALLOWED_METADATA_FIELDS: ReadonlySet<string> = new Set([
  "display_title_zh",
  "title_generation_status",
  "title_generation_error_code",
  "title_generation_error_message",
]);

/**
 * Event types that unconditionally force a snapshot reload, regardless of
 * payload. These are the pre-existing reliable reload triggers preserved
 * from the polling slice.
 *
 * Representation events (``projection_ops`` / ``record_state_changed``) are
 * NOT here — they are classified by payload via {@link classifyReaderEvent}.
 */
export const RELIABLE_RELOAD_EVENT_TYPES: ReadonlySet<ReaderEventType> = new Set([
  "layer_published",
  "record_product_state_updated",
  "projection_reset_required",
]);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Snapshot fence context: the generation and base_id of the currently
 * accepted snapshot. Used to detect representation events from a stale base.
 *
 * When ``null``, the fence check is skipped (no snapshot loaded yet or
 * unknown) — representation events still → ``reload_snapshot`` (fail-safe).
 */
export interface SnapshotFenceContext {
  /** Generation of the currently accepted snapshot, or null. */
  generation: number | null;
  /** base_id of the currently accepted snapshot, or null. */
  baseId: string | null;
}

export type ReaderEventClassification =
  | { kind: "reload_snapshot"; reason: string }
  | { kind: "cursor_only"; reason: string }
  | { kind: "reload_or_reset"; reason: string };

// ---------------------------------------------------------------------------
// Classifier
// ---------------------------------------------------------------------------

/**
 * Classify a single Reader Event against the representation event contract.
 *
 * Decision table:
 *
 * | Event type                     | Payload                         | Classification       |
 * |--------------------------------|---------------------------------|----------------------|
 * | layer_published                | (any)                           | reload_snapshot      |
 * | record_product_state_updated   | (any)                           | reload_snapshot      |
 * | projection_reset_required      | (any)                           | reload_snapshot      |
 * | projection_ops                 | valid representation (G1/G2)    | reload_snapshot      |
 * | projection_ops                 | invalid representation          | reload_or_reset      |
 * | projection_ops                 | representation fence mismatch   | reload_or_reset      |
 * | projection_ops                 | missing/non-object payload      | reload_or_reset      |
 * | projection_ops                 | no representation_section       | reload_or_reset      |
 * | record_state_changed           | valid representation (G3)       | reload_snapshot      |
 * | record_state_changed           | invalid representation          | reload_or_reset      |
 * | record_state_changed           | representation fence mismatch   | reload_or_reset      |
 * | record_state_changed           | missing/non-object payload      | reload_or_reset      |
 * | record_state_changed           | no representation_section       | reload_or_reset      |
 * | article_ready                  | (any)                           | cursor_only          |
 * | layer_failed                   | (any)                           | cursor_only          |
 * | parsed_decision_updated        | (any)                           | cursor_only          |
 * | action_required                | (any)                           | cursor_only          |
 * | run_completed                  | (any)                           | cursor_only          |
 * | record_superseded              | (any)                           | cursor_only          |
 * | (unknown)                      | (any)                           | reload_or_reset      |
 *
 * Fail-safe principle: ``projection_ops`` and ``record_state_changed`` are
 * representation-capable event types. Any payload that is missing, non-object,
 * lacks ``representation_section``, or fails any schema/section/operation/
 * target_keys/metadata/fence validation MUST return ``reload_or_reset`` —
 * never ``cursor_only``. Advancing the cursor without applying or reloading
 * would leave a stale snapshot, violating the O4 representation event contract.
 */
export function classifyReaderEvent(
  event: ReaderEventResponseDto,
  snapshotFence: SnapshotFenceContext | null,
): ReaderEventClassification {
  // 1. Pre-existing reliable reload triggers — always reload.
  if (RELIABLE_RELOAD_EVENT_TYPES.has(event.event_type)) {
    return { kind: "reload_snapshot", reason: event.event_type };
  }

  // 2. Representation-capable events (projection_ops / record_state_changed).
  //    These event types are ALWAYS classified by the representation payload
  //    classifier. Missing/invalid payload → reload_or_reset (never cursor_only).
  if (
    event.event_type === "projection_ops" ||
    event.event_type === "record_state_changed"
  ) {
    return classifyRepresentationPayload(event, snapshotFence);
  }

  // 3. Non-representation events → cursor_only with a documented reason.
  return classifyNonRepresentationEvent(event.event_type);
}

// ---------------------------------------------------------------------------
// Representation payload classifier
// ---------------------------------------------------------------------------

/**
 * Classify a representation payload on ``projection_ops`` or
 * ``record_state_changed``.
 *
 * Always returns a classification — never ``null``. Missing/non-object payload
 * or missing ``representation_section`` returns ``reload_or_reset`` (fail-safe):
 * these event types are representation-capable, so an unclassifiable payload
 * must not silently advance the cursor.
 */
function classifyRepresentationPayload(
  event: ReaderEventResponseDto,
  snapshotFence: SnapshotFenceContext | null,
): ReaderEventClassification {
  const payload = event.payload;
  if (!payload || typeof payload !== "object") {
    return {
      kind: "reload_or_reset",
      reason: "representation_missing_payload",
    };
  }

  if (!("representation_section" in payload)) {
    // projection_ops / record_state_changed without representation_section.
    // These event types are representation-capable; an unclassifiable payload
    // must not advance the cursor without applying or reloading.
    return {
      kind: "reload_or_reset",
      reason: "representation_missing_section",
    };
  }

  // --- Validate schema_version ---
  const schemaVersion = payload.schema_version;
  if (schemaVersion !== REPRESENTATION_PAYLOAD_SCHEMA_VERSION) {
    return {
      kind: "reload_or_reset",
      reason: `representation_unknown_schema:${String(schemaVersion)}`,
    };
  }

  // --- Validate representation_section ---
  const section = payload.representation_section;
  if (typeof section !== "string" || !ALLOWED_REPRESENTATION_SECTIONS.has(section)) {
    return {
      kind: "reload_or_reset",
      reason: `representation_unknown_section:${String(section)}`,
    };
  }

  // --- Validate operation ---
  const operation = payload.operation;
  const allowedOps = ALLOWED_OPERATIONS_BY_SECTION[section];
  if (typeof operation !== "string" || !allowedOps.has(operation)) {
    return {
      kind: "reload_or_reset",
      reason: `representation_unknown_operation:${String(operation)}`,
    };
  }

  // --- Validate target_keys ---
  const targetKeys = payload.target_keys;
  if (!Array.isArray(targetKeys) || targetKeys.length === 0) {
    return {
      kind: "reload_or_reset",
      reason: "representation_missing_target_keys",
    };
  }
  for (const key of targetKeys) {
    if (typeof key !== "string" || key.length === 0) {
      return {
        kind: "reload_or_reset",
        reason: "representation_invalid_target_key",
      };
    }
  }

  // --- For record_metadata, validate target_keys against metadata allowlist ---
  if (section === "record_metadata") {
    for (const key of targetKeys) {
      if (!ALLOWED_METADATA_FIELDS.has(key)) {
        return {
          kind: "reload_or_reset",
          reason: `representation_unknown_metadata_field:${key}`,
        };
      }
    }
  }

  // --- Validate generation / base_id fence ---
  const generation = payload.generation;
  const baseId = payload.base_id;

  if (typeof generation !== "number" || !Number.isFinite(generation) || generation < 1) {
    return {
      kind: "reload_or_reset",
      reason: "representation_invalid_generation",
    };
  }
  if (typeof baseId !== "string" || baseId.length === 0) {
    return {
      kind: "reload_or_reset",
      reason: "representation_invalid_base_id",
    };
  }

  // --- Check generation/base fence against current accepted snapshot ---
  if (
    snapshotFence !== null &&
    snapshotFence.generation !== null &&
    snapshotFence.baseId !== null
  ) {
    if (generation !== snapshotFence.generation || baseId !== snapshotFence.baseId) {
      return {
        kind: "reload_or_reset",
        reason: "representation_fence_mismatch",
      };
    }
  }

  // --- Valid representation event → reload snapshot ---
  return {
    kind: "reload_snapshot",
    reason: `representation:${section}:${operation}`,
  };
}

// ---------------------------------------------------------------------------
// Non-representation event classifier
// ---------------------------------------------------------------------------

/**
 * Classify events that are never representation events.
 *
 * Each cursor-only classification includes a short reason explaining why
 * the event does not affect the current snapshot representation.
 *
 * Note: ``projection_ops`` and ``record_state_changed`` are NOT handled here
 * — they are representation-capable event types and are always classified by
 * {@link classifyRepresentationPayload} in {@link classifyReaderEvent}.
 */
function classifyNonRepresentationEvent(
  eventType: ReaderEventType,
): ReaderEventClassification {
  switch (eventType) {
    case "article_ready":
      // Initial article readiness signal. The snapshot is loaded at page
      // entry; subsequent readiness transitions are surfaced via
      // record_product_state_updated or layer_published events.
      return { kind: "cursor_only", reason: "article_ready_initial_readiness_signal" };

    case "layer_failed":
      // Layer generation failure. Does not change published snapshot
      // representation; failure is surfaced via record_product_state_updated
      // or action_required events which trigger reload independently.
      return { kind: "cursor_only", reason: "layer_failed_no_published_change" };

    case "parsed_decision_updated":
      // Parsed decision update. Not part of the G1/G2/G3 representation
      // contract; changes are picked up on the next representation-triggered
      // reload or when the server sets reload_required.
      return {
        kind: "cursor_only",
        reason: "parsed_decision_not_in_representation_contract",
      };

    case "action_required":
      // User action required (e.g. confirm candidate document). Not a
      // snapshot representation change; UI surfaces the action via a callout.
      return { kind: "cursor_only", reason: "action_required_user_action_not_snapshot" };

    case "run_completed":
      // Worker run lifecycle signal. Representation changes produced by the
      // run are covered by layer_published / record_product_state_updated.
      return { kind: "cursor_only", reason: "run_completed_worker_lifecycle" };

    case "record_superseded":
      // Record superseded by a newer generation. The server signals the
      // need for reload via reload_required or record_product_state_updated.
      return { kind: "cursor_only", reason: "record_superseded_server_signals_reload" };

    default:
      // Unknown event type — fail-safe to reload_or_reset so the client
      // never silently consumes an event it cannot classify.
      return {
        kind: "reload_or_reset",
        reason: `unknown_event_type:${eventType}`,
      };
  }
}
