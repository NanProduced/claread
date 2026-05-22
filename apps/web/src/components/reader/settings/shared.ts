"use client";

import type { VisualTone } from "@/types/view/ReaderMockVm";

/* ------------------------------------------------------------------ */
/*  Value types                                                        */
/* ------------------------------------------------------------------ */

export type ReadingMode = "annotated" | "immersive";
export type TranslationDisplay = "hidden" | "muted" | "visible";
export type ReadingDensity = "compact" | "calm" | "roomy";
export type ReaderTheme = "warm" | "cool" | "sage";
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
  theme: ReaderTheme;
  columnWidth: ReaderColumnWidth;
  annotationVisibilityGroups: ReaderAnnotationVisibilityGroups;
  updatedAt?: string;
}

/* ------------------------------------------------------------------ */
/*  Defaults                                                           */
/* ------------------------------------------------------------------ */

export const READER_SETTINGS_STORAGE_KEY = "claread.reader.settings.v1";

export const defaultReaderSettings: ReaderSettingsState = {
  readingMode: "annotated",
  translationDisplay: "visible",
  fontSize: "normal",
  density: "calm",
  theme: "warm",
  columnWidth: "standard",
  annotationVisibilityGroups: {
    lexical: true,
    analysis: true,
    userAssets: true,
  },
};

/* ------------------------------------------------------------------ */
/*  Mode presets                                                       */
/* ------------------------------------------------------------------ */

/** Default overrides when the user switches into a reading mode.
 *  These only set the *initial* values for the mode; the user can
 *  subsequently fine-tune any individual setting. */
export const MODE_PRESETS: Record<ReadingMode, Partial<ReaderSettingsState>> = {
  annotated: {
    translationDisplay: "visible",
    annotationVisibilityGroups: { lexical: true, analysis: true, userAssets: true },
  },
  immersive: {
    translationDisplay: "hidden",
    annotationVisibilityGroups: { lexical: false, analysis: false, userAssets: false },
  },
};

/* ------------------------------------------------------------------ */
/*  Type guards                                                        */
/* ------------------------------------------------------------------ */

function isReadingMode(value: unknown): value is ReadingMode {
  return value === "annotated" || value === "immersive";
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

function isTheme(value: unknown): value is ReaderTheme {
  return value === "warm" || value === "cool" || value === "sage";
}

function isColumnWidth(value: unknown): value is ReaderColumnWidth {
  return value === "narrow" || value === "standard" || value === "wide";
}

/* ------------------------------------------------------------------ */
/*  Backward-compatible normalization                                  */
/* ------------------------------------------------------------------ */

/** Migrate legacy v1 formats where `showTranslation` was a boolean
 *  and `theme` used the old value set ("paper" | "white" | "green"). */
function migrateTranslation(payload: Record<string, unknown>): TranslationDisplay {
  // New key takes precedence
  if (isTranslationDisplay(payload.translationDisplay)) {
    return payload.translationDisplay;
  }
  // Migrate old boolean key
  if (typeof payload.showTranslation === "boolean") {
    return payload.showTranslation ? "visible" : "hidden";
  }
  return defaultReaderSettings.translationDisplay;
}

function migrateTheme(payload: Record<string, unknown>): ReaderTheme {
  if (isTheme(payload.theme)) {
    return payload.theme;
  }
  // Migrate old theme values
  if (payload.theme === "paper") return "warm";
  if (payload.theme === "white") return "cool";
  if (payload.theme === "green") return "sage";
  return defaultReaderSettings.theme;
}

export function normalizeReaderSettings(value: unknown): ReaderSettingsState {
  if (!value || typeof value !== "object") {
    return defaultReaderSettings;
  }

  const payload = value as Record<string, unknown>;
  const groups = payload.annotationVisibilityGroups as
    | Partial<ReaderAnnotationVisibilityGroups>
    | undefined;

  return {
    readingMode: isReadingMode(payload.readingMode)
      ? payload.readingMode
      : defaultReaderSettings.readingMode,
    translationDisplay: migrateTranslation(payload),
    fontSize: isFontSize(payload.fontSize)
      ? payload.fontSize
      : defaultReaderSettings.fontSize,
    density: isDensity(payload.density)
      ? payload.density
      : defaultReaderSettings.density,
    theme: migrateTheme(payload),
    columnWidth: isColumnWidth(payload.columnWidth)
      ? payload.columnWidth
      : defaultReaderSettings.columnWidth,
    annotationVisibilityGroups: {
      lexical:
        typeof groups?.lexical === "boolean"
          ? groups.lexical
          : defaultReaderSettings.annotationVisibilityGroups.lexical,
      analysis:
        typeof groups?.analysis === "boolean"
          ? groups.analysis
          : defaultReaderSettings.annotationVisibilityGroups.analysis,
      userAssets:
        typeof groups?.userAssets === "boolean"
          ? groups.userAssets
          : defaultReaderSettings.annotationVisibilityGroups.userAssets,
    },
    updatedAt:
      typeof payload.updatedAt === "string" ? payload.updatedAt : undefined,
  };
}

/* ------------------------------------------------------------------ */
/*  Persistence                                                        */
/* ------------------------------------------------------------------ */

export function readStoredReaderSettings(): ReaderSettingsState {
  if (typeof window === "undefined") {
    return defaultReaderSettings;
  }

  try {
    const raw = window.localStorage.getItem(READER_SETTINGS_STORAGE_KEY);
    if (!raw) {
      return defaultReaderSettings;
    }
    return normalizeReaderSettings(JSON.parse(raw));
  } catch {
    return defaultReaderSettings;
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
    // Ignore persistence failures. Reader settings should remain best-effort only.
  }
}

/* ------------------------------------------------------------------ */
/*  Convenience: is translation visible at all?                        */
/* ------------------------------------------------------------------ */

export function translationVisible(display: TranslationDisplay): boolean {
  return display !== "hidden";
}

/* ------------------------------------------------------------------ */
/*  Theme data-attribute (used on <article data-reader-theme="warm">)  */
/* ------------------------------------------------------------------ */

export function readerThemeDataValue(theme: ReaderTheme): string {
  return theme;
}

/** Legacy className helper — still used by daily-reader and examples
 *  pages that have not adopted the data-attribute approach. */
export function readerThemeClassName(theme: ReaderTheme) {
  if (theme === "cool") {
    return "reading-paper-cool";
  }
  if (theme === "sage") {
    return "reading-paper-sage";
  }
  return "reading-paper";
}

/* ------------------------------------------------------------------ */
/*  Typography helpers                                                 */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/*  Translation display class (for CSS-driven muted/visible styling)   */
/* ------------------------------------------------------------------ */

export function translationDisplayClassName(display: TranslationDisplay): string {
  if (display === "hidden") return "hidden";
  if (display === "muted") return "reader-translation-layer reader-translation--muted group/translation";
  return "reader-translation-layer group/translation";
}

/* ------------------------------------------------------------------ */
/*  Annotation visibility helpers                                      */
/* ------------------------------------------------------------------ */

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
