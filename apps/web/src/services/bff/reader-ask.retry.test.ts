import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
}));

vi.mock("@/services/api/reader-ask", () => ({
  retryUpstreamReadingRecordAskMessage: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import { retryUpstreamReadingRecordAskMessage } from "@/services/api/reader-ask";
import { retryReaderAskMessageForWeb } from "./reader-ask";

const mockSession = {
  kind: "authenticated" as const,
  sessionToken: "session-token",
  source: "cookie" as const,
};

const UUID = "11111111-1111-4111-8111-111111111111";

describe("retryReaderAskMessageForWeb", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects non-UUID message ids with typed 409 and never calls upstream", async () => {
    const res = await retryReaderAskMessageForWeb(
      "rr-1",
      "thread-1",
      "local-assistant-123",
      {},
    );
    expect(res.status).toBe(409);
    const body = (await res.json()) as { code?: string };
    expect(body.code).toBe("retry_target_not_persisted");
    expect(retryUpstreamReadingRecordAskMessage).not.toHaveBeenCalled();
  });

  it("forwards UUID targets to the upstream RR retry helper", async () => {
    const stream = new ReadableStream({
      start(c) {
        c.close();
      },
    });
    vi.mocked(retryUpstreamReadingRecordAskMessage).mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const res = await retryReaderAskMessageForWeb(
      "rr-1",
      "thread-1",
      UUID,
      { model: "ask-clarity" },
    );
    expect(res.status).toBe(200);
    expect(retryUpstreamReadingRecordAskMessage).toHaveBeenCalledWith(
      "rr-1",
      "thread-1",
      UUID,
      { model: "ask-clarity" },
      "session-token",
      undefined,
    );
  });
});
