import { describe, expect, it } from "vitest";
import type { ReaderAskStreamEnvelopeDto } from "@/types/api/reader-ask";
import {
  isReaderAskAgenticCompletedPayload,
  isReaderAskAgenticEvidenceScope,
  isReaderAskAgenticProgressPayload,
  isReaderAskAgenticRunStartedPayload,
  isReaderAskAgenticTerminalPayload,
  READER_ASK_AGENTIC_EXECUTION_VERSION,
} from "@/types/api/reader-ask";
import {
  consumeReaderAskSse,
  isReaderAskAgenticCompletedPayload as reexportedCompletedGuard,
  READER_ASK_AGENTIC_EXECUTION_VERSION as reexportedVersion,
} from "./sse";

function makeSseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let chunkIndex = 0;
  const stream = new ReadableStream({
    pull(controller) {
      if (chunkIndex < chunks.length) {
        controller.enqueue(encoder.encode(chunks[chunkIndex]));
        chunkIndex++;
      } else {
        controller.close();
      }
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

async function collectEvents(
  chunks: string[],
  signal?: AbortSignal,
): Promise<ReaderAskStreamEnvelopeDto[]> {
  const events: ReaderAskStreamEnvelopeDto[] = [];
  const response = makeSseResponse(chunks);
  await consumeReaderAskSse(response, (event) => events.push(event), signal);
  return events;
}

const AGENTIC_SEARCH_HIT_EVIDENCE = {
  handle_id: "evh_aabbccddeeff00112233445566778899",
  kind: "search_hit",
  source_tool: "search_current_article",
  snippet: "climate change impacts",
  unit_id: "u1",
  anchor_segment_id: "s1",
  rag_citation: {
    rag_substrate_id: "substrate-1",
    index_run_id: "index-run-1",
    index_version: "v1",
    plan_content_sha256: "plan-sha-abc",
    source_scope: "main_reading_text",
    block_type: "paragraph",
    chunk_id: "chunk-1",
    content_sha256: "content-sha-def",
    canonical_text_start_utf16: 10,
    canonical_text_end_utf16: 42,
    snippet: "climate change impacts",
    score: 0.91,
    stable_document_id: "doc-stable-1",
    base_id: "base-1",
    record_generation: 1,
    block_ids: ["b1"],
    unit_ids: ["u1"],
    anchor_segment_ids: ["s1"],
  },
} as const;

const AGENTIC_EVIDENCE_SCOPE = {
  reading_record_id: "22222222-2222-2222-2222-222222222222",
  base_id: "base-1",
  record_generation: 1,
  stable_document_id: "doc-stable-1",
} as const;

const AGENTIC_COMPLETED_PAYLOAD = {
  execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
  final_status: "ok",
  answer_text: "Climate change is discussed in paragraph 2.",
  message_id: "msg-agentic-1",
  thread_id: "thread-1",
  turn_run_id: "turn-run-1",
  envelope_fingerprint: "env-fp-1",
  evidence_scope: AGENTIC_EVIDENCE_SCOPE,
  evidence: [AGENTIC_SEARCH_HIT_EVIDENCE],
} as const;

/** Pre-R3B0 wire shape: no evidence_scope field (legacy v1 compatible). */
const AGENTIC_COMPLETED_PAYLOAD_LEGACY_NO_SCOPE = {
  execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
  final_status: "ok",
  answer_text: "Climate change is discussed in paragraph 2.",
  message_id: "msg-agentic-1",
  thread_id: "thread-1",
  turn_run_id: "turn-run-1",
  envelope_fingerprint: "env-fp-1",
  evidence: [AGENTIC_SEARCH_HIT_EVIDENCE],
} as const;

describe("consumeReaderAskSse", () => {
  it("parses a single message.delta event", async () => {
    const events = await collectEvents([
      'event: message.delta\ndata: {"delta":"Hello"}\n\n',
    ]);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({
      event: "message.delta",
      data: { delta: "Hello" },
    });
  });

  it("parses multi-event chunk with reasoning.delta and message.completed", async () => {
    const events = await collectEvents([
      'event: reasoning.delta\ndata: {"delta":"thinking"}\n\nevent: message.completed\ndata: {"content_md":"done"}\n\n',
    ]);

    expect(events).toHaveLength(2);
    expect(events[0].event).toBe("reasoning.delta");
    expect(events[0].data).toEqual({ delta: "thinking" });
    expect(events[1].event).toBe("message.completed");
    expect(events[1].data).toEqual({ content_md: "done" });
  });

  it("handles split chunk where one event spans two TCP chunks", async () => {
    const events = await collectEvents([
      'event: message.delta\ndata: {"delta":"Hel',
      'lo"}\n\n',
    ]);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({
      event: "message.delta",
      data: { delta: "Hello" },
    });
  });

  it("emits error event for broken JSON instead of silently dropping it", async () => {
    const events = await collectEvents([
      "event: message.delta\ndata: {broken json}\n\n",
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("error");
    expect(events[0].data).toMatchObject({
      code: "SSE_PARSE_ERROR",
      raw_data: "{broken json}",
    });
    expect(events[0].data.detail).toContain("message.delta");
  });

  it("ignores trailing buffer with incomplete event (no double newline)", async () => {
    const events = await collectEvents([
      'event: message.delta\ndata: {"delta":"ok"}\n\nevent: reasoning.delta\ndata: {"delta":"incom',
    ]);

    // Only the first complete event should be parsed;
    // trailing buffer has a broken JSON data line which becomes an error event
    expect(events).toHaveLength(2);
    expect(events[0].event).toBe("message.delta");
    expect(events[0].data).toEqual({ delta: "ok" });
    expect(events[1].event).toBe("error");
    expect(events[1].data).toMatchObject({ code: "SSE_PARSE_ERROR" });
  });

  it("handles multi-event chunk with mixed valid and broken JSON", async () => {
    const events = await collectEvents([
      'event: message.delta\ndata: {"delta":"valid"}\n\nevent: message.delta\ndata: {invalid}\n\n',
    ]);

    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({
      event: "message.delta",
      data: { delta: "valid" },
    });
    expect(events[1].event).toBe("error");
    expect(events[1].data).toMatchObject({
      code: "SSE_PARSE_ERROR",
      raw_data: "{invalid}",
    });
  });

  it("parses reasoning.started, reasoning.delta, and reasoning.completed in sequence", async () => {
    const events = await collectEvents([
      'event: reasoning.started\ndata: {"message_id":"msg-1"}\n\nevent: reasoning.delta\ndata: {"delta":"step 1"}\n\nevent: reasoning.completed\ndata: {}\n\n',
    ]);

    expect(events).toHaveLength(3);
    expect(events[0].event).toBe("reasoning.started");
    expect(events[1].event).toBe("reasoning.delta");
    expect(events[1].data).toEqual({ delta: "step 1" });
    expect(events[2].event).toBe("reasoning.completed");
  });

  it("parses message.completed with full payload", async () => {
    const events = await collectEvents([
      'event: message.completed\ndata: {"id":"msg-1","content_md":"done","citations":[]}\n\n',
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("message.completed");
    expect(events[0].data).toEqual({
      id: "msg-1",
      content_md: "done",
      citations: [],
    });
  });

  it("stops consuming after SSE_PARSE_ERROR even if message.completed follows in same chunk", async () => {
    const events = await collectEvents([
      'event: message.delta\ndata: {broken}\n\nevent: message.completed\ndata: {"id":"msg-1","content_md":"done"}\n\n',
    ]);

    // The parse error event should be emitted, but message.completed must NOT
    // be processed — once the stream is corrupted, subsequent events cannot be
    // trusted.
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("error");
    expect(events[0].data).toMatchObject({ code: "SSE_PARSE_ERROR" });
  });

  it("stops consuming after SSE_PARSE_ERROR even if message.completed follows in later chunk", async () => {
    const events = await collectEvents([
      "event: message.delta\ndata: {broken}\n\n",
      'event: message.completed\ndata: {"id":"msg-1","content_md":"done"}\n\n',
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("error");
    expect(events[0].data).toMatchObject({ code: "SSE_PARSE_ERROR" });
  });

  it("still parses legacy message.interrupted with content_md", async () => {
    const events = await collectEvents([
      'event: message.interrupted\ndata: {"content_md":"partial answer"}\n\n',
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("message.interrupted");
    expect(events[0].data).toEqual({ content_md: "partial answer" });
    expect(isReaderAskAgenticTerminalPayload(events[0].data)).toBe(false);
    expect(isReaderAskAgenticCompletedPayload(events[0].data)).toBe(false);
  });

  it("parses fragmented agentic SSE across TCP chunks", async () => {
    const payload = JSON.stringify({
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      message_id: "msg-agentic-1",
      thread_id: "thread-1",
      turn_run_id: "turn-run-1",
      envelope_fingerprint: "env-fp-1",
      has_initial_selection: true,
    });
    const events = await collectEvents([
      `event: agentic.run_started\ndata: ${payload.slice(0, 40)}`,
      `${payload.slice(40)}\n\n`,
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("agentic.run_started");
    expect(isReaderAskAgenticRunStartedPayload(events[0].data)).toBe(true);
    expect(events[0].data).toMatchObject({
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      message_id: "msg-agentic-1",
      has_initial_selection: true,
    });
  });

  it("parses agentic.run_started with typed payload", async () => {
    const events = await collectEvents([
      `event: agentic.run_started\ndata: ${JSON.stringify({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        envelope_fingerprint: "env-fp-1",
        has_initial_selection: false,
      })}\n\n`,
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("agentic.run_started");
    expect(isReaderAskAgenticRunStartedPayload(events[0].data)).toBe(true);
    expect(events[0].data).toEqual({
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      message_id: "msg-agentic-1",
      thread_id: "thread-1",
      turn_run_id: "turn-run-1",
      envelope_fingerprint: "env-fp-1",
      has_initial_selection: false,
    });
  });

  it("parses agentic.progress with typed payload", async () => {
    const events = await collectEvents([
      `event: agentic.progress\ndata: ${JSON.stringify({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        phase: "agent_running",
        summary: "Running Reading Record Ask agent",
      })}\n\n`,
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("agentic.progress");
    expect(isReaderAskAgenticProgressPayload(events[0].data)).toBe(true);
    expect(events[0].data).toEqual({
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      phase: "agent_running",
      summary: "Running Reading Record Ask agent",
    });
  });

  it("parses agentic message.completed with search_hit evidence and rag_citation", async () => {
    const events = await collectEvents([
      `event: message.completed\ndata: ${JSON.stringify(AGENTIC_COMPLETED_PAYLOAD)}\n\n`,
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("message.completed");
    expect(isReaderAskAgenticCompletedPayload(events[0].data)).toBe(true);
    // Must NOT look like legacy completed (no content_md / article_rag mapping).
    expect(events[0].data).not.toHaveProperty("content_md");
    expect(events[0].data).not.toHaveProperty("article_rag");
    expect(events[0].data).toEqual(AGENTIC_COMPLETED_PAYLOAD);

    const evidence = (
      events[0].data as typeof AGENTIC_COMPLETED_PAYLOAD
    ).evidence[0];
    expect(evidence.kind).toBe("search_hit");
    expect(evidence.rag_citation).toMatchObject({
      stable_document_id: "doc-stable-1",
      base_id: "base-1",
      record_generation: 1,
      canonical_text_start_utf16: 10,
      canonical_text_end_utf16: 42,
      rag_substrate_id: "substrate-1",
      index_run_id: "index-run-1",
      plan_content_sha256: "plan-sha-abc",
    });
  });

  it("parses failed agentic terminal on agentic.terminal and message.interrupted", async () => {
    const terminal = {
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      final_status: "failed",
      message_id: "msg-agentic-1",
      thread_id: "thread-1",
      turn_run_id: "turn-run-1",
      envelope_fingerprint: "env-fp-1",
      terminal_reason: "agentic_model_unconfigured: no validated model",
      rejected_handles: [],
    };

    const events = await collectEvents([
      `event: agentic.terminal\ndata: ${JSON.stringify(terminal)}\n\nevent: message.interrupted\ndata: ${JSON.stringify(terminal)}\n\n`,
    ]);

    expect(events).toHaveLength(2);
    expect(events[0].event).toBe("agentic.terminal");
    expect(events[1].event).toBe("message.interrupted");
    expect(isReaderAskAgenticTerminalPayload(events[0].data)).toBe(true);
    expect(isReaderAskAgenticTerminalPayload(events[1].data)).toBe(true);
    // Non-ok terminal must never be classified as completed success.
    expect(isReaderAskAgenticCompletedPayload(events[0].data)).toBe(false);
    expect(isReaderAskAgenticCompletedPayload(events[1].data)).toBe(false);
    expect(events[0].data).toMatchObject({
      final_status: "failed",
      terminal_reason: expect.stringContaining("agentic_model_unconfigured"),
      rejected_handles: [],
    });
  });

  it("parses context_stale agentic terminal without treating it as success", async () => {
    const terminal = {
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      final_status: "context_stale",
      message_id: "msg-agentic-1",
      thread_id: "thread-1",
      turn_run_id: "turn-run-1",
      envelope_fingerprint: "env-fp-1",
      terminal_reason: "generation mismatch",
      rejected_handles: [],
    };

    const events = await collectEvents([
      `event: agentic.terminal\ndata: ${JSON.stringify(terminal)}\n\nevent: message.interrupted\ndata: ${JSON.stringify(terminal)}\n\n`,
    ]);

    expect(events).toHaveLength(2);
    expect(events[0].event).toBe("agentic.terminal");
    expect(events[1].event).toBe("message.interrupted");
    expect(isReaderAskAgenticTerminalPayload(events[0].data)).toBe(true);
    expect(isReaderAskAgenticCompletedPayload(events[0].data)).toBe(false);
    expect(events[0].data).toMatchObject({
      final_status: "context_stale",
      terminal_reason: "generation mismatch",
    });
    // No displayable answer fields on stale terminal.
    expect(events[0].data).not.toHaveProperty("answer_text");
    expect(events[0].data).not.toHaveProperty("content_md");
  });

  it("passes unknown event names through without success misclassification", async () => {
    const events = await collectEvents([
      'event: agentic.future_signal\ndata: {"hint":"forward-compat"}\n\n',
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("agentic.future_signal");
    expect(events[0].data).toEqual({ hint: "forward-compat" });
    expect(isReaderAskAgenticCompletedPayload(events[0].data)).toBe(false);
    expect(isReaderAskAgenticTerminalPayload(events[0].data)).toBe(false);
  });

  it("re-exports agentic type guards and execution version from sse module", () => {
    expect(reexportedVersion).toBe(READER_ASK_AGENTIC_EXECUTION_VERSION);
    expect(reexportedCompletedGuard(AGENTIC_COMPLETED_PAYLOAD)).toBe(true);
    expect(reexportedCompletedGuard({ content_md: "legacy" })).toBe(false);
  });
});

describe("agentic payload type guards", () => {
  it("rejects legacy completed payloads as agentic completed", () => {
    expect(
      isReaderAskAgenticCompletedPayload({
        id: "msg-1",
        thread_id: "thread-1",
        content_md: "done",
        citations: [],
      }),
    ).toBe(false);
  });

  it("rejects incomplete agentic completed payloads", () => {
    expect(
      isReaderAskAgenticCompletedPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "ok",
        // missing answer_text / ids
      }),
    ).toBe(false);
  });

  it("rejects completed payloads whose evidence items are not typed DTOs", () => {
    expect(
      isReaderAskAgenticCompletedPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "ok",
        answer_text: "answer",
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
        envelope_fingerprint: "env-1",
        evidence: [{}],
      }),
    ).toBe(false);

    expect(
      isReaderAskAgenticCompletedPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "ok",
        answer_text: "answer",
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
        envelope_fingerprint: "env-1",
        evidence: [
          {
            handle_id: "evh_1",
            kind: "search_hit",
            source_tool: "search_current_article",
            rag_citation: {
              // missing identity + UTF-16 range fields
              snippet: "partial",
            },
          },
        ],
      }),
    ).toBe(false);
  });

  it("accepts completed payloads with typed evidence and optional rag_citation", () => {
    expect(isReaderAskAgenticCompletedPayload(AGENTIC_COMPLETED_PAYLOAD)).toBe(
      true,
    );
    expect(
      isReaderAskAgenticCompletedPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "ok",
        answer_text: "no search",
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
        envelope_fingerprint: "env-1",
        evidence: [
          {
            handle_id: "evh_anchor",
            kind: "initial_anchor",
            source_tool: "initial_anchor",
            snippet: "hello",
            unit_id: "u1",
          },
        ],
      }),
    ).toBe(true);
  });

  it("accepts complete evidence_scope on agentic completed", () => {
    expect(isReaderAskAgenticEvidenceScope(AGENTIC_EVIDENCE_SCOPE)).toBe(true);
    expect(isReaderAskAgenticCompletedPayload(AGENTIC_COMPLETED_PAYLOAD)).toBe(
      true,
    );
    expect(AGENTIC_COMPLETED_PAYLOAD.evidence_scope).toEqual(
      AGENTIC_EVIDENCE_SCOPE,
    );
  });

  it("accepts legacy completed without evidence_scope or with null scope", () => {
    // Missing field = old v1 compatible; navigation later uses legacy_scope_missing.
    expect(
      isReaderAskAgenticCompletedPayload(AGENTIC_COMPLETED_PAYLOAD_LEGACY_NO_SCOPE),
    ).toBe(true);
    expect(
      isReaderAskAgenticCompletedPayload({
        ...AGENTIC_COMPLETED_PAYLOAD_LEGACY_NO_SCOPE,
        evidence_scope: null,
      }),
    ).toBe(true);
  });

  it("rejects malformed evidence_scope on agentic completed (no half-parse)", () => {
    const base = {
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      final_status: "ok" as const,
      answer_text: "answer",
      message_id: "msg-1",
      thread_id: "thread-1",
      turn_run_id: "turn-1",
      envelope_fingerprint: "env-1",
      evidence: [] as const,
    };

    expect(
      isReaderAskAgenticCompletedPayload({
        ...base,
        evidence_scope: {},
      }),
    ).toBe(false);

    expect(
      isReaderAskAgenticCompletedPayload({
        ...base,
        evidence_scope: {
          reading_record_id: "r1",
          // missing base_id / generation / stable
        },
      }),
    ).toBe(false);

    expect(
      isReaderAskAgenticCompletedPayload({
        ...base,
        evidence_scope: {
          reading_record_id: "r1",
          base_id: "b1",
          record_generation: "1",
          stable_document_id: null,
        },
      }),
    ).toBe(false);

    expect(
      isReaderAskAgenticCompletedPayload({
        ...base,
        evidence_scope: {
          reading_record_id: "r1",
          base_id: "b1",
          record_generation: 0,
          stable_document_id: null,
        },
      }),
    ).toBe(false);

    expect(
      isReaderAskAgenticCompletedPayload({
        ...base,
        evidence_scope: {
          reading_record_id: "r1",
          base_id: "b1",
          record_generation: 1.5,
          stable_document_id: null,
        },
      }),
    ).toBe(false);

    expect(
      isReaderAskAgenticCompletedPayload({
        ...base,
        evidence_scope: {
          reading_record_id: "r1",
          base_id: "b1",
          record_generation: 1,
          stable_document_id: null,
          extra: "nope",
        },
      }),
    ).toBe(false);

    expect(isReaderAskAgenticEvidenceScope({ reading_record_id: "r" })).toBe(
      false,
    );
  });

  it("accepts evidence_scope with null stable_document_id (RAG off)", () => {
    expect(
      isReaderAskAgenticCompletedPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "ok",
        answer_text: "anchor only",
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
        envelope_fingerprint: "env-1",
        evidence_scope: {
          reading_record_id: "r1",
          base_id: "b1",
          record_generation: 2,
          stable_document_id: null,
        },
        evidence: [
          {
            handle_id: "evh_anchor",
            kind: "initial_anchor",
            source_tool: "initial_anchor",
            snippet: "hello",
          },
        ],
      }),
    ).toBe(true);
  });

  it("rejects final_status ok as agentic terminal", () => {
    expect(
      isReaderAskAgenticTerminalPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "ok",
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
        envelope_fingerprint: "env-1",
        rejected_handles: [],
      }),
    ).toBe(false);
  });

  it("accepts invalid_citations terminal payloads", () => {
    expect(
      isReaderAskAgenticTerminalPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "invalid_citations",
        terminal_reason: "bad handle",
        rejected_handles: ["evh_aabbccddeeff00112233445566778899"],
      }),
    ).toBe(true);
  });
});
