export type ReaderRecordSurfaceMode = "workbench" | "plate";

export const READER_RECORD_SURFACE_MODE_STORAGE_KEY =
  "claread:reader-record-surface-mode";
export const DEFAULT_READER_RECORD_SURFACE_MODE: ReaderRecordSurfaceMode = "plate";

declare global {
  // Test-only escape hatch for exercising both render paths without rebuilding.
  // Runtime control should use env or localStorage.
  var __CLAREAD_READER_RECORD_SURFACE_MODE__:
    | ReaderRecordSurfaceMode
    | undefined;
}

function normalizeReaderRecordSurfaceMode(
  value: string | null | undefined,
): ReaderRecordSurfaceMode | null {
  if (value === "workbench" || value === "plate") {
    return value;
  }
  return null;
}

export function configuredReaderRecordSurfaceMode(): ReaderRecordSurfaceMode {
  return (
    normalizeReaderRecordSurfaceMode(
      process.env.NEXT_PUBLIC_READER_RECORD_SURFACE_MODE,
    ) ?? DEFAULT_READER_RECORD_SURFACE_MODE
  );
}

export function getReaderRecordSurfaceMode(): ReaderRecordSurfaceMode {
  const globalMode = normalizeReaderRecordSurfaceMode(
    globalThis.__CLAREAD_READER_RECORD_SURFACE_MODE__,
  );
  if (globalMode) {
    return globalMode;
  }

  if (typeof window !== "undefined") {
    try {
      if (typeof window.localStorage?.getItem !== "function") {
        return configuredReaderRecordSurfaceMode();
      }
      const stored = normalizeReaderRecordSurfaceMode(
        window.localStorage.getItem(READER_RECORD_SURFACE_MODE_STORAGE_KEY),
      );
      if (stored) {
        return stored;
      }
    } catch {
      // Ignore storage access failures and keep the configured default.
    }
  }

  return configuredReaderRecordSurfaceMode();
}
