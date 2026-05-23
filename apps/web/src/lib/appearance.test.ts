import { describe, expect, it } from "vitest";
import {
  migrateLegacyAppearanceTheme,
  normalizeThemeName,
  themeColorForTheme,
} from "./appearance";

describe("appearance helpers", () => {
  it("normalizes unsupported values back to paper", () => {
    expect(normalizeThemeName("paper")).toBe("paper");
    expect(normalizeThemeName("light")).toBe("light");
    expect(normalizeThemeName("dark")).toBe("dark");
    expect(normalizeThemeName("system")).toBe("paper");
    expect(normalizeThemeName("sepia")).toBe("paper");
  });

  it("migrates legacy appearance values into the new theme names", () => {
    expect(migrateLegacyAppearanceTheme("light")).toBe("light");
    expect(migrateLegacyAppearanceTheme("dark")).toBe("dark");
    expect(migrateLegacyAppearanceTheme("system", "dark")).toBe("dark");
    expect(migrateLegacyAppearanceTheme("system", "light")).toBe("light");
    expect(migrateLegacyAppearanceTheme("unknown")).toBe("paper");
  });

  it("maps themes to theme-color values", () => {
    expect(themeColorForTheme("paper")).toBe("#f3efe6");
    expect(themeColorForTheme("light")).toBe("#f7f5f0");
    expect(themeColorForTheme("dark")).toBe("#121518");
  });
});
