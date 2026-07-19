import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const projectionMock = vi.fn();
vi.mock("@/services/bff/profile", () => ({
  getSettingsDialogProjection: () => projectionMock(),
}));

import { GET } from "./route";

describe("GET /api/web/settings-dialog", () => {
  it("returns 200 with the minimal DTO on success", async () => {
    projectionMock.mockResolvedValue({
      ok: true,
      httpStatus: 200,
      data: {
        accountData: {
          nickname: "Alice",
          displayFallback: "Alice",
          phone: "13800138000",
          status: "ready",
          avatarText: "A",
        },
        preferencesData: {
          readingGoal: "daily_reading",
          readingVariant: "intermediate_reading",
          canEdit: true,
        },
      },
    });

    const res = await GET();

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(true);
    expect(body.data).toEqual({
      accountData: {
        nickname: "Alice",
        displayFallback: "Alice",
        phone: "13800138000",
        status: "ready",
        avatarText: "A",
      },
      preferencesData: {
        readingGoal: "daily_reading",
        readingVariant: "intermediate_reading",
        canEdit: true,
      },
    });
  });

  it("success response can never produce data:null (type + runtime guarantee)", async () => {
    projectionMock.mockResolvedValue({
      ok: true,
      httpStatus: 200,
      data: {
        accountData: {
          nickname: "Carol",
          displayFallback: "Carol",
          phone: "13700000000",
          status: "ready",
          avatarText: "C",
        },
        preferencesData: {
          readingGoal: "exam",
          readingVariant: "cet",
          canEdit: true,
        },
      },
    });

    const res = await GET();

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(true);
    // The success arm of SettingsDialogProjectionResult guarantees a
    // non-null `data` at the type level; the route handler returns it
    // verbatim. Assert at runtime that the JSON body never carries
    // `data: null` on the success path.
    expect(body.data).not.toBeNull();
    expect(body.data).toBeDefined();
    expect(typeof body.data).toBe("object");
    // The success body must NOT carry an error-style `message` / `code` /
    // `status` envelope — those exist only on the error path.
    expect(body).not.toHaveProperty("message");
    expect(body).not.toHaveProperty("code");
    expect(body).not.toHaveProperty("status");
  });

  it("returns 401 with safe message when session is unauthenticated", async () => {
    projectionMock.mockResolvedValue({
      ok: false,
      httpStatus: 401,
      data: null,
      message: "当前会话已过期，请重新登录。",
    });

    const res = await GET();

    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(false);
    expect(body.status).toBe(401);
    expect(body.code).toBe("auth_required");
    expect(typeof body.message).toBe("string");
    expect((body.message as string).length).toBeGreaterThan(0);
  });

  it("returns 503 with safe message when upstream is unavailable", async () => {
    projectionMock.mockResolvedValue({
      ok: false,
      httpStatus: 503,
      data: null,
      message: "服务暂时不可用，请稍后重试。",
    });

    const res = await GET();

    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(false);
    expect(body.status).toBe(503);
    expect(body.code).toBe("upstream_unavailable");
    expect(body.message).toBe("服务暂时不可用，请稍后重试。");
  });

  it("returns 503 with safe message on upstream network failure (status 0)", async () => {
    projectionMock.mockResolvedValue({
      ok: false,
      httpStatus: 503,
      data: null,
      message: "服务暂时不可用，请稍后重试。",
    });

    const res = await GET();

    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(false);
    expect(body.code).toBe("upstream_unavailable");
  });

  it("passes through upstream 4xx status with safe message and code upstream_error", async () => {
    projectionMock.mockResolvedValue({
      ok: false,
      httpStatus: 404,
      data: null,
      message: "账户信息暂时不可用。",
    });

    const res = await GET();

    expect(res.status).toBe(404);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(false);
    expect(body.status).toBe(404);
    expect(body.code).toBe("upstream_error");
    expect(body.message).toBe("账户信息暂时不可用。");
  });

  it("never exposes quota / ledger / subscription fields in the success body", async () => {
    projectionMock.mockResolvedValue({
      ok: true,
      httpStatus: 200,
      data: {
        accountData: {
          nickname: "Alice",
          displayFallback: "Alice",
          phone: "13800138000",
          status: "ready",
          avatarText: "A",
        },
        preferencesData: {
          readingGoal: "daily_reading",
          readingVariant: "intermediate_reading",
          canEdit: true,
        },
      },
    });

    const res = await GET();
    const body = (await res.json()) as Record<string, unknown>;

    // Top-level forbidden fields
    expect(body).not.toHaveProperty("quota");
    expect(body).not.toHaveProperty("ledger");
    expect(body).not.toHaveProperty("subscription");

    // Nested forbidden fields inside data / accountData / preferencesData
    const data = body.data as Record<string, unknown>;
    expect(data).not.toHaveProperty("quota");
    expect(data).not.toHaveProperty("ledger");
    expect(data).not.toHaveProperty("subscription");
    const accountData = data.accountData as Record<string, unknown>;
    expect(accountData).not.toHaveProperty("quota");
    expect(accountData).not.toHaveProperty("ledger");
    expect(accountData).not.toHaveProperty("subscription");
    expect(accountData).not.toHaveProperty("quotaUsed");
    expect(accountData).not.toHaveProperty("quotaLimit");
    expect(accountData).not.toHaveProperty("bonusPoints");
    const preferencesData = data.preferencesData as Record<string, unknown>;
    expect(preferencesData).not.toHaveProperty("quota");
    expect(preferencesData).not.toHaveProperty("ledger");
    expect(preferencesData).not.toHaveProperty("subscription");
  });
});
