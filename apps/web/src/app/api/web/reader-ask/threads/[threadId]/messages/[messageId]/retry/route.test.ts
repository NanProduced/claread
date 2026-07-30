import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const getWebSession = vi.fn();
vi.mock("@/services/bff/session", () => ({
  getWebSession: (...args: unknown[]) => getWebSession(...args),
}));

const retryUpstreamReadingRecordAskMessage = vi.fn();
const retryUpstreamReaderAskMessage = vi.fn();
vi.mock("@/services/api/reader-ask", () => ({
  retryUpstreamReadingRecordAskMessage: (...args: unknown[]) =>
    retryUpstreamReadingRecordAskMessage(...args),
  retryUpstreamReaderAskMessage: (...args: unknown[]) =>
    retryUpstreamReaderAskMessage(...args),
}));

import { POST } from "./route";

const UUID = "11111111-1111-4111-8111-111111111111";
const LOCAL = "local-assistant-1710000000";

describe("POST /api/web/reader-ask/.../retry (ASK-RETRY-CONTRACT-R0)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getWebSession.mockResolvedValue({
      kind: "authenticated",
      sessionToken: "tok",
      source: "cookie",
    });
  });

  it("forwards UUID targets to upstream /retry/stream and never invents a browser alias", async () => {
    const bodyStream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("event: done\ndata: {}\n\n"));
        controller.close();
      },
    });
    retryUpstreamReadingRecordAskMessage.mockResolvedValue(
      new Response(bodyStream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const request = new Request(
      `http://127.0.0.1/api/web/reader-ask/threads/thread-1/messages/${UUID}/retry?recordId=rr-1&record_scope=reading_record`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ model: "ask-clarity" }),
      },
    );

    const res = await POST(request, {
      params: Promise.resolve({ threadId: "thread-1", messageId: UUID }),
    });

    expect(res.status).toBe(200);
    expect(retryUpstreamReadingRecordAskMessage).toHaveBeenCalledTimes(1);
    const call = retryUpstreamReadingRecordAskMessage.mock.calls[0];
    expect(call?.[0]).toBe("rr-1");
    expect(call?.[1]).toBe("thread-1");
    expect(call?.[2]).toBe(UUID);
    // Upstream helper always hits /retry/stream (asserted in services/api tests).
  });

  it("returns typed 409 for non-UUID local-assistant targets and does not call upstream", async () => {
    const request = new Request(
      `http://127.0.0.1/api/web/reader-ask/threads/thread-1/messages/${LOCAL}/retry?recordId=rr-1&record_scope=reading_record`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      },
    );

    const res = await POST(request, {
      params: Promise.resolve({ threadId: "thread-1", messageId: LOCAL }),
    });

    expect(res.status).toBe(409);
    const body = (await res.json()) as { code?: string };
    expect(body.code).toBe("retry_target_not_persisted");
    expect(retryUpstreamReadingRecordAskMessage).not.toHaveBeenCalled();
    expect(retryUpstreamReaderAskMessage).not.toHaveBeenCalled();
  });
});
