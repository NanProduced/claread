import "server-only";

import { listRecords } from "@/services/api/records";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { RecordResponseDto } from "@/types/api/records";
import type { RecordListItemVm } from "@/types/view/RecordListItemVm";

export type RecordsBffStatus =
  | "ready"
  | "unauthenticated"
  | "mock_session"
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function countEnglishWords(text: string): number {
  const matches = text.match(/[A-Za-z]+(?:['-][A-Za-z]+)*/g);
  return matches?.length ?? 0;
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

function getRenderScene(record: RecordResponseDto): Record<string, unknown> {
  return isRecord(record.render_scene_json) ? record.render_scene_json : {};
}

function countSceneItems(record: RecordResponseDto, key: string): number {
  return readArray(getRenderScene(record)[key]).length;
}

function projectRecordToListItem(record: RecordResponseDto): RecordListItemVm {
  const request = isRecord(record.request_payload_json) ? record.request_payload_json : {};

  return {
    id: record.id,
    title: record.title ?? titleFromSourceText(record.source_text),
    sourceText: record.source_text,
    readingGoal: record.reading_goal ?? readString(request.reading_goal, "daily_reading"),
    readingVariant: record.reading_variant ?? readString(request.reading_variant, "intermediate_reading"),
    createdAt: record.created_at,
    wordCount: countEnglishWords(record.source_text),
    inlineMarkCount: countSceneItems(record, "inline_marks"),
    sentenceEntryCount: countSceneItems(record, "sentence_entries"),
    translationCount: countSceneItems(record, "translations"),
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
      session.kind === "mock_phone" ? "mock_session" : "unauthenticated",
      session.kind === "mock_phone"
        ? "当前登录态不能访问真实记录，请使用真实登录会话后查看历史。"
        : "请先登录后查看历史记录。",
    );
  }

  const upstreamResult = await listRecords(session.sessionToken, normalizedOptions);

  if (!upstreamResult.ok) {
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
