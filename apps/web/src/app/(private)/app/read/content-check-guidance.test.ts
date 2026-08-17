/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";

import {
  applyContentCheckAutoFix,
  guidanceForContentCheckCode,
  locateContentCheckExcerpt,
} from "./content-check-guidance";

describe("guidanceForContentCheckCode", () => {
  it("returns specific guidance for real backend codes", () => {
    const fence = guidanceForContentCheckCode("has_unclosed_fence");
    expect(fence.title).toBe("代码块未闭合");
    expect(fence.hasAutoFix).toBe(true);
    expect(fence.tier).toBe("attention");

    const pdfDefault = guidanceForContentCheckCode("source_type_review_default");
    expect(pdfDefault.title).toBe("提取的正文需要过目");
    expect(pdfDefault.suggestion).toBe("提取的文字建议你看一眼再开始阅读");
    expect(pdfDefault.tier).toBe("routine");
    expect(pdfDefault.suggestion).not.toMatch(/警告|出错|失败/);
  });

  it("does not treat stale aliases as known codes", () => {
    for (const stale of [
      "unclosed_fence",
      "footnote_ref",
      "image_content",
      "math_content",
    ]) {
      const guidance = guidanceForContentCheckCode(stale);
      expect(guidance.title).toBe("需要过目的内容");
    }
  });

  it("falls back to generic guidance without technical language", () => {
    const guidance = guidanceForContentCheckCode("some_future_code");
    expect(guidance.title).toBe("需要过目的内容");
    expect(guidance.hasAutoFix).toBe(false);
    expect(guidance.tier).toBe("routine");
    expect(guidance.suggestion).toBe("这部分内容的格式系统拿不准，建议过目");
    expect(guidance.suggestion).not.toMatch(/code|message|classification|FALLBACK/i);
  });
});

describe("locateContentCheckExcerpt", () => {
  it("locates the unclosed fence with following lines", () => {
    const markdown = "# Title\n\n```python\ndef f():\n    pass\n";
    const excerpt = locateContentCheckExcerpt("has_unclosed_fence", markdown);
    expect(excerpt).toContain("```python");
    expect(excerpt).toContain("def f():");
  });

  it("returns null when the fence is actually closed", () => {
    const markdown = "```python\ndef f():\n```\n";
    expect(locateContentCheckExcerpt("has_unclosed_fence", markdown)).toBeNull();
  });

  it("locates the first footnote reference line", () => {
    const markdown = "Some text with a note[^1] here.\n\n[^1]: footnote body\n";
    const excerpt = locateContentCheckExcerpt("footnote_reference", markdown);
    expect(excerpt).toContain("[^1]");
  });

  it("locates the first table row", () => {
    const markdown = "Intro.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n";
    const excerpt = locateContentCheckExcerpt("table_structure_uncertain", markdown);
    expect(excerpt).toContain("| A | B |");
  });

  it("returns null for codes without a locator", () => {
    expect(locateContentCheckExcerpt("code_dominant", "anything")).toBeNull();
  });

  it("clips long excerpts", () => {
    const longLine = `[^x] ${"a".repeat(500)}`;
    const excerpt = locateContentCheckExcerpt("footnote_reference", longLine, 100);
    expect(excerpt).not.toBeNull();
    expect(excerpt!.length).toBeLessThanOrEqual(100);
  });
});

describe("applyContentCheckAutoFix", () => {
  it("appends a closing fence for an unclosed code block", () => {
    const markdown = "```python\ndef f():\n    pass\n";
    const fixed = applyContentCheckAutoFix("has_unclosed_fence", markdown);
    expect(fixed).toBe("```python\ndef f():\n    pass\n```\n");
  });

  it("returns null when nothing needs fixing", () => {
    expect(
      applyContentCheckAutoFix("has_unclosed_fence", "```\ncode\n```\n"),
    ).toBeNull();
  });

  it("returns null for codes without a mechanical fix", () => {
    expect(applyContentCheckAutoFix("footnote_reference", "[^1]")).toBeNull();
  });
});
