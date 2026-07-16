/** @vitest-environment jsdom */

import * as SharedModule from "./shared";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  READER_SETTINGS_STORAGE_KEY,
  createDefaultReaderSettings,
  defaultReaderSettings,
  modeShowsTranslation,
  modeVisibility,
  normalizeReaderSettings,
  persistReaderSettings,
  readerRecordPlateTypography,
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
  it("defaults the new Reader Record surface to sans without changing saved preferences", () => {
    expect(createDefaultReaderSettings()).toEqual({
      mode: "intensive",
      fontFamily: "sans",
      fontScale: "md",
    });

    const restored = normalizeReaderSettings({
      mode: "intensive",
      fontFamily: "editorial",
      fontScale: "md",
    });
    expect(restored.fontFamily).toBe("editorial");
  });

  it("normalizes supported payloads back to the new shape", () => {
    expect(
      normalizeReaderSettings({
        mode: "immersive",
        fontScale: "lg",
        fontFamily: "book",
      }),
    ).toEqual({
      ...defaultReaderSettings,
      mode: "immersive",
      fontScale: "lg",
      fontFamily: "book",
    });
  });

  it("migrates legacy reader settings to the new structure while ignoring legacy theme fields", () => {
    expect(
      normalizeReaderSettings({
        readingMode: "immersive",
        fontSize: "xlarge",
        readerPaperTheme: "cool",
        theme: "dark",
      }),
    ).toEqual({
      ...defaultReaderSettings,
      mode: "immersive",
      fontScale: "lg",
    });

    expect(
      normalizeReaderSettings({
        readingMode: "annotated",
        fontSize: "compact",
        readerPaperTheme: "sage",
      }),
    ).toEqual({
      ...createDefaultReaderSettings(),
      mode: "intensive",
      fontScale: "sm",
    });
  });

  it("uses defaults when no reader settings were persisted", () => {
    expect(readStoredReaderSettings()).toEqual(createDefaultReaderSettings());
  });

  it("persists and restores reader settings from localStorage", () => {
    const nextSettings = {
      ...defaultReaderSettings,
      mode: "immersive" as const,
      fontFamily: "book" as const,
      fontScale: "lg" as const,
    };

    persistReaderSettings(nextSettings);

    expect(storageMock.getItem(READER_SETTINGS_STORAGE_KEY)).toContain("\"mode\":\"immersive\"");
    expect(storageMock.getItem(READER_SETTINGS_STORAGE_KEY)).toContain("\"updatedAt\"");
    expect(storageMock.getItem(READER_SETTINGS_STORAGE_KEY)).not.toContain("\"theme\"");

    const restored = readStoredReaderSettings();
    expect(restored.mode).toBe("immersive");
    expect(restored.fontFamily).toBe("book");
    expect(restored.fontScale).toBe("lg");
  });

  it("migrates legacy storage when the new key is absent", () => {
    storageMock.setItem("claread.reader.settings.v3", JSON.stringify({
      mode: "immersive",
      theme: "dark",
      fontFamily: "book",
      fontScale: "lg",
    }));

    expect(readStoredReaderSettings()).toEqual({
      ...defaultReaderSettings,
      mode: "immersive",
      fontFamily: "book",
      fontScale: "lg",
    });

    storageMock.clear();
    storageMock.setItem("claread.reader.settings.v2", JSON.stringify({
      readingMode: "immersive",
      fontSize: "large",
      readerPaperTheme: "warm",
    }));

    expect(readStoredReaderSettings()).toEqual({
      ...createDefaultReaderSettings(),
      mode: "immersive",
      fontScale: "lg",
    });
  });

  it("falls back to defaults when storage is malformed", () => {
    storageMock.setItem(READER_SETTINGS_STORAGE_KEY, "{bad json");
    expect(readStoredReaderSettings()).toEqual(defaultReaderSettings);
  });

  it("derives mode visibility from the official two-mode model", () => {
    expect(modeVisibility("intensive")).toEqual({
      lexical: true,
      analysis: true,
      userAssets: true,
    });
    expect(modeVisibility("immersive")).toEqual({
      lexical: true,
      analysis: false,
      userAssets: true,
    });
    expect(modeShowsTranslation("intensive")).toBe(true);
    expect(modeShowsTranslation("immersive")).toBe(false);
  });

  it("derives Reader Record Plate typography from font family, scale, and mode", () => {
    expect(readerRecordPlateTypography(createDefaultReaderSettings())).toEqual({
      bodyClassName:
        "reader-font-sans text-ink reader-record-plate-font-sans reader-record-plate-type-md",
      columnClassName: "max-w-[46rem]",
      paragraphDensityClassName: "reader-record-plate-density-intensive",
    });

    expect(
      readerRecordPlateTypography({
        mode: "intensive",
        fontFamily: "editorial",
        fontScale: "sm",
      }),
    ).toEqual({
      bodyClassName:
        "reader-font-editorial text-ink reader-record-plate-font-editorial reader-record-plate-type-sm",
      columnClassName: "max-w-[44rem]",
      paragraphDensityClassName: "reader-record-plate-density-intensive",
    });

    expect(
      readerRecordPlateTypography({
        mode: "immersive",
        fontFamily: "book",
        fontScale: "lg",
      }),
    ).toEqual({
      bodyClassName:
        "reader-font-book text-ink reader-record-plate-font-book reader-record-plate-type-lg",
      columnClassName: "max-w-[42rem]",
      paragraphDensityClassName: "reader-record-plate-density-immersive",
    });
  });

  it("does not export any runtime Reader canvas theme class", () => {
    // The retired runtime canvas theme hook must not return. Reader canvas
    // theming is owned entirely by AppearanceProvider, and
    // `--reading-paper-surface` survives only as a class-free compat alias
    // derived from the root/.dark `--reader-paper` token.
    expect(SharedModule).not.toHaveProperty("READER_CANVAS_CLASS");
    expect(SharedModule).not.toHaveProperty("readerThemeClassName");
  });
});
