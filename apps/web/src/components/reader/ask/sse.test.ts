import { describe, expect, it } from "vitest";
import type { ReaderAskStreamEnvelopeDto } from "@/types/api/reader-ask";
import {
  isReaderAskAgenticCompletedPayload,
  isReaderAskAgenticEvidenceList,
  isReaderAskAgenticEvidenceScope,
  isReaderAskAgenticProgressPayload,
  isReaderAskAgenticReasoningCompletedPayload,
  isReaderAskAgenticReasoningDeltaPayload,
  isReaderAskAgenticReasoningStartedPayload,
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

/** Canonical public v2 completed payload (no-evh). */
const AGENTIC_COMPLETED_PAYLOAD = {
  execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
  final_status: "ok",
  answer_text: "Climate change is discussed in paragraph 2.",
  answer_blocks: [
    {
      text: "Climate change is discussed in paragraph 2.",
      citation_ids: ["c1"],
    },
  ],
  citations: [
    {
      citation_id: "c1",
      source_kind: "article",
      snippet: "climate change impacts",
    },
  ],
  knowledge_mode: "article_grounded",
  source_status: null,
  message_id: "msg-agentic-1",
  thread_id: "thread-1",
  turn_run_id: "turn-run-1",
} as const;

/** Legacy v1 wire shape — must fail the public v2 completed guard. */
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

  it("parses agentic message.completed with public citations (no raw evidence)", async () => {
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

    const completed = events[0].data as typeof AGENTIC_COMPLETED_PAYLOAD;
    expect(completed.citations[0].citation_id).toBe("c1");
    expect(completed.knowledge_mode).toBe("article_grounded");
    expect("evidence" in completed).toBe(false);
  });

  it("parses failed agentic terminal on agentic.terminal and message.interrupted", async () => {
    const terminal = {
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      final_status: "failed",
      message_id: "msg-agentic-1",
      thread_id: "thread-1",
      turn_run_id: "turn-run-1",
      terminal_reason: "agentic_model_unconfigured: no validated model",
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
    });
  });

  it("parses context_stale agentic terminal without treating it as success", async () => {
    const terminal = {
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      final_status: "context_stale",
      message_id: "msg-agentic-1",
      thread_id: "thread-1",
      turn_run_id: "turn-run-1",
      terminal_reason: "generation mismatch",
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

  it("accepts canonical public v2 completed payload", () => {
    expect(isReaderAskAgenticCompletedPayload(AGENTIC_COMPLETED_PAYLOAD)).toBe(
      true,
    );
  });

  it("rejects answer-only forgeries missing blocks/citations/mode", () => {
    expect(
      isReaderAskAgenticCompletedPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "ok",
        answer_text: "only answer",
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
      }),
    ).toBe(false);
  });

  it("evidence_scope helper remains for restricted cold paths; not on public completed", () => {
    expect(isReaderAskAgenticEvidenceScope(AGENTIC_EVIDENCE_SCOPE)).toBe(true);
    expect(isReaderAskAgenticCompletedPayload(AGENTIC_COMPLETED_PAYLOAD)).toBe(
      true,
    );
    expect("evidence_scope" in AGENTIC_COMPLETED_PAYLOAD).toBe(false);
  });

  it("rejects legacy completed that still carries evidence / fingerprint", () => {
    expect(
      isReaderAskAgenticCompletedPayload(AGENTIC_COMPLETED_PAYLOAD_LEGACY_NO_SCOPE),
    ).toBe(false);
    expect(
      isReaderAskAgenticCompletedPayload({
        ...AGENTIC_COMPLETED_PAYLOAD_LEGACY_NO_SCOPE,
        evidence_scope: null,
      }),
    ).toBe(false);
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

  it("scope helper accepts null stable_document_id; public completed rejects scope", () => {
    expect(
      isReaderAskAgenticEvidenceScope({
        reading_record_id: "r1",
        base_id: "b1",
        record_generation: 2,
        stable_document_id: null,
      }),
    ).toBe(true);
    expect(
      isReaderAskAgenticCompletedPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "ok",
        answer_text: "anchor only",
        answer_blocks: [],
        citations: [],
        knowledge_mode: "general_knowledge",
        source_status: null,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
        evidence_scope: {
          reading_record_id: "r1",
          base_id: "b1",
          record_generation: 2,
          stable_document_id: null,
        },
      }),
    ).toBe(false);
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

  it("accepts invalid_citations terminal payloads without rejected handles", () => {
    expect(
      isReaderAskAgenticTerminalPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "invalid_citations",
        terminal_reason: "bad handle",
      }),
    ).toBe(true);
    // Public terminal must not carry rejected_handles / evh_* leakage.
    expect(
      isReaderAskAgenticTerminalPayload({
        execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
        final_status: "invalid_citations",
        terminal_reason: "bad handle",
        rejected_handles: ["evh_aabbccddeeff00112233445566778899"],
      }),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// R4-A1: article_seed evidence wire guard
//
// article_seed is a new EvidenceKind with provenance `baseline_context`. The
// strict completed guard must accept legal article_seed evidence items and
// reject malformed ones (missing handle_id / unknown kind / bad rag_citation).
// Cold hydration via thread detail also relies on the same list guard.
// ---------------------------------------------------------------------------

describe("agentic payload type guards — article_seed (R4-A1)", () => {
  const ARTICLE_SEED_EVIDENCE = {
    handle_id: "evh_seed_aabbccddeeff00112233445566778899",
    kind: "article_seed",
    source_tool: "baseline_context",
    snippet: "article body snippet",
    unit_id: "u1",
    anchor_segment_id: null,
  } as const;

  const ARTICLE_SEED_COMPLETED = {
  execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
  final_status: "ok",
  answer_text: "Grounded answer.",
  answer_blocks: [{ text: "Grounded answer.", citation_ids: ["c1"] }],
  citations: [{ citation_id: "c1", source_kind: "article", snippet: "article body snippet" }],
  knowledge_mode: "article_grounded",
  source_status: null,
  message_id: "msg-seed-1",
  thread_id: "thread-1",
  turn_run_id: "turn-run-1",
} as const;

  it("accepts completed payload with legal article_seed evidence", () => {
    expect(
      isReaderAskAgenticCompletedPayload(ARTICLE_SEED_COMPLETED),
    ).toBe(true);
  });

  it("evidence list accepts article_seed with null snippet; completed rejects evidence", () => {
    expect(
      isReaderAskAgenticEvidenceList([
        {
          handle_id: "evh_seed_aabbccddeeff00112233445566778899",
          kind: "article_seed",
          source_tool: "baseline_context",
          snippet: null,
          unit_id: null,
          anchor_segment_id: null,
        },
      ]),
    ).toBe(true);
    expect(
      isReaderAskAgenticCompletedPayload({
        ...ARTICLE_SEED_COMPLETED,
        evidence: [
          {
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "baseline_context",
            snippet: null,
          },
        ],
      }),
    ).toBe(false);
  });

  it("rejects completed payload when article_seed has malformed rag_citation", () => {
    expect(
      isReaderAskAgenticCompletedPayload({
        ...ARTICLE_SEED_COMPLETED,
        evidence: [
          {
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "baseline_context",
            snippet: "snippet",
            // article_seed should never carry rag_citation, but if present
            // it must be a complete citation; partial citation is rejected.
            rag_citation: { snippet: "partial" },
          },
        ],
      }),
    ).toBe(false);
  });

  it("rejects completed payload with unknown evidence kind", () => {
    expect(
      isReaderAskAgenticCompletedPayload({
        ...ARTICLE_SEED_COMPLETED,
        evidence: [
          {
            handle_id: "evh_x_aabbccddeeff00112233445566778899",
            kind: "future_kind",
            source_tool: "baseline_context",
            snippet: "snippet",
          },
        ],
      }),
    ).toBe(false);
  });

  it("rejects completed payload with missing handle_id on article_seed", () => {
    expect(
      isReaderAskAgenticCompletedPayload({
        ...ARTICLE_SEED_COMPLETED,
        evidence: [
          {
            kind: "article_seed",
            source_tool: "baseline_context",
            snippet: "snippet",
          },
        ],
      }),
    ).toBe(false);
  });

  it("cold hydration: evidence list guard accepts article_seed items", () => {
    // ReaderAskMessageDto.agentic_evidence uses isReaderAskAgenticEvidenceList
    // for cold-load validation. The guard must accept article_seed items.
    const coldLoadEvidence = [
      ARTICLE_SEED_EVIDENCE,
      {
        handle_id: "evh_anchor_aabbccddeeff00112233445566778899",
        kind: "initial_anchor",
        source_tool: "initial_anchor",
        snippet: "selected sentence",
        unit_id: "u1",
        anchor_segment_id: "s1",
      },
    ];
    expect(isReaderAskAgenticEvidenceList(coldLoadEvidence)).toBe(true);
  });

  it("cold hydration: evidence list guard rejects malformed article_seed", () => {
    expect(
      isReaderAskAgenticEvidenceList([
        {
          handle_id: "evh_seed_aabbccddeeff00112233445566778899",
          // missing kind
          source_tool: "baseline_context",
          snippet: "snippet",
        },
      ]),
    ).toBe(false);
  });

  it("parses agentic message.completed with article_seed evidence over SSE", async () => {
    const events = await collectEvents([
      `event: message.completed\ndata: ${JSON.stringify(ARTICLE_SEED_COMPLETED)}\n\n`,
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("message.completed");
    expect(isReaderAskAgenticCompletedPayload(events[0].data)).toBe(true);
    const completed = events[0].data as typeof ARTICLE_SEED_COMPLETED;
    expect(completed.citations[0].snippet).toBe("article body snippet");
    expect(completed.answer_blocks[0].citation_ids).toEqual(["c1"]);
    expect("evidence" in completed).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// R4-A1 rework: strict cold/hot evidence legal-map contract
//
// The guard must reject illegal (kind, source_tool) combinations and
// rag_citation presence violations on both hot completed (SSE) and cold
// hydration (evidence list guard) paths. Mirrors backend
// `LEGAL_EVIDENCE_KIND_SOURCE` + rag_citation rules.
// ---------------------------------------------------------------------------

describe("agentic evidence legal-map — illegal combinations (R4-A1 rework)", () => {
  const BASE_COMPLETED = {
    execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
    final_status: "ok",
    answer_text: "Answer.",
    message_id: "msg-legal-1",
    thread_id: "thread-1",
    turn_run_id: "turn-run-1",
    envelope_fingerprint: "env-fp-legal",
    evidence_scope: AGENTIC_EVIDENCE_SCOPE,
  } as const;

  function makeCompletedWithEvidence(evidence: unknown) {
    return { ...BASE_COMPLETED, evidence: [evidence] };
  }

  describe("hot completed guard rejects illegal kind/source pairs", () => {
    it("rejects article_seed + initial_anchor (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "initial_anchor",
            snippet: "snippet",
          }),
        ),
      ).toBe(false);
    });

    it("rejects article_seed + read_range (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "read_range",
            snippet: "snippet",
          }),
        ),
      ).toBe(false);
    });

    it("rejects article_seed + search_current_article (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "search_current_article",
            snippet: "snippet",
          }),
        ),
      ).toBe(false);
    });

    it("rejects article_seed with any rag_citation present", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "baseline_context",
            snippet: "snippet",
            rag_citation: { snippet: "should not be here" },
          }),
        ),
      ).toBe(false);
    });

    it("rejects initial_anchor + baseline_context (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_anchor_aabbccddeeff00112233445566778899",
            kind: "initial_anchor",
            source_tool: "baseline_context",
            snippet: "snippet",
          }),
        ),
      ).toBe(false);
    });

    it("rejects initial_anchor + read_range (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_anchor_aabbccddeeff00112233445566778899",
            kind: "initial_anchor",
            source_tool: "read_range",
            snippet: "snippet",
          }),
        ),
      ).toBe(false);
    });

    it("rejects initial_anchor with rag_citation present", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_anchor_aabbccddeeff00112233445566778899",
            kind: "initial_anchor",
            source_tool: "initial_anchor",
            snippet: "snippet",
            rag_citation: { snippet: "should not be here" },
          }),
        ),
      ).toBe(false);
    });

    it("rejects read_range + baseline_context (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_range_aabbccddeeff00112233445566778899",
            kind: "read_range",
            source_tool: "baseline_context",
            snippet: "snippet",
          }),
        ),
      ).toBe(false);
    });

    it("rejects read_range with rag_citation present", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_range_aabbccddeeff00112233445566778899",
            kind: "read_range",
            source_tool: "read_range",
            snippet: "snippet",
            rag_citation: { snippet: "should not be here" },
          }),
        ),
      ).toBe(false);
    });

    it("rejects search_hit + initial_anchor (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_search_aabbccddeeff00112233445566778899",
            kind: "search_hit",
            source_tool: "initial_anchor",
            snippet: "snippet",
            rag_citation: AGENTIC_SEARCH_HIT_EVIDENCE.rag_citation,
          }),
        ),
      ).toBe(false);
    });

    it("rejects search_hit + baseline_context (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_search_aabbccddeeff00112233445566778899",
            kind: "search_hit",
            source_tool: "baseline_context",
            snippet: "snippet",
            rag_citation: AGENTIC_SEARCH_HIT_EVIDENCE.rag_citation,
          }),
        ),
      ).toBe(false);
    });

    it("rejects search_hit without rag_citation", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_search_aabbccddeeff00112233445566778899",
            kind: "search_hit",
            source_tool: "search_current_article",
            snippet: "snippet without citation",
          }),
        ),
      ).toBe(false);
    });

    it("rejects observation + baseline_context (illegal source)", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_obs_aabbccddeeff00112233445566778899",
            kind: "observation",
            source_tool: "baseline_context",
            snippet: "snippet",
          }),
        ),
      ).toBe(false);
    });

    it("rejects observation with rag_citation present", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_obs_aabbccddeeff00112233445566778899",
            kind: "observation",
            source_tool: "initial_anchor",
            snippet: "snippet",
            rag_citation: { snippet: "should not be here" },
          }),
        ),
      ).toBe(false);
    });
  });

  describe("cold hydration evidence list guard rejects illegal kind/source pairs", () => {
    it("rejects article_seed + initial_anchor in cold hydration", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "initial_anchor",
            snippet: "snippet",
          },
        ]),
      ).toBe(false);
    });

    it("rejects search_hit without rag_citation in cold hydration", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_search_aabbccddeeff00112233445566778899",
            kind: "search_hit",
            source_tool: "search_current_article",
            snippet: "no citation",
          },
        ]),
      ).toBe(false);
    });

    it("rejects initial_anchor with rag_citation in cold hydration", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_anchor_aabbccddeeff00112233445566778899",
            kind: "initial_anchor",
            source_tool: "initial_anchor",
            snippet: "no rag allowed",
            rag_citation: { snippet: "illegal" },
          },
        ]),
      ).toBe(false);
    });

    it("rejects article_seed with rag_citation in cold hydration", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "baseline_context",
            snippet: "no rag allowed",
            rag_citation: { snippet: "illegal" },
          },
        ]),
      ).toBe(false);
    });
  });

  describe("public completed never carries evidence (legal-map is list-only)", () => {
    it("rejects completed payloads that still embed raw evidence", () => {
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence({
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "baseline_context",
            snippet: "snippet",
          }),
        ),
      ).toBe(false);
    });
  });

  describe("evidence list guard accepts legal kind/source pairs", () => {
    it("accepts article_seed + baseline_context without rag_citation", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_seed_aabbccddeeff00112233445566778899",
            kind: "article_seed",
            source_tool: "baseline_context",
            snippet: "legal snippet",
          },
        ]),
      ).toBe(true);
    });

    it("accepts initial_anchor + initial_anchor without rag_citation", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_anchor_aabbccddeeff00112233445566778899",
            kind: "initial_anchor",
            source_tool: "initial_anchor",
            snippet: "legal snippet",
          },
        ]),
      ).toBe(true);
    });

    it("accepts read_range + read_range without rag_citation", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_range_aabbccddeeff00112233445566778899",
            kind: "read_range",
            source_tool: "read_range",
            snippet: "legal snippet",
          },
        ]),
      ).toBe(true);
    });

    it("accepts search_hit + search_current_article with complete rag_citation", () => {
      expect(isReaderAskAgenticEvidenceList([AGENTIC_SEARCH_HIT_EVIDENCE])).toBe(
        true,
      );
      // Public completed still rejects raw evidence blobs.
      expect(
        isReaderAskAgenticCompletedPayload(
          makeCompletedWithEvidence(AGENTIC_SEARCH_HIT_EVIDENCE),
        ),
      ).toBe(false);
    });

    it("accepts observation + initial_anchor without rag_citation", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_obs_aabbccddeeff00112233445566778899",
            kind: "observation",
            source_tool: "initial_anchor",
            snippet: "legal snippet",
          },
        ]),
      ).toBe(true);
    });

    it("accepts observation + read_range without rag_citation", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_obs_aabbccddeeff00112233445566778899",
            kind: "observation",
            source_tool: "read_range",
            snippet: "legal snippet",
          },
        ]),
      ).toBe(true);
    });

    it("accepts observation + search_current_article without rag_citation", () => {
      expect(
        isReaderAskAgenticEvidenceList([
          {
            handle_id: "evh_obs_aabbccddeeff00112233445566778899",
            kind: "observation",
            source_tool: "search_current_article",
            snippet: "legal snippet",
          },
        ]),
      ).toBe(true);
    });
  });
});

// ---------------------------------------------------------------------------
// ASK-REASONING-R1: agentic.reasoning.* payload guards
// ---------------------------------------------------------------------------

describe("agentic reasoning payload guards", () => {
  const BASE = {
    execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
    message_id: "msg-1",
    thread_id: "thread-1",
    turn_run_id: "turn-1",
  };

  it("accepts well-formed started / delta / completed payloads", () => {
    expect(
      isReaderAskAgenticReasoningStartedPayload({
        ...BASE,
        seq: 0,
        projection_policy_version: "reasoning_projection_v1",
      }),
    ).toBe(true);
    expect(
      isReaderAskAgenticReasoningDeltaPayload({
        ...BASE,
        seq: 1,
        delta: "投影增量",
      }),
    ).toBe(true);
    expect(
      isReaderAskAgenticReasoningCompletedPayload({
        ...BASE,
        seq: 2,
        has_content: true,
        truncated: false,
        projection_policy_version: "reasoning_projection_v1",
      }),
    ).toBe(true);
  });

  it("rejects started carrying content fields", () => {
    expect(
      isReaderAskAgenticReasoningStartedPayload({
        ...BASE,
        seq: 0,
        projection_policy_version: "reasoning_projection_v1",
        delta: "leak",
      }),
    ).toBe(false);
  });

  it("rejects delta with seq 0, empty delta, or internal fields", () => {
    expect(isReaderAskAgenticReasoningDeltaPayload({ ...BASE, seq: 0, delta: "x" })).toBe(false);
    expect(isReaderAskAgenticReasoningDeltaPayload({ ...BASE, seq: 1, delta: "" })).toBe(false);
    expect(
      isReaderAskAgenticReasoningDeltaPayload({
        ...BASE,
        seq: 1,
        delta: "x",
        envelope_fingerprint: "fp",
      }),
    ).toBe(false);
  });

  it("rejects completed carrying delta or missing flags", () => {
    expect(
      isReaderAskAgenticReasoningCompletedPayload({
        ...BASE,
        seq: 2,
        has_content: true,
        truncated: false,
        projection_policy_version: "reasoning_projection_v1",
        delta: "leak",
      }),
    ).toBe(false);
    expect(
      isReaderAskAgenticReasoningCompletedPayload({ ...BASE, seq: 2, has_content: true }),
    ).toBe(false);
  });

  it("rejects wrong execution_version on all three events", () => {
    const legacy = { ...BASE, execution_version: "legacy" };
    expect(
      isReaderAskAgenticReasoningStartedPayload({
        ...legacy,
        seq: 0,
        projection_policy_version: "v1",
      }),
    ).toBe(false);
    expect(isReaderAskAgenticReasoningDeltaPayload({ ...legacy, seq: 1, delta: "x" })).toBe(false);
    expect(
      isReaderAskAgenticReasoningCompletedPayload({
        ...legacy,
        seq: 2,
        has_content: true,
        truncated: false,
        projection_policy_version: "v1",
      }),
    ).toBe(false);
  });

  it("parses agentic.reasoning.delta frames through the SSE consumer", async () => {
    const events = await collectEvents([
      `event: agentic.reasoning.started\ndata: ${JSON.stringify({ ...BASE, seq: 0, projection_policy_version: "reasoning_projection_v1" })}\n\n`,
      `event: agentic.reasoning.delta\ndata: ${JSON.stringify({ ...BASE, seq: 1, delta: "思考" })}\n\n`,
      `event: agentic.reasoning.completed\ndata: ${JSON.stringify({ ...BASE, seq: 2, has_content: true, truncated: false, projection_policy_version: "reasoning_projection_v1" })}\n\n`,
    ]);

    expect(events.map((e) => e.event)).toEqual([
      "agentic.reasoning.started",
      "agentic.reasoning.delta",
      "agentic.reasoning.completed",
    ]);
    expect(isReaderAskAgenticReasoningStartedPayload(events[0].data)).toBe(true);
    expect(isReaderAskAgenticReasoningDeltaPayload(events[1].data)).toBe(true);
    expect(isReaderAskAgenticReasoningCompletedPayload(events[2].data)).toBe(true);
  });
});
