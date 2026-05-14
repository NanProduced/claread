import "server-only";

import { adaptRecordToReaderRecord, type ReaderRecordVm } from "@/adapters/records.adapter";
import {
  getUpstreamRecordByClientId,
  getUpstreamRecordById,
} from "@/services/api/records";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { RecordResponseDto } from "@/types/api/records";

export type ReaderDataSource =
  | "upstream-render-scene"
  | "upstream-source-text";

export type ReaderBffResult =
  | {
      ok: true;
      record: ReaderRecordVm;
      dataSource: ReaderDataSource;
      session: WebSession;
      message?: string;
    }
  | {
      ok: false;
      status: number;
      code:
        | "auth_required"
        | "upstream_auth_failed"
        | "record_not_found"
        | "upstream_unavailable"
        | "upstream_error";
      message: string;
      session: WebSession;
    };

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function hasRenderableScene(record: RecordResponseDto): boolean {
  const scene = record.render_scene_json;
  return Boolean(
    scene &&
      typeof scene === "object" &&
      "article" in scene &&
      (Array.isArray((scene as { translations?: unknown }).translations) ||
        Array.isArray((scene as { inline_marks?: unknown }).inline_marks)),
  );
}

export async function getReaderRecord(recordId: string): Promise<ReaderBffResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      session,
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能访问真实记录，请使用真实登录会话后打开 Reader。"
          : "请先登录后打开 Reader。",
    };
  }

  const upstreamResult = UUID_RE.test(recordId)
    ? await getUpstreamRecordById(recordId, session.sessionToken)
    : await getUpstreamRecordByClientId(recordId, session.sessionToken);

  if (!upstreamResult.ok) {
    return {
      ok: false,
      status: upstreamResult.status === 0 ? 503 : upstreamResult.status,
      code:
        upstreamResult.status === 0 || upstreamResult.status >= 500
          ? "upstream_unavailable"
          : upstreamResult.status === 401
            ? "upstream_auth_failed"
            : upstreamResult.status === 404
              ? "record_not_found"
              : "upstream_error",
      session,
      message:
        upstreamResult.status === 0 || upstreamResult.status >= 500
          ? "Reader 记录服务暂时不可用，请稍后重试。"
          : upstreamResult.status === 404
            ? "没有找到这条阅读记录。"
            : upstreamResult.message,
    };
  }

  const record = adaptRecordToReaderRecord(upstreamResult.data);

  return {
    ok: true,
    record,
    dataSource: hasRenderableScene(upstreamResult.data)
      ? "upstream-render-scene"
      : "upstream-source-text",
    session,
    message: hasRenderableScene(upstreamResult.data)
      ? undefined
      : "这条记录暂时没有完整解析结果，当前仅显示原文。",
  };
}
