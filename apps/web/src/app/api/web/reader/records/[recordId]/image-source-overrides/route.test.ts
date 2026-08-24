import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("server-only", () => ({}));

const upsertMock = vi.fn();
const deleteMock = vi.fn();
vi.mock("@/services/bff/reader-plate", () => ({
  upsertReaderImageSourceOverrideFromWeb: (...args: unknown[]) => upsertMock(...args),
  deleteReaderImageSourceOverrideFromWeb: (...args: unknown[]) => deleteMock(...args),
}));

import { PUT, DELETE } from "./route";

function ctx(recordId: string) {
  return { params: Promise.resolve({ recordId }) } as unknown as { params: Promise<{ recordId: string }> };
}

beforeEach(() => {
  upsertMock.mockReset();
  deleteMock.mockReset();
});

describe("PUT /api/web/reader/records/[recordId]/image-source-overrides", () => {
  it("forwards raw url verbatim including whitespace and unsafe", async () => {
    upsertMock.mockResolvedValue({ ok: true, last_event_sequence: 123 });
    const raw = "  javascript:alert(1)  ";
    const req = new Request("http://localhost/api/web/reader/records/rec1/image-source-overrides", {
      method: "PUT",
      body: JSON.stringify({
        stable_document_id: "doc-uuid-1",
        block_id: "b1",
        inline_ordinal: null,
        url: raw,
      }),
      headers: { "content-type": "application/json" },
    });
    const res = await PUT(req, ctx("rec1"));
    expect(res.status).toBe(200);
    expect(upsertMock).toHaveBeenCalledWith({
      recordId: "rec1",
      stableDocumentId: "doc-uuid-1",
      blockId: "b1",
      inlineOrdinal: null,
      url: raw,
    });
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({ ok: true, last_event_sequence: 123 });
    expect(body).not.toHaveProperty("sessionToken");
  });

  it("forwards empty string verbatim", async () => {
    upsertMock.mockResolvedValue({ ok: true, last_event_sequence: 124 });
    const req = new Request("http://localhost/api/web/reader/records/rec1/image-source-overrides", {
      method: "PUT",
      body: JSON.stringify({
        stable_document_id: "doc-uuid-1",
        block_id: "b1",
        inline_ordinal: null,
        url: "",
      }),
    });
    const res = await PUT(req, ctx("rec1"));
    expect(res.status).toBe(200);
    expect(upsertMock).toHaveBeenCalledWith(expect.objectContaining({ url: "" }));
  });

  it("returns 400 on invalid ordinal and does not leak session", async () => {
    upsertMock.mockResolvedValue({ ok: false, status: 400, code: "invalid_input", message: "inline_ordinal 必须为 null 或大于等于 0 的整数。" });
    const req = new Request("http://localhost/api/web/reader/records/rec1/image-source-overrides", {
      method: "PUT",
      body: JSON.stringify({
        stable_document_id: "doc-uuid-1",
        block_id: "b1",
        inline_ordinal: -1,
        url: "https://example.com/a.png",
      }),
    });
    const res = await PUT(req, ctx("rec1"));
    expect(upsertMock).toHaveBeenCalled();
    expect(res.status).toBe(400);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).not.toHaveProperty("sessionToken");
  });

  it("maps 404 to 404 and 5xx to 503 without leaking upstream", async () => {
    upsertMock.mockResolvedValue({ ok: false, status: 404, code: "record_not_found", message: "not found" });
    let req = new Request("http://localhost/api/web/reader/records/rec1/image-source-overrides", {
      method: "PUT",
      body: JSON.stringify({ stable_document_id: "doc", block_id: "b1", inline_ordinal: null, url: "https://a" }),
    });
    let res = await PUT(req, ctx("rec1"));
    expect(res.status).toBe(404);

    upsertMock.mockResolvedValue({ ok: false, status: 503, code: "upstream_unavailable", message: "透读服务暂时不可用，请稍后重试。" });
    req = new Request("http://localhost/api/web/reader/records/rec1/image-source-overrides", {
      method: "PUT",
      body: JSON.stringify({ stable_document_id: "doc", block_id: "b1", inline_ordinal: null, url: "https://a" }),
    });
    res = await PUT(req, ctx("rec1"));
    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ ok: false, code: "upstream_unavailable" });
  });

  it("requires session: maps auth_required to 401", async () => {
    upsertMock.mockResolvedValue({ ok: false, status: 401, code: "auth_required", message: "请先登录" });
    const req = new Request("http://localhost/api/web/reader/records/rec1/image-source-overrides", {
      method: "PUT",
      body: JSON.stringify({ stable_document_id: "doc", block_id: "b1", inline_ordinal: null, url: "https://a" }),
    });
    const res = await PUT(req, ctx("rec1"));
    expect(res.status).toBe(401);
  });
});

describe("DELETE /api/web/reader/records/[recordId]/image-source-overrides", () => {
  it("standalone has no inline_ordinal query", async () => {
    deleteMock.mockResolvedValue({ ok: true, last_event_sequence: 200 });
    const req = new Request(
      "http://localhost/api/web/reader/records/rec1/image-source-overrides?stable_document_id=doc-uuid-1&block_id=b1",
      { method: "DELETE" },
    );
    const res = await DELETE(req, ctx("rec1"));
    expect(res.status).toBe(200);
    expect(deleteMock).toHaveBeenCalledWith({
      recordId: "rec1",
      stableDocumentId: "doc-uuid-1",
      blockId: "b1",
      inlineOrdinal: null,
    });
  });

  it("inline has exact ordinal", async () => {
    deleteMock.mockResolvedValue({ ok: true, last_event_sequence: 201 });
    const req = new Request(
      "http://localhost/api/web/reader/records/rec1/image-source-overrides?stable_document_id=doc-uuid-1&block_id=b1&inline_ordinal=2",
      { method: "DELETE" },
    );
    const res = await DELETE(req, ctx("rec1"));
    expect(res.status).toBe(200);
    expect(deleteMock).toHaveBeenCalledWith({
      recordId: "rec1",
      stableDocumentId: "doc-uuid-1",
      blockId: "b1",
      inlineOrdinal: "2",
    });
  });

  it("invalid ordinal fails closed with 400", async () => {
    deleteMock.mockResolvedValue({ ok: false, status: 400, code: "invalid_input", message: "inline_ordinal 必须为 null 或大于等于 0 的整数。" });
    const req = new Request(
      "http://localhost/api/web/reader/records/rec1/image-source-overrides?stable_document_id=doc&block_id=b1&inline_ordinal=-1",
      { method: "DELETE" },
    );
    const res = await DELETE(req, ctx("rec1"));
    expect(deleteMock).toHaveBeenCalled();
    expect(res.status).toBe(400);
  });

  it("path encoding is preserved via BFF", async () => {
    deleteMock.mockResolvedValue({ ok: true, last_event_sequence: 202 });
    const blockId = "b/1?x";
    const req = new Request(
      `http://localhost/api/web/reader/records/rec1/image-source-overrides?stable_document_id=doc-uuid-1&block_id=${encodeURIComponent(blockId)}`,
      { method: "DELETE" },
    );
    const res = await DELETE(req, ctx("rec1"));
    expect(res.status).toBe(200);
    expect(deleteMock).toHaveBeenCalledWith(expect.objectContaining({ blockId }));
  });

  it("response only contains safe fields", async () => {
    deleteMock.mockResolvedValue({ ok: true, last_event_sequence: 203 });
    const req = new Request(
      "http://localhost/api/web/reader/records/rec1/image-source-overrides?stable_document_id=doc&block_id=b1",
      { method: "DELETE" },
    );
    const res = await DELETE(req, ctx("rec1"));
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({ ok: true, last_event_sequence: 203 });
    expect(body).not.toHaveProperty("sessionToken");
  });
});

describe("no GET", () => {
  it("GET is not exported", async () => {
    const mod = await import("./route");
    expect((mod as Record<string, unknown>).GET).toBeUndefined();
  });
});
