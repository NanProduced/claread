/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";
import {
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

  it("exposes a single global theme storage key", () => {
    expect(THEME_STORAGE_KEY).toBe("claread.theme.v1");
  });
});
