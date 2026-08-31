import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const sourcePreviewMock = vi.fn();
vi.mock("@/services/bff/reader-plate", () => ({
  getReaderSourcePreviewFromWeb: (...args: unknown[]) =>
    sourcePreviewMock(...args),
}));

import { dynamic, GET } from "./route";

function context(recordId: string) {
  return { params: Promise.resolve({ recordId }) };
}

beforeEach(() => {
  sourcePreviewMock.mockReset();
});

describe("GET /api/web/reader/records/[recordId]/source-preview", () => {
  it("passes the original request and exact recordId through and returns the binary Response", async () => {
    const upstreamResponse = new Response(Uint8Array.from([1, 2, 3]), {
      status: 200,
      headers: { "Content-Type": "image/png" },
    });
    sourcePreviewMock.mockResolvedValue(upstreamResponse);
    const request = new Request(
      "http://localhost/api/web/reader/records/record%2Fexact/source-preview?expected_generation=17",
    );

    const response = await GET(request, context("record/exact"));

    expect(sourcePreviewMock).toHaveBeenCalledWith(request, "record/exact");
    expect(response).toBe(upstreamResponse);
    expect(Array.from(new Uint8Array(await response.arrayBuffer()))).toEqual([
      1, 2, 3,
    ]);
  });

  it("returns the same empty error Response without JSON wrapping", async () => {
    const upstreamResponse = new Response(null, { status: 404 });
    sourcePreviewMock.mockResolvedValue(upstreamResponse);
    const request = new Request(
      "http://localhost/api/web/reader/records/missing/source-preview?expected_generation=1",
    );

    const response = await GET(request, context("missing"));

    expect(response).toBe(upstreamResponse);
    expect(response.status).toBe(404);
    expect(await response.text()).toBe("");
    expect(response.headers.get("content-type")).toBeNull();
  });

  it("is always dynamic", () => {
    expect(dynamic).toBe("force-dynamic");
  });
});
