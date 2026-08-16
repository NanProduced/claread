import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const bffMock = vi.fn();
vi.mock("@/services/bff/reading-records", () => ({
  getReadingRecordListFromWeb: (...args: unknown[]) => bffMock(...args),
}));

import { GET } from "./route";

describe("GET /api/web/reader/records", () => {
  it("maps recentOnly=true into the BFF options", async () => {
    bffMock.mockResolvedValue({ ok: true, items: [], total: 0, limit: 10 });

    const url = new URL("http://localhost/api/web/reader/records?limit=10&recentOnly=true");
    const res = await GET(new Request(url));

    expect(res.status).toBe(200);
    expect(bffMock).toHaveBeenCalledWith({
      limit: 10,
      recentOnly: true,
    });
  });

  it("does not send recentOnly when the query param is absent", async () => {
    bffMock.mockResolvedValue({ ok: true, items: [], total: 0, limit: 10 });

    const url = new URL("http://localhost/api/web/reader/records?limit=10");
    await GET(new Request(url));

    expect(bffMock).toHaveBeenCalledWith({
      limit: 10,
      recentOnly: undefined,
    });
  });

  it("maps recentOnly=false explicitly and keeps behavior identical", async () => {
    bffMock.mockResolvedValue({ ok: true, items: [], total: 0, limit: 10 });

    const url = new URL("http://localhost/api/web/reader/records?recentOnly=false");
    await GET(new Request(url));

    expect(bffMock).toHaveBeenCalledWith({
      recentOnly: false,
    });
  });

  it("passes through error results with their status", async () => {
    bffMock.mockResolvedValue({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
      message: "透读服务暂时不可用，请稍后重试。",
    });

    const url = new URL("http://localhost/api/web/reader/records");
    const res = await GET(new Request(url));

    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ ok: false, code: "upstream_unavailable" });
  });
});
