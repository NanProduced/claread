import "server-only";

import { appReaderRoute } from "@/lib/routes";
import {
  deleteReaderRecord,
  hideReaderRecordFromRecent,
  listUpstreamReadingRecords,
  recoverReaderRecordUpstream,
} from "@/services/api/reading-records";
import { getWebSession } from "@/services/bff/session";
import type {
  ReaderRecordDeletedResponseDto,
  ReaderRecordRecentRemovedResponseDto,
  ReaderRecoveryResponseDto,
  ReadingRecordProductState,
  ReadingRecordReadinessState,
} from "@/types/api/reading-records";

export type ReadingRecordsBffError = {
  ok: false;
  status: number;
  code:
    | "auth_required"
    | "upstream_auth_failed"
    | "limited_debug"
    | "upstream_unavailable"
    | "upstream_error";
  message: string;
};

export interface ReadingRecordListItemVm {
  readingRecordId: string;
  readerUrl: string;
  /**
   * Mapped from the backend-decided `display_title` (not the raw
   * `title` field) so the UI always shows the stable identity string.
   */
  title: string;
  createdAt: string;
  sourceType: string;
  productState: ReadingRecordProductState;
  readinessState: ReadingRecordReadinessState;
  lastEventSequence: number;
  lastOpenedAt: string | null;
  /**
   * Backend-controlled friendly source label (e.g. "粘贴文本",
   * "上传文件 · report.pdf"). Shown as the second line in Library rows.
   * Raw source_metadata is NOT exposed to the browser.
   */
  sourceLabel: string;
  /** 解析模式 code（原样透传，可能缺失；label 归 reading-defaults）。 */
  readingGoal?: string | null;
  readingVariant?: string | null;
}

export type ReadingRecordListResult =
  | {
      ok: true;
      items: ReadingRecordListItemVm[];
      total: number;
      limit: number;
    }
  | ReadingRecordsBffError;

export interface GetReadingRecordsOptions {
  limit?: number;
  query?: string;
  productStates?: ReadingRecordProductState[];
  recentOnly?: boolean;
}

export type ReadingRecordRecentRemovedResult =
  | { ok: true; data: ReaderRecordRecentRemovedResponseDto }
  | ReadingRecordsBffError;

export type ReadingRecordDeletedResult =
  | { ok: true; data: ReaderRecordDeletedResponseDto }
  | ReadingRecordsBffError;

export type ReaderRecoveryResult =
  | { ok: true; data: ReaderRecoveryResponseDto }
  | ReadingRecordsBffError;

const READING_RECORD_PRODUCT_STATES = [
  "processing",
  "needs_confirmation",
  "readable_enhancing",
  "action_required",
  "failed",
  "deleted",
] as const satisfies readonly ReadingRecordProductState[];

function isProductState(value: unknown): value is ReadingRecordProductState {
  return (
    typeof value === "string" &&
    (READING_RECORD_PRODUCT_STATES as readonly string[]).includes(value)
  );
}

function isIntegerAtLeast(value: unknown, min: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min;
}

/**
 * `fastApiFetch<T>` only asserts the type, so validate and rebuild the
 * recovery DTO here: drop any extra upstream fields and reject malformed
 * payloads instead of forwarding them to the browser.
 */
function sanitizeReaderRecoveryData(raw: unknown): ReaderRecoveryResponseDto | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return null;
  }
  const data = raw as Record<string, unknown>;
  const {
    record_id,
    outcome,
    previous_product_state,
    next_product_state,
    record_generation,
    successor_job_count,
  } = data;
  if (typeof record_id !== "string" || record_id.length === 0) {
    return null;
  }
  if (outcome !== "recovery_started" && outcome !== "nothing_to_recover") {
    return null;
  }
  if (!isProductState(previous_product_state) || !isProductState(next_product_state)) {
    return null;
  }
  if (!isIntegerAtLeast(record_generation, 1) || !isIntegerAtLeast(successor_job_count, 0)) {
    return null;
  }
  return {
    record_id,
    outcome,
    previous_product_state,
    next_product_state,
    record_generation,
    successor_job_count,
  };
}

function authRequired(message: string): ReadingRecordsBffError {
  return { ok: false, status: 401, code: "auth_required", message };
}

function limitedDebug(message: string): ReadingRecordsBffError {
  return { ok: false, status: 401, code: "limited_debug", message };
}

function upstreamError(status: number): ReadingRecordsBffError {
  if (status === 0 || status >= 500) {
    return {
      ok: false,
      status: 503,
      code: "upstream_unavailable",
      message: "透读服务暂时不可用，请稍后重试。",
    };
  }
  if (status === 401) {
    return {
      ok: false,
      status: 401,
      code: "upstream_auth_failed",
      message: "登录态已失效，请重新登录后再试。",
    };
  }
  // 4xx passthrough keeps the status/code but NEVER echoes the raw
  // upstream error text — it may contain internal diagnostics.
  return {
    ok: false,
    status,
    code: "upstream_error",
    message: "操作失败，请稍后重试。",
  };
}

export async function getReadingRecordListFromWeb(
  options: GetReadingRecordsOptions = {},
): Promise<ReadingRecordListResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous") {
    return authRequired("请先登录后查看阅读记录。");
  }

  if (session.kind === "mock_phone") {
    return limitedDebug("当前登录态无法访问阅读记录，请使用完整登录会话。");
  }

  const upstreamResult = await listUpstreamReadingRecords(
    session.sessionToken,
    {
      limit: options.limit,
      query: options.query,
      productStates: options.productStates,
      recentOnly: options.recentOnly,
    },
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status);
  }

  const data = upstreamResult.data;

  return {
    ok: true,
    items: data.items.map((item) => ({
      readingRecordId: item.record_id,
      readerUrl: appReaderRoute(item.record_id),
      // Use the backend-decided display_title instead of the raw
      // title field. The backend guarantees display_title is non-empty.
      title: item.display_title,
      createdAt: item.created_at,
      sourceType: item.source_type,
      productState: item.product_state,
      readinessState: item.readiness_state,
      lastEventSequence: item.last_event_sequence,
      lastOpenedAt: item.last_opened_at,
      sourceLabel: item.source_label,
      readingGoal: item.reading_goal ?? null,
      readingVariant: item.reading_variant ?? null,
    })),
    total: data.total,
    limit: data.limit,
  };
}

async function requireAuthenticatedSession():
  Promise<{ kind: "authenticated"; sessionToken: string } | ReadingRecordsBffError> {
  const session = await getWebSession();

  if (session.kind === "anonymous") {
    return authRequired("请先登录后继续操作。");
  }

  if (session.kind === "mock_phone") {
    return limitedDebug("当前登录态无法继续操作，请使用完整登录会话。");
  }

  return { kind: "authenticated", sessionToken: session.sessionToken };
}

export async function hideReaderRecordFromRecentFromWeb(
  recordId: string,
): Promise<ReadingRecordRecentRemovedResult> {
  const session = await requireAuthenticatedSession();
  if (!("sessionToken" in session)) {
    return session;
  }

  const upstreamResult = await hideReaderRecordFromRecent(
    session.sessionToken,
    recordId,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status);
  }

  return { ok: true, data: upstreamResult.data };
}

export async function deleteReaderRecordFromWeb(
  recordId: string,
): Promise<ReadingRecordDeletedResult> {
  const session = await requireAuthenticatedSession();
  if (!("sessionToken" in session)) {
    return session;
  }

  const upstreamResult = await deleteReaderRecord(
    session.sessionToken,
    recordId,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status);
  }

  return { ok: true, data: upstreamResult.data };
}

export async function recoverReaderRecordFromWeb(
  recordId: string,
): Promise<ReaderRecoveryResult> {
  const session = await requireAuthenticatedSession();
  if (!("sessionToken" in session)) {
    return session;
  }

  const upstreamResult = await recoverReaderRecordUpstream(
    session.sessionToken,
    recordId,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status);
  }

  const data = sanitizeReaderRecoveryData(upstreamResult.data);
  if (data === null) {
    // Anomalous upstream success payload: surface a sanitized 503 and
    // never forward the raw body.
    return upstreamError(503);
  }

  return { ok: true, data };
}
