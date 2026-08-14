import "server-only";

import { randomUUID } from "node:crypto";

import {
  completeUpstreamReaderSourceArtifactUpload,
  confirmUpstreamReaderCandidateDocument,
  ensureUpstreamReaderArticleRagIndex,
  getUpstreamReaderArticleRagIndexStatus,
  getUpstreamReaderArtifactPipelineStatus,
  getUpstreamReaderCandidateDocument,
  getUpstreamReaderConfirmedSource,
  getUpstreamReaderPlateSnapshot,
  getUpstreamReaderStableDocument,
  initUpstreamReaderSourceArtifactUpload,
  pollUpstreamReaderEvents,
  putUpstreamReaderConfirmedSource,
  submitUpstreamReaderPlainText,
  submitUpstreamReaderSectionTranslation,
  submitUpstreamReaderSourceArtifactInput,
  submitUpstreamReaderUnifiedInput,
} from "@/services/api/reader-plate";
import { appReaderRoute } from "@/lib/routes";
import {
  normalizeReaderRecordReadingDefaults,
  type ReaderRecordReadingDefaultState,
} from "@/lib/reading-defaults";
import {
  mapArticleRagIndexEnsure,
  mapArticleRagIndexStatus,
  mapArtifactPipelineStatus,
  type ReaderArticleRagIndexEnsureSafeDto,
  type ReaderArticleRagIndexStatusSafeDto,
  type ReaderArtifactPipelineStatusSafeDto,
} from "@/lib/reader-orchestration/status-mapper";
import { getWebSession } from "@/services/bff/session";
import type {
  ReaderArticleRagIndexEnsureRequestDto,
  ReaderArticleRagIndexEnsureResponseDto,
  ReaderArticleRagIndexStatusResponseDto,
  ReaderArtifactPipelineStatusResponseDto,
  ReaderCandidateDocumentConfirmRequestDto,
  ReaderCandidateDocumentConfirmResponseDto,
  ReaderCandidateDocumentConflictBody,
  ReaderCandidateDocumentNotFoundBody,
  ReaderCandidateDocumentOutlineItem,
  ReaderCandidateDocumentPreview,
  ReaderCandidateDocumentPreviewMode,
  ReaderCandidateDocumentReadResponse,
  ReaderCandidateDocumentRiskItem,
  ReaderAdaptationRecordDto,
  ReaderConfirmedSourceCandidateSummaryDto,
  ReaderConfirmedSourceReadResponseDto,
  ReaderConfirmedSourceUpdateRequestDto,
  ReaderConfirmedSourceUpdateResponseDto,
  ReaderEventPollResponseDto,
  ReaderInputAdapterSourceTypeDto,
  ReaderPlainTextSubmitResponseDto,
  ReaderPlateSnapshotDto,
  ReaderSectionTranslationRequestDto,
  ReaderSectionTranslationResponseDto,
  ReaderSourceArtifactSubmitInputRequestDto,
  ReaderSourceArtifactSubmitInputResponseDto,
  ReaderSourceArtifactUploadCompleteRequestDto,
  ReaderSourceArtifactUploadCompleteResponseDto,
  ReaderSourceArtifactUploadInitRequestDto,
  ReaderSourceArtifactUploadInitResponseDto,
  ReaderStableDocumentResponseDto,
  ReaderUnifiedInputSubmitRequestDto,
  ReaderUnifiedInputSubmitResponseDto,
} from "@/types/api/reader-plate";

export type ReaderPlateBffError = {
  ok: false;
  status: number;
  code:
    | "auth_required"
    | "upstream_auth_failed"
    | "record_not_found"
    | "record_not_ready"
    | "artifact_not_found"
    | "upstream_unavailable"
    | "upstream_error"
    | "empty_text"
    | "invalid_input"
    | "candidate_conflict"
    | "candidate_not_found"
    | "candidate_conflict_open_reader"
    | "candidate_conflict_return_to_library"
    // L2 confirmed-source conflicts (409 pass-through, 合同 “GET draft / resume 语义” / “PUT whole-document update”).
    | "confirmed_source_not_found"
    | "stale_source_revision"
    | "stale_candidate_revision"
    // Section-translation fence conflict (409 from upstream).
    | "section_translation_conflict";
  message: string;
  recordId?: string;
  /**
   * L2 confirmed-source: `current_revision` carried by a 409
   * `stale_source_revision` body (合同 “`expected_revision` 乐观并发”) so the client can
   * rebase its optimistic-concurrency expectation after reloading.
   */
  currentRevision?: number;
};

export type ReaderPlateSubmitResult =
  | ({ ok: true } & ReaderPlainTextSubmitResponseDto)
  | ReaderPlateBffError;

export type ReadingRecordSubmitResult =
  | {
      ok: true;
      message: string;
      readingRecordId: string;
      readerUrl: string;
      baseId: string;
      articleReadySequence: number;
      snapshot: ReaderPlateSnapshotDto;
    }
  | ReaderPlateBffError;

export type ReaderPlateSnapshotResult =
  | ({ ok: true } & ReaderPlateSnapshotDto)
  | ReaderPlateBffError;

export type ReaderPlateEventsResult =
  | ({ ok: true } & ReaderEventPollResponseDto)
  | ReaderPlateBffError;

export type ReaderUnifiedInputSubmitResult =
  | ({ ok: true } & ReaderUnifiedInputSubmitResponseDto)
  | ReaderPlateBffError;

export type ReaderSourceArtifactUploadInitResult =
  | ({ ok: true } & ReaderSourceArtifactUploadInitResponseDto)
  | ReaderPlateBffError;

export type ReaderSourceArtifactUploadCompleteResult =
  | ({ ok: true } & ReaderSourceArtifactUploadCompleteResponseDto)
  | ReaderPlateBffError;

export type ReaderSourceArtifactSubmitInputResult =
  | ({ ok: true } & ReaderSourceArtifactSubmitInputResponseDto)
  | ReaderPlateBffError;

export type ReaderArtifactPipelineStatusResult =
  | ({ ok: true } & ReaderArtifactPipelineStatusSafeDto)
  | ReaderPlateBffError;

export type ReaderCandidateDocumentConfirmResult =
  | ({ ok: true } & ReaderCandidateDocumentConfirmResponseDto)
  | ReaderPlateBffError;

export type ReaderStableDocumentResult =
  | ({ ok: true } & ReaderStableDocumentResponseDto)
  | ReaderPlateBffError;

export type ReaderCandidateDocumentReadResult =
  | ({ ok: true } & ReaderCandidateDocumentReadResponse)
  | ReaderPlateBffError;

export type ReaderConfirmedSourceReadResult =
  | ({ ok: true } & ReaderConfirmedSourceReadResponseDto)
  | ReaderPlateBffError;

export type ReaderConfirmedSourceUpdateResult =
  | ({ ok: true } & ReaderConfirmedSourceUpdateResponseDto)
  | ReaderPlateBffError;

export type ReaderArticleRagIndexStatusResult =
  | ({ ok: true } & ReaderArticleRagIndexStatusSafeDto)
  | ReaderPlateBffError;

export type ReaderArticleRagIndexEnsureResult =
  | ({ ok: true } & ReaderArticleRagIndexEnsureSafeDto)
  | ReaderPlateBffError;

function authRequired(message: string): ReaderPlateBffError {
  return { ok: false, status: 401, code: "auth_required", message };
}

function invalidInput(message: string): ReaderPlateBffError {
  return { ok: false, status: 400, code: "invalid_input", message };
}

function candidateConflict(message: string): ReaderPlateBffError {
  return { ok: false, status: 409, code: "candidate_conflict", message };
}

function candidateNotFound(message: string): ReaderPlateBffError {
  return { ok: false, status: 404, code: "candidate_not_found", message };
}

function candidateConflictOpenReader(message: string, recordId: string): ReaderPlateBffError {
  return {
    ok: false,
    status: 409,
    code: "candidate_conflict_open_reader",
    message,
    recordId,
  };
}

function candidateConflictReturnToLibrary(message: string): ReaderPlateBffError {
  return {
    ok: false,
    status: 409,
    code: "candidate_conflict_return_to_library",
    message,
  };
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

/**
 * Route-specific upstream error mapper for source-artifact endpoints.
 * A 404 on `/reader/source-artifacts/{artifact_id}/*` means the artifact
 * was not found — NOT that the reading record was not found. Mapping it
 * to `record_not_found` would surface the wrong user-facing message.
 */
function artifactUpstreamError(
  status: number,
  message: string,
): ReaderPlateBffError {
  if (status === 404) {
    return {
      ok: false,
      status: 404,
      code: "artifact_not_found",
      message: "没有找到这个上传文件，请重新上传或刷新后重试。",
    };
  }
  return upstreamError(status, message);
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

/**
 * Normalize raw (unknown) strategy fields from the request body into the
 * Reader Record submit scope. `academic` / `academic_general` and any
 * unrecognized value fall back to the default `daily_reading` /
 * `intermediate_reading` pair so the upstream API never receives an
 * unsupported strategy.
 */
function resolveReaderRecordStrategy(
  readingGoal: unknown,
  readingVariant: unknown,
): ReaderRecordReadingDefaultState {
  return normalizeReaderRecordReadingDefaults({
    readingGoal: readingGoal as ReaderRecordReadingDefaultState["readingGoal"] | undefined,
    readingVariant: readingVariant as ReaderRecordReadingDefaultState["readingVariant"] | undefined,
  });
}

export async function submitReaderPlainTextFromWeb(input: {
  plainText?: unknown;
  title?: unknown;
  language?: unknown;
  readingGoal?: unknown;
  readingVariant?: unknown;
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

  const strategy = resolveReaderRecordStrategy(
    input.readingGoal,
    input.readingVariant,
  );

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
      reading_goal: strategy.readingGoal,
      reading_variant: strategy.readingVariant,
    },
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

export async function submitReadingRecordPlainTextFromWeb(input: {
  plainText?: unknown;
  title?: unknown;
  language?: unknown;
  readingGoal?: unknown;
  readingVariant?: unknown;
}): Promise<ReadingRecordSubmitResult> {
  const result = await submitReaderPlainTextFromWeb(input);
  if (!result.ok) {
    return result;
  }

  return {
    ok: true,
    message: "阅读记录已创建，正在打开 Reader。",
    readingRecordId: result.record_id,
    readerUrl: appReaderRoute(result.record_id),
    baseId: result.base_id,
    articleReadySequence: result.article_ready_sequence,
    snapshot: result.snapshot,
  };
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
    if (
      upstreamResult.status === 409
      && upstreamResult.message === "reader snapshot requires an active base"
    ) {
      return {
        ok: false,
        status: 409,
        code: "record_not_ready",
        message: "文档仍在解析，请稍后重试。",
      };
    }

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

// ---------------------------------------------------------------------------
// Unified input submit (POST /reader/records/input)
//
// Coerces the request body from the Web client into the typed
// `ReaderUnifiedInputSubmitRequestDto`. The response is a discriminated
// union by `outcome`; the frontend MUST branch on `outcome` before reading
// outcome-specific fields.
// ---------------------------------------------------------------------------

const READER_INPUT_SOURCE_TYPES: ReadonlySet<ReaderInputAdapterSourceTypeDto> = new Set([
  "pasted_text",
  "txt_file",
  "markdown_file",
  "ocr_text",
  "pdf_text",
  "url_text",
]);

function resolveInputSourceType(value: unknown): ReaderInputAdapterSourceTypeDto {
  return typeof value === "string" && READER_INPUT_SOURCE_TYPES.has(
    value as ReaderInputAdapterSourceTypeDto,
  )
    ? (value as ReaderInputAdapterSourceTypeDto)
    : "pasted_text";
}

export async function submitReaderUnifiedInputFromWeb(input: {
  sourceType?: unknown;
  text?: unknown;
  filename?: unknown;
  language?: unknown;
  sourceMetadata?: unknown;
  clientRecordId?: unknown;
  readingGoal?: unknown;
  readingVariant?: unknown;
}): Promise<ReaderUnifiedInputSubmitResult> {
  const text = typeof input.text === "string" ? input.text.trim() : "";

  if (!text) {
    return invalidInput("请先粘贴需要透读的英文内容。");
  }

  const strategy = resolveReaderRecordStrategy(
    input.readingGoal,
    input.readingVariant,
  );

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const payload: ReaderUnifiedInputSubmitRequestDto = {
    source_type: resolveInputSourceType(input.sourceType),
    text,
    filename:
      typeof input.filename === "string" && input.filename.trim()
        ? input.filename
        : null,
    language:
      typeof input.language === "string" && input.language.trim()
        ? input.language
        : null,
    source_metadata:
      input.sourceMetadata && typeof input.sourceMetadata === "object"
        ? (input.sourceMetadata as Record<string, unknown>)
        : null,
    client_record_id:
      typeof input.clientRecordId === "string" && input.clientRecordId.trim()
        ? input.clientRecordId
        : `web-input-${randomUUID()}`,
    reading_goal: strategy.readingGoal,
    reading_variant: strategy.readingVariant,
  };

  const upstreamResult = await submitUpstreamReaderUnifiedInput(
    payload,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

// ---------------------------------------------------------------------------
// Source artifacts: init-upload / complete-upload / submit-input / pipeline-status
//
// These wrappers do not render debug-only fields. `pipeline-status` runs
// through `mapArtifactPipelineStatus` so the UI receives only the safe
// `outcome` / `next_action` pair and a stripped job summary.
// ---------------------------------------------------------------------------

export async function initReaderSourceArtifactUploadFromWeb(input: {
  artifactKind?: unknown;
  sourceFilename?: unknown;
  contentType?: unknown;
  byteSize?: unknown;
  contentSha256?: unknown;
  readingRecordId?: unknown;
  originalInputId?: unknown;
  sourceRefs?: unknown;
  metadata?: unknown;
  quality?: unknown;
}): Promise<ReaderSourceArtifactUploadInitResult> {
  if (input.artifactKind !== "original_upload") {
    return invalidInput("仅支持 original_upload 类型 artifact 上传。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const payload: ReaderSourceArtifactUploadInitRequestDto = {
    artifact_kind: "original_upload",
    source_filename:
      typeof input.sourceFilename === "string" && input.sourceFilename.trim()
        ? input.sourceFilename
        : null,
    content_type:
      typeof input.contentType === "string" && input.contentType.trim()
        ? input.contentType
        : null,
    byte_size:
      typeof input.byteSize === "number" && Number.isFinite(input.byteSize)
        ? input.byteSize
        : null,
    content_sha256:
      typeof input.contentSha256 === "string" && input.contentSha256.trim()
        ? input.contentSha256
        : null,
    reading_record_id:
      typeof input.readingRecordId === "string" && input.readingRecordId.trim()
        ? input.readingRecordId
        : null,
    original_input_id:
      typeof input.originalInputId === "string" && input.originalInputId.trim()
        ? input.originalInputId
        : null,
    source_refs:
      input.sourceRefs && typeof input.sourceRefs === "object"
        ? (input.sourceRefs as Record<string, unknown>)
        : null,
    metadata:
      input.metadata && typeof input.metadata === "object"
        ? (input.metadata as Record<string, unknown>)
        : null,
    quality:
      input.quality && typeof input.quality === "object"
        ? (input.quality as Record<string, unknown>)
        : null,
  };

  const upstreamResult = await initUpstreamReaderSourceArtifactUpload(
    payload,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

export async function completeReaderSourceArtifactUploadFromWeb(
  artifactId: string,
  input: {
    contentType?: unknown;
    byteSize?: unknown;
    contentSha256?: unknown;
    metadata?: unknown;
    quality?: unknown;
  },
): Promise<ReaderSourceArtifactUploadCompleteResult> {
  if (!artifactId) {
    return invalidInput("缺少 artifact_id。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const payload: ReaderSourceArtifactUploadCompleteRequestDto = {
    content_type:
      typeof input.contentType === "string" && input.contentType.trim()
        ? input.contentType
        : null,
    byte_size:
      typeof input.byteSize === "number" && Number.isFinite(input.byteSize)
        ? input.byteSize
        : null,
    content_sha256:
      typeof input.contentSha256 === "string" && input.contentSha256.trim()
        ? input.contentSha256
        : null,
    metadata:
      input.metadata && typeof input.metadata === "object"
        ? (input.metadata as Record<string, unknown>)
        : null,
    quality:
      input.quality && typeof input.quality === "object"
        ? (input.quality as Record<string, unknown>)
        : null,
  };

  const upstreamResult = await completeUpstreamReaderSourceArtifactUpload(
    artifactId,
    payload,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return artifactUpstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

export async function submitReaderSourceArtifactInputFromWeb(
  artifactId: string,
  input: {
    title?: unknown;
    language?: unknown;
    clientRecordId?: unknown;
    sourceMetadata?: unknown;
    readingGoal?: unknown;
    readingVariant?: unknown;
  },
): Promise<ReaderSourceArtifactSubmitInputResult> {
  if (!artifactId) {
    return invalidInput("缺少 artifact_id。");
  }

  const strategy = resolveReaderRecordStrategy(
    input.readingGoal,
    input.readingVariant,
  );

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const payload: ReaderSourceArtifactSubmitInputRequestDto = {
    title:
      typeof input.title === "string" && input.title.trim()
        ? input.title
        : null,
    language:
      typeof input.language === "string" && input.language.trim()
        ? input.language
        : null,
    client_record_id:
      typeof input.clientRecordId === "string" && input.clientRecordId.trim()
        ? input.clientRecordId
        : `web-artifact-${randomUUID()}`,
    source_metadata:
      input.sourceMetadata && typeof input.sourceMetadata === "object"
        ? (input.sourceMetadata as Record<string, unknown>)
        : null,
    reading_goal: strategy.readingGoal,
    reading_variant: strategy.readingVariant,
  };

  const upstreamResult = await submitUpstreamReaderSourceArtifactInput(
    artifactId,
    payload,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return artifactUpstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

export async function getReaderArtifactPipelineStatusFromWeb(
  artifactId: string,
): Promise<ReaderArtifactPipelineStatusResult> {
  if (!artifactId) {
    return invalidInput("缺少 artifact_id。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const upstreamResult = await getUpstreamReaderArtifactPipelineStatus(
    artifactId,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return artifactUpstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...mapArtifactPipelineStatus(upstreamResult.data) };
}

// ---------------------------------------------------------------------------
// Candidate document confirmation
// ---------------------------------------------------------------------------

export async function confirmReaderCandidateDocumentFromWeb(
  recordId: string,
  candidateDocumentId: string,
  input: { language?: unknown } = {},
): Promise<ReaderCandidateDocumentConfirmResult> {
  if (!recordId || !candidateDocumentId) {
    return invalidInput("缺少 record_id 或 candidate_document_id。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const payload: ReaderCandidateDocumentConfirmRequestDto = {
    language:
      typeof input.language === "string" && input.language.trim()
        ? input.language
        : null,
  };

  const upstreamResult = await confirmUpstreamReaderCandidateDocument(
    recordId,
    candidateDocumentId,
    payload,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    if (upstreamResult.status === 409) {
      return candidateConflict(
        "候选文档状态已变化，请刷新后重试。",
      );
    }
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

// ---------------------------------------------------------------------------
// Stable Document projection
// ---------------------------------------------------------------------------

export async function getReaderStableDocumentFromWeb(
  recordId: string,
): Promise<ReaderStableDocumentResult> {
  if (!recordId) {
    return invalidInput("缺少 record_id。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const upstreamResult = await getUpstreamReaderStableDocument(
    recordId,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...upstreamResult.data };
}

// ---------------------------------------------------------------------------
// Candidate document read (S4: input-page recovery)
//
// Runtime sanitizer: TypeScript types are erased at runtime, so the BFF
// explicitly projects the 11 declared top-level fields and the preview
// subtree from the upstream payload. Any extra keys (e.g. `source_text`,
// `blocks_json`, `canonical_text_preview`, `original_input_id`) that the
// upstream might leak in the future are dropped before the response reaches
// the browser.
// ---------------------------------------------------------------------------

const READ_CANDIDATE_DOCUMENT_ALLOWED_TOP_KEYS = [
  "record_id",
  "candidate_document_id",
  "record_generation",
  "status",
  "title",
  "preview",
  "source_type",
  "filename",
  "source_label",
  "created_at",
  "updated_at",
] as const;

const READ_PREVIEW_ALLOWED_KEYS = [
  "preview_mode",
  "preview_text",
  "is_truncated",
  "total_char_count",
  "document_outline",
  "risk_items",
] as const;

const READ_OUTLINE_ITEM_ALLOWED_KEYS = [
  "order_index",
  "block_type_label",
  "heading_text",
  "char_count",
] as const;

const READ_RISK_ITEM_ALLOWED_KEYS = [
  "risk_kind",
  "user_message",
  "severity",
] as const;

const READ_PREVIEW_MODE_VALUES: ReadonlySet<ReaderCandidateDocumentPreviewMode> =
  new Set(["full_text", "truncated_preview", "outline_only"]);

const safeEmptyPreview: ReaderCandidateDocumentPreview = {
  preview_mode: "outline_only",
  preview_text: "",
  is_truncated: false,
  total_char_count: 0,
  document_outline: [],
  risk_items: [],
};

function pickAllowed<T>(
  value: unknown,
  allowedKeys: readonly string[],
): T | null {
  if (!value || typeof value !== "object") return null;
  const source = value as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of allowedKeys) {
    out[key] = source[key];
  }
  return out as T;
}

function sanitizePreview(value: unknown): ReaderCandidateDocumentPreview {
  const obj = pickAllowed<Record<string, unknown>>(
    value,
    READ_PREVIEW_ALLOWED_KEYS,
  );
  if (!obj) return { ...safeEmptyPreview };

  const previewMode: ReaderCandidateDocumentPreviewMode =
    typeof obj.preview_mode === "string" &&
    READ_PREVIEW_MODE_VALUES.has(obj.preview_mode as ReaderCandidateDocumentPreviewMode)
      ? (obj.preview_mode as ReaderCandidateDocumentPreviewMode)
      : "outline_only";

  return {
    preview_mode: previewMode,
    preview_text: typeof obj.preview_text === "string" ? obj.preview_text : "",
    is_truncated: Boolean(obj.is_truncated),
    total_char_count:
      typeof obj.total_char_count === "number" ? obj.total_char_count : 0,
    document_outline: Array.isArray(obj.document_outline)
      ? (obj.document_outline as unknown[])
          .map((item) =>
            pickAllowed<ReaderCandidateDocumentOutlineItem>(
              item,
              READ_OUTLINE_ITEM_ALLOWED_KEYS,
            ),
          )
          .filter((item): item is ReaderCandidateDocumentOutlineItem => item !== null)
      : [],
    risk_items: Array.isArray(obj.risk_items)
      ? (obj.risk_items as unknown[])
          .map((item) =>
            pickAllowed<ReaderCandidateDocumentRiskItem>(
              item,
              READ_RISK_ITEM_ALLOWED_KEYS,
            ),
          )
          .filter((item): item is ReaderCandidateDocumentRiskItem => item !== null)
      : [],
  };
}

export async function getReaderCandidateDocumentFromWeb(
  recordId: string,
): Promise<ReaderCandidateDocumentReadResult> {
  if (!recordId) {
    return invalidInput("缺少 record_id。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const upstreamResult = await getUpstreamReaderCandidateDocument(
    recordId,
    sessionResult.sessionToken,
  );

  if (upstreamResult.ok) {
    const raw = upstreamResult.data as unknown as Record<string, unknown>;
    const top = pickAllowed<ReaderCandidateDocumentReadResponse>(
      raw,
      READ_CANDIDATE_DOCUMENT_ALLOWED_TOP_KEYS,
    );
    if (!top) {
      return upstreamError(502, "upstream returned no data");
    }
    return {
      ok: true,
      ...top,
      preview: sanitizePreview(raw?.preview),
    };
  }

  const body = upstreamResult.body;

  if (upstreamResult.status === 404) {
    const notFound = body as ReaderCandidateDocumentNotFoundBody | undefined;
    return candidateNotFound(
      notFound?.message?.trim() || "未找到可继续确认的内容。",
    );
  }

  if (upstreamResult.status === 409) {
    const conflict = body as ReaderCandidateDocumentConflictBody | undefined;
    if (conflict?.code === "record_state_advanced" && conflict.resolution === "open_reader") {
      return candidateConflictOpenReader(conflict.message, recordId);
    }
    // multiple_ready_candidates OR return_to_library → user lands on library
    return candidateConflictReturnToLibrary("这篇内容当前无法继续确认。");
  }

  return upstreamError(upstreamResult.status, upstreamResult.message);
}

// ---------------------------------------------------------------------------
// Confirmed Source (L2): draft read / resume entry + whole-document update
//
// Frozen contract: docs/initiatives/reader-agentic-orchestration/modules/schema-and-domain-contract.md — Confirmed Source 生命周期.
// The GET endpoint returns the full draft markdown (edit entry, “GET draft / resume 语义”), so
// the same runtime allowlist projection discipline as the candidate read
// applies: only the declared keys below reach the browser.
// ---------------------------------------------------------------------------

const CONFIRMED_SOURCE_ALLOWED_TOP_KEYS = [
  "source_document_id",
  "record_generation",
  "revision",
  "status",
  "markdown_text",
  "content_sha256",
  "edit_source",
  "updated_at",
  "candidate",
  "quality",
  "adaptation_notice",
  "content_check",
] as const;

const CONFIRMED_SOURCE_UPDATE_ALLOWED_TOP_KEYS = [
  "revision",
  "content_sha256",
  "outcome",
  "candidate",
  "quality",
  "adaptation_notice",
  "content_check",
] as const;

const CONFIRMED_SOURCE_CANDIDATE_ALLOWED_KEYS = [
  "candidate_document_id",
  "status",
  "canonical_text_preview",
] as const;

const ADAPTATION_RECORD_ALLOWED_KEYS = [
  "code",
  "message",
  "classification",
] as const;

const CONFIRMED_SOURCE_EDIT_SOURCES = new Set([
  "initial",
  "extraction",
  "wysiwyg",
  "source_mode",
  "content_check",
]);

function sanitizeAdaptationRecords(value: unknown): ReaderAdaptationRecordDto[] {
  if (!Array.isArray(value)) return [];
  return (value as unknown[])
    .map((item) =>
      pickAllowed<ReaderAdaptationRecordDto>(item, ADAPTATION_RECORD_ALLOWED_KEYS),
    )
    .filter((item): item is ReaderAdaptationRecordDto => item !== null);
}

function sanitizeConfirmedSourceCandidate(
  value: unknown,
): ReaderConfirmedSourceCandidateSummaryDto | null {
  const candidate = pickAllowed<ReaderConfirmedSourceCandidateSummaryDto>(
    value,
    CONFIRMED_SOURCE_CANDIDATE_ALLOWED_KEYS,
  );
  if (!candidate || typeof candidate.candidate_document_id !== "string") {
    return null;
  }
  return candidate;
}

interface ConfirmedSourceConflictBody {
  code?: string;
  message?: string;
  resolution?: string;
  current_revision?: number;
}

function mapConfirmedSourceConflict(
  recordId: string,
  status: number,
  body: unknown,
  fallbackMessage: string,
): ReaderPlateBffError {
  const conflict = (body ?? {}) as ConfirmedSourceConflictBody;
  const message = conflict.message?.trim() || fallbackMessage;

  if (status === 409) {
    if (conflict.code === "stale_source_revision") {
      return {
        ok: false,
        status: 409,
        code: "stale_source_revision",
        message,
        recordId,
        currentRevision:
          typeof conflict.current_revision === "number" &&
          Number.isFinite(conflict.current_revision)
            ? conflict.current_revision
            : undefined,
      };
    }
    if (conflict.code === "stale_candidate_revision") {
      return {
        ok: false,
        status: 409,
        code: "stale_candidate_revision",
        message,
        recordId,
      };
    }
    if (
      conflict.code === "source_frozen" ||
      conflict.code === "record_state_advanced"
    ) {
      // Both carry `resolution: "open_reader"` (合同 “GET draft / resume 语义” / “`expected_revision` 乐观并发”): the
      // record has left the needs_confirmation lifecycle, so the reader
      // route is the only safe destination.
      return candidateConflictOpenReader(message, recordId);
    }
    return candidateConflictReturnToLibrary(message);
  }

  return upstreamError(status, message);
}

export async function getReaderConfirmedSourceFromWeb(
  recordId: string,
): Promise<ReaderConfirmedSourceReadResult> {
  if (!recordId) {
    return invalidInput("缺少 record_id。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const upstreamResult = await getUpstreamReaderConfirmedSource(
    recordId,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    if (upstreamResult.status === 404) {
      // “GET draft / resume 语义” 404 collapse: not found / not owner / deleted / no draft
      // source are indistinguishable. The client uses this signal to fall
      // back to the legacy candidate-document read for pre-L2 records.
      return {
        ok: false,
        status: 404,
        code: "confirmed_source_not_found",
        message: "没有找到可继续确认的草稿。",
        recordId,
      };
    }
    if (upstreamResult.status === 409) {
      return mapConfirmedSourceConflict(
        recordId,
        409,
        upstreamResult.body,
        "这条记录的状态已经变化。",
      );
    }
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  const raw = upstreamResult.data as unknown as Record<string, unknown>;
  const top = pickAllowed<ReaderConfirmedSourceReadResponseDto>(
    raw,
    CONFIRMED_SOURCE_ALLOWED_TOP_KEYS,
  );
  if (!top) {
    return upstreamError(502, "upstream returned no data");
  }

  return {
    ok: true,
    ...top,
    candidate: sanitizeConfirmedSourceCandidate(raw.candidate),
    adaptation_notice: sanitizeAdaptationRecords(raw.adaptation_notice),
    content_check: sanitizeAdaptationRecords(raw.content_check),
  };
}

export async function updateReaderConfirmedSourceFromWeb(
  recordId: string,
  input: {
    expectedRevision?: unknown;
    markdownText?: unknown;
    editSource?: unknown;
  },
): Promise<ReaderConfirmedSourceUpdateResult> {
  if (!recordId) {
    return invalidInput("缺少 record_id。");
  }

  const expectedRevision =
    typeof input.expectedRevision === "number" &&
    Number.isFinite(input.expectedRevision) &&
    Number.isInteger(input.expectedRevision) &&
    input.expectedRevision >= 1
      ? input.expectedRevision
      : null;
  if (expectedRevision === null) {
    return invalidInput("expected_revision 必须是大于等于 1 的整数。");
  }

  if (typeof input.markdownText !== "string" || !input.markdownText.trim()) {
    return invalidInput("markdown_text 不能为空。");
  }

  const editSource =
    typeof input.editSource === "string" &&
    CONFIRMED_SOURCE_EDIT_SOURCES.has(input.editSource)
      ? (input.editSource as ReaderConfirmedSourceUpdateRequestDto["edit_source"])
      : "content_check";

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const payload: ReaderConfirmedSourceUpdateRequestDto = {
    expected_revision: expectedRevision,
    markdown_text: input.markdownText,
    edit_source: editSource,
  };

  const upstreamResult = await putUpstreamReaderConfirmedSource(
    recordId,
    payload,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    if (upstreamResult.status === 409) {
      return mapConfirmedSourceConflict(
        recordId,
        409,
        upstreamResult.body,
        "草稿已被其他更新抢先保存。",
      );
    }
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  const raw = upstreamResult.data as unknown as Record<string, unknown>;
  const top = pickAllowed<ReaderConfirmedSourceUpdateResponseDto>(
    raw,
    CONFIRMED_SOURCE_UPDATE_ALLOWED_TOP_KEYS,
  );
  if (!top) {
    return upstreamError(502, "upstream returned no data");
  }

  return {
    ok: true,
    ...top,
    candidate: sanitizeConfirmedSourceCandidate(raw.candidate),
    adaptation_notice: sanitizeAdaptationRecords(raw.adaptation_notice),
    content_check: sanitizeAdaptationRecords(raw.content_check),
  };
}

// ---------------------------------------------------------------------------
// Article RAG Index lifecycle
//
// The BFF always runs the upstream response through the status mapper so
// unknown enums fail closed to a safe fallback and `reason_code` is stripped
// before the response reaches the UI. The frontend NEVER sees raw
// `reason_code` values.
// ---------------------------------------------------------------------------

export async function getReaderArticleRagIndexStatusFromWeb(
  recordId: string,
): Promise<ReaderArticleRagIndexStatusResult> {
  if (!recordId) {
    return invalidInput("缺少 record_id。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const upstreamResult = await getUpstreamReaderArticleRagIndexStatus(
    recordId,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...mapArticleRagIndexStatus(upstreamResult.data) };
}

export async function ensureReaderArticleRagIndexFromWeb(
  recordId: string,
  input: { expectedGeneration?: unknown; indexVersion?: unknown },
): Promise<ReaderArticleRagIndexEnsureResult> {
  if (!recordId) {
    return invalidInput("缺少 record_id。");
  }

  const expectedGeneration =
    typeof input.expectedGeneration === "number" &&
    Number.isFinite(input.expectedGeneration) &&
    input.expectedGeneration >= 1
      ? Math.floor(input.expectedGeneration)
      : null;

  if (expectedGeneration === null) {
    return invalidInput("expected_generation 必须是大于等于 1 的整数。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const payload: ReaderArticleRagIndexEnsureRequestDto = {
    expected_generation: expectedGeneration,
    index_version:
      typeof input.indexVersion === "string" && input.indexVersion.trim()
        ? input.indexVersion
        : null,
  };

  const upstreamResult = await ensureUpstreamReaderArticleRagIndex(
    recordId,
    payload,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return upstreamError(upstreamResult.status, upstreamResult.message);
  }

  return { ok: true, ...mapArticleRagIndexEnsure(upstreamResult.data) };
}

// ---------------------------------------------------------------------------
// T5.6c — POST /reader/records/{record_id}/section-translation
//
// Synchronous explicit-section translation command. The Web BFF is a thin
// passthrough: it enforces session, validates the request shape, forwards the
// full section range witness to the upstream FastAPI route, and maps upstream
// errors to the standard `ReaderPlateBffError` codes. The upstream response
// body is leak-safe (no prompt / provider payload / envelope / secret) and is
// returned as-is to the client. 409 from upstream is mapped to a dedicated
// `section_translation_conflict` code so the client can distinguish fence
// conflicts from generic candidate conflicts.
// ---------------------------------------------------------------------------

export type ReaderSectionTranslationResult =
  | ({ ok: true } & ReaderSectionTranslationResponseDto)
  | ReaderPlateBffError;

function sectionTranslationUpstreamError(
  status: number,
  message: string,
): ReaderPlateBffError {
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
  if (status === 409) {
    return {
      ok: false,
      status: 409,
      code: "section_translation_conflict",
      message: "段落内容已更新，请刷新后再试。",
    };
  }
  if (status === 422) {
    return {
      ok: false,
      status: 400,
      code: "invalid_input",
      message: "请求参数不正确，请刷新后重试。",
    };
  }
  // Unenumerated upstream status (e.g. 400 / 418 / other 4xx). We must
  // never leak the upstream ``message`` verbatim — it can contain
  // provider payload, exception text, or other internal signals. Map
  // to a stable ``upstream_error`` code with a friendly generic
  // Chinese message; preserve the upstream ``status`` so the client
  // can still distinguish 4xx from 5xx-class failures if needed.
  return {
    ok: false,
    status,
    code: "upstream_error",
    message: "解析服务异常，请稍后重试。",
  };
}

function asNonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export async function submitReaderSectionTranslationFromWeb(
  recordId: string,
  input: {
    startUnitId?: unknown;
    endUnitId?: unknown;
    startAnchorSegmentId?: unknown;
    endAnchorSegmentId?: unknown;
    nodeId?: unknown;
    outlineRevision?: unknown;
  },
): Promise<ReaderSectionTranslationResult> {
  if (!recordId) {
    return invalidInput("缺少 record_id。");
  }

  const startUnitId = asNonEmptyString(input.startUnitId);
  const endUnitId = asNonEmptyString(input.endUnitId);
  if (startUnitId === null || endUnitId === null) {
    return invalidInput("start_unit_id 与 end_unit_id 必须同时提供。");
  }

  const sessionResult = await requireSession();
  if (!sessionResult.ok) {
    return sessionResult;
  }

  const payload: ReaderSectionTranslationRequestDto = {
    start_unit_id: startUnitId,
    end_unit_id: endUnitId,
    start_anchor_segment_id: asNonEmptyString(input.startAnchorSegmentId),
    end_anchor_segment_id: asNonEmptyString(input.endAnchorSegmentId),
    node_id: asNonEmptyString(input.nodeId),
    outline_revision: asNonEmptyString(input.outlineRevision),
  };

  const upstreamResult = await submitUpstreamReaderSectionTranslation(
    recordId,
    payload,
    sessionResult.sessionToken,
  );

  if (!upstreamResult.ok) {
    return sectionTranslationUpstreamError(
      upstreamResult.status,
      upstreamResult.message,
    );
  }

  return { ok: true, ...upstreamResult.data };
}
