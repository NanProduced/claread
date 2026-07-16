/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LEGACY_READER_THEME_STORAGE_KEY,
  mapLegacyReaderThemeValue,
  migrateLegacyReaderThemeStorage,
  normalizeThemePreference,
  resolveThemePreference,
  THEME_STORAGE_KEY,
  themeColorForTheme,
} from "./appearance";

describe("appearance helpers", () => {
  it("normalizes preference inputs, rejecting invalid values", () => {
    expect(normalizeThemePreference("system")).toBe("system");
    expect(normalizeThemePreference("light")).toBe("light");
    expect(normalizeThemePreference("dark")).toBe("dark");
    expect(normalizeThemePreference("paper")).toBe("system");
    expect(normalizeThemePreference("sepia")).toBe("system");
    expect(normalizeThemePreference(undefined)).toBe("system");
    expect(normalizeThemePreference(null)).toBe("system");
  });

  it("resolves system preference against the OS theme", () => {
    expect(resolveThemePreference("system", "light")).toBe("light");
    expect(resolveThemePreference("system", "dark")).toBe("dark");
    expect(resolveThemePreference("light", "dark")).toBe("light");
    expect(resolveThemePreference("dark", "light")).toBe("dark");
  });

  it("maps resolved themes to theme-color values", () => {
    expect(themeColorForTheme("light")).toBe("#f8f8f8");
    expect(themeColorForTheme("dark")).toBe("#161616");
    expect(themeColorForTheme(null)).toBe("#f8f8f8");
    expect(themeColorForTheme(undefined)).toBe("#f8f8f8");
  });
});

describe("legacy Reader theme migration", () => {
  it("maps retired paper sentinel to system preference", () => {
    expect(mapLegacyReaderThemeValue("paper")).toBe("system");
  });

  it("maps light and dark legacy values to themselves", () => {
    expect(mapLegacyReaderThemeValue("light")).toBe("light");
    expect(mapLegacyReaderThemeValue("dark")).toBe("dark");
  });

  it("returns null for invalid or unrecognized values", () => {
    expect(mapLegacyReaderThemeValue("sepia")).toBeNull();
    expect(mapLegacyReaderThemeValue(undefined)).toBeNull();
    expect(mapLegacyReaderThemeValue(null)).toBeNull();
    expect(mapLegacyReaderThemeValue("")).toBeNull();
  });
});

describe("migrateLegacyReaderThemeStorage", () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) => store.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => {
        store.set(key, value);
      }),
      removeItem: vi.fn((key: string) => {
        store.delete(key);
      }),
      clear: vi.fn(() => {
        store.clear();
      }),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("migrates a retired paper sentinel to system when no global preference exists and reports the migrated value", () => {
    window.localStorage.setItem(LEGACY_READER_THEME_STORAGE_KEY, "paper");
    const result = migrateLegacyReaderThemeStorage();

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
    expect(window.localStorage.getItem(LEGACY_READER_THEME_STORAGE_KEY)).toBeNull();
    expect(result.migrated).toBe("system");
  });

  it("migrates light and dark legacy values to the global preference when none exists and reports the migrated value", () => {
    window.localStorage.setItem(LEGACY_READER_THEME_STORAGE_KEY, "dark");
    const result = migrateLegacyReaderThemeStorage();

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(window.localStorage.getItem(LEGACY_READER_THEME_STORAGE_KEY)).toBeNull();
    expect(result.migrated).toBe("dark");
  });

  it("does not overwrite an existing valid global preference and reports no migration", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    window.localStorage.setItem(LEGACY_READER_THEME_STORAGE_KEY, "dark");
    const result = migrateLegacyReaderThemeStorage();

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(window.localStorage.getItem(LEGACY_READER_THEME_STORAGE_KEY)).toBeNull();
    expect(result.migrated).toBeNull();
  });

  it("clears the legacy key even when the legacy value is invalid and reports no migration", () => {
    window.localStorage.setItem(LEGACY_READER_THEME_STORAGE_KEY, "sepia");
    const result = migrateLegacyReaderThemeStorage();

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(LEGACY_READER_THEME_STORAGE_KEY)).toBeNull();
    expect(result.migrated).toBeNull();
  });

  it("is a no-op when the legacy key is absent and reports no migration", () => {
    const result = migrateLegacyReaderThemeStorage();

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(LEGACY_READER_THEME_STORAGE_KEY)).toBeNull();
    expect(result.migrated).toBeNull();
  });

  it("does not overwrite a valid global preference with an invalid legacy value and reports no migration", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    window.localStorage.setItem(LEGACY_READER_THEME_STORAGE_KEY, "sepia");
    const result = migrateLegacyReaderThemeStorage();

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(window.localStorage.getItem(LEGACY_READER_THEME_STORAGE_KEY)).toBeNull();
    expect(result.migrated).toBeNull();
  });
});
