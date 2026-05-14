import "server-only";

import {
  createUserAnnotation,
  listUserAnnotations,
} from "@/services/api/annotations";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type {
  UserAnnotationCreateRequestDto,
  UserAnnotationResponseDto,
  WebAnnotationCreateRequest,
  WebAnnotationVm,
} from "@/types/api/annotations";

export type AnnotationsBffStatus =
  | "ready"
  | "created"
  | "invalid_request"
  | "unauthenticated"
  | "mock_session"
  | "upstream_unavailable"
  | "upstream_error";

export interface AnnotationsBffResult {
  status: AnnotationsBffStatus;
  items: WebAnnotationVm[];
  session: ReturnType<typeof projectSession>;
  message?: string;
}

export type CreateAnnotationBffResult =
  | {
      ok: true;
      status: "created";
      item: WebAnnotationVm;
      session: ReturnType<typeof projectSession>;
    }
  | {
      ok: false;
      status: Exclude<AnnotationsBffStatus, "ready" | "created">;
      message: string;
      session: ReturnType<typeof projectSession>;
      httpStatus: number;
    };

function unavailableStatus(status: number): "upstream_unavailable" | "upstream_error" {
  return status === 0 || status >= 500 ? "upstream_unavailable" : "upstream_error";
}

function unavailableMessage(status: number, message: string): string {
  return status === 0 || status >= 500 ? "批注服务暂时不可用，请稍后重试。" : message;
}

function authError(session: WebSession): {
  status: "unauthenticated" | "mock_session";
  message: string;
  httpStatus: number;
} {
  return {
    status: session.kind === "mock_phone" ? "mock_session" : "unauthenticated",
    message:
      session.kind === "mock_phone"
        ? "当前登录态不能写入真实批注，请使用真实登录会话后再试。"
        : "请先登录后使用批注。",
    httpStatus: 401,
  };
}

function projectAnnotation(item: UserAnnotationResponseDto): WebAnnotationVm {
  return {
    id: item.id,
    recordId: item.analysis_record_id,
    type: item.annotation_type,
    anchorType: item.anchor_type,
    targetKey: item.target_key,
    paragraphId: item.paragraph_id,
    sentenceId: item.sentence_id,
    selectedText: item.selected_text,
    color: item.color,
    note: item.note,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value.trim() : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export async function getReaderAnnotations(recordId: string): Promise<AnnotationsBffResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    const error = authError(session);
    return {
      status: error.status,
      items: [],
      session: projectSession(session),
      message: error.message,
    };
  }

  const upstreamResult = await listUserAnnotations(session.sessionToken, {
    analysisRecordId: recordId,
    limit: 100,
    offset: 0,
  });

  if (!upstreamResult.ok) {
    return {
      status: unavailableStatus(upstreamResult.status),
      items: [],
      session: projectSession(session),
      message: unavailableMessage(upstreamResult.status, upstreamResult.message),
    };
  }

  return {
    status: "ready",
    items: upstreamResult.data.items.map(projectAnnotation),
    session: projectSession(session),
  };
}

export async function createReaderSentenceAnnotation(
  body: unknown,
): Promise<CreateAnnotationBffResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    const error = authError(session);
    return {
      ok: false,
      status: error.status,
      message: error.message,
      session: projectSession(session),
      httpStatus: error.httpStatus,
    };
  }

  if (!isRecord(body)) {
    return {
      ok: false,
      status: "invalid_request",
      message: "请求体格式不正确。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }

  const request = body as Partial<WebAnnotationCreateRequest>;
  const recordId = readString(request.recordId);
  const sentenceId = readString(request.sentenceId);
  const paragraphId = readString(request.paragraphId);
  const selectedText = readString(request.selectedText);
  const note = readString(request.note);

  if (!recordId || !sentenceId || !selectedText) {
    return {
      ok: false,
      status: "invalid_request",
      message: "recordId、sentenceId 和 selectedText 是必填项。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }

  const upstreamBody: UserAnnotationCreateRequestDto = {
    analysis_record_id: recordId,
    annotation_type: note ? "note" : "highlight",
    anchor_type: "sentence",
    paragraph_id: paragraphId,
    sentence_id: sentenceId,
    selected_text: selectedText,
    color: request.color ?? "soft_green",
    note: note || null,
    payload_json: {
      source: "web_reader_sentence_action",
      range_status: "sentence_anchor_no_text_range",
    },
  };

  const upstreamResult = await createUserAnnotation(session.sessionToken, upstreamBody);

  if (!upstreamResult.ok) {
    return {
      ok: false,
      status: unavailableStatus(upstreamResult.status),
      message: unavailableMessage(upstreamResult.status, upstreamResult.message),
      session: projectSession(session),
      httpStatus: upstreamResult.status === 0 ? 503 : upstreamResult.status,
    };
  }

  return {
    ok: true,
    status: "created",
    item: projectAnnotation(upstreamResult.data),
    session: projectSession(session),
  };
}
