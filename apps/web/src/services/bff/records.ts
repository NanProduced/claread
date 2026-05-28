import "server-only";

import { deleteUpstreamRecord, listRecords } from "@/services/api/records";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { RecordResponseDto } from "@/types/api/records";
import type { RecordListItemVm } from "@/types/view/RecordListItemVm";

export type RecordsBffStatus =
  | "ready"
  | "unauthenticated"
  | "limited_debug"
  | "upstream_unavailable"
  | "upstream_error";

export interface RecordsBffResult {
  status: RecordsBffStatus;
  records: RecordListItemVm[];
  total: number;
  page: number;
  limit: number;
  session: WebSession;
  message?: string;
}

export interface GetRecordsOptions {
  page?: number;
  limit?: number;
}

export type DeleteRecordBffResult =
  | {
      ok: true;
      deleted: boolean;
      message: string;
    }
  | {
      ok: false;
      status: number;
      code:
        | "bad_request"
        | "auth_required"
        | "not_found"
        | "upstream_auth_failed"
        | "upstream_unavailable"
        | "upstream_error";
      message: string;
    };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function titleFromSourceText(sourceText: string): string {
  const firstLine = sourceText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);

  if (!firstLine) {
    return "Untitled record";
  }

  return firstLine.length > 96 ? `${firstLine.slice(0, 96)}...` : firstLine;
}

function projectRecordToListItem(record: RecordResponseDto): RecordListItemVm {
  const request = isRecord(record.request_payload_json) ? record.request_payload_json : {};

  return {
    id: record.id,
    title: record.title ?? titleFromSourceText(record.source_text),
    sourceText: record.source_text,
    sourceTextExcerpt: record.source_text_excerpt ?? "",
    sourceType: record.source_type,
    readingGoal: record.reading_goal ?? readString(request.reading_goal, "daily_reading"),
    readingVariant: record.reading_variant ?? readString(request.reading_variant, "intermediate_reading"),
    analysisStatus: record.analysis_status,
    lastOpenedAt: record.last_opened_at,
    createdAt: record.created_at,
    updatedAt: record.updated_at,
    wordCount: record.word_count,
    noteCount: record.note_count,
    vocabularyCount: record.vocabulary_count,
    isFavorited: record.is_favorited,
  };
}

function emptyResult(
  session: WebSession,
  options: Required<GetRecordsOptions>,
  status: RecordsBffStatus,
  message?: string,
): RecordsBffResult {
  return {
    status,
    records: [],
    total: 0,
    page: options.page,
    limit: options.limit,
    session,
    message,
  };
}

export async function getRecordList(options: GetRecordsOptions = {}): Promise<RecordsBffResult> {
  const normalizedOptions = {
    page: options.page ?? 1,
    limit: options.limit ?? 20,
  };
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return emptyResult(
      session,
      normalizedOptions,
      session.kind === "mock_phone" ? "limited_debug" : "unauthenticated",
      session.kind === "mock_phone"
        ? "当前登录态不能访问真实记录，请使用真实登录会话后查看历史。"
        : "当前会话已过期，请重新登录。",
    );
  }

  const upstreamResult = await listRecords(session.sessionToken, normalizedOptions);

  if (!upstreamResult.ok) {
    if (upstreamResult.status === 401) {
      return emptyResult(
        session,
        normalizedOptions,
        "unauthenticated",
        "当前会话已过期，请重新登录。",
      );
    }

    return emptyResult(
      session,
      normalizedOptions,
      upstreamResult.status === 0 || upstreamResult.status >= 500
        ? "upstream_unavailable"
        : "upstream_error",
      upstreamResult.status === 0 || upstreamResult.status >= 500
        ? "历史记录服务暂时不可用，请稍后重试。"
        : upstreamResult.message,
    );
  }

  return {
    status: "ready",
    records: upstreamResult.data.items.map(projectRecordToListItem),
    total: upstreamResult.data.total,
    page: upstreamResult.data.page,
    limit: upstreamResult.data.limit,
    session,
  };
}

export async function deleteRecordFromWeb(recordId: string): Promise<DeleteRecordBffResult> {
  const normalizedRecordId = recordId.trim();

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
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能删除真实记录，请使用真实登录会话后再试。"
          : "请先登录后删除记录。",
    };
  }

  const upstreamResult = await deleteUpstreamRecord(normalizedRecordId, session.sessionToken);

  if (!upstreamResult.ok) {
    const unavailable = upstreamResult.status === 0 || upstreamResult.status >= 500;

    return {
      ok: false,
      status: upstreamResult.status === 0 ? 503 : upstreamResult.status,
      code: unavailable
        ? "upstream_unavailable"
        : upstreamResult.status === 401
          ? "upstream_auth_failed"
          : upstreamResult.status === 404
            ? "not_found"
            : "upstream_error",
      message: unavailable ? "历史记录服务暂时不可用，请稍后重试。" : upstreamResult.message,
    };
  }

  return {
    ok: true,
    deleted: upstreamResult.data.deleted,
    message: "已删除记录。",
  };
}
