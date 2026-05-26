import { describe, expect, it, vi } from "vitest";
import { isMac, getPrimaryModifier } from "./platform";
import { formatShortcut } from "./format-shortcut";
import { matchShortcut } from "./match-shortcut";

function mockKeyboardEvent(
  key: string,
  opts: { metaKey?: boolean; ctrlKey?: boolean; altKey?: boolean; shiftKey?: boolean } = {},
): KeyboardEvent {
  return {
    key,
    metaKey: opts.metaKey ?? false,
    ctrlKey: opts.ctrlKey ?? false,
    altKey: opts.altKey ?? false,
    shiftKey: opts.shiftKey ?? false,
  } as KeyboardEvent;
}

describe("platform", () => {
  it("isMac returns true for Mac platform", () => {
    vi.stubGlobal("navigator", { platform: "MacIntel" });
    expect(isMac()).toBe(true);
    vi.unstubAllGlobals();
  });

  it("isMac returns false for Windows platform", () => {
    vi.stubGlobal("navigator", { platform: "Win32" });
    expect(isMac()).toBe(false);
    vi.unstubAllGlobals();
  });

  it("getPrimaryModifier returns meta on Mac", () => {
    vi.stubGlobal("navigator", { platform: "MacIntel" });
    expect(getPrimaryModifier()).toBe("meta");
    vi.unstubAllGlobals();
  });

  it("getPrimaryModifier returns ctrl on Windows", () => {
    vi.stubGlobal("navigator", { platform: "Win32" });
    expect(getPrimaryModifier()).toBe("ctrl");
    vi.unstubAllGlobals();
  });
});

describe("formatShortcut", () => {
  it("formats Primary+K on Mac", () => {
    vi.stubGlobal("navigator", { platform: "MacIntel" });
    expect(formatShortcut("Primary+K")).toBe("⌘K");
    vi.unstubAllGlobals();
  });

  it("formats Primary+K on Windows", () => {
    vi.stubGlobal("navigator", { platform: "Win32" });
    expect(formatShortcut("Primary+K")).toBe("Ctrl+K");
    vi.unstubAllGlobals();
  });

  it("formats Escape", () => {
    expect(formatShortcut("Escape")).toBe("Esc");
  });
});

describe("matchShortcut", () => {
  it("matches Primary+K on Mac with metaKey", () => {
    vi.stubGlobal("navigator", { platform: "MacIntel" });
    const event = mockKeyboardEvent("k", { metaKey: true });
    expect(matchShortcut(event, "Primary+K")).toBe(true);
    vi.unstubAllGlobals();
  });

  it("matches Primary+K on Windows with ctrlKey", () => {
    vi.stubGlobal("navigator", { platform: "Win32" });
    const event = mockKeyboardEvent("k", { ctrlKey: true });
    expect(matchShortcut(event, "Primary+K")).toBe(true);
    vi.unstubAllGlobals();
  });

  it("does not match without modifier", () => {
    vi.stubGlobal("navigator", { platform: "MacIntel" });
    const event = mockKeyboardEvent("k");
    expect(matchShortcut(event, "Primary+K")).toBe(false);
    vi.unstubAllGlobals();
  });

  it("does not match with altKey", () => {
    vi.stubGlobal("navigator", { platform: "MacIntel" });
    const event = mockKeyboardEvent("k", { metaKey: true, altKey: true });
    expect(matchShortcut(event, "Primary+K")).toBe(false);
    vi.unstubAllGlobals();
  });

  it("does not match with shiftKey", () => {
    vi.stubGlobal("navigator", { platform: "MacIntel" });
    const event = mockKeyboardEvent("k", { metaKey: true, shiftKey: true });
    expect(matchShortcut(event, "Primary+K")).toBe(false);
    vi.unstubAllGlobals();
  });
});
