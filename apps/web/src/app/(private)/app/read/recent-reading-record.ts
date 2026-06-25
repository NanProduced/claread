export const RECENT_READING_RECORD_STORAGE_KEY =
  "claread:web:recent-reading-record";

const READING_RECORD_ROUTE_PREFIX = "/app/reader-record/";

export interface RecentReadingRecord {
  readingRecordId: string;
  readerUrl: string;
  title: string;
  createdAt: string;
}

export interface RecentReadingRecordInput {
  readingRecordId: string;
  readerUrl: string;
  title: string;
  createdAt?: string;
}

export function extractReadingRecordIdFromReaderUrl(readerUrl: string) {
  if (!readerUrl.startsWith(READING_RECORD_ROUTE_PREFIX)) {
    return null;
  }

  const rawId = readerUrl
    .slice(READING_RECORD_ROUTE_PREFIX.length)
    .split(/[?#]/, 1)[0];

  if (!rawId) {
    return null;
  }

  try {
    return decodeURIComponent(rawId);
  } catch {
    return rawId;
  }
}

function isValidRecentReadingRecord(value: unknown): value is RecentReadingRecord {
  if (!value || typeof value !== "object") {
    return false;
  }

  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.readingRecordId !== "string" ||
    candidate.readingRecordId.trim().length === 0 ||
    typeof candidate.readerUrl !== "string" ||
    typeof candidate.title !== "string" ||
    candidate.title.trim().length === 0 ||
    typeof candidate.createdAt !== "string"
  ) {
    return false;
  }

  return (
    extractReadingRecordIdFromReaderUrl(candidate.readerUrl) !== null &&
    !Number.isNaN(Date.parse(candidate.createdAt))
  );
}

export function readRecentReadingRecord(): RecentReadingRecord | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(RECENT_READING_RECORD_STORAGE_KEY);
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw) as unknown;
    return isValidRecentReadingRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function recentReadingRecordTitleFromText(text: string) {
  const firstLine = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);

  if (!firstLine) {
    return "Untitled reading";
  }

  const normalized = firstLine.replace(/\s+/g, " ");
  return normalized.length > 80
    ? `${normalized.slice(0, 77).trimEnd()}...`
    : normalized;
}

export function saveRecentReadingRecord(input: RecentReadingRecordInput) {
  if (typeof window === "undefined") {
    return false;
  }

  const record: RecentReadingRecord = {
    readingRecordId: input.readingRecordId,
    readerUrl: input.readerUrl,
    title: input.title.trim(),
    createdAt: input.createdAt ?? new Date().toISOString(),
  };

  if (!isValidRecentReadingRecord(record)) {
    return false;
  }

  try {
    window.localStorage.setItem(
      RECENT_READING_RECORD_STORAGE_KEY,
      JSON.stringify(record),
    );
    return true;
  } catch {
    return false;
  }
}
