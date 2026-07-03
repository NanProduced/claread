/**
 * Centralized Reader Orchestration status mappers.
 *
 * The backend returns closed-enum status fields on every Reader Orchestration
 * surface (artifact pipeline, article RAG index, Ask article RAG sidecar).
 * The frontend MUST treat those enums as untrusted boundary data: schema
 * drift, hostile fakes, or a partial backend rollout can return values
 * outside the closed set. This module is the single place that coercion
 * happens — components and BFF wrappers consume the normalized output and
 * never branch on raw upstream strings.
 *
 * Coercion contract (mirrors the formal status-map):
 *   - Artifact pipeline `outcome`           → `extraction_failed`
 *   - Artifact pipeline `next_action`       → `show_error`
 *   - Article RAG index lifecycle `status`  → `unavailable`
 *   - Article RAG index ensure `status`     → `error`
 *   - Ask article RAG sidecar `status`      → `not_indexed_or_unavailable`
 *
 * Unknown values MUST fail closed to the safe fallback above. The fallback
 * is intentionally a passive "unavailable / error" state so the reader
 * surface never blocks on a status it cannot interpret.
 *
 * Debug-only fields (`failure_code`, `rationale_code`, `reason_code`,
 * provider URI, token, query text, chunk text) are stripped here so they
 * cannot leak into UI state. UI components receive only the fields they are
 * allowed to render.
 */

import type {
  ReaderArtifactPipelineNextActionDto,
  ReaderArtifactPipelineOutcomeDto,
  ReaderArticleRagIndexEnsureStatusDto,
  ReaderArticleRagIndexLifecycleStatusDto,
  ReaderArticleRagIndexStatusResponseDto,
  ReaderArticleRagIndexEnsureResponseDto,
  ReaderArtifactPipelineStatusResponseDto,
} from "@/types/api/reader-plate";
import type {
  ReaderAskArticleRagSidecarDto,
  ReaderAskArticleRagStatusDto,
} from "@/types/api/reader-ask";

// ---------------------------------------------------------------------------
// Closed enum sets
// ---------------------------------------------------------------------------

const PIPELINE_OUTCOMES: ReadonlySet<ReaderArtifactPipelineOutcomeDto> = new Set([
  "upload_pending",
  "upload_available_not_submitted",
  "extraction_queued",
  "extraction_running",
  "extraction_retry_later",
  "extraction_failed",
  "materialization_queued",
  "materialization_running",
  "materialization_retry_later",
  "materialization_failed",
  "stable_document_ready",
  "candidate_document_required",
  "input_rejected_or_action_required",
]);

const PIPELINE_NEXT_ACTIONS: ReadonlySet<ReaderArtifactPipelineNextActionDto> = new Set([
  "complete_upload",
  "submit_input",
  "wait_for_worker",
  "retry_later",
  "show_error",
  "open_reader",
  "confirm_candidate_document",
  "revise_input",
]);

const ARTICLE_RAG_INDEX_STATUSES: ReadonlySet<ReaderArticleRagIndexLifecycleStatusDto> = new Set([
  "not_ready",
  "not_indexed",
  "queued",
  "indexing",
  "indexed",
  "failed",
  "superseded_or_stale",
  "unavailable",
]);

const ARTICLE_RAG_ENSURE_STATUSES: ReadonlySet<ReaderArticleRagIndexEnsureStatusDto> = new Set([
  "enqueued",
  "idempotent_noop",
  "not_ready",
  "no_active_base",
  "generation_mismatch",
  "record_not_found",
  "plan_hash_mismatch",
  "bootstrap_inconsistent",
  "error",
]);

const ASK_ARTICLE_RAG_STATUSES: ReadonlySet<ReaderAskArticleRagStatusDto> = new Set([
  "available",
  "empty",
  "not_indexed_or_unavailable",
  "composer_rejected",
  "disabled",
  "stale_due_to_repair",
]);

// ---------------------------------------------------------------------------
// Fallback values (exported for tests + UI constants)
// ---------------------------------------------------------------------------

export const PIPELINE_OUTCOME_FALLBACK: ReaderArtifactPipelineOutcomeDto =
  "extraction_failed";
export const PIPELINE_NEXT_ACTION_FALLBACK: ReaderArtifactPipelineNextActionDto =
  "show_error";
export const ARTICLE_RAG_INDEX_STATUS_FALLBACK: ReaderArticleRagIndexLifecycleStatusDto =
  "unavailable";
export const ARTICLE_RAG_ENSURE_STATUS_FALLBACK: ReaderArticleRagIndexEnsureStatusDto =
  "error";
export const ASK_ARTICLE_RAG_STATUS_FALLBACK: ReaderAskArticleRagStatusDto =
  "not_indexed_or_unavailable";

// ---------------------------------------------------------------------------
// Coercion primitives
// ---------------------------------------------------------------------------

function isPipelineOutcome(
  value: unknown,
): value is ReaderArtifactPipelineOutcomeDto {
  return typeof value === "string" && PIPELINE_OUTCOMES.has(
    value as ReaderArtifactPipelineOutcomeDto,
  );
}

function isPipelineNextAction(
  value: unknown,
): value is ReaderArtifactPipelineNextActionDto {
  return typeof value === "string" && PIPELINE_NEXT_ACTIONS.has(
    value as ReaderArtifactPipelineNextActionDto,
  );
}

function isArticleRagIndexStatus(
  value: unknown,
): value is ReaderArticleRagIndexLifecycleStatusDto {
  return typeof value === "string" && ARTICLE_RAG_INDEX_STATUSES.has(
    value as ReaderArticleRagIndexLifecycleStatusDto,
  );
}

function isArticleRagEnsureStatus(
  value: unknown,
): value is ReaderArticleRagIndexEnsureStatusDto {
  return typeof value === "string" && ARTICLE_RAG_ENSURE_STATUSES.has(
    value as ReaderArticleRagIndexEnsureStatusDto,
  );
}

function isAskArticleRagStatus(
  value: unknown,
): value is ReaderAskArticleRagStatusDto {
  return typeof value === "string" && ASK_ARTICLE_RAG_STATUSES.has(
    value as ReaderAskArticleRagStatusDto,
  );
}

// ---------------------------------------------------------------------------
// Public normalizers
// ---------------------------------------------------------------------------

export function normalizePipelineOutcome(
  value: unknown,
): ReaderArtifactPipelineOutcomeDto {
  return isPipelineOutcome(value) ? value : PIPELINE_OUTCOME_FALLBACK;
}

export function normalizePipelineNextAction(
  value: unknown,
): ReaderArtifactPipelineNextActionDto {
  return isPipelineNextAction(value) ? value : PIPELINE_NEXT_ACTION_FALLBACK;
}

export function normalizeArticleRagIndexStatus(
  value: unknown,
): ReaderArticleRagIndexLifecycleStatusDto {
  return isArticleRagIndexStatus(value)
    ? value
    : ARTICLE_RAG_INDEX_STATUS_FALLBACK;
}

export function normalizeArticleRagEnsureStatus(
  value: unknown,
): ReaderArticleRagIndexEnsureStatusDto {
  return isArticleRagEnsureStatus(value)
    ? value
    : ARTICLE_RAG_ENSURE_STATUS_FALLBACK;
}

export function normalizeAskArticleRagStatus(
  value: unknown,
): ReaderAskArticleRagStatusDto {
  return isAskArticleRagStatus(value)
    ? value
    : ASK_ARTICLE_RAG_STATUS_FALLBACK;
}

// ---------------------------------------------------------------------------
// UI-safe types — debug-only fields stripped at the type level so components
// cannot accidentally read them off mapped state.
// ---------------------------------------------------------------------------

export type ReaderArtifactPipelineJobSummarySafeDto = Omit<
  NonNullable<ReaderArtifactPipelineStatusResponseDto["extraction_job"]>,
  "failure_class" | "failure_code" | "rationale_code"
>;

export type ReaderArtifactPipelineStatusSafeDto = Omit<
  ReaderArtifactPipelineStatusResponseDto,
  "extraction_job" | "materialization_job"
> & {
  extraction_job: ReaderArtifactPipelineJobSummarySafeDto | null;
  materialization_job: ReaderArtifactPipelineJobSummarySafeDto | null;
};

export type ReaderArticleRagIndexStatusSafeDto = Omit<
  ReaderArticleRagIndexStatusResponseDto,
  "reason_code"
>;

export type ReaderArticleRagIndexEnsureSafeDto = Omit<
  ReaderArticleRagIndexEnsureResponseDto,
  "reason_code"
>;

export interface ReaderAskArticleRagSidecarSafeDto {
  status: ReaderAskArticleRagStatusDto;
  should_attach: boolean;
  context_ids: string[];
  citations: ReaderAskArticleRagSidecarDto["citations"];
}

// ---------------------------------------------------------------------------
// Whole-DTO mappers — strip debug-only fields and coerce unknown enums
// ---------------------------------------------------------------------------

/**
 * Strip debug-only fields and coerce unknown enums on an artifact pipeline
 * status response. Returns a UI-safe shape that always carries a known
 * `outcome` / `next_action` pair.
 *
 * The returned object omits `failure_class` / `failure_code` /
 * `rationale_code` from job summaries entirely — they are debug-only and
 * must not propagate to UI state.
 */
export function mapArtifactPipelineStatus(
  raw: ReaderArtifactPipelineStatusResponseDto,
): ReaderArtifactPipelineStatusSafeDto {
  return {
    artifact: { ...raw.artifact },
    record: raw.record ? { ...raw.record } : null,
    original_input: raw.original_input
      ? { ...raw.original_input }
      : null,
    extraction_job: raw.extraction_job
      ? stripJobSummaryDebugFields(raw.extraction_job)
      : null,
    materialization_job: raw.materialization_job
      ? stripJobSummaryDebugFields(raw.materialization_job)
      : null,
    candidate_document: raw.candidate_document
      ? { ...raw.candidate_document }
      : null,
    stable_document: raw.stable_document
      ? { ...raw.stable_document }
      : null,
    outcome: normalizePipelineOutcome(raw.outcome),
    next_action: normalizePipelineNextAction(raw.next_action),
  };
}

/**
 * Strip `reason_code` from an article RAG index status response and coerce
 * unknown `status` values to `unavailable`. The returned object is safe to
 * expose to UI state.
 */
export function mapArticleRagIndexStatus(
  raw: ReaderArticleRagIndexStatusResponseDto,
): ReaderArticleRagIndexStatusSafeDto {
  return {
    reading_record_id: raw.reading_record_id,
    status: normalizeArticleRagIndexStatus(raw.status),
    stable_document_id: raw.stable_document_id,
    base_id: raw.base_id,
    record_generation: raw.record_generation,
    index_run_id: raw.index_run_id,
    index_version: raw.index_version,
    plan_content_sha256: raw.plan_content_sha256,
    chunk_count: raw.chunk_count,
  };
}

/**
 * Strip `reason_code` from an article RAG index ensure response and coerce
 * unknown `status` values to `error`. The returned object is safe to expose
 * to UI state.
 */
export function mapArticleRagIndexEnsure(
  raw: ReaderArticleRagIndexEnsureResponseDto,
): ReaderArticleRagIndexEnsureSafeDto {
  return {
    reading_record_id: raw.reading_record_id,
    status: normalizeArticleRagEnsureStatus(raw.status),
    idempotent_noop: raw.idempotent_noop,
    stable_document_id: raw.stable_document_id,
    base_id: raw.base_id,
    record_generation: raw.record_generation,
    index_run_id: raw.index_run_id,
    job_id: raw.job_id,
    index_version: raw.index_version,
    chunker_version: raw.chunker_version,
  };
}

/**
 * Strip debug-only fields (`failure_code`, `retryable`, `fallback_allowed`,
 * `source_pack_hash`, `query_sha256`) from an Ask article RAG sidecar and
 * coerce unknown `status` values to `not_indexed_or_unavailable`.
 *
 * The returned object retains `citations` (truth pointers into the stable
 * document) and `should_attach` so the UI can decide whether to render
 * citation affordances. Citations are themselves truth pointers; their
 * text/anchor MUST be resolved through `GET /reader/records/{id}/stable-document`.
 */
export function mapAskArticleRagSidecar(
  raw: ReaderAskArticleRagSidecarDto | null | undefined,
): ReaderAskArticleRagSidecarSafeDto {
  if (!raw) {
    return {
      status: ASK_ARTICLE_RAG_STATUS_FALLBACK,
      should_attach: false,
      context_ids: [],
      citations: [],
    };
  }

  const normalizedStatus = normalizeAskArticleRagStatus(raw.status);

  // `should_attach` must be strictly `true` — truthy strings like "false"
  // must NOT be coerced to true.
  const shouldAttach = raw.should_attach === true;

  // Citations are only meaningful when the sidecar is `available`. All
  // other statuses (stale_due_to_repair, disabled, composer_rejected,
  // not_indexed_or_unavailable, empty, unknown) must return an empty
  // list so the UI never renders stale or invalid citations.
  const citations =
    normalizedStatus === "available" && Array.isArray(raw.citations)
      ? raw.citations.map((c) => ({ ...c }))
      : [];

  return {
    status: normalizedStatus,
    should_attach: shouldAttach,
    context_ids: Array.isArray(raw.context_ids) ? [...raw.context_ids] : [],
    citations,
  };
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function stripJobSummaryDebugFields(
  job: NonNullable<ReaderArtifactPipelineStatusResponseDto["extraction_job"]>,
): ReaderArtifactPipelineJobSummarySafeDto {
  // Intentionally omit `failure_class`, `failure_code`, `rationale_code`.
  return {
    job_id: job.job_id,
    status: job.status,
    attempt_count: job.attempt_count,
    max_attempts: job.max_attempts,
    available_at: job.available_at,
    updated_at: job.updated_at,
  };
}
