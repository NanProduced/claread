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
        showTranslation: false,
        fontSize: "invalid",
        annotationVisibilityGroups: {
          lexical: false,
        },
      }),
    ).toEqual({
      ...defaultReaderSettings,
      showTranslation: false,
      annotationVisibilityGroups: {
        lexical: false,
        analysis: true,
        userAssets: true,
      },
    });
  });

  it("persists and restores reader settings from localStorage", () => {
    const nextSettings = {
      ...defaultReaderSettings,
      fontSize: "large" as const,
      density: "roomy" as const,
      columnWidth: "wide" as const,
      annotationVisibilityGroups: {
        lexical: false,
        analysis: true,
        userAssets: false,
      },
    };

    persistReaderSettings(nextSettings);

    expect(storageMock.getItem(READER_SETTINGS_STORAGE_KEY)).toContain("\"columnWidth\":\"wide\"");
    expect(readStoredReaderSettings()).toEqual(nextSettings);
  });

  it("falls back to defaults when storage is malformed", () => {
    storageMock.setItem(READER_SETTINGS_STORAGE_KEY, "{bad json");
    expect(readStoredReaderSettings()).toEqual(defaultReaderSettings);
  });
});
