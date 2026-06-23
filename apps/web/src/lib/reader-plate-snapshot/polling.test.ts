import { describe, expect, it } from "vitest";

import {
  decidePollingAction,
  RELOAD_TRIGGER_EVENT_TYPES,
} from "@/lib/reader-plate-snapshot/polling";
import type {
  ReaderEventPollResponseDto,
  ReaderEventResponseDto,
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

    expect(decision).toEqual({
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

    expect(decision).toEqual({ kind: "reload", reason: "reload_required" });
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

    expect(decision).toEqual({ kind: "reload", reason: "layer_published" });
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

    expect(decision).toEqual({
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

    expect(decision).toEqual({
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

    expect(decision).toEqual({ kind: "reload", reason: "cursor_ahead_of_server" });
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

    expect(decision).toEqual({ kind: "reload", reason: "counter mismatch" });
  });

  it("RELOAD_TRIGGER_EVENT_TYPES contains reload-worthy reader events", () => {
    expect(RELOAD_TRIGGER_EVENT_TYPES.has("layer_published")).toBe(true);
    expect(RELOAD_TRIGGER_EVENT_TYPES.has("record_product_state_updated")).toBe(true);
    expect(RELOAD_TRIGGER_EVENT_TYPES.has("projection_reset_required")).toBe(true);
    expect(RELOAD_TRIGGER_EVENT_TYPES.has("article_ready")).toBe(false);
  });
});
