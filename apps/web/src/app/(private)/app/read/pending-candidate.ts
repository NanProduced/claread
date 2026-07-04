export const PENDING_CANDIDATE_STORAGE_KEY =
  "claread:web:pending-candidate";

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
  savedAt?: string;
}

export interface PendingCandidate extends PendingCandidateInput {
  savedAt: string;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isOptionalString(value: unknown): boolean {
  return value === null || value === undefined || typeof value === "string";
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

  if (typeof candidate.savedAt !== "string" || Number.isNaN(Date.parse(candidate.savedAt))) {
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