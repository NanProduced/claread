import "server-only";

import { appReadingRecordRoute } from "@/lib/routes";
import { listUpstreamReadingRecords } from "@/services/api/reading-records";
import { getWebSession } from "@/services/bff/session";
import type {
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
  title: string;
  createdAt: string;
  sourceType: string;
  sourceMetadata: Record<string, unknown>;
  productState: ReadingRecordProductState;
  readinessState: ReadingRecordReadinessState;
  lastEventSequence: number;
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
}

function authRequired(message: string): ReadingRecordsBffError {
  return { ok: false, status: 401, code: "auth_required", message };
}

function limitedDebug(message: string): ReadingRecordsBffError {
  return { ok: false, status: 401, code: "limited_debug", message };
}

function upstreamError(status: number, message: string): ReadingRecordsBffError {
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
  return { ok: false, status, code: "upstream_error", message };
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
    },
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  const data = upstreamResult.data;

  return {
    ok: true,
    items: data.items.map((item) => ({
      readingRecordId: item.record_id,
      readerUrl: appReadingRecordRoute(item.record_id),
      title: item.title ?? "未命名解读",
      createdAt: item.created_at,
      sourceType: item.source_type,
      sourceMetadata: item.source_metadata,
      productState: item.product_state,
      readinessState: item.readiness_state,
      lastEventSequence: item.last_event_sequence,
    })),
    total: data.total,
    limit: data.limit,
  };
}
