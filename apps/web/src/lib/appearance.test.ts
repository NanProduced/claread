import { describe, expect, it } from "vitest";
import {
  migrateLegacyAppearanceTheme,
  normalizeThemeName,
  normalizeThemePreference,
  resolveThemePreference,
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

  it("keeps the Reader-only normalize helper on its legacy contract", () => {
    expect(normalizeThemeName("paper")).toBe("paper");
    expect(normalizeThemeName("light")).toBe("light");
    expect(normalizeThemeName("dark")).toBe("dark");
    expect(normalizeThemeName("system")).toBe("light");
    expect(normalizeThemeName("sepia")).toBe("light");
  });

  it("maps resolved themes to theme-color values", () => {
    expect(themeColorForTheme("light")).toBe("#f7f5f0");
    expect(themeColorForTheme("dark")).toBe("#121518");
    expect(themeColorForTheme(null)).toBe("#f7f5f0");
    expect(themeColorForTheme(undefined)).toBe("#f7f5f0");
  });
});
