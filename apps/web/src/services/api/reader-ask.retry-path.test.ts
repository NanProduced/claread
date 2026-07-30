import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

describe("upstream retry path is /retry/stream only (BFF→FastAPI)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("retryUpstreamReadingRecordAskMessage hits .../retry/stream", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, { status: 200, headers: { "content-type": "text/event-stream" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { retryUpstreamReadingRecordAskMessage } = await import("./reader-ask");
    await retryUpstreamReadingRecordAskMessage(
      "rr-1",
      "thread-1",
      "11111111-1111-4111-8111-111111111111",
      { model: null },
      "tok",
    );

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/retry/stream");
    expect(url).toMatch(/\/messages\/[^/]+\/retry\/stream$/);
  });
});
