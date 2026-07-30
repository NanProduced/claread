import { describe, expect, it } from "vitest";

import {
  countEnglishWords,
  countEnglishWordsWithFallback,
  formatApproxWordCount,
} from "./word-count";

describe("countEnglishWords (Intl.Segmenter path)", () => {
  it("counts plain words", () => {
    expect(countEnglishWords("Hello world")).toBe(2);
    expect(countEnglishWords("The quick brown fox jumps over the lazy dog")).toBe(9);
  });

  it("keeps apostrophe contractions as single words", () => {
    expect(countEnglishWords("don't stop believing")).toBe(3);
    expect(countEnglishWords("it's")).toBe(1);
  });

  it("does not count punctuation or whitespace", () => {
    expect(countEnglishWords("Hello, world! ... really?")).toBe(3);
    expect(countEnglishWords("  \n\t  ")).toBe(0);
    expect(countEnglishWords("")).toBe(0);
  });

  it("counts numbers as words", () => {
    expect(countEnglishWords("over 3000 species")).toBe(3);
  });

  it("ignores markdown structure markers", () => {
    expect(countEnglishWords("# Title\n\n- first item\n- second item")).toBe(5);
  });
});

describe("countEnglishWordsWithFallback (regex path)", () => {
  it("matches the segmenter on plain prose", () => {
    expect(countEnglishWordsWithFallback("The quick brown fox")).toBe(4);
  });

  it("treats apostrophes, hyphens and abbreviation dots as word-internal", () => {
    expect(countEnglishWordsWithFallback("don't")).toBe(1);
    expect(countEnglishWordsWithFallback("state-of-the-art")).toBe(1);
    expect(countEnglishWordsWithFallback("e.g.")).toBe(1);
    expect(countEnglishWordsWithFallback("U.S.A.")).toBe(1);
  });

  it("does not count a trailing sentence period", () => {
    expect(countEnglishWordsWithFallback("The end.")).toBe(2);
  });

  it("returns 0 for empty input", () => {
    expect(countEnglishWordsWithFallback("")).toBe(0);
    expect(countEnglishWordsWithFallback("   ")).toBe(0);
  });
});

describe("formatApproxWordCount", () => {
  it("formats with 约 … 词 and zh-CN grouping", () => {
    expect(formatApproxWordCount("word ".repeat(1500).trim())).toBe("约 1,500 词");
  });

  it("returns null for empty text so callers stay silent", () => {
    expect(formatApproxWordCount("")).toBeNull();
    expect(formatApproxWordCount("  \n ")).toBeNull();
  });
});
