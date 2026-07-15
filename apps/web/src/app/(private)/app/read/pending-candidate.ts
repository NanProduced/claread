import type {
  ReaderCandidateDocumentOutlineItem,
  ReaderCandidateDocumentPreviewMode,
  ReaderCandidateDocumentRiskItem,
} from "@/types/api/reader-plate";

export const PENDING_CANDIDATE_STORAGE_KEY =
  "claread:web:pending-candidate";

export type PendingCandidateOrigin = "submit" | "resume";

export interface PendingCandidateInput {
  readingRecordId: string;
  candidateDocumentId: string;
  /**
   * Upstream original_input_id when the candidate is created from a unified
   * input submit. Optional because the artifact-pipeline candidate path may
   * not surface this id on the polling endpoint yet.
   */
  originalInputId?: string | null;
  /**
   * Snapshot of the user-provided text (text path) or the original filename
   * (artifact path). Used to restore the form when the user clicks "重新编辑".
   * Optional for non-form paths (e.g. zero-data pipeline candidate).
   */
  inputSnapshot?: string | null;
  filename?: string | null;
  /**
   * Short preview from `candidate_document.canonical_text_preview` so the
   * confirm-callout can show the user what they're about to confirm.
   */
  canonicalTextPreview?: string | null;
  /**
   * Typed outline from `candidate_document.preview.document_outline`. Surfaced
   * in the confirm dialog when `preview_mode === "outline_only"` or when the
   * upstream payload carries items even with `preview_text`. Optional because
   * the legacy submit path does not surface this field.
   */
  documentOutline?: ReaderCandidateDocumentOutlineItem[];
  /**
   * Typed risk items from `candidate_document.preview.risk_items`. Surfaced
   * in the confirm dialog as a short warning list. Optional for the same
   * reason as `documentOutline`.
   */
  riskItems?: ReaderCandidateDocumentRiskItem[];
  /**
   * The typed `preview_mode` from the candidate-document read response. Used
   * by the dialog to decide whether to render the outline/risk lists. May be
   * absent on legacy submit-origin candidates.
   */
  previewMode?: ReaderCandidateDocumentPreviewMode;
  /**
   * The source document's character count when the recovery response supplies
   * it. This is only used to make a truncated or outline-only preview honest
   * to the reader; it does not expose raw source content.
   */
  totalCharCount?: number;
  /**
   * How this pending candidate was created. `submit` is the normal
   * first-time submit flow (textarea may be pre-filled). `resume` is
   * the recovery flow from `?resume_candidate=` and disallows edit
   * affordances.
   */
  origin?: PendingCandidateOrigin;
  savedAt?: string;
}

export interface PendingCandidate extends PendingCandidateInput {
  origin: PendingCandidateOrigin;
  savedAt: string;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isOptionalString(value: unknown): boolean {
  return value === null || value === undefined || typeof value === "string";
}

function isOptionalArray(value: unknown): boolean {
  return value === undefined || Array.isArray(value);
}

function isValidPendingCandidate(value: unknown): value is PendingCandidate {
  if (!value || typeof value !== "object") {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  if (!isNonEmptyString(candidate.readingRecordId)) return false;
  if (!isNonEmptyString(candidate.candidateDocumentId)) return false;

  if (!isOptionalString(candidate.originalInputId)) return false;
  if (!isOptionalString(candidate.inputSnapshot)) return false;
  if (!isOptionalString(candidate.filename)) return false;
  if (!isOptionalString(candidate.canonicalTextPreview)) return false;
  if (!isOptionalArray(candidate.documentOutline)) return false;
  if (!isOptionalArray(candidate.riskItems)) return false;
  if (!isOptionalString(candidate.previewMode)) return false;
  if (
    candidate.totalCharCount !== undefined &&
    (typeof candidate.totalCharCount !== "number" || !Number.isFinite(candidate.totalCharCount) || candidate.totalCharCount < 0)
  ) return false;

  if (typeof candidate.savedAt !== "string" || Number.isNaN(Date.parse(candidate.savedAt))) {
    return false;
  }

  const origin = candidate.origin;
  if (origin !== "submit" && origin !== "resume") {
    return false;
  }

  return true;
}

export function readPendingCandidate(): PendingCandidate | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(PENDING_CANDIDATE_STORAGE_KEY);
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw) as unknown;
    return isValidPendingCandidate(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function savePendingCandidate(
  input: PendingCandidateInput,
): PendingCandidate | null {
  if (typeof window === "undefined") {
    return null;
  }

  const record: PendingCandidate = {
    readingRecordId: input.readingRecordId,
    candidateDocumentId: input.candidateDocumentId,
    originalInputId: input.originalInputId ?? null,
    inputSnapshot: input.inputSnapshot ?? null,
    filename: input.filename ?? null,
    canonicalTextPreview: input.canonicalTextPreview ?? null,
    documentOutline: input.documentOutline ?? undefined,
    riskItems: input.riskItems ?? undefined,
    previewMode: input.previewMode ?? undefined,
    totalCharCount: input.totalCharCount ?? undefined,
    origin: input.origin ?? "submit",
    savedAt: input.savedAt ?? new Date().toISOString(),
  };

  if (!isValidPendingCandidate(record)) {
    return null;
  }

  try {
    window.localStorage.setItem(
      PENDING_CANDIDATE_STORAGE_KEY,
      JSON.stringify(record),
    );
    return record;
  } catch {
    return null;
  }
}

export function clearPendingCandidate(): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.removeItem(PENDING_CANDIDATE_STORAGE_KEY);
  } catch {
    /* ignore storage access failures */
  }
}

export function isResumePendingCandidate(
  value: PendingCandidate | null | undefined,
): value is PendingCandidate {
  return Boolean(value && value.origin === "resume");
}