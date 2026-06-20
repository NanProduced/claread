import "server-only";

import { randomUUID } from "node:crypto";

import {
  getUpstreamReaderPlateSnapshot,
  pollUpstreamReaderEvents,
  submitUpstreamReaderPlainText,
} from "@/services/api/reader-plate";
import { getWebSession } from "@/services/bff/session";
import type {
  ReaderEventPollResponseDto,
  ReaderPlainTextSubmitResponseDto,
  ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";

export type ReaderPlateBffError = {
  ok: false;
  status: number;
  code:
    | "auth_required"
    | "upstream_auth_failed"
    | "record_not_found"
    | "upstream_unavailable"
    | "upstream_error"
    | "empty_text";
  message: string;
};

export type ReaderPlateSubmitResult =
  | ({ ok: true } & ReaderPlainTextSubmitResponseDto)
  | ReaderPlateBffError;

export type ReaderPlateSnapshotResult =
  | ({ ok: true } & ReaderPlateSnapshotDto)
  | ReaderPlateBffError;

export type ReaderPlateEventsResult =
  | ({ ok: true } & ReaderEventPollResponseDto)
  | ReaderPlateBffError;

function authRequired(message: string): ReaderPlateBffError {
  return { ok: false, status: 401, code: "auth_required", message };
}

function upstreamError(status: number, message: string): ReaderPlateBffError {
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
  if (status === 404) {
    return {
      ok: false,
      status: 404,
      code: "record_not_found",
      message: "没有找到这条阅读记录。",
    };
  }
  return { ok: false, status, code: "upstream_error", message };
}

async function requireSession(): Promise<
  { ok: true; sessionToken: string } | ReaderPlateBffError
> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return authRequired(
      session.kind === "mock_phone"
        ? "当前登录态无法提交文章，请使用完整登录会话。"
        : "请先登录后再提交文章。",
    );
  }

  return { ok: true, sessionToken: session.sessionToken };
}

export async function submitReaderPlainTextFromWeb(input: {
  plainText?: unknown;
  title?: unknown;
  language?: unknown;
}): Promise<ReaderPlateSubmitResult> {
  const plainText =
    typeof input.plainText === "string" ? input.plainText.trim() : "";

  if (!plainText) {
    return {
      ok: false,
      status: 400,
      code: "empty_text",
      message: "请先粘贴需要透读的英文内容。",
    };
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const upstreamResult = await submitUpstreamReaderPlainText(
    {
      plain_text: plainText,
      title: typeof input.title === "string" && input.title.trim() ? input.title : null,
      language:
        typeof input.language === "string" && input.language.trim()
          ? input.language
          : null,
      client_record_id: `web-plate-${randomUUID()}`,
    },
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

export async function getReaderPlateSnapshotFromWeb(
  recordId: string,
): Promise<ReaderPlateSnapshotResult> {
  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const upstreamResult = await getUpstreamReaderPlateSnapshot(
    recordId,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

export async function pollReaderEventsFromWeb(
  recordId: string,
  params: { afterSequence?: number; limit?: number } = {},
): Promise<ReaderPlateEventsResult> {
  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const upstreamResult = await pollUpstreamReaderEvents(
    recordId,
    sessionResult.sessionToken,
    params,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}
