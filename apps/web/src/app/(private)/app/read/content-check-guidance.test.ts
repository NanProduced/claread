/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";

import {
  applyContentCheckAutoFix,
  guidanceForContentCheckCode,
  locateContentCheckExcerpt,
} from "./content-check-guidance";

describe("guidanceForContentCheckCode", () => {
  it("returns specific guidance for known codes", () => {
    const guidance = guidanceForContentCheckCode("unclosed_fence");
    expect(guidance.title).toBe("代码块未闭合");
    expect(guidance.hasAutoFix).toBe(true);
  });

  it("falls back to generic guidance for unknown codes", () => {
    const guidance = guidanceForContentCheckCode("some_future_code");
    expect(guidance.title).toBe("需要你确认的位置");
    expect(guidance.hasAutoFix).toBe(false);
    expect(guidance.suggestion.length).toBeGreaterThan(0);
  });
});

describe("locateContentCheckExcerpt", () => {
  it("locates the unclosed fence with following lines", () => {
    const markdown = "# Title\n\n```python\ndef f():\n    pass\n";
    const excerpt = locateContentCheckExcerpt("unclosed_fence", markdown);
    expect(excerpt).toContain("```python");
    expect(excerpt).toContain("def f():");
  });

  it("returns null when the fence is actually closed", () => {
    const markdown = "```python\ndef f():\n```\n";
    expect(locateContentCheckExcerpt("unclosed_fence", markdown)).toBeNull();
  });

  it("locates the first footnote reference line", () => {
    const markdown = "Some text with a note[^1] here.\n\n[^1]: footnote body\n";
    const excerpt = locateContentCheckExcerpt("footnote_ref", markdown);
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
    const excerpt = locateContentCheckExcerpt("footnote_ref", longLine, 100);
    expect(excerpt).not.toBeNull();
    expect(excerpt!.length).toBeLessThanOrEqual(100);
  });
});

describe("applyContentCheckAutoFix", () => {
  it("appends a closing fence for an unclosed code block", () => {
    const markdown = "```python\ndef f():\n    pass\n";
    const fixed = applyContentCheckAutoFix("unclosed_fence", markdown);
    expect(fixed).toBe("```python\ndef f():\n    pass\n```\n");
  });

  it("returns null when nothing needs fixing", () => {
    expect(
      applyContentCheckAutoFix("unclosed_fence", "```\ncode\n```\n"),
    ).toBeNull();
  });

  it("returns null for codes without a mechanical fix", () => {
    expect(applyContentCheckAutoFix("footnote_ref", "[^1]")).toBeNull();
  });
});
