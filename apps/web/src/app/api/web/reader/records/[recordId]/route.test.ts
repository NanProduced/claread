import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const bffMock = vi.fn();
vi.mock("@/services/bff/reading-records", () => ({
  deleteReaderRecordFromWeb: (...args: unknown[]) => bffMock(...args),
}));

import { DELETE } from "./route";

describe("DELETE /api/web/reader/records/:recordId", () => {
  it("calls the BFF and returns ok with the delete DTO", async () => {
    bffMock.mockResolvedValue({
      ok: true,
      data: {
        record_id: "rr_1",
        status: "deleted",
        deleted_at: "2026-08-16T00:00:00Z",
        vector_gc_intent_recorded: true,
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
      status: "deleted",
      vector_gc_intent_recorded: true,
    });
  });

  it("passes idempotent already_deleted through as success", async () => {
    bffMock.mockResolvedValue({
      ok: true,
      data: {
        record_id: "rr_1",
        status: "already_deleted",
        deleted_at: "2026-08-01T00:00:00Z",
        vector_gc_intent_recorded: true,
      },
    });

    const res = await DELETE({} as never, {
      params: Promise.resolve({ recordId: "rr_1" }),
    });

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ ok: true, status: "already_deleted" });
  });

  it("maps BFF upstream_auth_failed to 401", async () => {
    bffMock.mockResolvedValue({
      ok: false,
      status: 401,
      code: "upstream_auth_failed",
      message: "登录态已失效，请重新登录后再试。",
    });

    const res = await DELETE({} as never, {
      params: Promise.resolve({ recordId: "rr_1" }),
    });

    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ ok: false, code: "upstream_auth_failed" });
  });

  it("maps BFF 5xx to 503 upstream_unavailable", async () => {
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
