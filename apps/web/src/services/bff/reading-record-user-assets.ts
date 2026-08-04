import "server-only";

import { USER_ANNOTATION_COLORS } from "@claread/contracts";
import {
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type UserEditorialAssetAnchorDto,
} from "@/types/api/reader-plate";
import { createUserAnnotation, deleteUserAnnotation, updateUserAnnotation } from "@/services/api/annotations";
import { createReaderNote, deleteReaderNote, updateReaderNote } from "@/services/api/reader-notes";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type {
  UserAnnotationColorDto,
  UserAnnotationCreateRequestDto,
  UserAnnotationResponseDto,
  UserAnnotationUpdateRequestDto,
} from "@/types/api/annotations";
import type {
  ReaderNoteCreateRequestDto,
  ReaderNoteResponseDto,
  ReaderNoteUpdateRequestDto,
} from "@/types/api/reader-notes";

type ReadingRecordUserAssetStatus =
  | "created"
  | "invalid_request"
  | "unauthenticated"
  | "limited_debug"
  | "upstream_unavailable"
  | "upstream_error";

type ReadingRecordProjectedSession = ReturnType<typeof projectSession>;

type AuthenticatedWebSession =
  | Extract<WebSession, { kind: "authenticated" }>
  | Extract<WebSession, { kind: "debug" }>;

type ReadingRecordUserAssetSuccess<T> = {
  ok: true;
  status: "created" | "updated" | "deleted";
  item: T;
  session: ReadingRecordProjectedSession;
};

type ReadingRecordUserAssetError = {
  ok: false;
  status: Exclude<ReadingRecordUserAssetStatus, "created">;
  message: string;
  session: ReadingRecordProjectedSession;
  httpStatus: number;
};

type ReadingRecordUserAssetResult<T> =
  | ReadingRecordUserAssetSuccess<T>
  | ReadingRecordUserAssetError;

const userAnnotationColorValues = new Set<string>(USER_ANNOTATION_COLORS);

function authError(session: WebSession): {
  status: "unauthenticated" | "limited_debug";
  message: string;
  httpStatus: number;
} {
  return {
    status: session.kind === "mock_phone" ? "limited_debug" : "unauthenticated",
    message:
      session.kind === "mock_phone"
        ? "当前登录态不能写入真实阅读资产，请使用真实登录会话后再试。"
        : "请先登录后使用阅读资产。",
    httpStatus: 401,
  };
}

function unavailableStatus(status: number): "upstream_unavailable" | "upstream_error" {
  return status === 0 || status >= 500 ? "upstream_unavailable" : "upstream_error";
}

function unavailableMessage(status: number, message: string): string {
  return status === 0 || status >= 500 ? "阅读资产服务暂时不可用，请稍后重试。" : message;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value.trim() : undefined;
}

function readRawString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function readInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) ? value : undefined;
}

function readUserAnnotationColor(value: unknown): UserAnnotationColorDto | undefined {
  const color = readString(value);
  return color && userAnnotationColorValues.has(color)
    ? (color as UserAnnotationColorDto)
    : undefined;
}

function parseAnchor(value: unknown): UserEditorialAssetAnchorDto | null {
  if (!isRecord(value)) {
    return null;
  }

  const recordId = readString(value.record_id);
  const baseId = readString(value.base_id);
  const generation = readInteger(value.generation);
  const unitId = readString(value.unit_id);
  const anchorSegmentId = readString(value.anchor_segment_id);
  const selectedText = readRawString(value.selected_text);
  const textHash = readString(value.text_hash);
  const startOffset = readInteger(value.start_offset);
  const endOffset = readInteger(value.end_offset);
  const rawScope = readString(value.scope);

  if (rawScope !== undefined && rawScope !== "stable_source") {
    return null;
  }

  if (
    !recordId ||
    !baseId ||
    generation === undefined ||
    generation < 1 ||
    !unitId ||
    !anchorSegmentId ||
    !selectedText?.trim() ||
    !textHash ||
    startOffset === undefined ||
    endOffset === undefined ||
    startOffset >= endOffset
  ) {
    return null;
  }

  return {
    record_id: recordId,
    base_id: baseId,
    generation,
    unit_id: unitId,
    anchor_segment_id: anchorSegmentId,
    scope: "stable_source",
    offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
    start_offset: startOffset,
    end_offset: endOffset,
    selected_text: selectedText,
    text_hash: textHash,
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
  };
}

function invalidRequest(session: WebSession, message: string): ReadingRecordUserAssetError {
  return {
    ok: false,
    status: "invalid_request",
    message,
    session: projectSession(session),
    httpStatus: 400,
  };
}

async function authenticatedSession(): Promise<
  | {
      ok: true;
      session: AuthenticatedWebSession;
    }
  | ReadingRecordUserAssetError
> {
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
  return { ok: true, session };
}

export async function createReadingRecordHighlight(
  body: unknown,
  expectedRecordId?: string,
): Promise<ReadingRecordUserAssetResult<UserAnnotationResponseDto>> {
  const sessionResult = await authenticatedSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const { session } = sessionResult;
  if (!isRecord(body)) {
    return invalidRequest(session, "请求体格式不正确。");
  }

  const anchor = parseAnchor(body.anchor);
  if (!anchor) {
    return invalidRequest(session, "anchor 是必填项。");
  }
  if (expectedRecordId?.trim() && anchor.record_id !== expectedRecordId.trim()) {
    return invalidRequest(session, "anchor.record_id 与路由阅读记录不一致。");
  }

  const selectedText = readRawString(body.selectedText) ?? anchor.selected_text;
  if (selectedText !== anchor.selected_text) {
    return invalidRequest(session, "selectedText 必须与 anchor.selected_text 一致。");
  }

  const color = body.color === undefined ? "warm_yellow" : readUserAnnotationColor(body.color);
  if (!color) {
    return invalidRequest(session, "color 必须是 warm_yellow、soft_mint 或 soft_rose。");
  }
  const upstreamBody: UserAnnotationCreateRequestDto = {
    selected_text: anchor.selected_text,
    color,
    payload_json: {
      source: "reader_record_plate_surface",
      action: "highlight",
      offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
      text_hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      range_status: "reading_record_anchor",
    },
    anchor,
  };

  const upstreamResult = await createUserAnnotation(
    session.sessionToken,
    upstreamBody,
  );

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
    item: upstreamResult.data,
    session: projectSession(session),
  };
}

export async function createReadingRecordNote(
  body: unknown,
  expectedRecordId?: string,
): Promise<ReadingRecordUserAssetResult<ReaderNoteResponseDto>> {
  const sessionResult = await authenticatedSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const { session } = sessionResult;
  if (!isRecord(body)) {
    return invalidRequest(session, "请求体格式不正确。");
  }

  const anchor = parseAnchor(body.anchor);
  const noteText = readString(body.noteText);
  if (!anchor) {
    return invalidRequest(session, "anchor 是必填项。");
  }
  if (expectedRecordId?.trim() && anchor.record_id !== expectedRecordId.trim()) {
    return invalidRequest(session, "anchor.record_id 与路由阅读记录不一致。");
  }
  if (!noteText) {
    return invalidRequest(session, "noteText 是必填项。");
  }

  const selectedText = readRawString(body.selectedText) ?? anchor.selected_text;
  if (selectedText !== anchor.selected_text) {
    return invalidRequest(session, "selectedText 必须与 anchor.selected_text 一致。");
  }

  const upstreamBody: ReaderNoteCreateRequestDto = {
    selected_text: anchor.selected_text,
    note_text: noteText,
    payload_json: {
      source: "reader_record_plate_surface",
      action: "note",
      offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
      text_hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      range_status: "reading_record_anchor",
    },
    anchor,
  };

  const upstreamResult = await createReaderNote(session.sessionToken, upstreamBody);

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
    item: upstreamResult.data,
    session: projectSession(session),
  };
}

export async function deleteReadingRecordHighlight(
  highlightId: string,
): Promise<ReadingRecordUserAssetResult<{ ok: boolean }>> {
  const sessionResult = await authenticatedSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const { session } = sessionResult;
  const trimmedId = highlightId.trim();
  if (!trimmedId) {
    return invalidRequest(session, "highlightId 是必填项。");
  }

  const upstreamResult = await deleteUserAnnotation(
    session.sessionToken,
    trimmedId,
  );

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
    item: upstreamResult.data,
    session: projectSession(session),
  };
}

export async function updateReadingRecordHighlight(
  highlightId: string,
  body: unknown,
): Promise<ReadingRecordUserAssetResult<UserAnnotationResponseDto>> {
  const sessionResult = await authenticatedSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const { session } = sessionResult;
  const trimmedId = highlightId.trim();
  if (!trimmedId) {
    return invalidRequest(session, "highlightId 是必填项。");
  }

  if (!isRecord(body)) {
    return invalidRequest(session, "请求体格式不正确。");
  }

  const color = readUserAnnotationColor(body.color);
  if (!color) {
    return invalidRequest(session, "color 必须是 warm_yellow、soft_mint 或 soft_rose。");
  }

  const upstreamBody: UserAnnotationUpdateRequestDto = { color };
  const upstreamResult = await updateUserAnnotation(
    session.sessionToken,
    trimmedId,
    upstreamBody,
  );

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
    item: upstreamResult.data,
    session: projectSession(session),
  };
}

export async function deleteReadingRecordNote(
  noteId: string,
): Promise<ReadingRecordUserAssetResult<{ ok: boolean }>> {
  const sessionResult = await authenticatedSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const { session } = sessionResult;
  const trimmedId = noteId.trim();
  if (!trimmedId) {
    return invalidRequest(session, "noteId 是必填项。");
  }

  const upstreamResult = await deleteReaderNote(
    session.sessionToken,
    trimmedId,
  );

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
    item: upstreamResult.data,
    session: projectSession(session),
  };
}

export async function updateReadingRecordNote(
  noteId: string,
  body: unknown,
): Promise<ReadingRecordUserAssetResult<ReaderNoteResponseDto>> {
  const sessionResult = await authenticatedSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const { session } = sessionResult;
  const trimmedId = noteId.trim();
  if (!trimmedId) {
    return invalidRequest(session, "noteId 是必填项。");
  }

  if (!isRecord(body)) {
    return invalidRequest(session, "请求体格式不正确。");
  }

  const noteText = readString(body.noteText);
  if (!noteText) {
    return invalidRequest(session, "noteText 是必填项。");
  }

  const upstreamBody: ReaderNoteUpdateRequestDto = { note_text: noteText };
  const upstreamResult = await updateReaderNote(
    session.sessionToken,
    trimmedId,
    upstreamBody,
  );

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
    item: upstreamResult.data,
    session: projectSession(session),
  };
}
