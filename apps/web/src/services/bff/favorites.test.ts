import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/services/bff/session", () => ({ getWebSession: vi.fn() }));
vi.mock("@/services/api/favorites", () => ({
  createFavorite: vi.fn(),
  deleteFavoriteByTargetKey: vi.fn(),
  listFavorites: vi.fn(),
}));

import {
  createFavorite,
  deleteFavoriteByTargetKey,
  listFavorites,
} from "@/services/api/favorites";
import { getWebSession } from "@/services/bff/session";
import {
  favoriteDailyReaderArticle,
  getDailyReaderArticleFavoriteState,
  unfavoriteDailyReaderArticle,
} from "./favorites";

const session = {
  kind: "authenticated" as const,
  sessionToken: "session-token",
  source: "cookie" as const,
};

describe("Daily Reader favorites BFF", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(session);
  });

  it("reads state using the canonical Daily Reader target identity", async () => {
    vi.mocked(listFavorites).mockResolvedValue({
      ok: true,
      data: {
        items: [
          {
            id: "favorite-id",
            user_id: "user-id",
            target_type: "daily_reader_article",
            target_key: "daily_reader_article:daily-test",
            payload_json: { article_id: "daily-test" },
            created_at: "2026-08-20T00:00:00Z",
            updated_at: "2026-08-20T00:00:00Z",
          },
        ],
        total: 1,
      },
    });

    const result = await getDailyReaderArticleFavoriteState(" daily-test ");

    expect(result).toMatchObject({ ok: true, favorited: true });
    expect(listFavorites).toHaveBeenCalledWith("session-token");
  });

  it("creates and removes the same target identity", async () => {
    vi.mocked(createFavorite).mockResolvedValue({
      ok: true,
      data: { id: "favorite-id", ok: true },
    });
    vi.mocked(deleteFavoriteByTargetKey).mockResolvedValue({
      ok: true,
      data: { deleted: true },
    });

    expect(await favoriteDailyReaderArticle("daily-test")).toMatchObject({
      ok: true,
      favorited: true,
    });
    expect(createFavorite).toHaveBeenCalledWith("session-token", {
      target_type: "daily_reader_article",
      target_key: "daily_reader_article:daily-test",
      payload_json: { article_id: "daily-test" },
    });

    expect(await unfavoriteDailyReaderArticle("daily-test")).toMatchObject({
      ok: true,
      favorited: false,
    });
    expect(deleteFavoriteByTargetKey).toHaveBeenCalledWith(
      "session-token",
      "daily_reader_article",
      "daily_reader_article:daily-test",
    );
  });

  it("turns an expired upstream session into a safe re-login response", async () => {
    vi.mocked(listFavorites).mockResolvedValue({
      ok: false,
      status: 401,
      message: "Invalid or expired session",
    });

    expect(await getDailyReaderArticleFavoriteState("daily-test")).toEqual({
      ok: false,
      status: 401,
      code: "upstream_auth_failed",
      message: "登录已失效，请重新登录。",
    });
  });
});
