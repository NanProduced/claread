import type { VisualTone } from "@/types/view/ReaderMockVm";

/**
 * Reader canvas theming is owned entirely by AppearanceProvider. There is
 * no per-Reader canvas theme class: the legacy Paper canvas selector and
 * Paper gradient have been retired, and `--reading-paper-surface`
 * survives only as a class-free compat alias derived from the root/.dark
 * `--reader-paper` token. Reader sub-systems must not re-introduce a
 * runtime canvas theme class.
 */

export type ReaderMode = "intensive" | "immersive";
export type ReaderFontFamily = "editorial" | "book" | "sans";
export type ReaderFontScale = "sm" | "md" | "lg";

export interface ReaderAnnotationVisibilityGroups {
  lexical: boolean;
  analysis: boolean;
  userAssets: boolean;
}

export interface ReaderSettingsState {
  mode: ReaderMode;
  fontFamily: ReaderFontFamily;
  fontScale: ReaderFontScale;
  updatedAt?: string;
}

export interface ReaderModeTypography {
  bodyClassName: string;
  translationClassName: string;
  columnClassName: string;
  paragraphDensityClassName: string;
}

export interface ReaderRecordPlateTypography {
  bodyClassName: string;
  columnClassName: string;
  paragraphDensityClassName: string;
}

export const READER_SETTINGS_STORAGE_KEY = "claread.reader.settings.v4";
const LEGACY_READER_SETTINGS_STORAGE_KEYS = [
  "claread.reader.settings.v3",
  "claread.reader.settings.v2",
] as const;

export function createDefaultReaderSettings(): ReaderSettingsState {
  return {
    mode: "intensive",
    fontFamily: "sans",
    fontScale: "md",
  };
}

export const defaultReaderSettings: ReaderSettingsState = createDefaultReaderSettings();

function isReaderMode(value: unknown): value is ReaderMode {
  return value === "intensive" || value === "immersive";
}

function isReaderFontFamily(value: unknown): value is ReaderFontFamily {
  return value === "editorial" || value === "book" || value === "sans";
}

function isReaderFontScale(value: unknown): value is ReaderFontScale {
  return value === "sm" || value === "md" || value === "lg";
}

function migrateLegacyMode(value: unknown): ReaderMode {
  if (value === "immersive") {
    return "immersive";
  }
  return "intensive";
}

function migrateLegacyFontScale(value: unknown): ReaderFontScale {
  if (value === "compact") return "sm";
  if (value === "normal") return "md";
  if (value === "large" || value === "xlarge") return "lg";
  return defaultReaderSettings.fontScale;
}

export function normalizeReaderSettings(
  value: unknown,
): ReaderSettingsState {
  const defaults = createDefaultReaderSettings();
  if (!value || typeof value !== "object") {
    return defaults;
  }

  const payload = value as Record<string, unknown>;

  return {
    mode: isReaderMode(payload.mode)
      ? payload.mode
      : migrateLegacyMode(payload.readingMode),
    fontFamily: isReaderFontFamily(payload.fontFamily)
      ? payload.fontFamily
      : defaults.fontFamily,
    fontScale: isReaderFontScale(payload.fontScale)
      ? payload.fontScale
      : migrateLegacyFontScale(payload.fontSize),
    updatedAt:
      typeof payload.updatedAt === "string" ? payload.updatedAt : undefined,
  };
}

export function readStoredReaderSettings(
): ReaderSettingsState {
  if (typeof window === "undefined") {
    return createDefaultReaderSettings();
  }

  try {
    const nextRaw = window.localStorage.getItem(READER_SETTINGS_STORAGE_KEY);
    if (nextRaw) {
      return normalizeReaderSettings(JSON.parse(nextRaw));
    }

    for (const legacyKey of LEGACY_READER_SETTINGS_STORAGE_KEYS) {
      const legacyRaw = window.localStorage.getItem(legacyKey);
      if (!legacyRaw) {
        continue;
      }
      const migrated = normalizeReaderSettings(JSON.parse(legacyRaw));
      persistReaderSettings(migrated);
      return migrated;
    }
  } catch {
    return createDefaultReaderSettings();
  }

  return createDefaultReaderSettings();
}

export function persistReaderSettings(value: ReaderSettingsState) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(
      READER_SETTINGS_STORAGE_KEY,
      JSON.stringify({ ...value, updatedAt: new Date().toISOString() }),
    );
  } catch {
  }
}

export function readerTextClassName({
  fontFamily,
  fontScale,
}: Pick<ReaderSettingsState, "fontFamily" | "fontScale">) {
  const fontClass =
    fontFamily === "book"
      ? "reader-font-book"
      : fontFamily === "sans"
        ? "reader-font-sans"
        : "reader-font-editorial";

  const sizeClass =
    fontScale === "sm"
      ? "text-[1.05rem] sm:text-[1.16rem]"
      : fontScale === "lg"
        ? "text-[1.24rem] sm:text-[1.4rem]"
        : "text-[1.14rem] sm:text-[1.28rem]";

  return `${fontClass} text-ink ${sizeClass} leading-[1.88]`;
}

export function readerModeTypography({
  mode,
  fontFamily,
  fontScale,
}: Pick<ReaderSettingsState, "mode" | "fontFamily" | "fontScale">): ReaderModeTypography {
  const fontClass =
    fontFamily === "book"
      ? "reader-font-book"
      : fontFamily === "sans"
        ? "reader-font-sans"
        : "reader-font-editorial";

  const bodySizeClass =
    mode === "immersive"
      ? fontScale === "sm"
        ? "text-[1.02rem] sm:text-[1.12rem]"
        : fontScale === "lg"
          ? "text-[1.2rem] sm:text-[1.36rem]"
          : "text-[1.1rem] sm:text-[1.24rem]"
      : fontScale === "sm"
        ? "text-[1.05rem] sm:text-[1.16rem]"
        : fontScale === "lg"
          ? "text-[1.24rem] sm:text-[1.4rem]"
          : "text-[1.14rem] sm:text-[1.28rem]";

  const translationSizeClass =
    fontScale === "sm"
      ? "text-[0.8rem] sm:text-[0.88rem]"
      : fontScale === "lg"
        ? "text-[0.96rem] sm:text-[1.05rem]"
        : "text-[0.88rem] sm:text-[0.96rem]";

  return {
    bodyClassName: `${fontClass} text-ink ${bodySizeClass} ${
      mode === "immersive"
        ? "leading-[1.86] tracking-[0.002em] text-ink/95"
        : "leading-[1.85] tracking-[0.001em] text-ink/96"
    }`,
    translationClassName: `reader-font-sans ${translationSizeClass} ${
      mode === "immersive" ? "leading-[1.66]" : "leading-[1.7] tracking-[0.006em]"
    }`,
    columnClassName: mode === "immersive" ? "max-w-[68ch]" : "max-w-[69ch]",
    paragraphDensityClassName:
      mode === "immersive" ? "reader-density-immersive" : "reader-density-intensive",
  };
}

export function readerRecordPlateTypography({
  mode,
  fontFamily,
  fontScale,
}: Pick<ReaderSettingsState, "mode" | "fontFamily" | "fontScale">): ReaderRecordPlateTypography {
  const fontClass =
    fontFamily === "book"
      ? "reader-font-book"
      : fontFamily === "sans"
        ? "reader-font-sans"
        : "reader-font-editorial";
  const readerRecordFontClass =
    fontFamily === "book"
      ? "reader-record-plate-font-book"
      : fontFamily === "sans"
        ? "reader-record-plate-font-sans"
        : "reader-record-plate-font-editorial";
  const typeScaleClass =
    fontScale === "sm"
      ? "reader-record-plate-type-sm"
      : fontScale === "lg"
        ? "reader-record-plate-type-lg"
        : "reader-record-plate-type-md";
  const columnClassName =
    fontFamily === "sans"
      ? mode === "immersive"
        ? "max-w-[44rem]"
        : "max-w-[46rem]"
      : mode === "immersive"
        ? "max-w-[42rem]"
        : "max-w-[44rem]";

  return {
    bodyClassName: `${fontClass} text-ink ${readerRecordFontClass} ${typeScaleClass}`,
    columnClassName,
    paragraphDensityClassName:
      mode === "immersive"
        ? "reader-record-plate-density-immersive"
        : "reader-record-plate-density-intensive",
  };
}

export function modeVisibility(mode: ReaderMode): ReaderAnnotationVisibilityGroups {
  if (mode === "immersive") {
    return {
      lexical: true,
      analysis: false,
      userAssets: true,
    };
  }

  return {
    lexical: true,
    analysis: true,
    userAssets: true,
  };
}

export function modeShowsTranslation(mode: ReaderMode): boolean {
  return mode === "intensive";
}

export function lexicalMarkVisible(
  visualTone: VisualTone | undefined,
  groups: ReaderAnnotationVisibilityGroups,
) {
  if (!visualTone) {
    return true;
  }

  if (visualTone === "vocab" || visualTone === "phrase" || visualTone === "context") {
    return groups.lexical;
  }

  return groups.analysis;
}

export function analysisEntryVisible(
  entryType: "grammar_note" | "sentence_analysis" | "term_note" | "logic_note" | "interpretation_note",
  groups: ReaderAnnotationVisibilityGroups,
) {
  if (
    entryType === "grammar_note" ||
    entryType === "sentence_analysis" ||
    entryType === "term_note" ||
    entryType === "logic_note" ||
    entryType === "interpretation_note"
  ) {
    return groups.analysis;
  }

  return true;
}
