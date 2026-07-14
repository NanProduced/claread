/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";
import {
  createDefaultWebPreferences,
  normalizeWebPreferences,
} from "./web-preferences";

describe("WebPreferences normalization", () => {
  it("defaults the preference to 'system' (Light/Dark are resolved at render)", () => {
    expect(createDefaultWebPreferences().theme).toBe("system");
  });

  it("accepts the three legal preference values", () => {
    expect(normalizeWebPreferences({ theme: "system" }).theme).toBe("system");
    expect(normalizeWebPreferences({ theme: "light" }).theme).toBe("light");
    expect(normalizeWebPreferences({ theme: "dark" }).theme).toBe("dark");
  });

  it("rejects invalid preference values back to the system default", () => {
    expect(normalizeWebPreferences({ theme: "paper" }).theme).toBe("system");
    expect(normalizeWebPreferences({ theme: "sepia" }).theme).toBe("system");
    expect(normalizeWebPreferences({ theme: undefined }).theme).toBe("system");
    expect(normalizeWebPreferences({ theme: null }).theme).toBe("system");
    expect(normalizeWebPreferences({ theme: 42 }).theme).toBe("system");
  });

  it("falls back to defaults when the payload is not a record", () => {
    expect(normalizeWebPreferences(null).theme).toBe("system");
    expect(normalizeWebPreferences("garbage").theme).toBe("system");
  });
});
