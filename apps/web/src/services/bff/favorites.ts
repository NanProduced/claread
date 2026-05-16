import "server-only";

import { FAVORITE_TARGET_TYPES } from "@claread/contracts";
import type { FavoriteTargetType } from "@claread/contracts";
import {
  createFavorite,
  deleteFavoriteByTarget,
  listFavorites,
} from "@/services/api/favorites";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { FavoriteResponseDto, WebFavoriteTargetVm } from "@/types/api/favorites";
import type { WebAnchorSegmentVm } from "@/types/api/annotations";

const ANALYSIS_RECORD_TARGET_TYPE = "analysis_record";
const ALLOWED_TARGET_TYPES = new Set<string>(FAVORITE_TARGET_TYPES);

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

export interface ReaderFavoriteTargetsResult {
  items: WebFavoriteTargetVm[];
  message?: string;
}

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

function normalizeTargetType(targetType: string): FavoriteTargetType | "" {
  const normalized = targetType.trim();
  return ALLOWED_TARGET_TYPES.has(normalized) ? (normalized as FavoriteTargetType) : "";
}

function normalizeTargetKey(targetKey: string): string {
  return targetKey.trim();
}

function findTargetFavorite(items: FavoriteResponseDto[], targetType: string, targetKey: string) {
  return items.find((item) => item.target_type === targetType && item.target_key === targetKey);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readPayload(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value) as unknown;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }
  return {};
}

function projectPayloadSegment(value: unknown): WebAnchorSegmentVm | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const payload = value as Record<string, unknown>;
  const sentenceId = readString(payload.sentence_id);
  const selectedText = readString(payload.selected_text);
  const startOffset = readNumber(payload.start_offset);
  const endOffset = readNumber(payload.end_offset);
  const textHash = readString(payload.text_hash);
  if (!sentenceId || !selectedText || startOffset === null || endOffset === null || !textHash) {
    return null;
  }
  return {
    paragraphId: readString(payload.paragraph_id) ?? null,
    sentenceId,
    selectedText,
    startOffset,
    endOffset,
    textHash,
  };
}

function sentenceIdFromTargetKey(targetKey: string): string | null {
  const sentenceMatch = targetKey.match(/:sentence:([^:]+)$/);
  if (sentenceMatch) {
    return sentenceMatch[1] ?? null;
  }
  const rangeMatch = targetKey.match(/:range:([^:]+):/);
  if (rangeMatch?.[1]) {
    return rangeMatch[1];
  }
  const multiMatch = targetKey.match(/:multi_text:/);
  return multiMatch ? null : null;
}

function projectFavoriteTarget(item: FavoriteResponseDto): WebFavoriteTargetVm | null {
  if (item.target_type !== "sentence" && item.target_type !== "text_range" && item.target_type !== "multi_text") {
    return null;
  }

  const payload = readPayload(item.payload_json);
  const segments = Array.isArray(payload.segments)
    ? payload.segments.map(projectPayloadSegment).filter((segment): segment is WebAnchorSegmentVm => Boolean(segment))
    : [];
  const sentenceId =
    readString(payload.sentence_id)
    ?? segments[0]?.sentenceId
    ?? sentenceIdFromTargetKey(item.target_key);
  if (!sentenceId && item.target_type !== "multi_text") {
    return null;
  }

  return {
    id: item.id,
    targetType: item.target_type,
    targetKey: item.target_key,
    recordId: item.analysis_record_id,
    anchorType:
      item.target_type === "text_range"
        ? "text_range"
        : item.target_type === "multi_text"
          ? "multi_text"
          : "sentence",
    sentenceId,
    selectedText: readString(payload.selected_text),
    startOffset: item.target_type === "text_range" ? readNumber(payload.start_offset) : null,
    endOffset: item.target_type === "text_range" ? readNumber(payload.end_offset) : null,
    textHash: item.target_type === "text_range" ? readString(payload.text_hash) : null,
    segments,
  };
}

export async function getReaderFavoriteTargets(recordId: string): Promise<ReaderFavoriteTargetsResult> {
  const normalizedRecordId = normalizeRecordId(recordId);
  const session = await getWebSession();

  if (!normalizedRecordId) {
    return { items: [], message: "Missing record id." };
  }

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return { items: [], message: authError(session).message };
  }

  const upstreamResult = await listFavorites(session.sessionToken);
  if (!upstreamResult.ok) {
    return { items: [], message: upstreamError(upstreamResult.status, upstreamResult.message).message };
  }

  return {
    items: upstreamResult.data.items
      .filter((item) => item.analysis_record_id === normalizedRecordId)
      .map(projectFavoriteTarget)
      .filter((item): item is WebFavoriteTargetVm => Boolean(item)),
  };
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
