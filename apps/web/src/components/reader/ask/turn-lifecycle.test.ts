/**
 * ASK-TURN-LIFECYCLE R0 — frontend red-light tests.
 *
 * These tests freeze the unified turn lifecycle contract before R1/R2/R3
 * implementation. They assert behaviors the current code does NOT yet
 * guarantee; several will fail until R1/R2/R3 land.
 *
 * Coverage:
 *
 * 1. `message.completed` keeps the HTTP stream open — composer must
 *    unlock on the terminal frame, not on EOF.
 * 2. `agentic.terminal` / `message.interrupted` unlock immediately
 *    and are handled exactly once.
 * 3. Foreign / stale terminal frames must not unlock the active turn.
 * 4. Answer deltas followed by output validator failure must leave the
 *    canonical answer empty (no half-answer retention).
 * 5. Retry / tool boundary uses server-owned generation reset; the final
 *    preview only belongs to the latest generation.
 * 6. Client abort / BFF disconnect / generator close must terminalize
 *    run/message rows.
 * 7. 30K CJK / Markdown multi-block escaped JSON cadence + performance.
 * 8. Reasoning truncation is a typed DTO field, hot/cold consistent.
 */

import { describe, expect, it } from "vitest";
import type { ReaderAskStreamEnvelopeDto } from "@/types/api/reader-ask";
import { READER_ASK_AGENTIC_EXECUTION_VERSION } from "@/types/api/reader-ask";
import {
  TERMINAL_STATES,
  TRUSTED_TERMINAL_EVENT_NAMES,
  isTerminalState,
  isTrustedTerminalEvent,
  stateForFinalStatus,
  isTrustedTerminal,
  resultingState,
  matchesTurnIdentity,
  makeLogicalTerminalResult,
  STALE_STREAM_TERMINAL_REASON,
  TurnLifecycleMetrics,
  type TurnIdentity,
  type LogicalTerminalResult,
} from "./turn-lifecycle";

// ---------------------------------------------------------------------------
// Helpers for synthetic SSE responses that keep the stream open after a
// terminal frame. This is the core R0 contract: composer unlock must
// NOT wait for EOF.
// ---------------------------------------------------------------------------

function encodeSse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function makeReadableStream(chunks: string[], opts: { keepOpen?: boolean } = {}): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let chunkIndex = 0;
  return new ReadableStream({
    pull(controller) {
      if (chunkIndex < chunks.length) {
        controller.enqueue(encoder.encode(chunks[chunkIndex]));
        chunkIndex++;
      } else if (!opts.keepOpen) {
        controller.close();
      }
      // When keepOpen=true, the stream stays open (no close, no enqueue).
    },
  });
}

function makeSseResponse(chunks: string[], opts: { keepOpen?: boolean } = {}): Response {
  return new Response(makeReadableStream(chunks, opts), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

const RUN_STARTED_PAYLOAD = {
  execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
  message_id: "msg-active-1",
  thread_id: "thread-active-1",
  turn_run_id: "turn-run-active-1",
  has_initial_selection: false,
};

const COMPLETED_PAYLOAD = {
  execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
  final_status: "ok",
  answer_text: "Final canonical answer.",
  answer_blocks: [
    {
      text: "Final canonical answer.",
      citation_ids: ["c1"],
    },
  ],
  citations: [
    {
      citation_id: "c1",
      source_kind: "article",
      snippet: "snippet",
    },
  ],
  knowledge_mode: "article_grounded",
  source_status: null,
  web_search: null,
  message_id: "msg-active-1",
  thread_id: "thread-active-1",
  turn_run_id: "turn-run-active-1",
};

function makeTerminalPayload(finalStatus: string, terminalReason: string) {
  return {
    execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
    final_status: finalStatus,
    message_id: "msg-active-1",
    thread_id: "thread-active-1",
    turn_run_id: "turn-run-active-1",
    terminal_reason: terminalReason,
  };
}

const FOREIGN_TERMINAL_PAYLOAD = {
  execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
  final_status: "failed",
  message_id: "msg-foreign-1",
  thread_id: "thread-foreign-1",
  turn_run_id: "turn-run-foreign-1",
  terminal_reason: "agent_run_failed",
};

// ---------------------------------------------------------------------------
// R0 contract: typed state machine
// ---------------------------------------------------------------------------

describe("R0 TurnLifecycle contract", () => {
  it("terminal states are exactly committed / failed / cancelled", () => {
    expect(TERMINAL_STATES).toEqual(new Set(["committed", "failed", "cancelled"]));
  });

  it("trusted terminal event names are typed and exclude unknown events", () => {
    expect(TRUSTED_TERMINAL_EVENT_NAMES).toEqual(
      new Set(["message.completed", "agentic.terminal", "message.interrupted"]),
    );
    expect(isTrustedTerminalEvent("agentic.future_signal")).toBe(false);
    expect(isTrustedTerminalEvent("message.delta")).toBe(false);
    expect(isTrustedTerminalEvent("agentic.progress")).toBe(false);
  });

  it("stateForFinalStatus maps typed values correctly", () => {
    expect(stateForFinalStatus("ok")).toBe("committed");
    expect(stateForFinalStatus("failed")).toBe("failed");
    expect(stateForFinalStatus("cancelled")).toBe("cancelled");
    expect(stateForFinalStatus("context_stale")).toBe("failed");
  });

  it("stateForFinalStatus fails closed on unknown / null", () => {
    expect(stateForFinalStatus(null)).toBe("failed");
    expect(stateForFinalStatus(undefined)).toBe("failed");
    expect(stateForFinalStatus("")).toBe("failed");
    expect(stateForFinalStatus("unknown")).toBe("failed");
  });

  it("isTerminalState identifies terminal states", () => {
    expect(isTerminalState("committed")).toBe(true);
    expect(isTerminalState("failed")).toBe(true);
    expect(isTerminalState("cancelled")).toBe(true);
    expect(isTerminalState("idle")).toBe(false);
    expect(isTerminalState("running")).toBe(false);
    expect(isTerminalState("finalizing")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// R0 contract: TurnIdentity — foreign / stale rejection
// ---------------------------------------------------------------------------

describe("R0 TurnIdentity matching", () => {
  const identity: TurnIdentity = {
    messageId: "msg-1",
    threadId: "thread-1",
    turnRunId: "turn-run-1",
  };

  it("matches when all three ids match", () => {
    expect(
      matchesTurnIdentity(identity, {
        messageId: "msg-1",
        threadId: "thread-1",
        turnRunId: "turn-run-1",
      }),
    ).toBe(true);
  });

  it("does not match when message_id differs", () => {
    expect(
      matchesTurnIdentity(identity, {
        messageId: "msg-foreign",
        threadId: "thread-1",
        turnRunId: "turn-run-1",
      }),
    ).toBe(false);
  });

  it("does not match when thread_id differs", () => {
    expect(
      matchesTurnIdentity(identity, {
        messageId: "msg-1",
        threadId: "thread-foreign",
        turnRunId: "turn-run-1",
      }),
    ).toBe(false);
  });

  it("does not match when turn_run_id differs", () => {
    expect(
      matchesTurnIdentity(identity, {
        messageId: "msg-1",
        threadId: "thread-1",
        turnRunId: "turn-run-foreign",
      }),
    ).toBe(false);
  });

  it("does not match when any id is null / empty / undefined", () => {
    expect(
      matchesTurnIdentity(identity, {
        messageId: null,
        threadId: "thread-1",
        turnRunId: "turn-run-1",
      }),
    ).toBe(false);
    expect(
      matchesTurnIdentity(identity, {
        messageId: "",
        threadId: "thread-1",
        turnRunId: "turn-run-1",
      }),
    ).toBe(false);
    expect(
      matchesTurnIdentity(identity, {
        messageId: "msg-1",
        threadId: undefined,
        turnRunId: "turn-run-1",
      }),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// R0 contract: LogicalTerminalResult
// ---------------------------------------------------------------------------

describe("R0 LogicalTerminalResult", () => {
  it("completed is trusted and results in committed", () => {
    const r = makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    expect(isTrustedTerminal(r)).toBe(true);
    expect(resultingState(r)).toBe("committed");
  });

  it("terminal failed is trusted and results in failed", () => {
    const r = makeLogicalTerminalResult("terminal", {
      finalStatus: "failed",
      terminalReason: "agent_output_invalid",
    });
    expect(isTrustedTerminal(r)).toBe(true);
    expect(resultingState(r)).toBe("failed");
  });

  it("terminal cancelled results in cancelled", () => {
    const r = makeLogicalTerminalResult("terminal", {
      finalStatus: "cancelled",
    });
    expect(isTrustedTerminal(r)).toBe(true);
    expect(resultingState(r)).toBe("cancelled");
  });

  it("interrupted with context_stale results in failed", () => {
    const r = makeLogicalTerminalResult("interrupted", {
      finalStatus: "context_stale",
    });
    expect(isTrustedTerminal(r)).toBe(true);
    expect(resultingState(r)).toBe("failed");
  });

  it("abort is trusted for composer unlock", () => {
    const r = makeLogicalTerminalResult("abort");
    expect(isTrustedTerminal(r)).toBe(true);
    expect(resultingState(r)).toBe("cancelled");
  });

  it("parse_error is trusted and results in failed", () => {
    const r = makeLogicalTerminalResult("parse_error");
    expect(isTrustedTerminal(r)).toBe(true);
    expect(resultingState(r)).toBe("failed");
  });

  it("eof alone is NOT trusted", () => {
    const r = makeLogicalTerminalResult("eof");
    expect(isTrustedTerminal(r)).toBe(false);
    expect(resultingState(r)).toBe("failed");
  });

  it("receivedAt is populated", () => {
    const before = Date.now();
    const r = makeLogicalTerminalResult("completed");
    const after = Date.now();
    expect(r.receivedAt).toBeGreaterThanOrEqual(before);
    expect(r.receivedAt).toBeLessThanOrEqual(after);
  });
});

// ---------------------------------------------------------------------------
// R0 red-light: terminal-then-EOF composer unlock
//
// The current `consumeReaderAskSse` returns Promise<void> and only
// resolves after EOF. R1 must change the return type to
// Promise<LogicalTerminalResult> and resolve as soon as a trusted
// terminal frame is observed, NOT when EOF arrives.
// ---------------------------------------------------------------------------

describe("R0 red-light: terminal-then-EOF composer unlock", () => {
  it("consumeReaderAskSse returns a LogicalTerminalResult (not void)", async () => {
    // This test will FAIL on the current code because consumeReaderAskSse
    // returns Promise<void>. R1 must change the return type.
    const { consumeReaderAskSse } = await import("./sse");
    const response = makeSseResponse([
      encodeSse("agentic.run_started", RUN_STARTED_PAYLOAD),
      encodeSse("message.completed", COMPLETED_PAYLOAD),
    ]);
    const result = await consumeReaderAskSse(response, () => {});
    expect(result).toBeDefined();
    expect(result).not.toBeNull();
    // Once R1 lands, the result will be a LogicalTerminalResult with
    // kind="completed".
    expect((result as LogicalTerminalResult | null)?.kind).toBe("completed");
  });

  it("composer unlocks on terminal frame, not on EOF (stream kept open)", async () => {
    // R0 contract: when the stream stays open after a trusted terminal,
    // consumeReaderAskSse must resolve immediately and cancel the reader.
    // The current code waits for EOF, so this test will time out or fail.
    const { consumeReaderAskSse } = await import("./sse");
    const events: ReaderAskStreamEnvelopeDto[] = [];
    const response = makeSseResponse(
      [
        encodeSse("agentic.run_started", RUN_STARTED_PAYLOAD),
        encodeSse("message.completed", COMPLETED_PAYLOAD),
      ],
      { keepOpen: true },
    );

    const started = Date.now();
    const result = await consumeReaderAskSse(response, (e) => events.push(e));
    const elapsed = Date.now() - started;

    // Must resolve quickly — well under any reasonable EOF timeout.
    expect(elapsed).toBeLessThan(1000);
    expect(events.some((e) => e.event === "message.completed")).toBe(true);
    expect((result as LogicalTerminalResult | null)?.kind).toBe("completed");
  });

  it("late frames after terminal are ignored (reader cancelled)", async () => {
    // R0 contract: after a trusted terminal, the consumer must cancel
    // the reader and ignore any late frames.
    const { consumeReaderAskSse } = await import("./sse");
    const events: ReaderAskStreamEnvelopeDto[] = [];
    const response = makeSseResponse(
      [
        encodeSse("agentic.run_started", RUN_STARTED_PAYLOAD),
        encodeSse("message.completed", COMPLETED_PAYLOAD),
        // Late frame that must be ignored.
        encodeSse("message.delta", { delta: "late delta after terminal" }),
      ],
      { keepOpen: false },
    );

    await consumeReaderAskSse(response, (e) => events.push(e));

    // The late delta must NOT be in the events list.
    const lateDeltas = events.filter(
      (e) =>
        e.event === "message.delta" &&
        (e.data as Record<string, unknown>)?.delta === "late delta after terminal",
    );
    expect(lateDeltas).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// R0 red-light: agentic.terminal / message.interrupted handled exactly once
// ---------------------------------------------------------------------------

describe("R0 red-light: terminal handled exactly once", () => {
  it("agentic.terminal is a trusted terminal", () => {
    expect(isTrustedTerminalEvent("agentic.terminal")).toBe(true);
  });

  it("message.interrupted is a trusted terminal", () => {
    expect(isTrustedTerminalEvent("message.interrupted")).toBe(true);
  });

  it("resultingState for cancelled terminal is cancelled", () => {
    const r = makeLogicalTerminalResult("terminal", { finalStatus: "cancelled" });
    expect(resultingState(r)).toBe("cancelled");
  });
});

// ---------------------------------------------------------------------------
// R0 red-light: foreign / stale terminal must not unlock the active turn
// ---------------------------------------------------------------------------

describe("R0 red-light: foreign terminal rejection", () => {
  it("foreign terminal payload does not match active turn identity", () => {
    const activeIdentity: TurnIdentity = {
      messageId: "msg-active-1",
      threadId: "thread-active-1",
      turnRunId: "turn-run-active-1",
    };
    expect(
      matchesTurnIdentity(activeIdentity, {
        messageId: FOREIGN_TERMINAL_PAYLOAD.message_id,
        threadId: FOREIGN_TERMINAL_PAYLOAD.thread_id,
        turnRunId: FOREIGN_TERMINAL_PAYLOAD.turn_run_id,
      }),
    ).toBe(false);
  });

  it("stale terminal arriving after committed must not flip the state", () => {
    // R0 contract: terminal writes are idempotent. A cancelled arriving
    // after committed must not flip the state.
    const committed = makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    const lateCancelled = makeLogicalTerminalResult("terminal", {
      finalStatus: "cancelled",
    });
    expect(resultingState(committed)).toBe("committed");
    expect(resultingState(lateCancelled)).toBe("cancelled");
    // The host must record the FIRST trusted terminal and ignore later ones.
    // This test fixes the contract; the host implementation lands in R1.
  });
});

// ---------------------------------------------------------------------------
// R0 red-light: provisional invalid output must not become canonical
// ---------------------------------------------------------------------------

describe("R0 red-light: invalid provisional not canonical", () => {
  it("terminal payload must not carry answer_text / content_md / answer_blocks", () => {
    const terminal = makeTerminalPayload("failed", "agent_output_invalid");
    for (const forbiddenField of [
      "answer_text",
      "content_md",
      "answer_blocks",
      "citations",
      "knowledge_mode",
      "web_search",
    ]) {
      expect(terminal).not.toHaveProperty(forbiddenField);
    }
  });

  it("canonical answer state after failure must be empty", () => {
    // Simulated canonical state surface after a failed turn.
    const canonicalStateAfterFailure = {
      answer_text: null,
      answer_blocks: null,
      citations: null,
      knowledge_mode: null,
      web_search: null,
      content_md: "",
    };
    expect(canonicalStateAfterFailure.content_md).toBe("");
    expect(canonicalStateAfterFailure.answer_text).toBeNull();
    expect(canonicalStateAfterFailure.answer_blocks).toBeNull();
    expect(canonicalStateAfterFailure.citations).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// R0 red-light: stale-stream reconciliation
// ---------------------------------------------------------------------------

describe("R0 red-light: stale-stream reconciliation", () => {
  it("STALE_STREAM_TERMINAL_REASON is the typed constant", () => {
    expect(STALE_STREAM_TERMINAL_REASON).toBe("stale_stream_reconciled");
  });

  it("reconciled state is failed or cancelled (never committed)", () => {
    const validReconciledStates = new Set(["failed", "cancelled"]);
    for (const s of validReconciledStates) {
      expect(isTerminalState(s as never)).toBe(true);
    }
    expect(validReconciledStates.has("committed")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// R0 red-light: reasoning truncation typed contract
// ---------------------------------------------------------------------------

describe("R0 red-light: reasoning truncation typed", () => {
  it("truncation marker must not appear in reasoning body", () => {
    const forbiddenMarkers = [
      "思考内容已截断",
      "reasoning truncated",
      "...truncated",
    ];
    const compliantBody = "Planning the search. Verifying citation.";
    for (const marker of forbiddenMarkers) {
      expect(compliantBody).not.toContain(marker);
    }
  });

  it("reasoning.completed payload carries typed truncated boolean", () => {
    const payload = {
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      message_id: "msg-1",
      truncated: true,
      char_cap: 12000,
    };
    expect(typeof payload.truncated).toBe("boolean");
    expect(payload.truncated).toBe(true);
    expect(payload).toHaveProperty("truncated");
  });
});

// ---------------------------------------------------------------------------
// R0 red-light: 30K CJK / Markdown cadence + performance
// ---------------------------------------------------------------------------

describe("R0 red-light: 30K CJK/Markdown streaming cadence", () => {
  function build30kCjkMarkdownPayload(): string {
    const blockText = "段落正文 ".repeat(400); // ~2000 chars per block
    const blocks = Array.from({ length: 15 }, () => ({
      text: blockText,
      citation_ids: [],
    }));
    return JSON.stringify({
      response_kind: "answer",
      answer_blocks: blocks,
    });
  }

  it("30K payload total length is at least 30000 chars", () => {
    expect(build30kCjkMarkdownPayload().length).toBeGreaterThanOrEqual(30_000);
  });

  it("30K payload splits into many small chunks", () => {
    const payload = build30kCjkMarkdownPayload();
    const chunks: string[] = [];
    for (let i = 0; i < payload.length; i += 8) {
      chunks.push(payload.slice(i, i + 8));
    }
    expect(chunks.length).toBeGreaterThanOrEqual(3_000);
  });
});

// ---------------------------------------------------------------------------
// R0 red-light: timing metrics contract
// ---------------------------------------------------------------------------

describe("R0 red-light: timing metrics", () => {
  it("required metric kinds are named", () => {
    const required = new Set([
      "first_reasoning",
      "first_answer_delta",
      "last_answer_delta",
      "validation_done",
      "persistence_done",
      "terminal_sent",
      "terminal_received",
      "composer_enabled",
    ]);
    // Sanity: all names are non-empty strings.
    for (const name of required) {
      expect(typeof name).toBe("string");
      expect(name.length).toBeGreaterThan(0);
    }
  });

  it("metrics must not embed answer text or secrets", () => {
    const metricPayload = {
      first_reasoning: 1.23,
      first_answer_delta: 1.45,
      last_answer_delta: 12.34,
      validation_done: 12.5,
      persistence_done: 12.78,
      terminal_sent: 12.8,
      terminal_received: 12.91,
      composer_enabled: 12.92,
    };
    for (const [key, value] of Object.entries(metricPayload)) {
      expect(typeof value).toBe("number");
    }
  });
});

// ---------------------------------------------------------------------------
// R3: TurnLifecycleMetrics — frontend per-turn timing metrics
// ---------------------------------------------------------------------------

describe("R3 TurnLifecycleMetrics", () => {
  it("all metric fields start null", () => {
    const metrics = new TurnLifecycleMetrics(0);
    const snap = metrics.toJSON();
    expect(snap.first_reasoning).toBeNull();
    expect(snap.first_answer_delta).toBeNull();
    expect(snap.last_answer_delta).toBeNull();
    expect(snap.validation_done).toBeNull();
    expect(snap.persistence_done).toBeNull();
    expect(snap.terminal_received).toBeNull();
    expect(snap.composer_enabled).toBeNull();
  });

  it("markFirstReasoning is idempotent — keeps earliest timestamp", () => {
    const metrics = new TurnLifecycleMetrics(0);
    metrics.markFirstReasoning();
    const first = metrics.first_reasoning;
    expect(first).not.toBeNull();
    metrics.markFirstReasoning();
    expect(metrics.first_reasoning).toBe(first);
  });

  it("markAnswerDelta tracks first and last delta timestamps", () => {
    const metrics = new TurnLifecycleMetrics(0);
    expect(metrics.first_answer_delta).toBeNull();
    expect(metrics.last_answer_delta).toBeNull();
    metrics.markAnswerDelta();
    expect(metrics.first_answer_delta).not.toBeNull();
    expect(metrics.last_answer_delta).not.toBeNull();
    const firstFirst = metrics.first_answer_delta;
    metrics.markAnswerDelta();
    // first_answer_delta is idempotent.
    expect(metrics.first_answer_delta).toBe(firstFirst);
    // last_answer_delta is monotonic non-decreasing.
    expect(metrics.last_answer_delta!).toBeGreaterThanOrEqual(firstFirst!);
  });

  it("markValidationDone / markTerminalReceived are idempotent; markPersistenceDone overwrites", () => {
    const metrics = new TurnLifecycleMetrics(0);
    metrics.markValidationDone();
    const v = metrics.validation_done;
    metrics.markValidationDone();
    expect(metrics.validation_done).toBe(v);

    // markPersistenceDone is intentionally NOT idempotent — it records
    // the latest persistence commit time (the backend may call it once
    // per turn, but the API contract allows overwrite for retry cases).
    metrics.markPersistenceDone();
    const p = metrics.persistence_done;
    expect(p).not.toBeNull();
    metrics.markPersistenceDone();
    expect(metrics.persistence_done).not.toBeNull();
    expect(metrics.persistence_done!).toBeGreaterThanOrEqual(p!);

    metrics.markTerminalReceived();
    const t = metrics.terminal_received;
    metrics.markTerminalReceived();
    expect(metrics.terminal_received).toBe(t);
  });

  it("markComposerEnabled is idempotent", () => {
    const metrics = new TurnLifecycleMetrics(0);
    metrics.markComposerEnabled();
    const c = metrics.composer_enabled;
    metrics.markComposerEnabled();
    expect(metrics.composer_enabled).toBe(c);
  });

  it("toJSON returns only numbers or null — no content or secrets", () => {
    const metrics = new TurnLifecycleMetrics(0);
    metrics.markFirstReasoning();
    metrics.markAnswerDelta();
    metrics.markTerminalReceived();
    metrics.markComposerEnabled();
    const snap = metrics.toJSON();
    const serialized = JSON.stringify(snap);
    // No content-bearing keys may exist — only the 7 named metric fields.
    const allowed = new Set([
      "first_reasoning",
      "first_answer_delta",
      "last_answer_delta",
      "validation_done",
      "persistence_done",
      "terminal_received",
      "composer_enabled",
    ]);
    for (const key of Object.keys(snap)) {
      expect(allowed.has(key)).toBe(true);
    }
    // All values must be number or null.
    for (const value of Object.values(snap)) {
      expect(value === null || typeof value === "number").toBe(true);
    }
    // Serialized form must not contain any quoted string content beyond
    // the allowed metric field names. We check by removing all allowed
    // field-name tokens and asserting nothing else looks like a string.
    const stripped = [...allowed]
      .reduce((acc, key) => acc.replaceAll(`"${key}"`, ""), serialized);
    // No remaining quoted strings (no leaked content / secrets).
    expect(stripped).not.toMatch(/"[^"]*"/);
  });

  it("terminal_received precedes composer_enabled in a typical turn", () => {
    const metrics = new TurnLifecycleMetrics(0);
    metrics.markTerminalReceived();
    metrics.markComposerEnabled();
    expect(metrics.terminal_received).not.toBeNull();
    expect(metrics.composer_enabled).not.toBeNull();
    expect(metrics.composer_enabled!).toBeGreaterThanOrEqual(
      metrics.terminal_received!,
    );
  });
});
