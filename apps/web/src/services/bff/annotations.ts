import "server-only";

import {
  TEXT_RANGE_HASH_ALGORITHM,
  TEXT_RANGE_OFFSET_UNIT,
  USER_ANNOTATION_ANCHOR_TYPES,
  USER_ANNOTATION_COLORS,
} from "@claread/contracts";
import {
  createUserAnnotation,
  deleteUserAnnotation,
  listUserAnnotations,
  updateUserAnnotation,
} from "@/services/api/annotations";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type {
  UserAnchorSegmentDto,
  UserAnnotationCreateRequestDto,
  UserAnnotationUpdateRequestDto,
  UserAnnotationResponseDto,
  WebAnchorSegmentVm,
  WebAnnotationCreateRequest,
  WebAnnotationUpdateRequest,
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

const annotationAnchorTypeValues = new Set<string>(USER_ANNOTATION_ANCHOR_TYPES);
const annotationColorValues = new Set<string>(USER_ANNOTATION_COLORS);

function isUserAnnotationColor(value: unknown): value is NonNullable<WebAnnotationUpdateRequest["color"]> {
  return typeof value === "string" && annotationColorValues.has(value);
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

export type UpdateAnnotationBffResult =
  | {
      ok: true;
      status: "updated";
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

export type DeleteAnnotationBffResult =
  | {
      ok: true;
      status: "deleted";
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

function projectSegment(segment: UserAnchorSegmentDto): WebAnchorSegmentVm {
  return {
    paragraphId: segment.paragraph_id ?? null,
    sentenceId: segment.sentence_id,
    selectedText: segment.selected_text,
    startOffset: segment.start_offset,
    endOffset: segment.end_offset,
    textHash: segment.text_hash,
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
    startOffset: item.start_offset,
    endOffset: item.end_offset,
    textHash: item.text_hash,
    segments: Array.isArray(item.segments) ? item.segments.map(projectSegment) : [],
    color: item.color,
    note: item.note,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value.trim() : undefined;
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
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
  const anchorType = request.anchorType ?? "sentence";
  const startOffset = readNumber(request.startOffset);
  const endOffset = readNumber(request.endOffset);
  const textHash = readString(request.textHash);
  const segments = Array.isArray(request.segments)
    ? request.segments
        .filter((segment) => isRecord(segment))
        .map((segment) => ({
          paragraph_id: readString(segment.paragraphId) ?? null,
          sentence_id: readString(segment.sentenceId) ?? "",
          selected_text: readString(segment.selectedText) ?? "",
          start_offset: readNumber(segment.startOffset) ?? -1,
          end_offset: readNumber(segment.endOffset) ?? -1,
          text_hash: readString(segment.textHash) ?? "",
        }))
    : [];

  if (!recordId || !selectedText) {
    return {
      ok: false,
      status: "invalid_request",
      message: "recordId 和 selectedText 是必填项。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }

  if (!annotationAnchorTypeValues.has(anchorType)) {
    return {
      ok: false,
      status: "invalid_request",
      message: "anchorType 不正确。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }

  if (anchorType === "text_range") {
    if (!sentenceId) {
      return {
        ok: false,
        status: "invalid_request",
        message: "text_range 需要 sentenceId。",
        session: projectSession(session),
        httpStatus: 400,
      };
    }
    if (startOffset === undefined || endOffset === undefined || startOffset < 0 || startOffset >= endOffset || !textHash) {
      return {
        ok: false,
        status: "invalid_request",
        message: "text_range 需要有效的 startOffset、endOffset 和 textHash。",
        session: projectSession(session),
        httpStatus: 400,
      };
    }
    if (selectedText.length !== endOffset - startOffset) {
      return {
        ok: false,
        status: "invalid_request",
        message: "text_range 的 selectedText 长度必须匹配 startOffset/endOffset。",
        session: projectSession(session),
        httpStatus: 400,
      };
    }
  }

  if (anchorType === "multi_text") {
    if (
      segments.length < 2 ||
      segments.some(
        (segment) =>
          !segment.sentence_id ||
          !segment.selected_text ||
          segment.start_offset < 0 ||
          segment.end_offset <= segment.start_offset ||
          !segment.text_hash,
      )
    ) {
      return {
        ok: false,
        status: "invalid_request",
        message: "multi_text 需要至少两个有效 segments。",
        session: projectSession(session),
        httpStatus: 400,
      };
    }
  }

  const upstreamBody: UserAnnotationCreateRequestDto = {
    analysis_record_id: recordId,
    annotation_type: note ? "note" : "highlight",
    anchor_type: anchorType,
    paragraph_id: paragraphId,
    sentence_id: sentenceId,
    selected_text: selectedText,
    start_offset: anchorType === "text_range" ? startOffset : null,
    end_offset: anchorType === "text_range" ? endOffset : null,
    text_hash: anchorType === "text_range" ? textHash : null,
    segments: anchorType === "multi_text" ? segments : [],
    color: request.color ?? "soft_green",
    note: note || null,
    payload_json: {
      ...(isRecord(request.payloadJson) ? request.payloadJson : {}),
      source: anchorType === "sentence" ? "web_reader_sentence_action" : "web_reader_text_action",
      offset_unit: anchorType === "sentence" ? undefined : TEXT_RANGE_OFFSET_UNIT,
      text_hash_algorithm: anchorType === "sentence" ? undefined : TEXT_RANGE_HASH_ALGORITHM,
      range_status:
        anchorType === "sentence"
          ? "sentence_anchor"
          : anchorType === "multi_text"
            ? "multi_text_anchor"
            : "text_range_anchor",
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

export async function updateReaderAnnotation(
  annotationId: string,
  body: unknown,
): Promise<UpdateAnnotationBffResult> {
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

  if (!annotationId.trim() || !isRecord(body)) {
    return {
      ok: false,
      status: "invalid_request",
      message: "请求体格式不正确。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }

  const request = body as Partial<WebAnnotationUpdateRequest>;
  const upstreamBody: UserAnnotationUpdateRequestDto = {};

  if ("color" in request) {
    if (request.color !== null && request.color !== undefined && !isUserAnnotationColor(request.color)) {
      return {
        ok: false,
        status: "invalid_request",
        message: "color 不正确。",
        session: projectSession(session),
        httpStatus: 400,
      };
    }
    upstreamBody.color = request.color ?? null;
  }

  if ("note" in request) {
    upstreamBody.note = typeof request.note === "string" ? request.note.trim() || null : null;
  }

  if (!("color" in upstreamBody) && !("note" in upstreamBody)) {
    return {
      ok: false,
      status: "invalid_request",
      message: "至少需要提供 color 或 note。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }

  const upstreamResult = await updateUserAnnotation(session.sessionToken, annotationId, upstreamBody);

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
    status: "updated",
    item: projectAnnotation(upstreamResult.data),
    session: projectSession(session),
  };
}

export async function deleteReaderAnnotation(annotationId: string): Promise<DeleteAnnotationBffResult> {
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

  if (!annotationId.trim()) {
    return {
      ok: false,
      status: "invalid_request",
      message: "annotationId 是必填项。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }

  const upstreamResult = await deleteUserAnnotation(session.sessionToken, annotationId);

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
    status: "deleted",
    session: projectSession(session),
  };
}
