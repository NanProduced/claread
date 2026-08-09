import "server-only";

import {
  createUpstreamReadingRecordAskDefaultThread,
  createUpstreamReadingRecordAskStream,
  getUpstreamReadingRecordAskThread,
  listUpstreamReadingRecordAskModelOptions,
  listUpstreamReadingRecordAskThreads,
  resetUpstreamReadingRecordAskThread,
  retryUpstreamReadingRecordAskMessage,
  getUpstreamReadingRecordAskSubmission,
  navigateUpstreamReadingRecordAskCitation,
  type ReadingRecordAskCitationNavigateResultDto,
} from "@/services/api/reader-ask";
import { getWebSession } from "@/services/bff/session";
import type {
  ReaderAskMessageRetryRequestDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskModelOptionListResponseDto,
  ReaderAskSubmissionReconcileDto,
  ReaderAskThreadCreateRequestDto,
  ReaderAskThreadDetailDto,
  ReaderAskThreadListResponseDto,
  ReaderAskThreadSummaryDto,
} from "@/types/api/reader-ask";

type ReaderAskThreadTransportRequest = ReaderAskThreadCreateRequestDto;

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
    const nestedDetail = (payload as { detail?: unknown }).detail;
    const nestedEnvelope =
      nestedDetail && typeof nestedDetail === "object" ? nestedDetail : null;
    const code = typeof (payload as { code?: unknown }).code === "string"
      ? (payload as { code: string }).code
      : typeof (nestedEnvelope as { code?: unknown } | null)?.code === "string"
        ? (nestedEnvelope as { code: string }).code
        : fallbackCode;
    const detail = typeof (payload as { detail?: unknown }).detail === "string"
      ? (payload as { detail: string }).detail
      : typeof (payload as { message?: unknown }).message === "string"
        ? (payload as { message: string }).message
        : typeof (nestedEnvelope as { message?: unknown } | null)?.message === "string"
          ? (nestedEnvelope as { message: string }).message
        : fallbackDetail;
    return { code, detail };
  }
  if (typeof payload === "string" && payload.trim()) {
    return { code: fallbackCode, detail: payload.trim() };
  }
  return { code: fallbackCode, detail: fallbackDetail };
}

function missingReadingRecordIdResponse() {
  return new Response(JSON.stringify({ message: "Missing reading record id." }), {
    status: 400,
    headers: { "content-type": "application/json" },
  });
}

/** ASK-RETRY-CONTRACT-R0 — fail-closed before any upstream hop. */
const PERSISTED_MESSAGE_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isPersistedMessageId(messageId: string): boolean {
  return PERSISTED_MESSAGE_ID_RE.test(messageId.trim());
}

/**
 * Typed 409 when the browser attempts retry with a non-UUID target
 * (e.g. `local-assistant-*`). Never forwards to FastAPI.
 */
function retryTargetNotPersistedResponse(messageId: string): Response {
  return new Response(
    JSON.stringify({
      code: "retry_target_not_persisted",
      detail: "这轮回答尚未保存，请重新发送，不要直接重新生成。",
      message_id: messageId,
    }),
    {
      status: 409,
      headers: { "content-type": "application/json" },
    },
  );
}

export async function listReaderAskModelOptionsForWeb(
  recordId: string,
): Promise<Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }

  if (!recordId.trim()) {
    return missingReadingRecordIdResponse();
  }
  const upstream = await listUpstreamReadingRecordAskModelOptions(
    recordId,
    session.sessionToken,
  );
  if (!upstream.ok) {
    const fallbackDetail = upstream.status === 401 ? "请先登录后再使用 Ask Claread。" : "Ask Claread 模型列表暂时不可用。";
    const { code, detail } = normalizeUpstreamError(
      upstream.payload,
      "READER_ASK_MODEL_OPTIONS_FAILED",
      fallbackDetail,
    );
    return new Response(JSON.stringify({ code, detail }), {
      status: upstream.status,
      headers: { "content-type": "application/json" },
    });
  }

  const data: ReaderAskModelOptionListResponseDto = upstream.data;
  return Response.json(data);
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
  const detail =
    error.code === "web_search_unavailable"
      ? fallbackDetail
      : isDevelopmentRuntime()
        ? error.detail
        : fallbackDetail;
  const code =
    error.code === "web_search_unavailable" || isDevelopmentRuntime()
      ? error.code
      : "UPSTREAM_ERROR";

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

export async function listReaderAskThreadsForWeb(
  recordId: string,
): Promise<ReaderAskThreadListResponseDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  if (!recordId.trim()) {
    return missingReadingRecordIdResponse();
  }
  const upstream = await listUpstreamReadingRecordAskThreads(recordId, session.sessionToken);
  if (!upstream.ok) {
    const message = upstream.message || "请求失败";
    return new Response(JSON.stringify({ message }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function createReaderAskThreadForWeb(
  recordId: string,
  body: ReaderAskThreadTransportRequest,
): Promise<ReaderAskThreadSummaryDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  if (!recordId.trim()) {
    return missingReadingRecordIdResponse();
  }
  const upstream = await createUpstreamReadingRecordAskDefaultThread(
    recordId,
    session.sessionToken,
  );
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function getReaderAskThreadForWeb(
  recordId: string,
  threadId: string,
): Promise<ReaderAskThreadDetailDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  if (!recordId.trim()) {
    return missingReadingRecordIdResponse();
  }
  const upstream = await getUpstreamReadingRecordAskThread(recordId, threadId, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function resetReaderAskThreadForWeb(
  recordId: string,
  threadId: string,
): Promise<ReaderAskThreadDetailDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  if (!recordId.trim()) {
    return missingReadingRecordIdResponse();
  }
  const upstream = await resetUpstreamReadingRecordAskThread(recordId, threadId, session.sessionToken);
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}

export async function createReaderAskStreamForWeb(
  recordId: string,
  threadId: string,
  body: ReaderAskMessageStreamRequestDto,
  signal?: AbortSignal,
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

  if (!recordId.trim()) {
    return missingReadingRecordIdResponse();
  }

  // ASK-TURN-LIFECYCLE R1: forward the browser-supplied AbortSignal to the
  // upstream fetch so a user stop / network abort / page navigation cancels
  // the upstream connection. This is what triggers the FastAPI generator's
  // ``finally`` block (ASGI cancellation) which in turn reconciles any
  // still-streaming turn_run / message row to ``cancelled``.
  const upstream = await createUpstreamReadingRecordAskStream(
    recordId,
    threadId,
    body,
    session.sessionToken,
    signal,
  );
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

/** Regenerate (not resume/continue) the assistant answer for a message. */
export async function retryReaderAskMessageForWeb(
  recordId: string,
  threadId: string,
  messageId: string,
  body: ReaderAskMessageRetryRequestDto,
  signal?: AbortSignal,
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

  // ASK-RETRY-CONTRACT-R0: non-UUID message ids (local-assistant-*) must
  // never reach FastAPI. Typed 409 — fail-closed at the BFF edge.
  if (!isPersistedMessageId(messageId)) {
    return retryTargetNotPersistedResponse(messageId);
  }

  if (!recordId.trim()) {
    return missingReadingRecordIdResponse();
  }

  // ASK-TURN-LIFECYCLE R1: see createReaderAskStreamForWeb.
  // Upstream path is always `/retry/stream` (BFF → FastAPI only).
  const upstream = await retryUpstreamReadingRecordAskMessage(
    recordId,
    threadId,
    messageId,
    body,
    session.sessionToken,
    signal,
  );
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

/**
 * ASK-RETRY-CONTRACT-R4 — typed submission reconcile (Browser → FastAPI).
 * Reading-record scope only for R4 (agentic + RR Ask cutover path).
 */
export async function reconcileReaderAskSubmissionForWeb(
  recordId: string,
  threadId: string,
  clientSubmissionId: string,
): Promise<Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  if (!recordId.trim()) {
    return new Response(
      JSON.stringify({
        code: "reconcile_scope_unsupported",
        detail: "Submission reconcile is only available for Reading Record Ask.",
      }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }
  if (!isPersistedMessageId(clientSubmissionId)) {
    return new Response(
      JSON.stringify({
        code: "invalid_client_submission_id",
        detail: "client_submission_id must be a UUID.",
      }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }

  const upstream = await getUpstreamReadingRecordAskSubmission(
    recordId,
    threadId,
    clientSubmissionId,
    session.sessionToken,
  );
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  const data: ReaderAskSubmissionReconcileDto = upstream.data;
  return Response.json(data);
}

/** Secure citation navigate — message_id + citation_id only; server owns fence. */
export async function navigateReadingRecordAskCitationForWeb(
  recordId: string,
  messageId: string,
  citationId: string,
): Promise<ReadingRecordAskCitationNavigateResultDto | Response> {
  const session = await requireUpstreamSession();
  if (!session) {
    return authError("请先登录后再使用 Ask Claread。");
  }
  if (!recordId?.trim()) {
    return missingReadingRecordIdResponse();
  }
  const upstream = await navigateUpstreamReadingRecordAskCitation(
    recordId,
    messageId,
    citationId,
    session.sessionToken,
  );
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: upstream.message }), {
      status: upstream.status || 503,
      headers: { "content-type": "application/json" },
    });
  }
  return upstream.data;
}
