import { describe, expect, it } from "vitest";
import {
  normalizeAppearance,
  themeColorForAppearance,
} from "./appearance";

describe("appearance helpers", () => {
  it("normalizes unsupported values back to system", () => {
    expect(normalizeAppearance("light")).toBe("light");
    expect(normalizeAppearance("dark")).toBe("dark");
    expect(normalizeAppearance("system")).toBe("system");
    expect(normalizeAppearance("sepia")).toBe("system");
  });

  it("maps appearances to theme-color values", () => {
    expect(themeColorForAppearance("light")).toBe("#f7f6f2");
    expect(themeColorForAppearance("dark")).toBe("#181713");
    expect(themeColorForAppearance("system")).toBe("#f7f6f2");
  });
});
