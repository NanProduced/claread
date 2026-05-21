import "server-only";

import {
  createReaderNote,
  deleteReaderNote,
  listReaderNotes,
  updateReaderNote,
} from "@/services/api/reader-notes";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type {
  ReaderNoteCreateRequestDto,
  ReaderNoteUpdateRequestDto,
  ReaderNoteResponseDto,
  WebReaderNoteCreateRequest,
  WebReaderNoteVm,
} from "@/types/api/reader-notes";

type ReaderNotesBffStatus =
  | "ready"
  | "created"
  | "updated"
  | "deleted"
  | "invalid_request"
  | "unauthenticated"
  | "mock_session"
  | "upstream_unavailable"
  | "upstream_error";

export interface ReaderNotesBffResult {
  status: ReaderNotesBffStatus;
  items: WebReaderNoteVm[];
  session: ReturnType<typeof projectSession>;
  message?: string;
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
        ? "当前登录态不能写入真实笔记，请使用真实登录会话后再试。"
        : "请先登录后使用笔记。",
    httpStatus: 401,
  };
}

function unavailableStatus(status: number): "upstream_unavailable" | "upstream_error" {
  return status === 0 || status >= 500 ? "upstream_unavailable" : "upstream_error";
}

function projectNote(item: ReaderNoteResponseDto): WebReaderNoteVm {
  return {
    id: item.id,
    recordId: item.analysis_record_id,
    anchorSentenceId: item.anchor_sentence_id,
    quoteMode: item.quote_mode,
    targetKey: item.target_key,
    paragraphId: item.paragraph_id,
    sentenceId: item.sentence_id,
    selectedText: item.selected_text,
    startOffset: item.start_offset,
    endOffset: item.end_offset,
    textHash: item.text_hash,
    segments: (item.segments ?? []).map((segment) => ({
      paragraphId: segment.paragraph_id ?? null,
      sentenceId: segment.sentence_id,
      selectedText: segment.selected_text,
      startOffset: segment.start_offset,
      endOffset: segment.end_offset,
      textHash: segment.text_hash,
    })),
    noteText: item.note_text,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

export async function getReaderNotes(recordId: string): Promise<ReaderNotesBffResult> {
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

  const upstreamResult = await listReaderNotes(session.sessionToken, recordId);
  if (!upstreamResult.ok) {
    return {
      status: unavailableStatus(upstreamResult.status),
      items: [],
      session: projectSession(session),
      message: upstreamResult.message,
    };
  }
  return {
    status: "ready",
    items: upstreamResult.data.items.map(projectNote),
    session: projectSession(session),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export async function createWebReaderNote(body: unknown) {
  const session = await getWebSession();
  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    const error = authError(session);
    return {
      ok: false as const,
      status: error.status,
      message: error.message,
      session: projectSession(session),
      httpStatus: error.httpStatus,
    };
  }
  if (!isRecord(body)) {
    return {
      ok: false as const,
      status: "invalid_request" as const,
      message: "请求体格式不正确。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }
  const request = body as unknown as WebReaderNoteCreateRequest;
  const upstreamBody: ReaderNoteCreateRequestDto = {
    analysis_record_id: request.recordId,
    quote_mode: request.quoteMode,
    anchor_sentence_id: request.anchorSentenceId,
    paragraph_id: request.paragraphId ?? null,
    sentence_id: request.sentenceId ?? null,
    selected_text: request.selectedText,
    start_offset: request.startOffset ?? null,
    end_offset: request.endOffset ?? null,
    text_hash: request.textHash ?? null,
    segments: (request.segments ?? []).map((segment) => ({
      paragraph_id: segment.paragraphId ?? null,
      sentence_id: segment.sentenceId,
      selected_text: segment.selectedText,
      start_offset: segment.startOffset,
      end_offset: segment.endOffset,
      text_hash: segment.textHash,
    })),
    note_text: request.noteText,
    payload_json: request.payloadJson ?? {},
  };
  const upstreamResult = await createReaderNote(session.sessionToken, upstreamBody);
  if (!upstreamResult.ok) {
    return {
      ok: false as const,
      status: unavailableStatus(upstreamResult.status),
      message: upstreamResult.message,
      session: projectSession(session),
      httpStatus: upstreamResult.status === 0 ? 503 : upstreamResult.status,
    };
  }
  return {
    ok: true as const,
    status: "created" as const,
    item: projectNote(upstreamResult.data),
    session: projectSession(session),
  };
}

export async function updateWebReaderNote(noteId: string, body: unknown) {
  const session = await getWebSession();
  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    const error = authError(session);
    return {
      ok: false as const,
      status: error.status,
      message: error.message,
      session: projectSession(session),
      httpStatus: error.httpStatus,
    };
  }
  if (!isRecord(body) || typeof body.noteText !== "string" || !body.noteText.trim()) {
    return {
      ok: false as const,
      status: "invalid_request" as const,
      message: "noteText 是必填项。",
      session: projectSession(session),
      httpStatus: 400,
    };
  }
  const upstreamBody: ReaderNoteUpdateRequestDto = {
    note_text: body.noteText.trim(),
  };
  const upstreamResult = await updateReaderNote(session.sessionToken, noteId, upstreamBody);
  if (!upstreamResult.ok) {
    return {
      ok: false as const,
      status: unavailableStatus(upstreamResult.status),
      message: upstreamResult.message,
      session: projectSession(session),
      httpStatus: upstreamResult.status === 0 ? 503 : upstreamResult.status,
    };
  }
  return {
    ok: true as const,
    status: "updated" as const,
    item: projectNote(upstreamResult.data),
    session: projectSession(session),
  };
}

export async function deleteWebReaderNote(noteId: string) {
  const session = await getWebSession();
  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    const error = authError(session);
    return {
      ok: false as const,
      status: error.status,
      message: error.message,
      session: projectSession(session),
      httpStatus: error.httpStatus,
    };
  }
  const upstreamResult = await deleteReaderNote(session.sessionToken, noteId);
  if (!upstreamResult.ok) {
    return {
      ok: false as const,
      status: unavailableStatus(upstreamResult.status),
      message: upstreamResult.message,
      session: projectSession(session),
      httpStatus: upstreamResult.status === 0 ? 503 : upstreamResult.status,
    };
  }
  return {
    ok: true as const,
    status: "deleted" as const,
    session: projectSession(session),
  };
}
