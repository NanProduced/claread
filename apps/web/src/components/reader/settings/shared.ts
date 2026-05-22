import type { VisualTone } from "@/types/view/ReaderMockVm";

export type ReadingMode = "annotated" | "immersive" | "custom";
export type TranslationDisplay = "hidden" | "muted" | "visible";
export type ReadingDensity = "compact" | "calm" | "roomy";
export type ReaderPaperTheme = "warm" | "cool" | "sage";
export type ReaderFontSize = "compact" | "normal" | "large" | "xlarge";
export type ReaderColumnWidth = "narrow" | "standard" | "wide";

export interface ReaderAnnotationVisibilityGroups {
  lexical: boolean;
  analysis: boolean;
  userAssets: boolean;
}

export interface ReaderSettingsState {
  readingMode: ReadingMode;
  translationDisplay: TranslationDisplay;
  fontSize: ReaderFontSize;
  density: ReadingDensity;
  readerPaperTheme: ReaderPaperTheme;
  columnWidth: ReaderColumnWidth;
  annotationVisibilityGroups: ReaderAnnotationVisibilityGroups;
  updatedAt?: string;
}

export const READER_SETTINGS_STORAGE_KEY = "claread.reader.settings.v2";
export const READER_DEFAULT_PAPER_THEME_STORAGE_KEY = "claread.reader.paper-theme.v1";

export function createDefaultReaderSettings(
  readerPaperTheme: ReaderPaperTheme = "warm",
): ReaderSettingsState {
  return {
    readingMode: "annotated",
    translationDisplay: "visible",
    fontSize: "normal",
    density: "calm",
    readerPaperTheme,
    columnWidth: "standard",
    annotationVisibilityGroups: {
      lexical: true,
      analysis: true,
      userAssets: true,
    },
  };
}

export const defaultReaderSettings: ReaderSettingsState = createDefaultReaderSettings();

export const MODE_PRESETS: Record<Exclude<ReadingMode, "custom">, Partial<ReaderSettingsState>> = {
  annotated: {
    translationDisplay: "visible",
    annotationVisibilityGroups: { lexical: true, analysis: true, userAssets: true },
  },
  immersive: {
    translationDisplay: "hidden",
    annotationVisibilityGroups: { lexical: false, analysis: false, userAssets: false },
  },
};

function isReadingMode(value: unknown): value is ReadingMode {
  return value === "annotated" || value === "immersive" || value === "custom";
}

function isTranslationDisplay(value: unknown): value is TranslationDisplay {
  return value === "hidden" || value === "muted" || value === "visible";
}

function isFontSize(value: unknown): value is ReaderFontSize {
  return value === "compact" || value === "normal" || value === "large" || value === "xlarge";
}

function isDensity(value: unknown): value is ReadingDensity {
  return value === "compact" || value === "calm" || value === "roomy";
}

function isReaderPaperTheme(value: unknown): value is ReaderPaperTheme {
  return value === "warm" || value === "cool" || value === "sage";
}

function isColumnWidth(value: unknown): value is ReaderColumnWidth {
  return value === "narrow" || value === "standard" || value === "wide";
}

function migrateTranslation(payload: Record<string, unknown>): TranslationDisplay {
  if (isTranslationDisplay(payload.translationDisplay)) {
    return payload.translationDisplay;
  }
  if (typeof payload.showTranslation === "boolean") {
    return payload.showTranslation ? "visible" : "hidden";
  }
  return defaultReaderSettings.translationDisplay;
}

function normalizeReaderPaperTheme(value: unknown): ReaderPaperTheme {
  if (isReaderPaperTheme(value)) {
    return value;
  }
  if (value === "paper") return "warm";
  if (value === "white") return "cool";
  if (value === "green") return "sage";
  return defaultReaderSettings.readerPaperTheme;
}

function migrateReaderPaperTheme(payload: Record<string, unknown>): ReaderPaperTheme {
  if (isReaderPaperTheme(payload.readerPaperTheme)) {
    return payload.readerPaperTheme;
  }
  return normalizeReaderPaperTheme(payload.theme);
}

export function normalizeReaderSettings(value: unknown): ReaderSettingsState {
  if (!value || typeof value !== "object") {
    return defaultReaderSettings;
  }

  const payload = value as Record<string, unknown>;
  const groups = payload.annotationVisibilityGroups as
    | Partial<ReaderAnnotationVisibilityGroups>
    | undefined;
  const readerPaperTheme = migrateReaderPaperTheme(payload);
  const baseDefaults = createDefaultReaderSettings(readerPaperTheme);

  return {
    readingMode: isReadingMode(payload.readingMode)
      ? payload.readingMode
      : baseDefaults.readingMode,
    translationDisplay: migrateTranslation(payload),
    fontSize: isFontSize(payload.fontSize)
      ? payload.fontSize
      : baseDefaults.fontSize,
    density: isDensity(payload.density)
      ? payload.density
      : baseDefaults.density,
    readerPaperTheme,
    columnWidth: isColumnWidth(payload.columnWidth)
      ? payload.columnWidth
      : baseDefaults.columnWidth,
    annotationVisibilityGroups: {
      lexical:
        typeof groups?.lexical === "boolean"
          ? groups.lexical
          : baseDefaults.annotationVisibilityGroups.lexical,
      analysis:
        typeof groups?.analysis === "boolean"
          ? groups.analysis
          : baseDefaults.annotationVisibilityGroups.analysis,
      userAssets:
        typeof groups?.userAssets === "boolean"
          ? groups.userAssets
          : baseDefaults.annotationVisibilityGroups.userAssets,
    },
    updatedAt:
      typeof payload.updatedAt === "string" ? payload.updatedAt : undefined,
  };
}

export function readStoredDefaultReaderPaperTheme(): ReaderPaperTheme {
  if (typeof window === "undefined") {
    return defaultReaderSettings.readerPaperTheme;
  }

  try {
    return normalizeReaderPaperTheme(
      window.localStorage.getItem(READER_DEFAULT_PAPER_THEME_STORAGE_KEY),
    );
  } catch {
    return defaultReaderSettings.readerPaperTheme;
  }
}

export function persistDefaultReaderPaperTheme(value: ReaderPaperTheme) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(READER_DEFAULT_PAPER_THEME_STORAGE_KEY, value);
  } catch {
  }
}

export function readStoredReaderSettings(): ReaderSettingsState {
  if (typeof window === "undefined") {
    return defaultReaderSettings;
  }

  try {
    const raw = window.localStorage.getItem(READER_SETTINGS_STORAGE_KEY);
    if (!raw) {
      return createDefaultReaderSettings(readStoredDefaultReaderPaperTheme());
    }
    return normalizeReaderSettings(JSON.parse(raw));
  } catch {
    return createDefaultReaderSettings(readStoredDefaultReaderPaperTheme());
  }
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

export function translationVisible(display: TranslationDisplay): boolean {
  return display !== "hidden";
}

export function readerPaperThemeDataValue(theme: ReaderPaperTheme): string {
  return theme;
}

export function readerPaperThemeClassName(theme: ReaderPaperTheme) {
  if (theme === "cool") {
    return "reading-paper-cool";
  }
  if (theme === "sage") {
    return "reading-paper-sage";
  }
  return "reading-paper";
}

export function readerTextClassName({
  density,
  fontSize,
}: Pick<ReaderSettingsState, "density" | "fontSize">) {
  const sizeClass =
    fontSize === "compact"
      ? "text-[1.04rem] sm:text-[1.16rem]"
      : fontSize === "large"
        ? "text-[1.24rem] sm:text-[1.42rem]"
        : fontSize === "xlarge"
          ? "text-[1.38rem] sm:text-[1.58rem]"
          : "text-[1.12rem] sm:text-[1.28rem]";

  const densityClass =
    density === "compact"
      ? "leading-[1.72]"
      : density === "roomy"
        ? "leading-[2.08]"
        : "leading-[1.88]";

  return `reader-serif text-ink ${sizeClass} ${densityClass}`;
}

export function readerColumnWidthClassName(columnWidth: ReaderColumnWidth) {
  if (columnWidth === "narrow") {
    return "max-w-[82ch]";
  }
  if (columnWidth === "wide") {
    return "max-w-[108ch]";
  }
  return "max-w-[96ch]";
}

export function translationDisplayClassName(display: TranslationDisplay): string {
  if (display === "hidden") return "hidden";
  if (display === "muted") return "reader-translation-layer reader-translation--muted group/translation";
  return "reader-translation-layer group/translation";
}

export function withCustomReadingMode(
  current: ReaderSettingsState,
  patch: Partial<ReaderSettingsState>,
): ReaderSettingsState {
  return {
    ...current,
    ...patch,
    readingMode: "custom",
  };
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
