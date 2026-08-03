import { describe, expect, it, vi } from "vitest";

import { createCurrentPageIdentityLoader } from "./current-page-identity-loader";

const FENCE = {
  readingRecordId: "record-1",
  baseId: "base-1",
  recordGeneration: 3,
} as const;

function okBody(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    reading_record_id: FENCE.readingRecordId,
    active_base_id: FENCE.baseId,
    record_generation: FENCE.recordGeneration,
    stable_document: { stable_document_id: "stable-1" },
    base: { base_id: FENCE.baseId },
    blocks: [],
    anchor_segments: [],
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("createCurrentPageIdentityLoader", () => {
  it("29. ready response matches snapshot fence", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(okBody()));
    const load = createCurrentPageIdentityLoader({ ...FENCE, fetchImpl });
    const identity = await load();
    expect(identity).toEqual({
      readingRecordId: "record-1",
      baseId: "base-1",
      recordGeneration: 3,
      stableDocument: { status: "ready", stableDocumentId: "stable-1" },
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const firstCall = fetchImpl.mock.calls[0] as unknown as [string, ...unknown[]];
    expect(String(firstCall[0])).toContain(
      "/api/web/reader/records/record-1/stable-document",
    );
  });

  it("30. record mismatch → stale", async () => {
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () =>
        jsonResponse(okBody({ reading_record_id: "other-record" })),
    });
    expect(await load()).toMatchObject({
      stableDocument: { status: "stale", stableDocumentId: null },
    });
  });

  it("31. base mismatch → stale", async () => {
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () => jsonResponse(okBody({ active_base_id: "base-other" })),
    });
    expect(await load()).toMatchObject({
      stableDocument: { status: "stale", stableDocumentId: null },
    });
  });

  it("32. generation mismatch → stale", async () => {
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () => jsonResponse(okBody({ record_generation: 99 })),
    });
    expect(await load()).toMatchObject({
      stableDocument: { status: "stale", stableDocumentId: null },
    });
  });

  it("33. 404/not-ready", async () => {
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () =>
        jsonResponse({ ok: false, status: 404, message: "missing" }, 404),
    });
    expect(await load()).toMatchObject({
      stableDocument: { status: "not_ready", stableDocumentId: null },
    });
  });

  it("34. network failure → failed", async () => {
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () => {
        throw new Error("SECRET_NETWORK_DOWN");
      },
    });
    const identity = await load();
    expect(identity.stableDocument.status).toBe("failed");
    expect(JSON.stringify(identity)).not.toContain("SECRET_NETWORK_DOWN");
  });

  it("35. 5xx → failed", async () => {
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () =>
        jsonResponse({ ok: false, status: 503, message: "boom" }, 503),
    });
    expect(await load()).toMatchObject({
      stableDocument: { status: "failed", stableDocumentId: null },
    });
  });

  it("36. malformed JSON → failed", async () => {
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () =>
        new Response("not-json", {
          status: 200,
          headers: { "content-type": "text/plain" },
        }),
    });
    expect(await load()).toMatchObject({
      stableDocument: { status: "failed", stableDocumentId: null },
    });
  });

  it("37. does not leak error message text into identity", async () => {
    const secret = "UPSTREAM_STACK_TRACE_xyz";
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () =>
        jsonResponse({ ok: false, status: 500, message: secret }, 500),
    });
    const identity = await load();
    expect(JSON.stringify(identity)).not.toContain(secret);
  });

  it("38. concurrent loads share one in-flight request", async () => {
    let resolveFetch!: (value: Response) => void;
    const fetchImpl = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const load = createCurrentPageIdentityLoader({ ...FENCE, fetchImpl });
    const p1 = load();
    const p2 = load();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    resolveFetch!(jsonResponse(okBody()));
    const [a, b] = await Promise.all([p1, p2]);
    expect(a).toEqual(b);
    expect(a.stableDocument.status).toBe("ready");
  });

  it("39. ready results are cached", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(okBody()));
    const load = createCurrentPageIdentityLoader({ ...FENCE, fetchImpl });
    await load();
    await load();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("40. failed/not_ready/stale can retry", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ ok: false, status: 503, message: "down" }, 503),
      )
      .mockResolvedValueOnce(jsonResponse(okBody()));
    const load = createCurrentPageIdentityLoader({ ...FENCE, fetchImpl });
    expect((await load()).stableDocument.status).toBe("failed");
    expect((await load()).stableDocument.status).toBe("ready");
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("HTTP 200 without ok:true is failed (not ready cache)", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        // missing ok
        reading_record_id: FENCE.readingRecordId,
        active_base_id: FENCE.baseId,
        record_generation: FENCE.recordGeneration,
        stable_document: { stable_document_id: "stable-1" },
      }),
    );
    const load = createCurrentPageIdentityLoader({ ...FENCE, fetchImpl });
    expect((await load()).stableDocument.status).toBe("failed");
    // Not cached as ready — second call retries
    expect((await load()).stableDocument.status).toBe("failed");
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("HTTP 200 with ok:false-typed values is failed", async () => {
    for (const badOk of [false, "true", 1, null]) {
      const load = createCurrentPageIdentityLoader({
        ...FENCE,
        fetchImpl: async () =>
          jsonResponse({
            ok: badOk,
            reading_record_id: FENCE.readingRecordId,
            active_base_id: FENCE.baseId,
            record_generation: FENCE.recordGeneration,
            stable_document: { stable_document_id: "stable-1" },
          }),
      });
      expect((await load()).stableDocument.status).toBe("failed");
    }
  });

  it("empty body 404 is not_ready (HTTP status preferred)", async () => {
    const load = createCurrentPageIdentityLoader({
      ...FENCE,
      fetchImpl: async () =>
        new Response("", {
          status: 404,
          headers: { "content-type": "text/plain" },
        }),
    });
    expect(await load()).toMatchObject({
      stableDocument: { status: "not_ready", stableDocumentId: null },
    });
  });
});
