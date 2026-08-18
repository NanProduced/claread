import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const bffMock = vi.fn();
vi.mock("@/services/bff/reading-records", () => ({
  recoverReaderRecordFromWeb: (...args: unknown[]) => bffMock(...args),
}));

import { POST } from "./route";

function callRoute(recordId = "rr_1") {
  const request = new Request("http://localhost/recovery", {
    method: "POST",
  });
  return POST(request, { params: Promise.resolve({ recordId }) });
}

describe("POST /api/web/reader/records/:recordId/recovery", () => {
  beforeEach(() => {
    bffMock.mockReset();
  });

  it("returns { ok: true, ...dto } for a successful recovery", async () => {
    bffMock.mockResolvedValue({
      ok: true,
      data: {
        record_id: "rr_1",
        outcome: "recovery_started",
        previous_product_state: "failed",
        next_product_state: "readable_enhancing",
        record_generation: 2,
        successor_job_count: 2,
      },
    });

    const res = await callRoute();

    expect(res.status).toBe(200);
    expect(bffMock).toHaveBeenCalledWith("rr_1");
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({
      ok: true,
      record_id: "rr_1",
      outcome: "recovery_started",
      previous_product_state: "failed",
      next_product_state: "readable_enhancing",
      record_generation: 2,
      successor_job_count: 2,
    });
  });

  it("never reads the request body", async () => {
    bffMock.mockResolvedValue({
      ok: true,
      data: {
        record_id: "rr_1",
        outcome: "nothing_to_recover",
        previous_product_state: "failed",
        next_product_state: "failed",
        record_generation: 2,
        successor_job_count: 0,
      },
    });

    const request = new Request("http://localhost/recovery", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ trigger: "automatic", trace_id: "x" }),
    });
    const res = await POST(request, {
      params: Promise.resolve({ recordId: "rr_1" }),
    });

    expect(res.status).toBe(200);
    expect(bffMock).toHaveBeenCalledTimes(1);
    expect(bffMock).toHaveBeenCalledWith("rr_1");
  });

  it.each([
    [404, "auth_required"],
    [409, "upstream_error"],
    [503, "upstream_unavailable"],
  ] as const)(
    "returns the sanitized %i envelope without raw upstream text",
    async (status, code) => {
      bffMock.mockResolvedValue({
        ok: false,
        status,
        code,
        message: "固定友好文案",
        raw_upstream: "SELECT secret FROM credentials",
      });

      const res = await callRoute();

      expect(res.status).toBe(status);
      const body = (await res.json()) as Record<string, unknown>;
      expect(body).toEqual({
        ok: false,
        status,
        code,
        message: "固定友好文案",
      });
      const text = JSON.stringify(body);
      expect(text).not.toContain("SELECT secret FROM credentials");
      expect("raw_upstream" in body).toBe(false);
    },
  );
});
