export const PENDING_CANDIDATE_STORAGE_KEY =
  "claread:web:pending-candidate";

export type PendingCandidateOrigin = "submit" | "resume";

/** L2 Content Check 的「稍后处理」恢复入口。不含旧确认模态字段。 */
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
   * (artifact path). Used to restore the form when the user clicks "返回修改".
   */
  inputSnapshot?: string | null;
  filename?: string | null;
  /**
   * How this pending candidate was created. `submit` is the normal
   * first-time submit flow. `resume` is the recovery flow from
   * `?resume_candidate=` and hides "返回修改".
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