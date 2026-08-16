import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
}));

vi.mock("@/services/api/reader-plate", () => ({
  submitUpstreamReaderPlainText: vi.fn(),
  submitUpstreamReaderUnifiedInput: vi.fn(),
  getUpstreamReaderPlateSnapshot: vi.fn(),
  pollUpstreamReaderEvents: vi.fn(),
  putUpstreamReaderConfirmedSource: vi.fn(),
  initUpstreamReaderSourceArtifactUpload: vi.fn(),
  completeUpstreamReaderSourceArtifactUpload: vi.fn(),
  submitUpstreamReaderSourceArtifactInput: vi.fn(),
  getUpstreamReaderArtifactPipelineStatus: vi.fn(),
  confirmUpstreamReaderCandidateDocument: vi.fn(),
  getUpstreamReaderCandidateDocument: vi.fn(),
  getUpstreamReaderStableDocument: vi.fn(),
  getUpstreamReaderArticleRagIndexStatus: vi.fn(),
  ensureUpstreamReaderArticleRagIndex: vi.fn(),
  submitUpstreamReaderSectionTranslation: vi.fn(),
  submitUpstreamReaderAnalysisSectionRequest: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import {
  completeUpstreamReaderSourceArtifactUpload,
  confirmUpstreamReaderCandidateDocument,
  ensureUpstreamReaderArticleRagIndex,
  getUpstreamReaderArticleRagIndexStatus,
  getUpstreamReaderArtifactPipelineStatus,
  getUpstreamReaderCandidateDocument,
  getUpstreamReaderPlateSnapshot,
  getUpstreamReaderStableDocument,
  initUpstreamReaderSourceArtifactUpload,
  pollUpstreamReaderEvents,
  putUpstreamReaderConfirmedSource,
  submitUpstreamReaderPlainText,
  submitUpstreamReaderAnalysisSectionRequest,
  submitUpstreamReaderSectionTranslation,
  submitUpstreamReaderSourceArtifactInput,
  submitUpstreamReaderUnifiedInput,
} from "@/services/api/reader-plate";
import {
  completeReaderSourceArtifactUploadFromWeb,
  confirmReaderCandidateDocumentFromWeb,
  ensureReaderArticleRagIndexFromWeb,
  getReaderArticleRagIndexStatusFromWeb,
  getReaderArtifactPipelineStatusFromWeb,
  getReaderCandidateDocumentFromWeb,
  getReaderPlateSnapshotFromWeb,
  getReaderStableDocumentFromWeb,
  initReaderSourceArtifactUploadFromWeb,
  pollReaderEventsFromWeb,
  submitReaderPlainTextFromWeb,
  submitReaderAnalysisSectionRequestFromWeb,
  submitReaderSectionTranslationFromWeb,
  submitReaderSourceArtifactInputFromWeb,
  submitReaderUnifiedInputFromWeb,
  submitReadingRecordPlainTextFromWeb,
  updateReaderConfirmedSourceFromWeb,
} from "./reader-plate";
import { appReaderRoute } from "@/lib/routes";
import { makeAnalysisProgressDto } from "@/test/fixtures/reader-analysis-progress";
import {
  type ReaderArticleRagIndexEnsureResponseDto,
  type ReaderArticleRagIndexStatusResponseDto,
  type ReaderArtifactPipelineStatusResponseDto,
  type ReaderCandidateDocumentConfirmResponseDto,
  type ReaderCandidateDocumentReadResponse,
  type ReaderPlateSnapshotDto,
  type ReaderSectionTranslationResponseDto,
  type ReaderSourceArtifactSubmitInputResponseDto,
  type ReaderSourceArtifactUploadCompleteResponseDto,
  type ReaderSourceArtifactUploadInitResponseDto,
  type ReaderStableDocumentResponseDto,
  type ReaderUnifiedInputSubmitResponseDto,
} from "@/types/api/reader-plate";

const mockSession = {
  kind: "authenticated" as const,
  sessionToken: "session-token",
  source: "cookie" as const,
};

function makeSnapshot(): ReaderPlateSnapshotDto {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: "reader_snapshot_abc",
    snapshot_taken_at: "2026-06-21T00:00:00Z",
    last_event_sequence: 1,
    record_id: "rec_1",
    record: {
      title: "Reader Plate BFF Fixture",
      display_title_zh: null,
      title_generation_status: "pending",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      created_at: "2026-06-21T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: "base_1",
      content_sha256: "a".repeat(64),
      canonicalizer_version: "1.0.0",
      builder_version: "1.0.0",
      segmenter_version: "1.0.0",
      text_length_utf16: 12,
      hash_algorithm: "fnv1a32-utf16",
    },
    navigation: { units: [] },
    anchor_segments: [],
    enhancement_layers: [],
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: [],
    analysis_progress: makeAnalysisProgressDto(),
  };
}

describe("reader-plate BFF submit", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty text with empty_text before hitting session or upstream", async () => {
    const result = await submitReaderPlainTextFromWeb({ plainText: "   " });

    expect(result).toMatchObject({
      ok: false,
      status: 400,
      code: "empty_text",
    });
    expect(getWebSession).not.toHaveBeenCalled();
    expect(submitUpstreamReaderPlainText).not.toHaveBeenCalled();
  });

  it("rejects non-string plainText as empty_text", async () => {
    const result = await submitReaderPlainTextFromWeb({ plainText: undefined });

    expect(result).toMatchObject({ ok: false, status: 400, code: "empty_text" });
  });

  it("rejects anonymous sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(submitUpstreamReaderPlainText).not.toHaveBeenCalled();
  });

  it("rejects mock_phone sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "mock_phone",
      source: "mock",
      phone: "13800138000",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(submitUpstreamReaderPlainText).not.toHaveBeenCalled();
  });

  it("maps upstream 401 to upstream_auth_failed", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: false,
      status: 401,
      message: "token expired",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 401,
      code: "upstream_auth_failed",
    });
  });

  it("maps upstream 409 to upstream_error with original status", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: false,
      status: 409,
      message: "client_record_id already exists for this user",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "upstream_error",
    });
  });

  it("maps upstream 500 to upstream_unavailable (503)", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: false,
      status: 500,
      message: "internal error",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("maps upstream network failure (status 0) to upstream_unavailable", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: false,
      status: 0,
      message: "fetch failed",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("returns ok with snapshot on successful submit", async () => {
    const snapshot = makeSnapshot();
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: true,
      data: {
        record_id: "rec_1",
        base_id: "base_1",
        article_ready_sequence: 1,
        snapshot,
      },
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record_id).toBe("rec_1");
      expect(result.snapshot.schema_kind).toBe("reader_plate_snapshot");
      expect(result.article_ready_sequence).toBe(1);
    }
    expect(vi.mocked(submitUpstreamReaderPlainText).mock.calls[0][0]).toMatchObject({
      plain_text: "Hello.",
      client_record_id: expect.stringMatching(/^web-plate-/),
    });
  });

  it("forwards reading_goal / reading_variant to the upstream payload", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: true,
      data: {
        record_id: "rec_strategy",
        base_id: "base_1",
        article_ready_sequence: 1,
        snapshot: makeSnapshot(),
      },
    });

    const result = await submitReaderPlainTextFromWeb({
      plainText: "Hello.",
      readingGoal: "exam",
      readingVariant: "cet",
    });

    expect(result.ok).toBe(true);
    expect(vi.mocked(submitUpstreamReaderPlainText).mock.calls[0][0]).toMatchObject({
      plain_text: "Hello.",
      reading_goal: "exam",
      reading_variant: "cet",
    });
  });

  it("filters academic / academic_general to daily_reading / intermediate_reading", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: true,
      data: {
        record_id: "rec_academic_filter",
        base_id: "base_1",
        article_ready_sequence: 1,
        snapshot: makeSnapshot(),
      },
    });

    const result = await submitReaderPlainTextFromWeb({
      plainText: "Hello.",
      readingGoal: "academic",
      readingVariant: "academic_general",
    });

    expect(result.ok).toBe(true);
    const upstreamPayload = vi.mocked(submitUpstreamReaderPlainText).mock.calls[0][0];
    expect(upstreamPayload.reading_goal).toBe("daily_reading");
    expect(upstreamPayload.reading_variant).toBe("intermediate_reading");
  });

  it("returns a product submit contract with Reading Record id semantics", async () => {
    const snapshot = makeSnapshot();
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: true,
      data: {
        record_id: "reading_record_1",
        base_id: "base_1",
        article_ready_sequence: 1,
        snapshot,
      },
    });

    const result = await submitReadingRecordPlainTextFromWeb({
      plainText: "Hello.",
      title: "Test",
      language: "en",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result).toMatchObject({
        message: "阅读记录已创建，正在打开 Reader。",
        readingRecordId: "reading_record_1",
        readerUrl: appReaderRoute("reading_record_1"),
        baseId: "base_1",
        articleReadySequence: 1,
      });
      expect("recordId" in result).toBe(false);
      expect("record_id" in result).toBe(false);
    }
  });

  it("keeps the new product submit BFF free of legacy analysis routing", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/services/bff/reader-plate.ts"),
      "utf-8",
    );

    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("/app/reader/");
    expect(source).not.toContain("analysis-tasks");
  });
});

describe("reader-plate BFF snapshot", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects anonymous sessions", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
  });

  it("maps upstream 404 to record_not_found", async () => {
    vi.mocked(getUpstreamReaderPlateSnapshot).mockResolvedValue({
      ok: false,
      status: 404,
      message: "Reader record not found",
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "record_not_found",
    });
  });

  it("maps a pre-base snapshot conflict to record_not_ready", async () => {
    vi.mocked(getUpstreamReaderPlateSnapshot).mockResolvedValue({
      ok: false,
      status: 409,
      message: "reader snapshot requires an active base",
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "record_not_ready",
      message: "文档仍在解析，请稍后重试。",
    });
  });

  it("maps upstream 409 to upstream_error", async () => {
    vi.mocked(getUpstreamReaderPlateSnapshot).mockResolvedValue({
      ok: false,
      status: 409,
      message: "snapshot stale",
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "upstream_error",
    });
  });

  it("returns ok with snapshot on success", async () => {
    const snapshot = makeSnapshot();
    vi.mocked(getUpstreamReaderPlateSnapshot).mockResolvedValue({
      ok: true,
      data: snapshot,
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.snapshot_id).toBe("reader_snapshot_abc");
      expect(result.last_event_sequence).toBe(1);
    }
  });
});

describe("reader-plate BFF events polling", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects mock_phone sessions", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "mock_phone",
      source: "mock",
      phone: "13800138000",
    });

    const result = await pollReaderEventsFromWeb("rec_1", { afterSequence: 0 });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
  });

  it("maps upstream 500 to upstream_unavailable", async () => {
    vi.mocked(pollUpstreamReaderEvents).mockResolvedValue({
      ok: false,
      status: 502,
      message: "bad gateway",
    });

    const result = await pollReaderEventsFromWeb("rec_1", { afterSequence: 0 });

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("returns ok with poll response on success", async () => {
    vi.mocked(pollUpstreamReaderEvents).mockResolvedValue({
      ok: true,
      data: {
        reading_record_id: "rec_1",
        after_sequence: 0,
        next_after_sequence: 0,
        last_event_sequence: 0,
        has_more: false,
        truncated: false,
        reload_required: false,
        reload_reason: null,
        events: [],
      },
    });

    const result = await pollReaderEventsFromWeb("rec_1", {
      afterSequence: 0,
      limit: 50,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.events).toEqual([]);
      expect(result.reload_required).toBe(false);
    }
    expect(vi.mocked(pollUpstreamReaderEvents).mock.calls[0]).toEqual([
      "rec_1",
      "session-token",
      { afterSequence: 0, limit: 50 },
    ]);
  });
});

// ---------------------------------------------------------------------------
// Unified input submit (POST /reader/records/input)
// ---------------------------------------------------------------------------

describe("reader-plate BFF unified input submit", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty text with invalid_input before hitting session or upstream", async () => {
    const result = await submitReaderUnifiedInputFromWeb({ text: "   " });

    expect(result).toMatchObject({
      ok: false,
      status: 400,
      code: "invalid_input",
    });
    expect(getWebSession).not.toHaveBeenCalled();
    expect(submitUpstreamReaderUnifiedInput).not.toHaveBeenCalled();
  });

  it("rejects non-string text as invalid_input", async () => {
    const result = await submitReaderUnifiedInputFromWeb({ text: undefined });

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
  });

  it("rejects anonymous sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await submitReaderUnifiedInputFromWeb({ text: "Hello." });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(submitUpstreamReaderUnifiedInput).not.toHaveBeenCalled();
  });

  it("falls back unknown sourceType to pasted_text", async () => {
    vi.mocked(submitUpstreamReaderUnifiedInput).mockResolvedValue({
      ok: true,
      data: makeUnifiedInputStableResponse(),
    });

    const result = await submitReaderUnifiedInputFromWeb({
      text: "Hello.",
      sourceType: "totally_unknown_source_type",
    });

    expect(result.ok).toBe(true);
    expect(vi.mocked(submitUpstreamReaderUnifiedInput).mock.calls[0][0]).toMatchObject({
      source_type: "pasted_text",
      text: "Hello.",
    });
  });

  it("filters academic / academic_general to daily_reading / intermediate_reading", async () => {
    vi.mocked(submitUpstreamReaderUnifiedInput).mockResolvedValue({
      ok: true,
      data: makeUnifiedInputStableResponse(),
    });

    await submitReaderUnifiedInputFromWeb({
      text: "Hello.",
      readingGoal: "academic",
      readingVariant: "academic_general",
    });

    const payload = vi.mocked(submitUpstreamReaderUnifiedInput).mock.calls[0][0];
    expect(payload.reading_goal).toBe("daily_reading");
    expect(payload.reading_variant).toBe("intermediate_reading");
  });

  it("maps upstream 500 to upstream_unavailable", async () => {
    vi.mocked(submitUpstreamReaderUnifiedInput).mockResolvedValue({
      ok: false,
      status: 500,
      message: "internal error",
    });

    const result = await submitReaderUnifiedInputFromWeb({ text: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("returns ok with stable_document_ready outcome on success", async () => {
    const data = makeUnifiedInputStableResponse();
    vi.mocked(submitUpstreamReaderUnifiedInput).mockResolvedValue({
      ok: true,
      data,
    });

    const result = await submitReaderUnifiedInputFromWeb({ text: "Hello." });

    expect(result.ok).toBe(true);
    if (result.ok && result.outcome === "stable_document_ready") {
      expect(result.reading_record_id).toBe("rec_unified_1");
      expect(result.stable_document_id).toBe("sd_1");
    }
  });

  it("passes through candidate_document_required outcome without modification", async () => {
    vi.mocked(submitUpstreamReaderUnifiedInput).mockResolvedValue({
      ok: true,
      data: makeUnifiedInputCandidateResponse(),
    });

    const result = await submitReaderUnifiedInputFromWeb({ text: "Hello." });

    expect(result.ok).toBe(true);
    if (result.ok && result.outcome === "candidate_document_required") {
      expect(result.candidate_document_id).toBe("cand_1");
      expect(result.original_input_id).toBe("inp_cand_1");
    }
  });
});

// ---------------------------------------------------------------------------
// Source artifacts — init-upload / complete-upload / submit-input / pipeline-status
// ---------------------------------------------------------------------------

describe("reader-plate BFF source artifact init-upload", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects non-original_upload artifactKind with invalid_input", async () => {
    const result = await initReaderSourceArtifactUploadFromWeb({
      artifactKind: "pdf_page_image",
    });

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
    expect(getWebSession).not.toHaveBeenCalled();
  });

  it("rejects anonymous sessions", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await initReaderSourceArtifactUploadFromWeb({
      artifactKind: "original_upload",
    });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
  });

  it("returns ok with init response on success", async () => {
    vi.mocked(initUpstreamReaderSourceArtifactUpload).mockResolvedValue({
      ok: true,
      data: makeInitUploadResponse(),
    });

    const result = await initReaderSourceArtifactUploadFromWeb({
      artifactKind: "original_upload",
      sourceFilename: "doc.pdf",
      contentType: "application/pdf",
      byteSize: 1024,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.artifact_id).toBe("art_1");
      expect(result.upload_method).toBe("oss_put_object_presigned");
    }
    expect(vi.mocked(initUpstreamReaderSourceArtifactUpload).mock.calls[0][0]).toMatchObject({
      artifact_kind: "original_upload",
      source_filename: "doc.pdf",
      content_type: "application/pdf",
      byte_size: 1024,
    });
  });

  it("does not map init-upload upstream 404 to artifact_not_found", async () => {
    vi.mocked(initUpstreamReaderSourceArtifactUpload).mockResolvedValue({
      ok: false,
      status: 404,
      message: "reading record not found",
    });

    const result = await initReaderSourceArtifactUploadFromWeb({
      artifactKind: "original_upload",
    });

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "record_not_found",
    });
    if (!result.ok) {
      expect(result.message).toContain("阅读记录");
      expect(result.message).not.toContain("上传文件");
    }
  });
});

describe("reader-plate BFF source artifact complete-upload", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty artifactId with invalid_input", async () => {
    const result = await completeReaderSourceArtifactUploadFromWeb("", {
      contentSha256: "abc",
    });

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
    expect(getWebSession).not.toHaveBeenCalled();
  });

  it("returns ok with complete response on success", async () => {
    vi.mocked(completeUpstreamReaderSourceArtifactUpload).mockResolvedValue({
      ok: true,
      data: makeCompleteUploadResponse(),
    });

    const result = await completeReaderSourceArtifactUploadFromWeb("art_1", {
      contentSha256: "abc",
      byteSize: 1024,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.artifact_id).toBe("art_1");
      expect(result.upload_completed).toBe(true);
    }
    expect(vi.mocked(completeUpstreamReaderSourceArtifactUpload).mock.calls[0]).toEqual([
      "art_1",
      expect.objectContaining({
        content_sha256: "abc",
        byte_size: 1024,
      }),
      "session-token",
    ]);
  });

  it("maps upstream 404 to artifact_not_found (not record_not_found)", async () => {
    vi.mocked(completeUpstreamReaderSourceArtifactUpload).mockResolvedValue({
      ok: false,
      status: 404,
      message: "artifact not found",
    });

    const result = await completeReaderSourceArtifactUploadFromWeb("art_1", {});

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "artifact_not_found",
    });
    if (!result.ok) {
      expect(result.message).not.toContain("阅读记录");
      expect(result.message).toContain("上传文件");
    }
  });
});

describe("reader-plate BFF source artifact submit-input", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty artifactId with invalid_input", async () => {
    const result = await submitReaderSourceArtifactInputFromWeb("", {
      title: "Test",
    });

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
  });

  it("filters academic strategy before forwarding to upstream", async () => {
    vi.mocked(submitUpstreamReaderSourceArtifactInput).mockResolvedValue({
      ok: true,
      data: makeSubmitInputResponse(),
    });

    await submitReaderSourceArtifactInputFromWeb("art_1", {
      readingGoal: "academic",
      readingVariant: "academic_general",
    });

    const payload = vi.mocked(submitUpstreamReaderSourceArtifactInput).mock.calls[0][1];
    expect(payload.reading_goal).toBe("daily_reading");
    expect(payload.reading_variant).toBe("intermediate_reading");
  });

  it("returns ok with submit-input response on success", async () => {
    vi.mocked(submitUpstreamReaderSourceArtifactInput).mockResolvedValue({
      ok: true,
      data: makeSubmitInputResponse(),
    });

    const result = await submitReaderSourceArtifactInputFromWeb("art_1", {
      title: "Test",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.reading_record_id).toBe("rec_art_1");
      expect(result.artifact_id).toBe("art_1");
    }
  });

  it("maps upstream 404 to artifact_not_found (not record_not_found)", async () => {
    vi.mocked(submitUpstreamReaderSourceArtifactInput).mockResolvedValue({
      ok: false,
      status: 404,
      message: "artifact not found",
    });

    const result = await submitReaderSourceArtifactInputFromWeb("art_1", {
      title: "Test",
    });

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "artifact_not_found",
    });
    if (!result.ok) {
      expect(result.message).not.toContain("阅读记录");
    }
  });
});

describe("reader-plate BFF source artifact pipeline-status", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty artifactId with invalid_input", async () => {
    const result = await getReaderArtifactPipelineStatusFromWeb("");

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
  });

  it("strips debug fields from job summaries before returning to UI", async () => {
    vi.mocked(getUpstreamReaderArtifactPipelineStatus).mockResolvedValue({
      ok: true,
      data: makePipelineStatusRaw(),
    });

    const result = await getReaderArtifactPipelineStatusFromWeb("art_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.extraction_job).not.toHaveProperty("failure_class");
      expect(result.extraction_job).not.toHaveProperty("failure_code");
      expect(result.extraction_job).not.toHaveProperty("rationale_code");
      expect(result.outcome).toBe("extraction_running");
    }
  });

  it("coerces unknown outcome to extraction_failed via status mapper", async () => {
    const raw = makePipelineStatusRaw();
    (raw as { outcome: unknown }).outcome = "unknown_outcome";
    (raw as { next_action: unknown }).next_action = "unknown_action";
    vi.mocked(getUpstreamReaderArtifactPipelineStatus).mockResolvedValue({
      ok: true,
      data: raw,
    });

    const result = await getReaderArtifactPipelineStatusFromWeb("art_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.outcome).toBe("extraction_failed");
      expect(result.next_action).toBe("show_error");
    }
  });

  it("maps upstream 401 to upstream_auth_failed", async () => {
    vi.mocked(getUpstreamReaderArtifactPipelineStatus).mockResolvedValue({
      ok: false,
      status: 401,
      message: "token expired",
    });

    const result = await getReaderArtifactPipelineStatusFromWeb("art_1");

    expect(result).toMatchObject({
      ok: false,
      status: 401,
      code: "upstream_auth_failed",
    });
  });

  it("maps upstream 404 to artifact_not_found (not record_not_found)", async () => {
    vi.mocked(getUpstreamReaderArtifactPipelineStatus).mockResolvedValue({
      ok: false,
      status: 404,
      message: "artifact not found",
    });

    const result = await getReaderArtifactPipelineStatusFromWeb("art_1");

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "artifact_not_found",
    });
    if (!result.ok) {
      expect(result.message).not.toContain("阅读记录");
    }
  });
});

// ---------------------------------------------------------------------------
// Candidate document confirmation
// ---------------------------------------------------------------------------

describe("reader-plate BFF candidate document confirm", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects missing recordId with invalid_input", async () => {
    const result = await confirmReaderCandidateDocumentFromWeb("", "cand_1", {});

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
  });

  it("rejects missing candidateDocumentId with invalid_input", async () => {
    const result = await confirmReaderCandidateDocumentFromWeb("rec_1", "", {});

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
  });

  it("maps upstream 409 to candidate_conflict", async () => {
    vi.mocked(confirmUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: false,
      status: 409,
      message: "candidate already confirmed",
    });

    const result = await confirmReaderCandidateDocumentFromWeb("rec_1", "cand_1", {});

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "candidate_conflict",
    });
    if (!result.ok) {
      expect(result.message).toContain("候选文档");
    }
  });

  it("returns ok with confirm response on success", async () => {
    vi.mocked(confirmUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: true,
      data: makeCandidateConfirmResponse(),
    });

    const result = await confirmReaderCandidateDocumentFromWeb("rec_1", "cand_1", {
      language: "en",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.reading_record_id).toBe("rec_1");
      expect(result.candidate_document_id).toBe("cand_1");
      expect(result.stable_document_id).toBe("sd_1");
      expect(result.candidate_confirmed).toBe(true);
    }
    expect(vi.mocked(confirmUpstreamReaderCandidateDocument).mock.calls[0]).toEqual([
      "rec_1",
      "cand_1",
      { language: "en" },
      "session-token",
    ]);
  });
});

// ---------------------------------------------------------------------------
// Stable Document projection
// ---------------------------------------------------------------------------

describe("reader-plate BFF stable document", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty recordId with invalid_input", async () => {
    const result = await getReaderStableDocumentFromWeb("");

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
  });

  it("returns ok with stable document response on success", async () => {
    vi.mocked(getUpstreamReaderStableDocument).mockResolvedValue({
      ok: true,
      data: makeStableDocumentResponse(),
    });

    const result = await getReaderStableDocumentFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.reading_record_id).toBe("rec_1");
      expect(result.active_base_id).toBe("base_1");
      expect(result.base.base_id).toBe("base_1");
      expect(result.blocks).toHaveLength(1);
    }
  });

  it("maps upstream 404 to record_not_found", async () => {
    vi.mocked(getUpstreamReaderStableDocument).mockResolvedValue({
      ok: false,
      status: 404,
      message: "record not found",
    });

    const result = await getReaderStableDocumentFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "record_not_found",
    });
  });
});

// ---------------------------------------------------------------------------
// Article RAG Index — status / ensure
// ---------------------------------------------------------------------------

describe("reader-plate BFF article rag index status", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty recordId with invalid_input", async () => {
    const result = await getReaderArticleRagIndexStatusFromWeb("");

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
  });

  it("strips reason_code via status mapper before returning to UI", async () => {
    vi.mocked(getUpstreamReaderArticleRagIndexStatus).mockResolvedValue({
      ok: true,
      data: makeRagStatusRaw(),
    });

    const result = await getReaderArticleRagIndexStatusFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result).not.toHaveProperty("reason_code");
      expect(result.status).toBe("indexed");
      expect(result.chunk_count).toBe(42);
    }
  });

  it("coerces unknown status to unavailable via status mapper", async () => {
    const raw = makeRagStatusRaw();
    (raw as { status: unknown }).status = "totally_unknown_status";
    vi.mocked(getUpstreamReaderArticleRagIndexStatus).mockResolvedValue({
      ok: true,
      data: raw,
    });

    const result = await getReaderArticleRagIndexStatusFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.status).toBe("unavailable");
    }
  });
});

describe("reader-plate BFF article rag index ensure", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty recordId with invalid_input", async () => {
    const result = await ensureReaderArticleRagIndexFromWeb("", {
      expectedGeneration: 1,
    });

    expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
  });

  it.each([0, -1, NaN, "1", undefined, null])(
    "rejects invalid expectedGeneration %p with invalid_input",
    async (expectedGeneration) => {
      const result = await ensureReaderArticleRagIndexFromWeb("rec_1", {
        expectedGeneration,
      });

      expect(result).toMatchObject({ ok: false, status: 400, code: "invalid_input" });
      expect(ensureUpstreamReaderArticleRagIndex).not.toHaveBeenCalled();
    },
  );

  it("accepts valid expectedGeneration and forwards to upstream", async () => {
    vi.mocked(ensureUpstreamReaderArticleRagIndex).mockResolvedValue({
      ok: true,
      data: makeRagEnsureRaw(),
    });

    const result = await ensureReaderArticleRagIndexFromWeb("rec_1", {
      expectedGeneration: 2,
      indexVersion: "v2",
    });

    expect(result.ok).toBe(true);
    expect(vi.mocked(ensureUpstreamReaderArticleRagIndex).mock.calls[0]).toEqual([
      "rec_1",
      { expected_generation: 2, index_version: "v2" },
      "session-token",
    ]);
  });

  it("strips reason_code via status mapper before returning to UI", async () => {
    vi.mocked(ensureUpstreamReaderArticleRagIndex).mockResolvedValue({
      ok: true,
      data: makeRagEnsureRaw(),
    });

    const result = await ensureReaderArticleRagIndexFromWeb("rec_1", {
      expectedGeneration: 1,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result).not.toHaveProperty("reason_code");
      expect(result.status).toBe("enqueued");
    }
  });

  it("coerces unknown ensure status to error via status mapper", async () => {
    const raw = makeRagEnsureRaw();
    (raw as { status: unknown }).status = "totally_unknown_ensure_status";
    vi.mocked(ensureUpstreamReaderArticleRagIndex).mockResolvedValue({
      ok: true,
      data: raw,
    });

    const result = await ensureReaderArticleRagIndexFromWeb("rec_1", {
      expectedGeneration: 1,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.status).toBe("error");
    }
  });
});

// ---------------------------------------------------------------------------
// Candidate document read (input-page recovery)
// ---------------------------------------------------------------------------

describe("reader-plate BFF candidate document read", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects missing recordId with invalid_input before hitting session or upstream", async () => {
    const result = await getReaderCandidateDocumentFromWeb("");

    expect(result).toMatchObject({
      ok: false,
      status: 400,
      code: "invalid_input",
    });
    expect(getWebSession).not.toHaveBeenCalled();
    expect(getUpstreamReaderCandidateDocument).not.toHaveBeenCalled();
  });

  it("rejects anonymous sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(getUpstreamReaderCandidateDocument).not.toHaveBeenCalled();
  });

  it("rejects mock_phone sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "mock_phone",
      source: "mock",
      phone: "13800138000",
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
  });

  it("200 full_text returns typed DTO with no internal leaks", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: true,
      data: makeCandidateDocumentReadResponse({
        preview_mode: "full_text",
        preview_text: "Hello world.",
        is_truncated: false,
      }),
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.preview.preview_mode).toBe("full_text");
      expect(result.preview.preview_text).toBe("Hello world.");
      expect(result.preview.is_truncated).toBe(false);
    }
    expect(vi.mocked(getUpstreamReaderCandidateDocument).mock.calls[0]).toEqual([
      "rec_1",
      "session-token",
    ]);
  });

  it("200 truncated_preview returns typed DTO with is_truncated=true", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: true,
      data: makeCandidateDocumentReadResponse({
        preview_mode: "truncated_preview",
        preview_text: "Hello ...",
        is_truncated: true,
        total_char_count: 1200,
      }),
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.preview.preview_mode).toBe("truncated_preview");
      expect(result.preview.preview_text).toBe("Hello ...");
      expect(result.preview.is_truncated).toBe(true);
      expect(result.preview.total_char_count).toBe(1200);
    }
  });

  it("200 outline_only returns typed DTO with preview_text='' and is_truncated=true", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: true,
      data: makeCandidateDocumentReadResponse({
        preview_mode: "outline_only",
        preview_text: "",
        is_truncated: true,
      }),
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.preview.preview_mode).toBe("outline_only");
      expect(result.preview.preview_text).toBe("");
      expect(result.preview.is_truncated).toBe(true);
    }
  });

  it("200 response never leaks any BFF-forbidden field names", async () => {
    // The BFF uses a runtime sanitizer to project the 11 declared top-level
    // fields and the preview subtree from the upstream payload. This test
    // feeds a fixture that includes runtime extras (top-level forbidden keys
    // and `secret_extra` inside outline/risk items) and asserts the BFF
    // strips them so they never reach the browser.
    const upstreamFixture = {
      record_id: "rec_1",
      candidate_document_id: "cand_1",
      record_generation: 1,
      status: "ready" as const,
      title: "Test",
      preview: {
        preview_mode: "full_text" as const,
        preview_text: "hello",
        is_truncated: false,
        total_char_count: 5,
        document_outline: [
          {
            order_index: 0,
            block_type_label: "heading" as const,
            heading_text: "H",
            char_count: 1,
            secret_extra: "leak",
          },
        ],
        risk_items: [
          {
            risk_kind: "low_confidence_ocr" as const,
            user_message: "msg",
            severity: "warning" as const,
            secret_extra: "leak",
          },
        ],
      },
      source_type: "plain_text" as const,
      filename: null,
      source_label: "粘贴文本",
      created_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:00Z",
      // EXTRAS that must NOT reach the browser:
      source_text: "SECRET_SOURCE_TEXT",
      blocks_json: [{ secret: "leak" }],
      quality_json: { secret: "leak" },
      source_refs_json: { secret: "leak" },
      canonical_text_preview: "secret",
      original_input_id: "secret",
    };

    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: true,
      // Cast through `unknown` so TS accepts the runtime extras. The
      // purpose of the test is to prove the BFF strips them at runtime.
      data: upstreamFixture as unknown as ReaderCandidateDocumentReadResponse,
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok result");

    // Top-level keys: the typed DTO plus the BFF envelope `ok` flag.
    const expectedDtoKeys = [
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
    ].sort();
    const actualTopLevelKeys = Object.keys(result).sort();
    expect(actualTopLevelKeys).toEqual([...expectedDtoKeys, "ok"].sort());

    // No top-level extras must reach the browser.
    for (const forbidden of [
      "source_text",
      "blocks_json",
      "quality_json",
      "source_refs_json",
      "canonical_text_preview",
      "original_input_id",
    ]) {
      expect(forbidden in result).toBe(false);
    }

    // Outline / risk items must not carry `secret_extra`.
    const outline = result.preview.document_outline as unknown as Array<Record<string, unknown>>;
    expect(outline[0]).not.toHaveProperty("secret_extra");
    const risks = result.preview.risk_items as unknown as Array<Record<string, unknown>>;
    expect(risks[0]).not.toHaveProperty("secret_extra");

    // The serialised JSON must NOT contain any of the runtime extras.
    const serialized = JSON.stringify(result);
    for (const needle of [
      "SECRET_SOURCE_TEXT",
      "secret_extra",
      "leak",
      "source_text",
      "blocks_json",
      "quality_json",
      "source_refs_json",
      "canonical_text_preview",
      "original_input_id",
    ]) {
      expect(serialized).not.toContain(needle);
    }
  });

  it("404 upstream → candidate_not_found with upstream message passthrough", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: false,
      status: 404,
      message: "upstream 404 message",
      body: {
        ok: false,
        code: "not_found",
        message: "无对应的候选文档。",
      },
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "candidate_not_found",
      message: "无对应的候选文档。",
    });
  });

  it("404 upstream with empty body → candidate_not_found with fallback message", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: false,
      status: 404,
      message: "",
      body: {
        ok: false,
        code: "not_found",
        message: "   ",
      },
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "candidate_not_found",
      message: "未找到可继续确认的内容。",
    });
  });

  it("409 record_state_advanced + open_reader → candidate_conflict_open_reader with recordId", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: false,
      status: 409,
      message: "record already advanced",
      body: {
        ok: false,
        code: "record_state_advanced",
        resolution: "open_reader",
        message: "请直接打开阅读器。",
      },
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "candidate_conflict_open_reader",
      message: "请直接打开阅读器。",
      recordId: "rec_1",
    });
  });

  it("409 record_state_advanced + return_to_library → candidate_conflict_return_to_library with fixed message", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: false,
      status: 409,
      message: "record already advanced",
      body: {
        ok: false,
        code: "record_state_advanced",
        resolution: "return_to_library",
        message: "any upstream message",
      },
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "candidate_conflict_return_to_library",
      message: "这篇内容当前无法继续确认。",
    });
  });

  it("409 multiple_ready_candidates → candidate_conflict_return_to_library", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: false,
      status: 409,
      message: "multiple ready candidates",
      body: {
        ok: false,
        code: "multiple_ready_candidates",
        resolution: "return_to_library",
        message: "any upstream message",
      },
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "candidate_conflict_return_to_library",
      message: "这篇内容当前无法继续确认。",
    });
  });

  it("5xx upstream → upstream_unavailable", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValue({
      ok: false,
      status: 502,
      message: "bad gateway",
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("network error → upstream_unavailable", async () => {
    vi.mocked(getUpstreamReaderCandidateDocument).mockResolvedValueOnce({
      ok: false,
      status: 0,
      message: "fetch failed",
    });

    const result = await getReaderCandidateDocumentFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeUnifiedInputStableResponse(): ReaderUnifiedInputSubmitResponseDto {
  return {
    outcome: "stable_document_ready",
    reading_record_id: "rec_unified_1",
    stable_document_id: "sd_1",
    base_id: "base_1",
    record_generation: 1,
    document_version: 1,
    title: null,
    content_sha256: "abc",
    canonical_text_sha256: "def",
    block_count: 1,
    article_ready_event_id: "evt_1",
    article_ready_sequence: 1,
    suitability: {
      outcome: "stable_document_ready",
      source_type: "pasted_text",
      word_count: 10,
      english_word_ratio: 1,
      natural_language_score: 0.95,
      flags: [],
      reasons: [],
      normalized_preview: "Hello.",
    },
    snapshot: makeSnapshot(),
  };
}

function makeUnifiedInputCandidateResponse(): ReaderUnifiedInputSubmitResponseDto {
  return {
    outcome: "candidate_document_required",
    reading_record_id: "rec_unified_2",
    candidate_document_id: "cand_1",
    original_input_id: "inp_cand_1",
    record_generation: 1,
    status: "ready",
    title: null,
    block_count: 1,
    source_type: "pasted_text",
    filename: null,
    suitability: {
      outcome: "candidate_document_required",
      source_type: "pasted_text",
      word_count: 10,
      english_word_ratio: 1,
      natural_language_score: 0.95,
      flags: [],
      reasons: [],
      normalized_preview: "Hello.",
    },
  };
}

function makeInitUploadResponse(): ReaderSourceArtifactUploadInitResponseDto {
  return {
    artifact_id: "art_1",
    artifact_kind: "original_upload",
    storage_provider: "oss",
    bucket: "claread",
    endpoint: "https://oss.example.com",
    object_key: "artifacts/art_1.bin",
    status: "pending",
    content_type: "application/pdf",
    byte_size: 1024,
    content_sha256: "abc",
    source_filename: "doc.pdf",
    upload_method: "oss_put_object_presigned",
    headers: {},
    presigned_url: "https://oss.example.com/artifacts/art_1.bin?signature=...",
    presigned_method: "PUT",
    presigned_expires_at: "2026-07-01T01:00:00Z",
  };
}

function makeCompleteUploadResponse(): ReaderSourceArtifactUploadCompleteResponseDto {
  return {
    artifact_id: "art_1",
    artifact_kind: "original_upload",
    storage_provider: "oss",
    bucket: "claread",
    endpoint: "https://oss.example.com",
    object_key: "artifacts/art_1.bin",
    status: "available",
    content_type: "application/pdf",
    byte_size: 1024,
    content_sha256: "abc",
    source_filename: "doc.pdf",
    upload_completed: true,
    idempotent_noop: false,
  };
}

function makeSubmitInputResponse(): ReaderSourceArtifactSubmitInputResponseDto {
  return {
    reading_record_id: "rec_art_1",
    original_input_id: "inp_1",
    artifact_id: "art_1",
    record_generation: 1,
    source_type: "file",
    input_type: "file_ref",
    product_state: "processing",
    readiness_state: "submitted",
    title: "Test",
    language: null,
    extraction_required: true,
    bucket: "claread",
    endpoint: "https://oss.example.com",
    object_key: "artifacts/art_1.bin",
    content_type: "application/pdf",
    byte_size: 1024,
    content_sha256: "abc",
    source_filename: "doc.pdf",
    extraction_job_id: "job_1",
    extraction_job_status: "queued",
  };
}

function makePipelineStatusRaw(): ReaderArtifactPipelineStatusResponseDto {
  return {
    artifact: {
      artifact_id: "art_1",
      status: "extraction_running",
      artifact_kind: "original_upload",
      storage_provider: "oss",
      bucket: "claread",
      endpoint: "https://oss.example.com",
      object_key: "artifacts/art_1.bin",
      content_type: "application/pdf",
      byte_size: 1024,
      content_sha256: "abc",
      source_filename: "doc.pdf",
      reading_record_id: "rec_1",
      original_input_id: "inp_1",
    },
    record: {
      reading_record_id: "rec_1",
      generation: 1,
      product_state: "processing",
      readiness_state: "submitted",
      active_base_id: null,
      source_type: "pdf_text",
      title: null,
      language: null,
    },
    original_input: {
      original_input_id: "inp_1",
      input_type: "original_upload",
      content_sha256: "abc",
      has_source_text: false,
      extraction_status: "running",
      metadata: {},
    },
    extraction_job: {
      job_id: "job_1",
      status: "running",
      attempt_count: 1,
      max_attempts: 3,
      failure_class: "TransienceError",
      failure_code: "upstream_timeout",
      rationale_code: "retry_later_policy",
      available_at: "2026-07-01T00:00:00Z",
      updated_at: "2026-07-01T00:01:00Z",
    },
    materialization_job: null,
    candidate_document: null,
    stable_document: null,
    outcome: "extraction_running",
    next_action: "wait_for_worker",
  };
}

function makeCandidateConfirmResponse(): ReaderCandidateDocumentConfirmResponseDto {
  return {
    reading_record_id: "rec_1",
    candidate_document_id: "cand_1",
    stable_document_id: "sd_1",
    base_id: "base_1",
    record_generation: 1,
    document_version: 1,
    content_sha256: "abc",
    canonical_text_sha256: "def",
    block_count: 1,
    candidate_confirmed: true,
    freeze_idempotent_noop: false,
    article_ready_event_id: "evt_1",
    article_ready_sequence: 1,
    snapshot: makeSnapshot(),
  };
}

function makeStableDocumentResponse(): ReaderStableDocumentResponseDto {
  return {
    reading_record_id: "rec_1",
    record_generation: 1,
    active_base_id: "base_1",
    base: {
      base_id: "base_1",
      content_sha256: "abc",
      content_utf16_length: 12,
      canonicalizer_version: "1.0.0",
      builder_version: "1.0.0",
      segmenter_version: "1.0.0",
      language: "en",
      title_snapshot: "Hello.",
      navigation: {},
      text: "Hello world.",
    },
    stable_document: {
      stable_document_id: "sd_1",
      document_version: 1,
      title: "Hello.",
      language: "en",
      source_profile: {},
      content_sha256: "abc",
      status: "ready",
    },
    blocks: [
      {
        block_id: "block_1",
        parent_block_id: null,
        order_index: 0,
        block_type: "paragraph",
        text_content: "Hello world.",
        payload: {},
        source_refs: {},
        quality: {},
        canonical_text_start_utf16: 0,
        canonical_text_end_utf16: 12,
        interpretation_policy: {},
      },
    ],
    anchor_segments: [],
  };
}

function makeRagStatusRaw(): ReaderArticleRagIndexStatusResponseDto {
  return {
    reading_record_id: "rec_1",
    status: "indexed",
    stable_document_id: "sd_1",
    base_id: "base_1",
    record_generation: 1,
    index_run_id: "run_1",
    index_version: "v1",
    plan_content_sha256: "abc",
    chunk_count: 42,
    reason_code: "debug_internal_reason",
  };
}

function makeRagEnsureRaw(): ReaderArticleRagIndexEnsureResponseDto {
  return {
    reading_record_id: "rec_1",
    status: "enqueued",
    reason_code: "internal_debug",
    idempotent_noop: false,
    stable_document_id: "sd_1",
    base_id: "base_1",
    record_generation: 1,
    index_run_id: "run_1",
    job_id: "job_1",
    index_version: "v1",
    chunker_version: "chunker_v1",
  };
}

function makeCandidateDocumentReadResponse(
  overrides: Partial<ReaderCandidateDocumentReadResponse> & {
    preview_mode?: ReaderCandidateDocumentReadResponse["preview"]["preview_mode"];
    preview_text?: string;
    is_truncated?: boolean;
    total_char_count?: number;
  } = {},
): ReaderCandidateDocumentReadResponse {
  const {
    preview_mode,
    preview_text,
    is_truncated,
    total_char_count,
    ...rest
  } = overrides;
  return {
    record_id: "rec_1",
    candidate_document_id: "cand_1",
    record_generation: 1,
    status: "ready",
    title: null,
    preview: {
      preview_mode: preview_mode ?? "full_text",
      preview_text: preview_text ?? "Hello world.",
      is_truncated: is_truncated ?? false,
      total_char_count: total_char_count ?? 12,
      document_outline: [],
      risk_items: [],
    },
    source_type: "plain_text",
    filename: null,
    source_label: "粘贴文本",
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...rest,
  };
}

describe("reader-plate BFF confirmed source update", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects a fractional expected_revision instead of silently flooring it", async () => {
    const result = await updateReaderConfirmedSourceFromWeb("rec_1", {
      expectedRevision: 1.9,
      markdownText: "# Draft",
      editSource: "content_check",
    });

    expect(result).toMatchObject({
      ok: false,
      status: 400,
      code: "invalid_input",
    });
    expect(getWebSession).not.toHaveBeenCalled();
    expect(putUpstreamReaderConfirmedSource).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Section translation (synchronous explicit-section command)
// ---------------------------------------------------------------------------

function makeSectionTranslationResponse(
  overrides: Partial<ReaderSectionTranslationResponseDto> = {},
): ReaderSectionTranslationResponseDto {
  return {
    outcome: "succeeded",
    job_id: "job_section_1",
    detail: null,
    ...overrides,
  };
}

describe("reader-plate BFF section translation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty recordId with invalid_input before hitting session or upstream", async () => {
    const result = await submitReaderSectionTranslationFromWeb("", {
      startUnitId: "u1",
      endUnitId: "u2",
    });

    expect(result).toMatchObject({
      ok: false,
      status: 400,
      code: "invalid_input",
    });
    expect(getWebSession).not.toHaveBeenCalled();
    expect(submitUpstreamReaderSectionTranslation).not.toHaveBeenCalled();
  });

  it.each([
    ["missing startUnitId", { endUnitId: "u2" }],
    ["missing endUnitId", { startUnitId: "u1" }],
    ["blank startUnitId", { startUnitId: "   ", endUnitId: "u2" }],
    ["blank endUnitId", { startUnitId: "u1", endUnitId: "" }],
    ["non-string startUnitId", { startUnitId: 42, endUnitId: "u2" }],
  ])("rejects %p with invalid_input before session or upstream", async (_label, input) => {
    const result = await submitReaderSectionTranslationFromWeb("rec_1", input);

    expect(result).toMatchObject({
      ok: false,
      status: 400,
      code: "invalid_input",
    });
    expect(getWebSession).not.toHaveBeenCalled();
    expect(submitUpstreamReaderSectionTranslation).not.toHaveBeenCalled();
  });

  it("rejects anonymous sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await submitReaderSectionTranslationFromWeb("rec_1", {
      startUnitId: "u1",
      endUnitId: "u2",
    });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(submitUpstreamReaderSectionTranslation).not.toHaveBeenCalled();
  });

  it("rejects mock_phone sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "mock_phone",
      source: "mock",
      phone: "13800138000",
    });

    const result = await submitReaderSectionTranslationFromWeb("rec_1", {
      startUnitId: "u1",
      endUnitId: "u2",
    });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
  });

  it("forwards full range witness (anchors + audit-only fields) to upstream", async () => {
    vi.mocked(submitUpstreamReaderSectionTranslation).mockResolvedValue({
      ok: true,
      data: makeSectionTranslationResponse(),
    });

    const result = await submitReaderSectionTranslationFromWeb("rec_1", {
      startUnitId: "u3",
      endUnitId: "u4",
      startAnchorSegmentId: "seg_a",
      endAnchorSegmentId: "seg_b",
      nodeId: "node_1",
      outlineRevision: "rev_9",
    });

    expect(result.ok).toBe(true);
    expect(vi.mocked(submitUpstreamReaderSectionTranslation).mock.calls[0]).toEqual([
      "rec_1",
      {
        start_unit_id: "u3",
        end_unit_id: "u4",
        start_anchor_segment_id: "seg_a",
        end_anchor_segment_id: "seg_b",
        node_id: "node_1",
        outline_revision: "rev_9",
      },
      "session-token",
    ]);
  });

  it("forwards minimal range witness (no anchors / no audit) to upstream", async () => {
    vi.mocked(submitUpstreamReaderSectionTranslation).mockResolvedValue({
      ok: true,
      data: makeSectionTranslationResponse({ outcome: "already_covered_or_inflight" }),
    });

    const result = await submitReaderSectionTranslationFromWeb("rec_1", {
      startUnitId: "u1",
      endUnitId: "u2",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.outcome).toBe("already_covered_or_inflight");
    }
    expect(vi.mocked(submitUpstreamReaderSectionTranslation).mock.calls[0]).toEqual([
      "rec_1",
      {
        start_unit_id: "u1",
        end_unit_id: "u2",
        start_anchor_segment_id: null,
        end_anchor_segment_id: null,
        node_id: null,
        outline_revision: null,
      },
      "session-token",
    ]);
  });

  it("passes through all six stable outcome values without mutation", async () => {
    const outcomes: Array<ReaderSectionTranslationResponseDto["outcome"]> = [
      "succeeded",
      "retry_later",
      "already_covered_or_inflight",
      "budget_exhausted",
      "rejected",
      "superseded",
    ];
    for (const outcome of outcomes) {
      vi.mocked(submitUpstreamReaderSectionTranslation).mockResolvedValue({
        ok: true,
        data: makeSectionTranslationResponse({ outcome, detail: "rationale_code_x" }),
      });
      const result = await submitReaderSectionTranslationFromWeb("rec_1", {
        startUnitId: "u1",
        endUnitId: "u2",
      });
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.outcome).toBe(outcome);
        // detail (a stable reason code, not an exception message) is
        // preserved as-is; the BFF does not mutate it.
        expect(result.detail).toBe("rationale_code_x");
      }
    }
  });

  it("strips whitespace from range witness before forwarding", async () => {
    vi.mocked(submitUpstreamReaderSectionTranslation).mockResolvedValue({
      ok: true,
      data: makeSectionTranslationResponse(),
    });

    await submitReaderSectionTranslationFromWeb("rec_1", {
      startUnitId: "  u1  ",
      endUnitId: "\tu2\n",
      nodeId: "  node_1  ",
    });

    expect(vi.mocked(submitUpstreamReaderSectionTranslation).mock.calls[0]).toEqual([
      "rec_1",
      {
        start_unit_id: "u1",
        end_unit_id: "u2",
        start_anchor_segment_id: null,
        end_anchor_segment_id: null,
        node_id: "node_1",
        outline_revision: null,
      },
      "session-token",
    ]);
  });

  it.each([
    [404, "record_not_found"],
    [401, "upstream_auth_failed"],
    [409, "section_translation_conflict"],
    [422, "invalid_input"],
    [500, "upstream_unavailable"],
    [0, "upstream_unavailable"],
  ])("maps upstream %p to BFF code %p", async (status, code) => {
    vi.mocked(submitUpstreamReaderSectionTranslation).mockResolvedValue({
      ok: false,
      status,
      message: "upstream error body",
    });

    const result = await submitReaderSectionTranslationFromWeb("rec_1", {
      startUnitId: "u1",
      endUnitId: "u2",
    });

    expect(result).toMatchObject({ ok: false, code });
    // The BFF never leaks the upstream error message verbatim — it surfaces
    // a stable, friendly message per code.
    if (!result.ok) {
      expect(result.message).not.toContain("upstream error body");
    }
  });

  it("does not leak upstream exception messages or provider payload on success", async () => {
    vi.mocked(submitUpstreamReaderSectionTranslation).mockResolvedValue({
      ok: true,
      data: makeSectionTranslationResponse({ detail: null }),
    });

    const result = await submitReaderSectionTranslationFromWeb("rec_1", {
      startUnitId: "u1",
      endUnitId: "u2",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      // Only the three contract fields are present on the success shape.
      expect(Object.keys(result).sort()).toEqual(
        ["detail", "job_id", "ok", "outcome"].sort(),
      );
    }
  });

  it("422 from upstream surfaces invalid_input (not section_translation_conflict)", async () => {
    vi.mocked(submitUpstreamReaderSectionTranslation).mockResolvedValue({
      ok: false,
      status: 422,
      message: "start_unit_id missing",
    });

    const result = await submitReaderSectionTranslationFromWeb("rec_1", {
      startUnitId: "u1",
      endUnitId: "u2",
    });

    expect(result).toMatchObject({ ok: false, code: "invalid_input", status: 400 });
  });

  it.each([
    [400, "InternalValidationError: provider=acme payload={secret: 'abc'}"],
    [418, "I'm a teapot: provider_response.id=internal-trace-928374"],
    [451, "LegalReason: blocked due to upstream filter rule 'unauthenticated_record_access'"],
  ])(
    "unenumerated upstream %p does not leak internal upstream message verbatim",
    async (status, internalMessage) => {
      vi.mocked(submitUpstreamReaderSectionTranslation).mockResolvedValue({
        ok: false,
        status,
        message: internalMessage,
      });

      const result = await submitReaderSectionTranslationFromWeb("rec_1", {
        startUnitId: "u1",
        endUnitId: "u2",
      });

      expect(result).toMatchObject({
        ok: false,
        code: "upstream_error",
        status,
      });
      if (!result.ok) {
        // Stable, friendly generic message — never the upstream string.
        expect(result.message).not.toContain(internalMessage);
        // Negative coverage: none of the obvious internal tokens surface.
        expect(result.message).not.toContain("InternalValidationError");
        expect(result.message).not.toContain("secret");
        expect(result.message).not.toContain("provider");
        expect(result.message).not.toContain("internal-trace");
        expect(result.message).not.toContain("LegalReason");
        expect(result.message).not.toContain("unauthenticated_record_access");
      }
    },
  );
});

describe("submitReaderAnalysisSectionRequestFromWeb", () => {
  const inputSingle = { scope: "single", sectionId: "ras1_a" };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("forwards single as snake_case section_id", async () => {
    vi.mocked(submitUpstreamReaderAnalysisSectionRequest).mockResolvedValue({
      ok: true,
      data: {
        outcome: "started",
        accepted_section_ids: ["ras1_a"],
        event_sequence: 4,
        reason_code: null,
      },
    });

    const result = await submitReaderAnalysisSectionRequestFromWeb(" rec_1 ", inputSingle);

    expect(submitUpstreamReaderAnalysisSectionRequest).toHaveBeenCalledWith(
      "rec_1",
      { scope: "single", section_id: "ras1_a" },
      "session-token",
    );
    expect(result).toEqual({
      ok: true,
      outcome: "started",
      accepted_section_ids: ["ras1_a"],
      event_sequence: 4,
      reason_code: null,
    });
  });

  it("forwards remaining with section_id null", async () => {
    vi.mocked(submitUpstreamReaderAnalysisSectionRequest).mockResolvedValue({
      ok: true,
      data: {
        outcome: "already_active",
        accepted_section_ids: [],
        event_sequence: null,
        reason_code: null,
      },
    });

    const result = await submitReaderAnalysisSectionRequestFromWeb("rec_1", {
      scope: "remaining",
      sectionId: "   ",
    });

    expect(submitUpstreamReaderAnalysisSectionRequest).toHaveBeenCalledWith(
      "rec_1",
      { scope: "remaining", section_id: null },
      "session-token",
    );
    expect(result).toMatchObject({ ok: true, outcome: "already_active" });
  });

  it.each(["started", "already_active", "already_complete", "paused_quota", "rejected"] as const)(
    "treats HTTP 200 outcome %s as transport success",
    async (outcome) => {
      vi.mocked(submitUpstreamReaderAnalysisSectionRequest).mockResolvedValue({
        ok: true,
        data: {
          outcome,
          accepted_section_ids: [],
          event_sequence: outcome === "started" ? 2 : null,
          reason_code: outcome === "rejected" ? "analysis_mode_not_segmented" : null,
        },
      });

      const result = await submitReaderAnalysisSectionRequestFromWeb("rec_1", {
        scope: "remaining",
      });
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.outcome).toBe(outcome);
        expect(result.reason_code ?? null).toBe(
          outcome === "rejected" ? "analysis_mode_not_segmented" : null,
        );
      }
    },
  );

  it("rejects empty recordId", async () => {
    const result = await submitReaderAnalysisSectionRequestFromWeb("  ", inputSingle);
    expect(result).toMatchObject({ ok: false, code: "invalid_input", status: 400 });
    expect(submitUpstreamReaderAnalysisSectionRequest).not.toHaveBeenCalled();
  });

  it("rejects remaining with non-empty sectionId", async () => {
    const result = await submitReaderAnalysisSectionRequestFromWeb("rec_1", {
      scope: "remaining",
      sectionId: "ras1_a",
    });
    expect(result).toMatchObject({ ok: false, code: "invalid_input", status: 400 });
  });

  it("rejects single without sectionId", async () => {
    const result = await submitReaderAnalysisSectionRequestFromWeb("rec_1", {
      scope: "single",
    });
    expect(result).toMatchObject({ ok: false, code: "invalid_input", status: 400 });
  });

  it("maps 404 to record_not_found without leaking upstream message", async () => {
    vi.mocked(submitUpstreamReaderAnalysisSectionRequest).mockResolvedValue({
      ok: false,
      status: 404,
      message: "SQL detail leaked",
    });
    const result = await submitReaderAnalysisSectionRequestFromWeb("rec_1", inputSingle);
    expect(result).toMatchObject({ ok: false, code: "record_not_found", status: 404 });
    if (!result.ok) {
      expect(result.message).not.toContain("SQL");
    }
  });

  it("maps 409 to analysis_section_conflict", async () => {
    vi.mocked(submitUpstreamReaderAnalysisSectionRequest).mockResolvedValue({
      ok: false,
      status: 409,
      message: "inconsistent_active_base",
    });
    const result = await submitReaderAnalysisSectionRequestFromWeb("rec_1", inputSingle);
    expect(result).toEqual({
      ok: false,
      status: 409,
      code: "analysis_section_conflict",
      message: "文章状态已更新，请刷新后重试。",
    });
  });

  it("maps 422 to invalid_input 400", async () => {
    vi.mocked(submitUpstreamReaderAnalysisSectionRequest).mockResolvedValue({
      ok: false,
      status: 422,
      message: "validation failed",
    });
    const result = await submitReaderAnalysisSectionRequestFromWeb("rec_1", inputSingle);
    expect(result).toMatchObject({ ok: false, code: "invalid_input", status: 400 });
    if (!result.ok) {
      expect(result.message).not.toContain("validation failed");
    }
  });
});
