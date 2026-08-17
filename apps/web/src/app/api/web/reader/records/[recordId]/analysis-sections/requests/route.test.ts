import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const commandMock = vi.fn();
vi.mock("@/services/bff/reader-plate", () => ({
  submitReaderAnalysisSectionRequestFromWeb: (...args: unknown[]) =>
    commandMock(...args),
}));

import { POST } from "./route";

function request(body: unknown, jsonFail = false): Request {
  return {
    json: jsonFail
      ? () => Promise.reject(new SyntaxError("bad json"))
      : () => Promise.resolve(body),
  } as Request;
}

describe("POST /api/web/reader/records/:recordId/analysis-sections/requests", () => {
  it("forwards single body to the BFF command", async () => {
    commandMock.mockResolvedValue({
      ok: true,
      outcome: "started",
      accepted_section_ids: ["ras1_a"],
      event_sequence: 3,
      reason_code: null,
    });

    const res = await POST(request({ scope: "single", sectionId: "ras1_a" }), {
      params: Promise.resolve({ recordId: "rec_1" }),
    });

    expect(commandMock).toHaveBeenCalledWith("rec_1", {
      scope: "single",
      sectionId: "ras1_a",
    });
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({
      ok: true,
      outcome: "started",
    });
  });

  it("forwards remaining body to the BFF command", async () => {
    commandMock.mockResolvedValue({
      ok: true,
      outcome: "already_complete",
      accepted_section_ids: [],
      event_sequence: null,
      reason_code: null,
    });

    const res = await POST(request({ scope: "remaining" }), {
      params: Promise.resolve({ recordId: "rec_1" }),
    });

    expect(commandMock).toHaveBeenCalledWith("rec_1", {
      scope: "remaining",
      sectionId: undefined,
    });
    expect(res.status).toBe(200);
  });

  it("returns BFF error status", async () => {
    commandMock.mockResolvedValue({
      ok: false,
      status: 409,
      code: "analysis_section_conflict",
      message: "文章状态已更新，请刷新后重试。",
    });

    const res = await POST(request({ scope: "single", sectionId: "ras1_a" }), {
      params: Promise.resolve({ recordId: "rec_1" }),
    });

    expect(res.status).toBe(409);
    await expect(res.json()).resolves.toMatchObject({
      ok: false,
      code: "analysis_section_conflict",
    });
  });

  it("returns 400 for malformed JSON", async () => {
    commandMock.mockResolvedValue({
      ok: false,
      status: 400,
      code: "invalid_input",
      message: "scope 必须是 single 或 remaining。",
    });

    const res = await POST(request({}, true), {
      params: Promise.resolve({ recordId: "rec_1" }),
    });

    expect(res.status).toBe(400);
    expect(commandMock).toHaveBeenCalledWith("rec_1", {
      scope: undefined,
      sectionId: undefined,
    });
  });

  it.each([null, [], "text", 1, true] as const)(
    "returns 400 for JSON root %j",
    async (root) => {
      commandMock.mockResolvedValue({
        ok: false,
        status: 400,
        code: "invalid_input",
        message: "scope 必须是 single 或 remaining。",
      });

      const res = await POST(request(root), {
        params: Promise.resolve({ recordId: "rec_1" }),
      });

      expect(res.status).toBe(400);
      await expect(res.json()).resolves.toMatchObject({
        ok: false,
        code: "invalid_input",
      });
      expect(commandMock).toHaveBeenCalledWith("rec_1", {
        scope: undefined,
        sectionId: undefined,
      });
    },
  );
});
