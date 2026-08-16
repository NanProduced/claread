import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const bffMock = vi.fn();
vi.mock("@/services/bff/reading-records", () => ({
  hideReaderRecordFromRecentFromWeb: (...args: unknown[]) => bffMock(...args),
}));

import { DELETE } from "./route";

describe("DELETE /api/web/reader/records/:recordId/recent", () => {
  it("calls the BFF and returns ok with the DTO", async () => {
    bffMock.mockResolvedValue({
      ok: true,
      data: {
        record_id: "rr_1",
        status: "removed_from_recent",
        recent_hidden_at: "2026-08-16T00:00:00Z",
      },
    });

    const res = await DELETE({} as never, {
      params: Promise.resolve({ recordId: "rr_1" }),
    });

    expect(res.status).toBe(200);
    expect(bffMock).toHaveBeenCalledWith("rr_1");
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({
      ok: true,
      record_id: "rr_1",
      status: "removed_from_recent",
    });
  });

  it("passes idempotent already_removed through as success", async () => {
    bffMock.mockResolvedValue({
      ok: true,
      data: {
        record_id: "rr_1",
        status: "already_removed",
        recent_hidden_at: "2026-08-01T00:00:00Z",
      },
    });

    const res = await DELETE({} as never, {
      params: Promise.resolve({ recordId: "rr_1" }),
    });

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ ok: true, status: "already_removed" });
  });

  it("maps BFF auth_required to 401 without upstream raw text", async () => {
    bffMock.mockResolvedValue({
      ok: false,
      status: 401,
      code: "auth_required",
      message: "请先登录。",
    });

    const res = await DELETE({} as never, {
      params: Promise.resolve({ recordId: "rr_1" }),
    });

    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ ok: false, code: "auth_required" });
  });

  it("maps BFF upstream_unavailable to 503", async () => {
    bffMock.mockResolvedValue({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
      message: "透读服务暂时不可用，请稍后重试。",
    });

    const res = await DELETE({} as never, {
      params: Promise.resolve({ recordId: "rr_1" }),
    });

    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ ok: false, code: "upstream_unavailable" });
  });

  it("never echoes upstream raw error text in any error envelope", async () => {
    bffMock.mockResolvedValue({
      ok: false,
      status: 404,
      code: "upstream_error",
      message: "操作失败，请稍后重试。",
    });

    const res = await DELETE({} as never, {
      params: Promise.resolve({ recordId: "rr_1" }),
    });

    const body = await res.text();
    expect(body).not.toContain("raw upstream detail");
  });
});
