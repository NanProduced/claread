import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReaderArticleRagIndexEnsureRequestDto,
  ReaderArticleRagIndexEnsureResponseDto,
  ReaderArticleRagIndexStatusResponseDto,
  ReaderArtifactPipelineStatusResponseDto,
  ReaderCandidateDocumentConfirmRequestDto,
  ReaderCandidateDocumentConfirmResponseDto,
  ReaderCandidateDocumentReadResponse,
  ReaderEventPollResponseDto,
  ReaderPlainTextSubmitRequestDto,
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

/**
 * Upstream client for the Reader Plate vertical slice.
 *
 * Targets the new endpoints introduced by the reader-agentic-orchestration
 * initiative:
 *   - POST /reader/records/plain-text
 *   - GET  /reader/records/{record_id}/snapshot
 *   - GET  /reader/records/{record_id}/events
 *   - POST /reader/records/input                              (unified input)
 *   - POST /reader/source-artifacts/init-upload
 *   - POST /reader/source-artifacts/{artifact_id}/complete-upload
 *   - POST /reader/source-artifacts/{artifact_id}/submit-input
 *   - GET  /reader/source-artifacts/{artifact_id}/pipeline-status
 *   - POST /reader/records/{record_id}/candidate-documents/{candidate_document_id}/confirm
 *   - GET  /reader/records/{record_id}/stable-document
 *   - GET  /reader/records/{record_id}/article-rag-index/status
 *   - POST /reader/records/{record_id}/article-rag-index/ensure
 *   - POST /reader/records/{record_id}/section-translation      (T5.6c)
 *
 * This module intentionally does NOT touch the legacy `/scene` endpoints.
 */

export function submitUpstreamReaderPlainText(
  payload: ReaderPlainTextSubmitRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderPlainTextSubmitResponseDto>> {
  return fastApiFetch<ReaderPlainTextSubmitResponseDto>(
    `/reader/records/plain-text`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}

export function submitUpstreamReaderUnifiedInput(
  payload: ReaderUnifiedInputSubmitRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderUnifiedInputSubmitResponseDto>> {
  return fastApiFetch<ReaderUnifiedInputSubmitResponseDto>(
    `/reader/records/input`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}

export function getUpstreamReaderPlateSnapshot(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderPlateSnapshotDto>> {
  return fastApiFetch<ReaderPlateSnapshotDto>(
    `/reader/records/${encodeURIComponent(recordId)}/snapshot`,
    { sessionToken },
  );
}

export interface PollUpstreamReaderEventsParams {
  afterSequence?: number;
  limit?: number;
}

export function pollUpstreamReaderEvents(
  recordId: string,
  sessionToken: string,
  params: PollUpstreamReaderEventsParams = {},
): Promise<UpstreamResult<ReaderEventPollResponseDto>> {
  const searchParams = new URLSearchParams();

  if (params.afterSequence !== undefined) {
    searchParams.set("after_sequence", String(params.afterSequence));
  }

  if (params.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }

  const query = searchParams.toString();

  return fastApiFetch<ReaderEventPollResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/events${query ? `?${query}` : ""}`,
    { sessionToken },
  );
}

// ---------------------------------------------------------------------------
// Source artifacts: init-upload / complete-upload / submit-input / pipeline-status
// ---------------------------------------------------------------------------

export function initUpstreamReaderSourceArtifactUpload(
  payload: ReaderSourceArtifactUploadInitRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderSourceArtifactUploadInitResponseDto>> {
  return fastApiFetch<ReaderSourceArtifactUploadInitResponseDto>(
    `/reader/source-artifacts/init-upload`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}

export function completeUpstreamReaderSourceArtifactUpload(
  artifactId: string,
  payload: ReaderSourceArtifactUploadCompleteRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderSourceArtifactUploadCompleteResponseDto>> {
  return fastApiFetch<ReaderSourceArtifactUploadCompleteResponseDto>(
    `/reader/source-artifacts/${encodeURIComponent(artifactId)}/complete-upload`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}

export function submitUpstreamReaderSourceArtifactInput(
  artifactId: string,
  payload: ReaderSourceArtifactSubmitInputRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderSourceArtifactSubmitInputResponseDto>> {
  return fastApiFetch<ReaderSourceArtifactSubmitInputResponseDto>(
    `/reader/source-artifacts/${encodeURIComponent(artifactId)}/submit-input`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}

export function getUpstreamReaderArtifactPipelineStatus(
  artifactId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderArtifactPipelineStatusResponseDto>> {
  return fastApiFetch<ReaderArtifactPipelineStatusResponseDto>(
    `/reader/source-artifacts/${encodeURIComponent(artifactId)}/pipeline-status`,
    { sessionToken },
  );
}

// ---------------------------------------------------------------------------
// Candidate document confirmation
// ---------------------------------------------------------------------------

export function confirmUpstreamReaderCandidateDocument(
  recordId: string,
  candidateDocumentId: string,
  payload: ReaderCandidateDocumentConfirmRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderCandidateDocumentConfirmResponseDto>> {
  return fastApiFetch<ReaderCandidateDocumentConfirmResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/candidate-documents/${encodeURIComponent(
      candidateDocumentId,
    )}/confirm`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}

// ---------------------------------------------------------------------------
// Stable Document projection
// ---------------------------------------------------------------------------

export function getUpstreamReaderStableDocument(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderStableDocumentResponseDto>> {
  return fastApiFetch<ReaderStableDocumentResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/stable-document`,
    { sessionToken },
  );
}

// ---------------------------------------------------------------------------
// Candidate document read (S4: input-page recovery)
// ---------------------------------------------------------------------------

export function getUpstreamReaderCandidateDocument(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderCandidateDocumentReadResponse>> {
  return fastApiFetch<ReaderCandidateDocumentReadResponse>(
    `/reader/records/${encodeURIComponent(recordId)}/candidate-document`,
    { sessionToken },
  );
}

// ---------------------------------------------------------------------------
// Article RAG Index lifecycle (status / ensure)
// ---------------------------------------------------------------------------

export function getUpstreamReaderArticleRagIndexStatus(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderArticleRagIndexStatusResponseDto>> {
  return fastApiFetch<ReaderArticleRagIndexStatusResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/article-rag-index/status`,
    { sessionToken },
  );
}

export function ensureUpstreamReaderArticleRagIndex(
  recordId: string,
  payload: ReaderArticleRagIndexEnsureRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderArticleRagIndexEnsureResponseDto>> {
  return fastApiFetch<ReaderArticleRagIndexEnsureResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/article-rag-index/ensure`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}

// ---------------------------------------------------------------------------
// T5.6c — Section translation (synchronous explicit-section command)
// ---------------------------------------------------------------------------

export function submitUpstreamReaderSectionTranslation(
  recordId: string,
  payload: ReaderSectionTranslationRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderSectionTranslationResponseDto>> {
  return fastApiFetch<ReaderSectionTranslationResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/section-translation`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}
