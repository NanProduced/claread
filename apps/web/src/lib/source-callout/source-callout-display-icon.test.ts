import { describe, expect, it } from "vitest";

import {
  extractCalloutDisplayIcon,
  isSafeCalloutEmoji,
  normalizeCalloutDisplayIcons,
} from "./source-callout-display-icon";

describe("source callout display icon", () => {
  it.each(["🎯", "💡", "⚠️", "👩‍💻", "🇨🇳"])(
    "accepts one safe emoji grapheme: %s",
    (value) => {
      expect(isSafeCalloutEmoji(value)).toBe(true);
    },
  );

  it.each(["🎯 body", "https://example.com", "C:\\icon.png", "red"])(
    "rejects non-emoji icon values: %s",
    (value) => {
      expect(isSafeCalloutEmoji(value)).toBe(false);
    },
  );

  it("promotes the first icon-only paragraph and removes it from the body", () => {
    const result = extractCalloutDisplayIcon([
      { type: "p", children: [{ text: "🎯" }] },
      { type: "p", children: [{ text: "Body" }] },
    ]);

    expect(result.displayIcon).toBe("🎯");
    expect(result.children).toEqual([
      { type: "p", children: [{ text: "Body" }] },
    ]);
  });

  it("normalizes nested source callouts without duplicating the icon", () => {
    const result = normalizeCalloutDisplayIcons([
      {
        type: "source_callout",
        children: [
          { type: "p", children: [{ text: "🎯" }] },
          { type: "p", children: [{ text: "Body" }] },
        ],
      },
    ]);

    expect(result).toEqual([
      {
        type: "source_callout",
        displayIcon: "🎯",
        children: [{ type: "p", children: [{ text: "Body" }] }],
      },
    ]);
  });
});
