/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  decidePollingAction,
  useReaderPlatePolling,
} from "@/lib/reader-plate-snapshot/polling";
import { RELIABLE_RELOAD_EVENT_TYPES } from "@/lib/reader-plate-snapshot/representation-event-classifier";
import type {
  ReaderEventPollResponseDto,
  ReaderEventResponseDto,
  ReaderEventType,
} from "@/types/api/reader-plate";

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

function makeResponse(
  overrides: Partial<ReaderEventPollResponseDto>,
): ReaderEventPollResponseDto {
  return {
    reading_record_id: "rec_1",
    after_sequence: 0,
    next_after_sequence: 0,
    last_event_sequence: 0,
    has_more: false,
    truncated: false,
    reload_required: false,
    events: [],
    ...overrides,
  };
}

describe("decidePollingAction", () => {
  it("returns caught_up when after_sequence equals last_event_sequence and no events", () => {
    const decision = decidePollingAction({
      afterSequence: 5,
      response: makeResponse({
        after_sequence: 5,
        next_after_sequence: 5,
        last_event_sequence: 5,
        events: [],
      }),
    });

    expect(decision).toEqual({ kind: "caught_up", cursor: 5 });
  });

  it("returns caught_up when no events and after_sequence is 0 (fresh record)", () => {
    const decision = decidePollingAction({
      afterSequence: 0,
      response: makeResponse({
        after_sequence: 0,
        next_after_sequence: 0,
        last_event_sequence: 0,
        events: [],
      }),
    });

    expect(decision).toEqual({ kind: "caught_up", cursor: 0 });
  });

  it("returns reload when reload_required is true", () => {
    const decision = decidePollingAction({
      afterSequence: 3,
      response: makeResponse({
        reload_required: true,
        reload_reason: "reader event sequence gap detected",
        last_event_sequence: 10,
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "reader event sequence gap detected",
    });
  });

  it("returns reload with default reason when reload_required is true but reason is missing", () => {
    const decision = decidePollingAction({
      afterSequence: 3,
      response: makeResponse({
        reload_required: true,
        reload_reason: null,
      }),
    });

    expect(decision).toMatchObject({ kind: "reload", reason: "reload_required" });
  });

  it("returns reload when a layer_published event arrives", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "layer_published",
            payload: { layer_type: "translation" },
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({ kind: "reload", reason: "layer_published" });
  });

  it("returns reload when a projection_reset_required event arrives", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "projection_reset_required",
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "projection_reset_required",
    });
  });

  it("returns reload when a record_product_state_updated event arrives", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "record_product_state_updated",
            payload: { product_state: "failed" },
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "record_product_state_updated",
    });
  });

  it("returns advance when non-trigger events are consumed", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 3,
        last_event_sequence: 3,
        has_more: false,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "article_ready",
          }),
          makeEvent({
            sequence: 3,
            event_type: "parsed_decision_updated",
          }),
        ],
      }),
    });

    expect(decision).toEqual({ kind: "advance", cursor: 3, hasMore: false });
  });

  it("returns advance with hasMore when the response is truncated", () => {
    const decision = decidePollingAction({
      afterSequence: 0,
      response: makeResponse({
        after_sequence: 0,
        next_after_sequence: 100,
        last_event_sequence: 250,
        has_more: true,
        truncated: true,
        events: Array.from({ length: 100 }, (_, index) =>
          makeEvent({
            id: `evt_${index + 1}`,
            sequence: index + 1,
            event_type: "article_ready",
          }),
        ),
      }),
    });

    expect(decision).toEqual({ kind: "advance", cursor: 100, hasMore: true });
  });

  it("returns reload when cursor is ahead of server last_event_sequence", () => {
    const decision = decidePollingAction({
      afterSequence: 10,
      response: makeResponse({
        after_sequence: 10,
        next_after_sequence: 10,
        last_event_sequence: 5,
        events: [],
      }),
    });

    expect(decision).toMatchObject({ kind: "reload", reason: "cursor_ahead_of_server" });
  });

  it("reload_required takes precedence over layer_published events", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      response: makeResponse({
        reload_required: true,
        reload_reason: "counter mismatch",
        after_sequence: 1,
        next_after_sequence: 1,
        last_event_sequence: 5,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "layer_published",
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({ kind: "reload", reason: "counter mismatch" });
  });

  it("RELIABLE_RELOAD_EVENT_TYPES contains reload-worthy reader events", () => {
    expect(RELIABLE_RELOAD_EVENT_TYPES.has("layer_published")).toBe(true);
    expect(RELIABLE_RELOAD_EVENT_TYPES.has("record_product_state_updated")).toBe(true);
    expect(RELIABLE_RELOAD_EVENT_TYPES.has("projection_reset_required")).toBe(true);
    expect(RELIABLE_RELOAD_EVENT_TYPES.has("article_ready")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Payload-aware representation event classification.
// These tests verify that decidePollingAction delegates to the
// payload-aware classifier for representation events (G1/G2/G3) and that
// invalid payloads / fence mismatches force a reload (never cursor-only).
// ---------------------------------------------------------------------------

describe("decidePollingAction — payload-aware representation events", () => {
  const MATCHING_FENCE = { generation: 1, baseId: "base_1" };
  const MISMATCH_FENCE = { generation: 2, baseId: "base_other" };

  function makeRepresentationPayload(
    overrides: Record<string, unknown> = {},
  ): Record<string, unknown> {
    return {
      schema_version: 1,
      representation_section: "user_assets",
      operation: "upsert",
      target_keys: ["asset_1"],
      generation: 1,
      base_id: "base_1",
      ...overrides,
    };
  }

  it("reloads on G1 projection_ops + user_assets + upsert (matching fence)", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MATCHING_FENCE,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "projection_ops",
            payload: makeRepresentationPayload({
              representation_section: "user_assets",
              operation: "upsert",
            }),
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "representation:user_assets:upsert",
    });
  });

  it("reloads on G2 projection_ops + ask_supplements + reactivate", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MATCHING_FENCE,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "projection_ops",
            payload: makeRepresentationPayload({
              representation_section: "ask_supplements",
              operation: "reactivate",
              target_keys: ["supp_1"],
            }),
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "representation:ask_supplements:reactivate",
    });
  });

  it("reloads on G3 record_state_changed + record_metadata + status_changed", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MATCHING_FENCE,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "record_state_changed",
            payload: makeRepresentationPayload({
              representation_section: "record_metadata",
              operation: "status_changed",
              target_keys: ["display_title_zh"],
            }),
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "representation:record_metadata:status_changed",
    });
  });

  it("reloads on representation event with unknown schema_version (fail-safe)", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MATCHING_FENCE,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "projection_ops",
            payload: makeRepresentationPayload({ schema_version: 99 }),
          }),
        ],
      }),
    });

    expect(decision.kind).toBe("reload");
    if (decision.kind === "reload") {
      expect(decision.reason).toContain("representation_unknown_schema");
    }
  });

  it("reloads on representation event with fence mismatch (stale base)", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MISMATCH_FENCE,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "projection_ops",
            payload: makeRepresentationPayload(),
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "representation_fence_mismatch",
    });
  });

  it("reloads on representation event with missing target_keys (fail-safe)", () => {
    const payload = makeRepresentationPayload();
    delete payload.target_keys;
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MATCHING_FENCE,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "projection_ops",
            payload,
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "representation_missing_target_keys",
    });
  });

  it("reloads on legacy projection_ops without representation_section (fail-safe, never cursor_only)", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MATCHING_FENCE,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "projection_ops",
            payload: { op: "legacy_op" },
          }),
        ],
      }),
    });

    expect(decision.kind).toBe("reload");
    if (decision.kind === "reload") {
      expect(decision.reason).toBe("representation_missing_section");
    }
  });

  it("reloads on first representation event even when followed by cursor_only events", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MATCHING_FENCE,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 3,
        last_event_sequence: 3,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "article_ready",
          }),
          makeEvent({
            sequence: 3,
            event_type: "projection_ops",
            payload: makeRepresentationPayload({
              representation_section: "user_assets",
              operation: "delete",
            }),
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({
      kind: "reload",
      reason: "representation:user_assets:delete",
    });
  });

  it("reloads on unknown event type (fail-safe, never cursor_only)", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: null,
      response: makeResponse({
        after_sequence: 1,
        next_after_sequence: 2,
        last_event_sequence: 2,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "brand_new_type" as never,
          }),
        ],
      }),
    });

    expect(decision.kind).toBe("reload");
    if (decision.kind === "reload") {
      expect(decision.reason).toContain("unknown_event_type");
    }
  });

  it("representation event reload does not regress reload_required precedence", () => {
    const decision = decidePollingAction({
      afterSequence: 1,
      snapshotFence: MATCHING_FENCE,
      response: makeResponse({
        reload_required: true,
        reload_reason: "server gap",
        after_sequence: 1,
        next_after_sequence: 1,
        last_event_sequence: 5,
        events: [
          makeEvent({
            sequence: 2,
            event_type: "projection_ops",
            payload: makeRepresentationPayload(),
          }),
        ],
      }),
    });

    expect(decision).toMatchObject({ kind: "reload", reason: "server gap" });
  });
});

// ---------------------------------------------------------------------------
// Reader event payload audit.
// Enumerate every ReaderEventType and classify it into one of three tiers:
//
//   1. Unconditional reload (3 types): always force a snapshot reload
//      regardless of payload. These are the RELIABLE_RELOAD_EVENT_TYPES.
//   2. Payload-dependent (2 types): projection_ops / record_state_changed.
//      When the payload carries a valid representation_section, the
//      payload-aware classifier decides reload_snapshot / reload_or_reset.
//      Without a valid representation payload (missing/non-object payload,
//      missing section, unknown schema/operation, invalid target_keys,
//      fence mismatch), they return reload_or_reset — NEVER cursor_only.
//   3. Always cursor-only (6 types): never affect snapshot representation.
//
// This test is intentionally exhaustive so that adding a new ReaderEventType
// forces the author to decide its classification here.
// ---------------------------------------------------------------------------

const ALL_READER_EVENT_TYPES: readonly ReaderEventType[] = [
  "article_ready",
  "record_product_state_updated",
  "layer_published",
  "layer_failed",
  "parsed_decision_updated",
  "record_state_changed",
  "action_required",
  "run_completed",
  "record_superseded",
  "projection_ops",
  "projection_reset_required",
] as const;

const EXPECTED_RELOAD_TRIGGERS: readonly ReaderEventType[] = [
  "record_product_state_updated",
  "layer_published",
  "projection_reset_required",
] as const;

const PAYLOAD_DEPENDENT_TYPES: readonly ReaderEventType[] = [
  "projection_ops",
  "record_state_changed",
] as const;

const ALWAYS_CURSOR_ONLY_TYPES: readonly ReaderEventType[] = [
  "article_ready",
  "layer_failed",
  "parsed_decision_updated",
  "action_required",
  "run_completed",
  "record_superseded",
] as const;

describe("reader event reload audit", () => {
  it("every ReaderEventType is classified exactly once", () => {
    const seen = new Set(ALL_READER_EVENT_TYPES);
    expect(seen.size).toBe(ALL_READER_EVENT_TYPES.length);

    const triggers = new Set(EXPECTED_RELOAD_TRIGGERS);
    for (const eventType of ALL_READER_EVENT_TYPES) {
      // Each event type must be either an unconditional reload trigger or
      // a non-trigger (payload-dependent or cursor-only) — no event type
      // should be unclassified.
      const isTrigger = RELIABLE_RELOAD_EVENT_TYPES.has(eventType);
      const expectedTrigger = triggers.has(eventType);
      expect(isTrigger).toBe(expectedTrigger);
    }
  });

  it("exactly 3 unconditional reload-trigger event types force a full snapshot reload", () => {
    expect(RELIABLE_RELOAD_EVENT_TYPES.size).toBe(3);
    for (const eventType of EXPECTED_RELOAD_TRIGGERS) {
      expect(RELIABLE_RELOAD_EVENT_TYPES.has(eventType)).toBe(true);
    }
  });

  it("2 payload-dependent types are NOT in the unconditional reload set", () => {
    expect(PAYLOAD_DEPENDENT_TYPES).toHaveLength(2);
    for (const eventType of PAYLOAD_DEPENDENT_TYPES) {
      expect(RELIABLE_RELOAD_EVENT_TYPES.has(eventType)).toBe(false);
    }
  });

  it("6 always-cursor-only types are NOT in the unconditional reload set", () => {
    expect(ALWAYS_CURSOR_ONLY_TYPES).toHaveLength(6);
    for (const eventType of ALWAYS_CURSOR_ONLY_TYPES) {
      expect(RELIABLE_RELOAD_EVENT_TYPES.has(eventType)).toBe(false);
    }
  });

  it("unconditional + payload-dependent + cursor-only = all 11 ReaderEventTypes", () => {
    const all = new Set<ReaderEventType>([
      ...EXPECTED_RELOAD_TRIGGERS,
      ...PAYLOAD_DEPENDENT_TYPES,
      ...ALWAYS_CURSOR_ONLY_TYPES,
    ]);
    expect(all.size).toBe(ALL_READER_EVENT_TYPES.length);
    for (const eventType of ALL_READER_EVENT_TYPES) {
      expect(all.has(eventType)).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// Hook-level tests for reload cursor semantics.
// The cursor advances to `next_after_sequence` ONLY when `onReloadRequired`
// resolves to `true` (a fresh snapshot was applied). On `false` (skip /
// in-flight) or rejection, the cursor stays put so the next tick re-asks
// with the same `after_sequence` and the reload-required events are not
// silently consumed. The success path also verifies no regression to the
// duplicate-reload bug (only one reload call after the second tick).
// ---------------------------------------------------------------------------

describe("useReaderPlatePolling reload cursor semantics", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    vi.useFakeTimers();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function installFetch(eventsByCursor: Map<number, ReaderEventPollResponseDto>) {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (!url.pathname.endsWith("/events")) {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      const afterSequence = Number(url.searchParams.get("after_sequence") ?? "0");
      const payload = eventsByCursor.get(afterSequence);
      if (!payload) {
        throw new Error(`no mock for after_sequence=${afterSequence}`);
      }
      return new Response(JSON.stringify({ ok: true, ...payload }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    return fetchMock;
  }

  function makeReloadResponse(): ReaderEventPollResponseDto {
    return makeResponse({
      after_sequence: 1,
      next_after_sequence: 2,
      last_event_sequence: 2,
      events: [
        makeEvent({
          sequence: 2,
          event_type: "layer_published",
        }),
      ],
    });
  }

  it("advances cursor only when onReloadRequired resolves true (success path, no regression)", async () => {
    const onReloadRequired = vi.fn(async (): Promise<boolean> => {
      // Simulate the parent successfully applying a fresh snapshot.
      return true;
    });

    const eventsAtCursor1 = makeReloadResponse();
    const eventsAtCursor2 = makeResponse({
      after_sequence: 2,
      next_after_sequence: 2,
      last_event_sequence: 2,
      events: [],
    });
    const fetchMock = installFetch(
      new Map([
        [1, eventsAtCursor1],
        [2, eventsAtCursor2],
      ]),
    );

    const { result } = renderHook(() =>
      useReaderPlatePolling({
        recordId: "rec_1",
        initialCursor: 1,
        enabled: true,
        pollIntervalMs: 3000,
        onReloadRequired,
      }),
    );

    // First tick: fire the polling interval.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(onReloadRequired).toHaveBeenCalledTimes(1);
    expect(onReloadRequired).toHaveBeenCalledWith(
      expect.objectContaining({ reason: "layer_published" }),
    );
    // Cursor advanced to next_after_sequence (2) because reload returned true.
    expect(result.current.cursor).toBe(2);

    // Second tick: polls with the advanced cursor (2), sees no events,
    // hits caught_up — no second reload (regression check).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(onReloadRequired).toHaveBeenCalledTimes(1);
    const secondEventCall = fetchMock.mock.calls.find(
      ([input], idx) =>
        idx >= 1 && String(input).includes("/events?after_sequence=2"),
    );
    expect(secondEventCall).toBeTruthy();
  });

  it("keeps cursor when onReloadRequired resolves false (skip / in-flight)", async () => {
    const onReloadRequired = vi.fn(async (): Promise<boolean> => {
      // Simulate the parent skipping the reload (e.g. in-flight guard).
      return false;
    });

    const eventsAtCursor1 = makeReloadResponse();
    const fetchMock = installFetch(
      new Map([
        [1, eventsAtCursor1],
        // Same events still visible at cursor 1 on retry.
        [1, eventsAtCursor1],
      ]),
    );

    const { result } = renderHook(() =>
      useReaderPlatePolling({
        recordId: "rec_1",
        initialCursor: 1,
        enabled: true,
        pollIntervalMs: 3000,
        onReloadRequired,
      }),
    );

    // First tick: reload decision, onReloadRequired returns false.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(onReloadRequired).toHaveBeenCalledTimes(1);
    // Cursor NOT advanced — stays at 1 so the next tick re-asks the same
    // reload-required events.
    expect(result.current.cursor).toBe(1);

    // Second tick: re-asks with after_sequence=1 (original cursor), sees
    // the same layer_published event, triggers another reload attempt.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(onReloadRequired).toHaveBeenCalledTimes(2);
    // Still at cursor 1 — events not consumed.
    expect(result.current.cursor).toBe(1);

    // Verify the second poll used after_sequence=1 (NOT 2).
    const secondPollUrl = String(fetchMock.mock.calls[1]?.[0]);
    expect(secondPollUrl).toContain("after_sequence=1");
  });

  it("keeps cursor and surfaces error when onReloadRequired rejects", async () => {
    const onReloadRequired = vi.fn(async (): Promise<boolean> => {
      throw new Error("snapshot fetch exploded");
    });

    const eventsAtCursor1 = makeReloadResponse();
    installFetch(
      new Map([
        [1, eventsAtCursor1],
        [1, eventsAtCursor1],
      ]),
    );

    const { result } = renderHook(() =>
      useReaderPlatePolling({
        recordId: "rec_1",
        initialCursor: 1,
        enabled: true,
        pollIntervalMs: 3000,
        onReloadRequired,
      }),
    );

    // First tick: reload decision, onReloadRequired rejects.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(onReloadRequired).toHaveBeenCalledTimes(1);
    // Cursor NOT advanced — stays at 1.
    expect(result.current.cursor).toBe(1);
    // Error surfaced from the rejected reload promise.
    expect(result.current.error).toBe("snapshot fetch exploded");

    // Second tick: re-asks with after_sequence=1 (cursor kept), retries.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(onReloadRequired).toHaveBeenCalledTimes(2);
    expect(result.current.cursor).toBe(1);
  });
});
