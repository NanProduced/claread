import type { ThemeName } from "@/lib/appearance";
import type { VisualTone } from "@/types/view/ReaderMockVm";

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

export const READER_SETTINGS_STORAGE_KEY = "claread.reader.settings.v4";
const LEGACY_READER_SETTINGS_STORAGE_KEYS = [
  "claread.reader.settings.v3",
  "claread.reader.settings.v2",
] as const;

export function createDefaultReaderSettings(): ReaderSettingsState {
  return {
    mode: "intensive",
    fontFamily: "editorial",
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

export function readerThemeClassName(theme: ThemeName) {
  if (theme === "light") {
    return "reading-paper-light";
  }

  if (theme === "dark") {
    return "reading-paper-dark";
  }

  return "reading-paper";
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
    fontScale === "sm"
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
      mode === "immersive" ? "leading-[1.84]" : "leading-[1.76]"
    }`,
    translationClassName: `reader-font-sans ${translationSizeClass} ${
      mode === "immersive" ? "leading-[1.7]" : "leading-[1.62]"
    }`,
    columnClassName: mode === "immersive" ? "max-w-[82ch]" : "max-w-[72ch]",
    paragraphDensityClassName:
      mode === "immersive" ? "reader-density-immersive" : "reader-density-intensive",
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
