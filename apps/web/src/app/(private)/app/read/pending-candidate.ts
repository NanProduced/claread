export const PENDING_CANDIDATE_STORAGE_KEY =
  "claread:web:pending-candidate";

export interface PendingCandidateInput {
  readingRecordId: string;
  candidateDocumentId: string;
  originalInputId: string;
  inputSnapshot: string;
  savedAt?: string;
}

export interface PendingCandidate extends PendingCandidateInput {
  savedAt: string;
}

function isValidPendingCandidate(value: unknown): value is PendingCandidate {
  if (!value || typeof value !== "object") {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  if (
    typeof candidate.readingRecordId !== "string" ||
    candidate.readingRecordId.trim().length === 0 ||
    typeof candidate.candidateDocumentId !== "string" ||
    candidate.candidateDocumentId.trim().length === 0 ||
    typeof candidate.originalInputId !== "string" ||
    candidate.originalInputId.trim().length === 0
  ) {
    return false;
  }

  if (
    typeof candidate.inputSnapshot !== "string" ||
    candidate.inputSnapshot.length === 0
  ) {
    return false;
  }

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
    originalInputId: input.originalInputId,
    inputSnapshot: input.inputSnapshot,
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
