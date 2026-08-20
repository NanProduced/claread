import "server-only";

import {
  createFavorite,
  deleteFavoriteByTargetKey,
  listFavorites,
} from "@/services/api/favorites";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { FavoriteResponseDto } from "@/types/api/favorites";
import type { FavoriteTargetType } from "@claread/contracts";

const READING_RECORD_TARGET_TYPE: FavoriteTargetType = "reading_record";
const DAILY_READER_TARGET_TYPE: FavoriteTargetType = "daily_reader_article";

export type FavoriteBffResult =
  | {
      ok: true;
      favorited: boolean;
      favorite?: FavoriteResponseDto;
      message?: string;
    }
  | {
      ok: false;
      status: number;
      code:
        | "bad_request"
        | "auth_required"
        | "upstream_auth_failed"
        | "upstream_unavailable"
        | "upstream_error";
      message: string;
    };

function normalizeTargetKey(value: string): string {
  return value.trim();
}

function dailyReaderTargetKey(articleId: string): string {
  return `${DAILY_READER_TARGET_TYPE}:${articleId}`;
}

function badRequest(message: string): FavoriteBffResult {
  return { ok: false, status: 400, code: "bad_request", message };
}

function authError(session: WebSession): FavoriteBffResult {
  return {
    ok: false,
    status: 401,
    code: "auth_required",
    message:
      session.kind === "mock_phone"
        ? "当前登录态不能访问真实收藏，请使用真实登录会话后操作。"
        : "请先登录后再操作收藏。",
  };
}

function upstreamError(status: number, message: string): FavoriteBffResult {
  const unavailable = status === 0 || status >= 500;

  return {
    ok: false,
    status: status === 0 ? 503 : status,
    code: unavailable
      ? "upstream_unavailable"
      : status === 401
        ? "upstream_auth_failed"
        : "upstream_error",
    message: unavailable
      ? "收藏服务暂时不可用，请稍后重试。"
      : status === 401
        ? "登录已失效，请重新登录。"
        : message,
  };
}

function findFavorite(
  items: FavoriteResponseDto[],
  targetType: FavoriteTargetType,
  targetKey: string,
) {
  return items.find(
    (item) => item.target_type === targetType && item.target_key === targetKey,
  );
}

async function getFavoriteState(
  targetType: FavoriteTargetType,
  targetKey: string,
): Promise<FavoriteBffResult> {
  const session = await getWebSession();
  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await listFavorites(session.sessionToken);
  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  const favorite = findFavorite(upstreamResult.data.items, targetType, targetKey);

  return { ok: true, favorited: Boolean(favorite), favorite };
}

async function favoriteTarget(
  targetType: FavoriteTargetType,
  targetKey: string,
  payload: Record<string, unknown>,
): Promise<FavoriteBffResult> {
  const session = await getWebSession();
  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await createFavorite(session.sessionToken, {
    target_type: targetType,
    target_key: targetKey,
    payload_json: payload,
  });
  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, favorited: true, message: "已收藏。" };
}

async function unfavoriteTarget(
  targetType: FavoriteTargetType,
  targetKey: string,
): Promise<FavoriteBffResult> {
  const session = await getWebSession();
  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await deleteFavoriteByTargetKey(
    session.sessionToken,
    targetType,
    targetKey,
  );
  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return {
    ok: true,
    favorited: false,
    message: upstreamResult.data.deleted ? "已取消收藏。" : "这条记录尚未收藏。",
  };
}

export async function getRecordFavoriteState(recordId: string): Promise<FavoriteBffResult> {
  const normalizedRecordId = normalizeTargetKey(recordId);
  return normalizedRecordId
    ? getFavoriteState(READING_RECORD_TARGET_TYPE, normalizedRecordId)
    : badRequest("Missing record id.");
}

export async function favoriteRecord(recordId: string): Promise<FavoriteBffResult> {
  const normalizedRecordId = normalizeTargetKey(recordId);
  return normalizedRecordId
    ? favoriteTarget(READING_RECORD_TARGET_TYPE, normalizedRecordId, {})
    : badRequest("Missing record id.");
}

export async function unfavoriteRecord(recordId: string): Promise<FavoriteBffResult> {
  const normalizedRecordId = normalizeTargetKey(recordId);
  return normalizedRecordId
    ? unfavoriteTarget(READING_RECORD_TARGET_TYPE, normalizedRecordId)
    : badRequest("Missing record id.");
}

export async function getDailyReaderArticleFavoriteState(
  articleId: string,
): Promise<FavoriteBffResult> {
  const normalizedArticleId = normalizeTargetKey(articleId);
  return normalizedArticleId
    ? getFavoriteState(DAILY_READER_TARGET_TYPE, dailyReaderTargetKey(normalizedArticleId))
    : badRequest("Missing article id.");
}

export async function favoriteDailyReaderArticle(articleId: string): Promise<FavoriteBffResult> {
  const normalizedArticleId = normalizeTargetKey(articleId);
  return normalizedArticleId
    ? favoriteTarget(DAILY_READER_TARGET_TYPE, dailyReaderTargetKey(normalizedArticleId), {
        article_id: normalizedArticleId,
      })
    : badRequest("Missing article id.");
}

export async function unfavoriteDailyReaderArticle(articleId: string): Promise<FavoriteBffResult> {
  const normalizedArticleId = normalizeTargetKey(articleId);
  return normalizedArticleId
    ? unfavoriteTarget(DAILY_READER_TARGET_TYPE, dailyReaderTargetKey(normalizedArticleId))
    : badRequest("Missing article id.");
}
