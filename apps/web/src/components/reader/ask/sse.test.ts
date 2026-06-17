import { describe, expect, it } from "vitest";
import type { ReaderAskStreamEnvelopeDto } from "@/types/api/reader-ask";
import { consumeReaderAskSse } from "./sse";

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

async function collectEvents(chunks: string[], signal?: AbortSignal): Promise<ReaderAskStreamEnvelopeDto[]> {
  const events: ReaderAskStreamEnvelopeDto[] = [];
  const response = makeSseResponse(chunks);
  await consumeReaderAskSse(response, (event) => events.push(event), signal);
  return events;
}

describe("consumeReaderAskSse", () => {
  it("parses a single message.delta event", async () => {
    const events = await collectEvents([
      "event: message.delta\ndata: {\"delta\":\"Hello\"}\n\n",
    ]);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({
      event: "message.delta",
      data: { delta: "Hello" },
    });
  });

  it("parses multi-event chunk with reasoning.delta and message.completed", async () => {
    const events = await collectEvents([
      "event: reasoning.delta\ndata: {\"delta\":\"thinking\"}\n\nevent: message.completed\ndata: {\"content_md\":\"done\"}\n\n",
    ]);

    expect(events).toHaveLength(2);
    expect(events[0].event).toBe("reasoning.delta");
    expect(events[0].data).toEqual({ delta: "thinking" });
    expect(events[1].event).toBe("message.completed");
    expect(events[1].data).toEqual({ content_md: "done" });
  });

  it("handles split chunk where one event spans two TCP chunks", async () => {
    const events = await collectEvents([
      "event: message.delta\ndata: {\"delta\":\"Hel",
      "lo\"}\n\n",
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
      "event: message.delta\ndata: {\"delta\":\"ok\"}\n\nevent: reasoning.delta\ndata: {\"delta\":\"incom",
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
      "event: message.delta\ndata: {\"delta\":\"valid\"}\n\nevent: message.delta\ndata: {invalid}\n\n",
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
      "event: reasoning.started\ndata: {\"message_id\":\"msg-1\"}\n\nevent: reasoning.delta\ndata: {\"delta\":\"step 1\"}\n\nevent: reasoning.completed\ndata: {}\n\n",
    ]);

    expect(events).toHaveLength(3);
    expect(events[0].event).toBe("reasoning.started");
    expect(events[1].event).toBe("reasoning.delta");
    expect(events[1].data).toEqual({ delta: "step 1" });
    expect(events[2].event).toBe("reasoning.completed");
  });

  it("parses message.completed with full payload", async () => {
    const events = await collectEvents([
      "event: message.completed\ndata: {\"id\":\"msg-1\",\"content_md\":\"done\",\"citations\":[]}\n\n",
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
      "event: message.delta\ndata: {broken}\n\nevent: message.completed\ndata: {\"id\":\"msg-1\",\"content_md\":\"done\"}\n\n",
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
      "event: message.completed\ndata: {\"id\":\"msg-1\",\"content_md\":\"done\"}\n\n",
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("error");
    expect(events[0].data).toMatchObject({ code: "SSE_PARSE_ERROR" });
  });
});
