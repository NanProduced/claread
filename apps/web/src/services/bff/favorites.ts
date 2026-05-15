import "server-only";

import {
  createFavorite,
  deleteFavoriteByTarget,
  listFavorites,
} from "@/services/api/favorites";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { FavoriteResponseDto } from "@/types/api/favorites";

const ANALYSIS_RECORD_TARGET_TYPE = "analysis_record";
const ALLOWED_TARGET_TYPES = new Set([
  "analysis_record",
  "sentence",
  "paragraph",
  "phrase",
  "vocab",
  "text_range",
]);

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

function normalizeRecordId(recordId: string): string {
  return recordId.trim();
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
    message: unavailable ? "收藏服务暂时不可用，请稍后重试。" : message,
  };
}

function findRecordFavorite(items: FavoriteResponseDto[], recordId: string) {
  return items.find(
    (item) =>
      item.target_type === ANALYSIS_RECORD_TARGET_TYPE &&
      (item.analysis_record_id === recordId || item.target_key === recordId),
  );
}

function normalizeTargetType(targetType: string): string {
  const normalized = targetType.trim();
  return ALLOWED_TARGET_TYPES.has(normalized) ? normalized : "";
}

function normalizeTargetKey(targetKey: string): string {
  return targetKey.trim();
}

function findTargetFavorite(items: FavoriteResponseDto[], targetType: string, targetKey: string) {
  return items.find((item) => item.target_type === targetType && item.target_key === targetKey);
}

export async function getRecordFavoriteState(recordId: string): Promise<FavoriteBffResult> {
  const normalizedRecordId = normalizeRecordId(recordId);

  if (!normalizedRecordId) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Missing record id.",
    };
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await listFavorites(session.sessionToken);

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  const favorite = findRecordFavorite(upstreamResult.data.items, normalizedRecordId);

  return {
    ok: true,
    favorited: Boolean(favorite),
    favorite,
  };
}

export async function favoriteRecord(recordId: string): Promise<FavoriteBffResult> {
  const normalizedRecordId = normalizeRecordId(recordId);

  if (!normalizedRecordId) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Missing record id.",
    };
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await createFavorite(session.sessionToken, {
    analysis_record_id: normalizedRecordId,
    target_type: ANALYSIS_RECORD_TARGET_TYPE,
    target_key: normalizedRecordId,
    payload_json: {},
  });

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return {
    ok: true,
    favorited: true,
    message: "已收藏。",
  };
}

export async function unfavoriteRecord(recordId: string): Promise<FavoriteBffResult> {
  const normalizedRecordId = normalizeRecordId(recordId);

  if (!normalizedRecordId) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Missing record id.",
    };
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await deleteFavoriteByTarget(
    session.sessionToken,
    ANALYSIS_RECORD_TARGET_TYPE,
    normalizedRecordId,
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

export async function getFavoriteTargetState(
  targetType: string,
  targetKey: string,
): Promise<FavoriteBffResult> {
  const normalizedTargetType = normalizeTargetType(targetType);
  const normalizedTargetKey = normalizeTargetKey(targetKey);

  if (!normalizedTargetType || !normalizedTargetKey) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Missing favorite target.",
    };
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await listFavorites(session.sessionToken);

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  const favorite = findTargetFavorite(upstreamResult.data.items, normalizedTargetType, normalizedTargetKey);

  return {
    ok: true,
    favorited: Boolean(favorite),
    favorite,
  };
}

export async function favoriteTarget(input: {
  recordId?: string | null;
  targetType: string;
  targetKey: string;
  payloadJson?: Record<string, unknown>;
  note?: string | null;
}): Promise<FavoriteBffResult> {
  const normalizedTargetType = normalizeTargetType(input.targetType);
  const normalizedTargetKey = normalizeTargetKey(input.targetKey);

  if (!normalizedTargetType || !normalizedTargetKey) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Missing favorite target.",
    };
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await createFavorite(session.sessionToken, {
    analysis_record_id: input.recordId ?? null,
    target_type: normalizedTargetType,
    target_key: normalizedTargetKey,
    payload_json: input.payloadJson ?? {},
    note: input.note ?? null,
  });

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return {
    ok: true,
    favorited: true,
    message: "已收藏。",
  };
}

export async function unfavoriteTarget(
  targetType: string,
  targetKey: string,
): Promise<FavoriteBffResult> {
  const normalizedTargetType = normalizeTargetType(targetType);
  const normalizedTargetKey = normalizeTargetKey(targetKey);

  if (!normalizedTargetType || !normalizedTargetKey) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Missing favorite target.",
    };
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authError(session);
  }

  const upstreamResult = await deleteFavoriteByTarget(
    session.sessionToken,
    normalizedTargetType,
    normalizedTargetKey,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return {
    ok: true,
    favorited: false,
    message: upstreamResult.data.deleted ? "已取消收藏。" : "这个目标尚未收藏。",
  };
}
