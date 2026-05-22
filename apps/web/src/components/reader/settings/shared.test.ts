/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  READER_SETTINGS_STORAGE_KEY,
  defaultReaderSettings,
  normalizeReaderSettings,
  persistReaderSettings,
  readStoredReaderSettings,
} from "./shared";

type StorageMock = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
  clear: () => void;
};

let storageMock: StorageMock;

beforeEach(() => {
  const store = new Map<string, string>();
  storageMock = {
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value);
    }),
    clear: vi.fn(() => {
      store.clear();
    }),
  };

  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storageMock,
  });
});

afterEach(() => {
  storageMock.clear();
});

describe("reader settings storage", () => {
  it("normalizes partial payloads back to supported defaults", () => {
    expect(
      normalizeReaderSettings({
        translationDisplay: "muted",
        fontSize: "invalid",
        annotationVisibilityGroups: {
          lexical: false,
        },
      }),
    ).toEqual({
      ...defaultReaderSettings,
      translationDisplay: "muted",
      annotationVisibilityGroups: {
        lexical: false,
        analysis: true,
        userAssets: true,
      },
    });
  });

  it("migrates legacy boolean showTranslation to translationDisplay", () => {
    expect(
      normalizeReaderSettings({
        showTranslation: false,
        fontSize: "normal",
      }),
    ).toEqual({
      ...defaultReaderSettings,
      translationDisplay: "hidden",
    });

    expect(
      normalizeReaderSettings({
        showTranslation: true,
        fontSize: "normal",
      }),
    ).toEqual({
      ...defaultReaderSettings,
      translationDisplay: "visible",
    });
  });

  it("migrates legacy theme values", () => {
    expect(
      normalizeReaderSettings({ theme: "paper" }),
    ).toEqual({
      ...defaultReaderSettings,
      theme: "warm",
    });

    expect(
      normalizeReaderSettings({ theme: "white" }),
    ).toEqual({
      ...defaultReaderSettings,
      theme: "cool",
    });

    expect(
      normalizeReaderSettings({ theme: "green" }),
    ).toEqual({
      ...defaultReaderSettings,
      theme: "sage",
    });
  });

  it("persists and restores reader settings from localStorage", () => {
    const nextSettings = {
      ...defaultReaderSettings,
      readingMode: "immersive" as const,
      translationDisplay: "muted" as const,
      fontSize: "large" as const,
      density: "roomy" as const,
      columnWidth: "wide" as const,
      theme: "sage" as const,
      annotationVisibilityGroups: {
        lexical: false,
        analysis: true,
        userAssets: false,
      },
    };

    persistReaderSettings(nextSettings);

    expect(storageMock.getItem(READER_SETTINGS_STORAGE_KEY)).toContain("\"columnWidth\":\"wide\"");
    expect(storageMock.getItem(READER_SETTINGS_STORAGE_KEY)).toContain("\"translationDisplay\":\"muted\"");
    expect(storageMock.getItem(READER_SETTINGS_STORAGE_KEY)).toContain("\"readingMode\":\"immersive\"");
    expect(storageMock.getItem(READER_SETTINGS_STORAGE_KEY)).toContain("\"updatedAt\"");

    const restored = readStoredReaderSettings();
    expect(restored.readingMode).toBe("immersive");
    expect(restored.translationDisplay).toBe("muted");
    expect(restored.fontSize).toBe("large");
    expect(restored.density).toBe("roomy");
    expect(restored.columnWidth).toBe("wide");
    expect(restored.theme).toBe("sage");
    expect(restored.annotationVisibilityGroups).toEqual({
      lexical: false,
      analysis: true,
      userAssets: false,
    });
  });

  it("falls back to defaults when storage is malformed", () => {
    storageMock.setItem(READER_SETTINGS_STORAGE_KEY, "{bad json");
    expect(readStoredReaderSettings()).toEqual(defaultReaderSettings);
  });

  it("handles xlarge font size", () => {
    const result = normalizeReaderSettings({ fontSize: "xlarge" });
    expect(result.fontSize).toBe("xlarge");
  });

  it("handles compact density", () => {
    const result = normalizeReaderSettings({ density: "compact" });
    expect(result.density).toBe("compact");
  });
});
