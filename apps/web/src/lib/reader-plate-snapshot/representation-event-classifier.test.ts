import { describe, expect, it } from "vitest";

import {
  ALLOWED_METADATA_FIELDS,
  ALLOWED_OPERATIONS_BY_SECTION,
  ALLOWED_REPRESENTATION_SECTIONS,
  REPRESENTATION_PAYLOAD_SCHEMA_VERSION,
  RELIABLE_RELOAD_EVENT_TYPES,
  classifyReaderEvent,
  type SnapshotFenceContext,
} from "@/lib/reader-plate-snapshot/representation-event-classifier";
import type { ReaderEventResponseDto } from "@/types/api/reader-plate";

function makeEvent(overrides: Partial<ReaderEventResponseDto>): ReaderEventResponseDto {
  return {
    id: "evt_1",
    reading_record_id: "rec_1",
    sequence: 1,
    event_type: "article_ready",
    payload: {},
    created_at: "2026-06-21T00:00:00Z",
    ...overrides,
  };
}

function makeRepresentationPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: REPRESENTATION_PAYLOAD_SCHEMA_VERSION,
    representation_section: "user_assets",
    operation: "upsert",
    target_keys: ["asset_1"],
    generation: 1,
    base_id: "base_1",
    ...overrides,
  };
}

const MATCHING_FENCE: SnapshotFenceContext = { generation: 1, baseId: "base_1" };
const MISMATCH_FENCE: SnapshotFenceContext = { generation: 2, baseId: "base_other" };

// ---------------------------------------------------------------------------
// Reliable reload event types
// ---------------------------------------------------------------------------

describe("classifyReaderEvent — reliable reload event types", () => {
  it("reloads on layer_published regardless of payload", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "layer_published", payload: { layer_type: "translation" } }),
      null,
    );
    expect(result).toEqual({ kind: "reload_snapshot", reason: "layer_published" });
  });

  it("reloads on record_product_state_updated regardless of payload", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "record_product_state_updated", payload: { product_state: "failed" } }),
      null,
    );
    expect(result).toEqual({ kind: "reload_snapshot", reason: "record_product_state_updated" });
  });

  it("reloads on projection_reset_required regardless of payload", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "projection_reset_required", payload: {} }),
      null,
    );
    expect(result).toEqual({ kind: "reload_snapshot", reason: "projection_reset_required" });
  });

  it("reloads on reliable types even with mismatched fence (payload ignored)", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "layer_published", payload: {} }),
      MISMATCH_FENCE,
    );
    expect(result.kind).toBe("reload_snapshot");
  });
});

// ---------------------------------------------------------------------------
// G1: projection_ops + user_assets
// ---------------------------------------------------------------------------

describe("classifyReaderEvent — G1 projection_ops + user_assets", () => {
  it("reloads on valid G1 upsert with matching fence", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "user_assets",
          operation: "upsert",
          target_keys: ["asset_highlight_1"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_snapshot",
      reason: "representation:user_assets:upsert",
    });
  });

  it("reloads on valid G1 delete with matching fence", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "user_assets",
          operation: "delete",
          target_keys: ["asset_note_1"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_snapshot",
      reason: "representation:user_assets:delete",
    });
  });

  it("reloads on valid G1 merge with matching fence", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "user_assets",
          operation: "merge",
          target_keys: ["asset_1", "asset_2"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_snapshot",
      reason: "representation:user_assets:merge",
    });
  });

  it("reloads on valid G1 even when fence is null (fail-safe, no snapshot loaded)", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "user_assets",
          operation: "upsert",
        }),
      }),
      null,
    );
    expect(result.kind).toBe("reload_snapshot");
  });

  it("returns reload_or_reset on G1 fence mismatch (stale base)", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "user_assets",
          operation: "upsert",
        }),
      }),
      MISMATCH_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_fence_mismatch",
    });
  });

  it("returns reload_or_reset on G1 generation mismatch only", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "user_assets",
          operation: "upsert",
          generation: 5,
          base_id: "base_1",
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_fence_mismatch",
    });
  });
});

// ---------------------------------------------------------------------------
// G2: projection_ops + ask_supplements
// ---------------------------------------------------------------------------

describe("classifyReaderEvent — G2 projection_ops + ask_supplements", () => {
  it("reloads on valid G2 upsert with matching fence", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "ask_supplements",
          operation: "upsert",
          target_keys: ["supp_1"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_snapshot",
      reason: "representation:ask_supplements:upsert",
    });
  });

  it("reloads on valid G2 delete with matching fence", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "ask_supplements",
          operation: "delete",
          target_keys: ["supp_1"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_snapshot",
      reason: "representation:ask_supplements:delete",
    });
  });

  it("reloads on valid G2 reactivate with matching fence", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "ask_supplements",
          operation: "reactivate",
          target_keys: ["supp_1"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_snapshot",
      reason: "representation:ask_supplements:reactivate",
    });
  });
});

// ---------------------------------------------------------------------------
// G3: record_state_changed + record_metadata + status_changed
// ---------------------------------------------------------------------------

describe("classifyReaderEvent — G3 record_state_changed + record_metadata", () => {
  it("reloads on valid G3 status_changed with matching fence", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "record_state_changed",
        payload: makeRepresentationPayload({
          representation_section: "record_metadata",
          operation: "status_changed",
          target_keys: ["display_title_zh"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_snapshot",
      reason: "representation:record_metadata:status_changed",
    });
  });

  it("reloads on G3 with title_generation_status target key", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "record_state_changed",
        payload: makeRepresentationPayload({
          representation_section: "record_metadata",
          operation: "status_changed",
          target_keys: ["title_generation_status"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result.kind).toBe("reload_snapshot");
  });

  it("returns reload_or_reset on G3 with disallowed metadata target key", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "record_state_changed",
        payload: makeRepresentationPayload({
          representation_section: "record_metadata",
          operation: "status_changed",
          target_keys: ["unknown_field"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_unknown_metadata_field:unknown_field",
    });
  });

  it("returns reload_or_reset on G3 with disallowed operation", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "record_state_changed",
        payload: makeRepresentationPayload({
          representation_section: "record_metadata",
          operation: "upsert",
          target_keys: ["display_title_zh"],
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result.kind).toBe("reload_or_reset");
    expect(result.reason).toContain("representation_unknown_operation");
  });
});

// ---------------------------------------------------------------------------
// projection_ops / record_state_changed without valid representation payload
// → reload_or_reset (fail-safe, never cursor_only)
// ---------------------------------------------------------------------------

describe("classifyReaderEvent — representation-capable events without valid representation payload", () => {
  it("returns reload_or_reset on projection_ops with non-representation payload (no section)", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "projection_ops", payload: { op: "something" } }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_section",
    });
  });

  it("returns reload_or_reset on record_state_changed with non-representation payload (no section)", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "record_state_changed", payload: { readiness: "article_ready" } }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_section",
    });
  });

  it("returns reload_or_reset on projection_ops with empty payload object", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "projection_ops", payload: {} }),
      null,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_section",
    });
  });

  it("returns reload_or_reset on record_state_changed with empty payload object", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "record_state_changed", payload: {} }),
      null,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_section",
    });
  });

  it("returns reload_or_reset on projection_ops with null payload", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "projection_ops", payload: null as unknown as Record<string, unknown> }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_payload",
    });
  });

  it("returns reload_or_reset on record_state_changed with null payload", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "record_state_changed", payload: null as unknown as Record<string, unknown> }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_payload",
    });
  });

  it("returns reload_or_reset on projection_ops with non-object payload", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "projection_ops", payload: "not_an_object" as unknown as Record<string, unknown> }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_payload",
    });
  });

  it("returns reload_or_reset on record_state_changed with non-object payload", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "record_state_changed", payload: 42 as unknown as Record<string, unknown> }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_payload",
    });
  });
});

// ---------------------------------------------------------------------------
// Invalid / unknown representation payloads → reload_or_reset (fail-safe)
// ---------------------------------------------------------------------------

describe("classifyReaderEvent — invalid representation payloads", () => {
  it("returns reload_or_reset on unknown schema_version", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ schema_version: 99 }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_unknown_schema:99",
    });
  });

  it("returns reload_or_reset on missing schema_version", () => {
    const payload = makeRepresentationPayload();
    delete payload.schema_version;
    const result = classifyReaderEvent(
      makeEvent({ event_type: "projection_ops", payload }),
      MATCHING_FENCE,
    );
    expect(result.kind).toBe("reload_or_reset");
    expect(result.reason).toContain("representation_unknown_schema");
  });

  it("returns reload_or_reset on unknown representation_section", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ representation_section: "unknown_section" }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_unknown_section:unknown_section",
    });
  });

  it("returns reload_or_reset on non-string representation_section", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ representation_section: 123 }),
      }),
      MATCHING_FENCE,
    );
    expect(result.kind).toBe("reload_or_reset");
  });

  it("returns reload_or_reset on unknown operation for section", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          representation_section: "user_assets",
          operation: "reactivate",
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result.kind).toBe("reload_or_reset");
    expect(result.reason).toContain("representation_unknown_operation");
  });

  it("returns reload_or_reset on missing target_keys", () => {
    const payload = makeRepresentationPayload();
    delete payload.target_keys;
    const result = classifyReaderEvent(
      makeEvent({ event_type: "projection_ops", payload }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_target_keys",
    });
  });

  it("returns reload_or_reset on empty target_keys array", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ target_keys: [] }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_missing_target_keys",
    });
  });

  it("returns reload_or_reset on non-string target_key entry", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ target_keys: [123] }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_invalid_target_key",
    });
  });

  it("returns reload_or_reset on empty string target_key", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ target_keys: [""] }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_invalid_target_key",
    });
  });

  it("returns reload_or_reset on invalid generation (non-number)", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ generation: "abc" }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_invalid_generation",
    });
  });

  it("returns reload_or_reset on invalid generation (zero)", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ generation: 0 }),
      }),
      MATCHING_FENCE,
    );
    expect(result.kind).toBe("reload_or_reset");
    expect(result.reason).toBe("representation_invalid_generation");
  });

  it("returns reload_or_reset on invalid generation (negative)", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ generation: -1 }),
      }),
      MATCHING_FENCE,
    );
    expect(result.kind).toBe("reload_or_reset");
    expect(result.reason).toBe("representation_invalid_generation");
  });

  it("returns reload_or_reset on invalid generation (NaN)", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ generation: NaN }),
      }),
      MATCHING_FENCE,
    );
    expect(result.kind).toBe("reload_or_reset");
    expect(result.reason).toBe("representation_invalid_generation");
  });

  it("returns reload_or_reset on missing base_id", () => {
    const payload = makeRepresentationPayload();
    delete payload.base_id;
    const result = classifyReaderEvent(
      makeEvent({ event_type: "projection_ops", payload }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_invalid_base_id",
    });
  });

  it("returns reload_or_reset on empty string base_id", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({ base_id: "" }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_invalid_base_id",
    });
  });

  it("returns reload_or_reset on base_id mismatch only", () => {
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload({
          generation: 1,
          base_id: "base_other",
        }),
      }),
      MATCHING_FENCE,
    );
    expect(result).toEqual({
      kind: "reload_or_reset",
      reason: "representation_fence_mismatch",
    });
  });

  it("returns reload_or_reset when fence generation is null but payload generation differs (null fence → skip check, reload)", () => {
    // When fence has null generation, the fence check is skipped (no accepted
    // snapshot known). A valid representation payload → reload_snapshot.
    const partialFence: SnapshotFenceContext = { generation: null, baseId: null };
    const result = classifyReaderEvent(
      makeEvent({
        event_type: "projection_ops",
        payload: makeRepresentationPayload(),
      }),
      partialFence,
    );
    expect(result.kind).toBe("reload_snapshot");
  });
});

// ---------------------------------------------------------------------------
// Cursor-only non-representation events (each with a documented reason)
// ---------------------------------------------------------------------------

describe("classifyReaderEvent — cursor-only non-representation events", () => {
  it("classifies article_ready as cursor_only", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "article_ready" }),
      null,
    );
    expect(result.kind).toBe("cursor_only");
    expect(result.reason).toBe("article_ready_initial_readiness_signal");
  });

  it("classifies layer_failed as cursor_only", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "layer_failed" }),
      null,
    );
    expect(result.kind).toBe("cursor_only");
    expect(result.reason).toBe("layer_failed_no_published_change");
  });

  it("classifies parsed_decision_updated as cursor_only", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "parsed_decision_updated" }),
      null,
    );
    expect(result.kind).toBe("cursor_only");
    expect(result.reason).toBe("parsed_decision_not_in_representation_contract");
  });

  it("classifies action_required as cursor_only", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "action_required" }),
      null,
    );
    expect(result.kind).toBe("cursor_only");
    expect(result.reason).toBe("action_required_user_action_not_snapshot");
  });

  it("classifies run_completed as cursor_only", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "run_completed" }),
      null,
    );
    expect(result.kind).toBe("cursor_only");
    expect(result.reason).toBe("run_completed_worker_lifecycle");
  });

  it("classifies record_superseded as cursor_only", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "record_superseded" }),
      null,
    );
    expect(result.kind).toBe("cursor_only");
    expect(result.reason).toBe("record_superseded_server_signals_reload");
  });
});

// ---------------------------------------------------------------------------
// Unknown event types → reload_or_reset (fail-safe, never cursor_only)
// ---------------------------------------------------------------------------

describe("classifyReaderEvent — unknown event types fail-safe", () => {
  it("returns reload_or_reset for an unknown event type string", () => {
    const result = classifyReaderEvent(
      makeEvent({ event_type: "some_new_event_type" as never }),
      null,
    );
    expect(result.kind).toBe("reload_or_reset");
    expect(result.reason).toBe("unknown_event_type:some_new_event_type");
  });
});

// ---------------------------------------------------------------------------
// Constants audit
// ---------------------------------------------------------------------------

describe("classifier constants audit", () => {
  it("REPRESENTATION_PAYLOAD_SCHEMA_VERSION is 1", () => {
    expect(REPRESENTATION_PAYLOAD_SCHEMA_VERSION).toBe(1);
  });

  it("ALLOWED_REPRESENTATION_SECTIONS contains exactly G1/G2/G3 sections", () => {
    expect(ALLOWED_REPRESENTATION_SECTIONS.size).toBe(3);
    expect(ALLOWED_REPRESENTATION_SECTIONS.has("user_assets")).toBe(true);
    expect(ALLOWED_REPRESENTATION_SECTIONS.has("ask_supplements")).toBe(true);
    expect(ALLOWED_REPRESENTATION_SECTIONS.has("record_metadata")).toBe(true);
  });

  it("ALLOWED_OPERATIONS_BY_SECTION mirrors backend payload v1", () => {
    expect(ALLOWED_OPERATIONS_BY_SECTION.user_assets).toEqual(
      new Set(["upsert", "delete", "merge"]),
    );
    expect(ALLOWED_OPERATIONS_BY_SECTION.ask_supplements).toEqual(
      new Set(["upsert", "delete", "reactivate"]),
    );
    expect(ALLOWED_OPERATIONS_BY_SECTION.record_metadata).toEqual(
      new Set(["status_changed"]),
    );
  });

  it("ALLOWED_METADATA_FIELDS mirrors backend payload v1", () => {
    expect(ALLOWED_METADATA_FIELDS.size).toBe(4);
    expect(ALLOWED_METADATA_FIELDS.has("display_title_zh")).toBe(true);
    expect(ALLOWED_METADATA_FIELDS.has("title_generation_status")).toBe(true);
    expect(ALLOWED_METADATA_FIELDS.has("title_generation_error_code")).toBe(true);
    expect(ALLOWED_METADATA_FIELDS.has("title_generation_error_message")).toBe(true);
  });

  it("RELIABLE_RELOAD_EVENT_TYPES contains exactly 3 unconditional reload types", () => {
    expect(RELIABLE_RELOAD_EVENT_TYPES.size).toBe(3);
    expect(RELIABLE_RELOAD_EVENT_TYPES.has("layer_published")).toBe(true);
    expect(RELIABLE_RELOAD_EVENT_TYPES.has("record_product_state_updated")).toBe(true);
    expect(RELIABLE_RELOAD_EVENT_TYPES.has("projection_reset_required")).toBe(true);
  });
});
