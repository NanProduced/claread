"use client";

import type { VisualTone } from "@/types/view/ReaderMockVm";

export type ReadingDensity = "calm" | "roomy";
export type ReaderTheme = "paper" | "white" | "green";
export type ReaderFontSize = "compact" | "normal" | "large";
export type ReaderColumnWidth = "narrow" | "standard" | "wide";

export interface ReaderAnnotationVisibilityGroups {
  lexical: boolean;
  analysis: boolean;
  userAssets: boolean;
}

export interface ReaderSettingsState {
  showTranslation: boolean;
  fontSize: ReaderFontSize;
  density: ReadingDensity;
  theme: ReaderTheme;
  columnWidth: ReaderColumnWidth;
  annotationVisibilityGroups: ReaderAnnotationVisibilityGroups;
}

export const READER_SETTINGS_STORAGE_KEY = "claread.reader.settings.v1";

export const defaultReaderSettings: ReaderSettingsState = {
  showTranslation: true,
  fontSize: "normal",
  density: "calm",
  theme: "paper",
  columnWidth: "standard",
  annotationVisibilityGroups: {
    lexical: true,
    analysis: true,
    userAssets: true,
  },
};

function isFontSize(value: unknown): value is ReaderFontSize {
  return value === "compact" || value === "normal" || value === "large";
}

function isDensity(value: unknown): value is ReadingDensity {
  return value === "calm" || value === "roomy";
}

function isTheme(value: unknown): value is ReaderTheme {
  return value === "paper" || value === "white" || value === "green";
}

function isColumnWidth(value: unknown): value is ReaderColumnWidth {
  return value === "narrow" || value === "standard" || value === "wide";
}

export function normalizeReaderSettings(value: unknown): ReaderSettingsState {
  if (!value || typeof value !== "object") {
    return defaultReaderSettings;
  }

  const payload = value as Partial<ReaderSettingsState>;
  const groups = payload.annotationVisibilityGroups;

  return {
    showTranslation:
      typeof payload.showTranslation === "boolean"
        ? payload.showTranslation
        : defaultReaderSettings.showTranslation,
    fontSize: isFontSize(payload.fontSize)
      ? payload.fontSize
      : defaultReaderSettings.fontSize,
    density: isDensity(payload.density)
      ? payload.density
      : defaultReaderSettings.density,
    theme: isTheme(payload.theme)
      ? payload.theme
      : defaultReaderSettings.theme,
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
  };
}

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
    window.localStorage.setItem(READER_SETTINGS_STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Ignore persistence failures. Reader settings should remain best-effort only.
  }
}

export function readerThemeClassName(theme: ReaderTheme) {
  if (theme === "white") {
    return "bg-surface";
  }
  if (theme === "green") {
    return "bg-[#F3F7EF]";
  }
  return "reading-paper";
}

export function readerTextClassName({
  density,
  fontSize,
}: Pick<ReaderSettingsState, "density" | "fontSize">) {
  return `reader-serif text-ink ${
    fontSize === "compact"
      ? "text-[1.04rem] sm:text-[1.16rem]"
      : fontSize === "large"
        ? "text-[1.24rem] sm:text-[1.42rem]"
        : "text-[1.12rem] sm:text-[1.28rem]"
  } ${density === "roomy" ? "leading-[2.08]" : "leading-[1.88]"}`;
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
