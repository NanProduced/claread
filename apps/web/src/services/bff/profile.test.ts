import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const sessionMock = vi.fn();
vi.mock("@/services/bff/session", () => ({
  getWebSession: () => sessionMock(),
  projectSession: (session: unknown) => {
    const s = (session ?? {}) as { kind?: string };
    return {
      state:
        s.kind === "authenticated"
          ? "signed_in"
          : s.kind === "anonymous"
            ? "signed_out"
            : "limited_debug",
      source: "mock",
      hasAppAccess: true,
    };
  },
}));

const sessionMeMock = vi.fn();
const patchProfileMock = vi.fn();
vi.mock("@/services/api/auth", () => ({
  getUpstreamSessionMe: (...args: unknown[]) => sessionMeMock(...args),
  patchUpstreamProfile: (...args: unknown[]) => patchProfileMock(...args),
}));

const quotaMock = vi.fn();
vi.mock("@/services/api/quota", () => ({
  getUpstreamQuota: (...args: unknown[]) => quotaMock(...args),
}));

import { getSettingsDialogProjection } from "./profile";

describe("getSettingsDialogProjection", () => {
  beforeEach(() => {
    sessionMock.mockReset();
    sessionMeMock.mockReset();
    patchProfileMock.mockReset();
    quotaMock.mockReset();
  });

  it("returns ok projection with derived accountData and preferencesData on authenticated session", async () => {
    sessionMock.mockResolvedValue({
      kind: "authenticated",
      sessionToken: "tok",
      source: "cookie",
    });
    sessionMeMock.mockResolvedValue({
      ok: true,
      data: {
        user_id: "u_1",
        session_id: "s_1",
        nickname: "Alice",
        avatar_url: "",
        cumulative_article_count: 3,
        settings: {
          default_reading_goal: "exam",
          default_reading_variant: "cet",
        },
      },
    });

    const result = await getSettingsDialogProjection();

    expect(result.ok).toBe(true);
    // After `result.ok` narrowing, TypeScript guarantees the success arm
    // where `data` is `SettingsDialogData` (non-null). The guard below
    // exists only to satisfy the compiler's control-flow analysis; if the
    // discriminated union regresses, this test will fail at compile time.
    if (!result.ok) {
      throw new Error("expected success arm");
    }
    expect(result.httpStatus).toBe(200);
    expect(result.data).not.toBeNull();
    // Direct (non-optional) access — type-level proof that data is non-null.
    expect(result.data.accountData).toEqual({
      nickname: "Alice",
      displayFallback: "Alice",
      status: "ready",
      avatarText: "A",
    });
    expect(result.data.preferencesData).toEqual({
      readingGoal: "exam",
      readingVariant: "cet",
      canEdit: true,
    });
  });

  it("success projection can never produce data:null (type + runtime guarantee)", async () => {
    sessionMock.mockResolvedValue({
      kind: "authenticated",
      sessionToken: "tok",
      source: "cookie",
    });
    sessionMeMock.mockResolvedValue({
      ok: true,
      data: {
        user_id: "u_1",
        session_id: "s_1",
        nickname: "Bob",
        avatar_url: "",
        cumulative_article_count: 0,
        settings: {},
      },
    });

    const result = await getSettingsDialogProjection();

    // Type-level: `result.ok === true` narrows to the success arm where
    // `data` is `SettingsDialogData` (not nullable). The following
    // access would be a compile error if the discriminated union
    // allowed `data: null` on the success arm.
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error("expected success arm");
    }
    expect(result.data).not.toBeNull();
    expect(result.data).toBeDefined();
    expect(typeof result.data).toBe("object");
    expect(result.data.accountData).toBeDefined();
    expect(result.data.preferencesData).toBeDefined();
    // The success arm must NOT carry a `message` field — that field only
    // exists on the error arm. Asserting its absence documents the union.
    expect(result).not.toHaveProperty("message");
  });

  it("does NOT call getUpstreamQuota even on the happy path", async () => {
    sessionMock.mockResolvedValue({
      kind: "authenticated",
      sessionToken: "tok",
      source: "cookie",
    });
    sessionMeMock.mockResolvedValue({
      ok: true,
      data: {
        user_id: "u_1",
        session_id: "s_1",
        nickname: "Alice",
        avatar_url: "",
        cumulative_article_count: 0,
        settings: {},
      },
    });

    await getSettingsDialogProjection();

    expect(quotaMock).not.toHaveBeenCalled();
  });

  it("falls back to 'Web User' display name when nickname is empty", async () => {
    sessionMock.mockResolvedValue({
      kind: "authenticated",
      sessionToken: "tok",
      source: "cookie",
    });
    sessionMeMock.mockResolvedValue({
      ok: true,
      data: {
        user_id: "u_1",
        session_id: "s_1",
        nickname: "",
        avatar_url: "",
        cumulative_article_count: 0,
        settings: null,
      },
    });

    const result = await getSettingsDialogProjection();

    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error("expected success arm");
    }
    expect(result.data.accountData.nickname).toBe("");
    expect(result.data.accountData.displayFallback).toBe("Web User");
    expect(result.data.accountData.avatarText).toBe("W");
    // missing settings -> default reading defaults
    expect(result.data.preferencesData.readingGoal).toBe("daily_reading");
    expect(result.data.preferencesData.readingVariant).toBe("intermediate_reading");
    expect(result.data.preferencesData.canEdit).toBe(true);
  });

  it("returns 401 with null data for anonymous sessions without upstream calls", async () => {
    sessionMock.mockResolvedValue({ kind: "anonymous", source: "none" });

    const result = await getSettingsDialogProjection();

    expect(result.ok).toBe(false);
    if (result.ok) {
      throw new Error("expected error arm");
    }
    expect(result.httpStatus).toBe(401);
    expect(result.data).toBeNull();
    expect(result.message).toBeTruthy();
    expect(sessionMeMock).not.toHaveBeenCalled();
    expect(quotaMock).not.toHaveBeenCalled();
  });

  it("maps upstream 5xx session/me failure to 503 with safe message and null data", async () => {
    sessionMock.mockResolvedValue({
      kind: "authenticated",
      sessionToken: "tok",
      source: "cookie",
    });
    sessionMeMock.mockResolvedValue({
      ok: false,
      status: 500,
      message: "internal error detail",
    });

    const result = await getSettingsDialogProjection();

    expect(result.ok).toBe(false);
    if (result.ok) {
      throw new Error("expected error arm");
    }
    expect(result.httpStatus).toBe(503);
    expect(result.data).toBeNull();
    // safe fallback Chinese message, no leak
    expect(result.message).not.toContain("internal error detail");
    expect(quotaMock).not.toHaveBeenCalled();
  });

  it("maps upstream network failure (status 0) to 503 with safe message", async () => {
    sessionMock.mockResolvedValue({
      kind: "authenticated",
      sessionToken: "tok",
      source: "cookie",
    });
    sessionMeMock.mockResolvedValue({
      ok: false,
      status: 0,
      message: "fetch failed",
    });

    const result = await getSettingsDialogProjection();

    expect(result.ok).toBe(false);
    if (result.ok) {
      throw new Error("expected error arm");
    }
    expect(result.httpStatus).toBe(503);
    expect(result.data).toBeNull();
    expect(result.message).not.toContain("fetch failed");
    expect(quotaMock).not.toHaveBeenCalled();
  });

  it("passes through upstream 4xx (non-5xx, non-0) failure status with safe message", async () => {
    sessionMock.mockResolvedValue({
      kind: "authenticated",
      sessionToken: "tok",
      source: "cookie",
    });
    sessionMeMock.mockResolvedValue({
      ok: false,
      status: 404,
      message: "user not found",
    });

    const result = await getSettingsDialogProjection();

    expect(result.ok).toBe(false);
    if (result.ok) {
      throw new Error("expected error arm");
    }
    expect(result.httpStatus).toBe(404);
    expect(result.data).toBeNull();
    expect(result.message).not.toContain("user not found");
    expect(quotaMock).not.toHaveBeenCalled();
  });
});
