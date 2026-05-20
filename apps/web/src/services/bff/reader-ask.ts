import "server-only";

import {
  confirmUpstreamReaderAskAction,
  createUpstreamReaderAskStream,
  createUpstreamReaderAskThread,
  deleteUpstreamReaderAskSupplement,
  getUpstreamReaderAskThread,
  listUpstreamReaderAskContextRecords,
  listUpstreamReaderAskThreads,
  resetUpstreamReaderAskThread,
  retryUpstreamReaderAskMessage,
} from "@/services/api/reader-ask";
import { getWebSession } from "@/services/bff/session";
import type {
  ReaderAskActionConfirmRequestDto,
  ReaderAskActionConfirmResponseDto,
  ReaderAskContextRecordSearchResponseDto,
  ReaderAskDeleteSupplementResponseDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskThreadCreateRequestDto,
  ReaderAskThreadDetailDto,
  ReaderAskThreadListResponseDto,
  ReaderAskThreadSummaryDto,
} from "@/types/api/reader-ask";

function isDevelopmentRuntime() {
  return process.env.NODE_ENV !== "production";
}

function authError(message: string) {
  return new Response(JSON.stringify({ message }), {
    status: 401,
    headers: { "content-type": "application/json" },
  });
}

async function requireUpstreamSession() {
  const session = await getWebSession();
  if (session.kind === "authenticated" || session.kind === "debug") {
    return session;
  }
  return null;
}

function normalizeUpstreamError(
  payload: unknown,
  fallbackCode: string,
  fallbackDetail: string,
): { code: string; detail: string } {
  if (payload && typeof payload === "object") {
    const code = typeof (payload as { code?: unknown }).code === "string"
      ? (payload as { code: string }).code
      : fallbackCode;
    const detail = typeof (payload as { detail?: unknown }).detail === "string"
      ? (payload as { detail: string }).detail
      : typeof (payload as { message?: unknown }).message === "string"
        ? (payload as { message: string }).message
        : fallbackDetail;
    return { code, detail };
  }
  if (typeof payload === "string" && payload.trim()) {
    return { code: fallbackCode, detail: payload.trim() };
  }
  return { code: fallbackCode, detail: fallbackDetail };
}

async function buildStreamErrorResponse(upstream: Response): Promise<Response> {
  const fallbackDetail = upstream.status === 401 ? "请先登录后再使用 Ask Claread。" : "Ask Claread 暂时不可用。";
  let payload: unknown = null;

  try {
    const rawText = await upstream.text();
    if (rawText.trim()) {
      try {
        payload = JSON.parse(rawText) as unknown;
      } catch {
        payload = rawText;
      }
    }
  } catch {
    payload = null;
  }

  const error = normalizeUpstreamError(payload, "UPSTREAM_ERROR", fallbackDetail);
  const detail = isDevelopmentRuntime() ? error.detail : fallbackDetail;
  const code = isDevelopmentRuntime() ? error.code : "UPSTREAM_ERROR";

  return new Response(
    `event: error\ndata: ${JSON.stringify({ code, detail }, undefined, 0)}\n\n`,
    {
      status: upstream.status || 503,
      headers: {
        "cache-control": "no-cache",
        connection: "keep-alive",
        "content-type": "text/event-stream",
      },
    },
  );
}

export async function listReaderAskThreadsForWeb(recordId: string): Promise<ReaderAskThreadListResponseDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  const upstream = await listUpstreamReaderAskThreads(recordId, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message, payload: upstream.payload }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function listReaderAskContextRecordsForWeb(
  query: string,
  excludeRecordId: string | null,
): Promise<ReaderAskContextRecordSearchResponseDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  const upstream = await listUpstreamReaderAskContextRecords(query, excludeRecordId, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message, payload: upstream.payload }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function createReaderAskThreadForWeb(
  body: ReaderAskThreadCreateRequestDto,
): Promise<ReaderAskThreadSummaryDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  const upstream = await createUpstreamReaderAskThread(body, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message, payload: upstream.payload }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function getReaderAskThreadForWeb(threadId: string): Promise<ReaderAskThreadDetailDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  const upstream = await getUpstreamReaderAskThread(threadId, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message, payload: upstream.payload }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function resetReaderAskThreadForWeb(threadId: string): Promise<ReaderAskThreadDetailDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  const upstream = await resetUpstreamReaderAskThread(threadId, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message, payload: upstream.payload }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function confirmReaderAskActionForWeb(
  threadId: string,
  actionId: string,
  body: ReaderAskActionConfirmRequestDto,
): Promise<ReaderAskActionConfirmResponseDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  const upstream = await confirmUpstreamReaderAskAction(threadId, actionId, body, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message, payload: upstream.payload }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function deleteReaderAskSupplementForWeb(
  supplementId: string,
): Promise<ReaderAskDeleteSupplementResponseDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  const upstream = await deleteUpstreamReaderAskSupplement(supplementId, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message, payload: upstream.payload }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function createReaderAskStreamForWeb(
  threadId: string,
  body: ReaderAskMessageStreamRequestDto,
): Promise<Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return new Response(
      'event: error\ndata: {"code":"AUTH_REQUIRED","detail":"请先登录后再使用 Ask Claread。"}\n\n',
      {
        status: 401,
        headers: {
          "cache-control": "no-cache",
          connection: "keep-alive",
          "content-type": "text/event-stream",
        },
      },
    );
  }

  const upstream = await createUpstreamReaderAskStream(threadId, body, session.sessionToken);
  if (!upstream.ok || !upstream.body) {
    return buildStreamErrorResponse(upstream);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "cache-control": "no-cache",
      connection: "keep-alive",
      "content-type": "text/event-stream",
      "x-accel-buffering": "no",
    },
  });
}

export async function retryReaderAskMessageForWeb(
  threadId: string,
  messageId: string,
): Promise<Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return new Response(
      'event: error\ndata: {"code":"AUTH_REQUIRED","detail":"请先登录后再使用 Ask Claread。"}\n\n',
      {
        status: 401,
        headers: {
          "cache-control": "no-cache",
          connection: "keep-alive",
          "content-type": "text/event-stream",
        },
      },
    );
  }

  const upstream = await retryUpstreamReaderAskMessage(threadId, messageId, session.sessionToken);
  if (!upstream.ok || !upstream.body) {
    return buildStreamErrorResponse(upstream);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "cache-control": "no-cache",
      connection: "keep-alive",
      "content-type": "text/event-stream",
      "x-accel-buffering": "no",
    },
  });
}
