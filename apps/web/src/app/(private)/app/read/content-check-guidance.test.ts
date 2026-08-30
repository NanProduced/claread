/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";

import {
  applyContentCheckProposedPatch,
  guidanceForContentCheckCode,
  inspectContentCheckAnchor,
} from "./content-check-guidance";
import type { ReaderContentCheckItemDto } from "@/types/api/reader-plate";

function makeItem(
  overrides: Partial<ReaderContentCheckItemDto> = {},
): ReaderContentCheckItemDto {
  return {
    code: "has_unclosed_fence",
    message: "technical detail",
    classification: "content_check",
    issue_id: "0123456789abcdef",
    tier: "attention",
    target_scope: "range",
    source_anchor: { start_utf16: 1, end_utf16: 6 },
    anchor_hash: "04528a1537daf12ca95144aabdecb6d9deba9d9c65bd2a903afee75328357380",
    evidence: { excerpt_text: "😀中\r\n", proposed_patch: "替换" },
    source_media_coordinate: null,
    ...overrides,
  };
}

describe("guidanceForContentCheckCode", () => {
  it("returns specific guidance for real backend codes", () => {
    const fence = guidanceForContentCheckCode("has_unclosed_fence");
    expect(fence.title).toBe("代码块未闭合");
    expect(fence).not.toHaveProperty("hasAutoFix");
    expect(fence).not.toHaveProperty("tier");

    const pdfDefault = guidanceForContentCheckCode("source_type_review_default");
    expect(pdfDefault.title).toBe("提取的正文需要过目");
    expect(pdfDefault.suggestion).toBe("提取的文字建议你看一眼再开始阅读");
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
    expect(guidance.suggestion).toBe("这部分内容的格式系统拿不准，建议过目");
    expect(guidance.suggestion).not.toMatch(/code|message|classification|FALLBACK/i);
  });
});

describe("structured Content Check anchors", () => {
  it("uses JavaScript UTF-16 offsets exactly for emoji, CJK, and CRLF", async () => {
    const markdown = "A😀中\r\nB";

    await expect(inspectContentCheckAnchor(makeItem(), markdown)).resolves.toEqual({
      status: "valid",
      excerpt: "😀中\r\n",
    });
  });

  it("marks the location changed when the anchored content hash no longer matches", async () => {
    await expect(
      inspectContentCheckAnchor(makeItem(), "A😃中\r\nB"),
    ).resolves.toEqual({ status: "changed", excerpt: null });
  });

  it("fails safely for an out-of-bounds range", async () => {
    const item = makeItem({
      source_anchor: { start_utf16: 1, end_utf16: 99 },
    });

    await expect(inspectContentCheckAnchor(item, "short")).resolves.toEqual({
      status: "changed",
      excerpt: null,
    });
  });

  it("never fabricates a marker for document scope or an unmapped block id", async () => {
    await expect(
      inspectContentCheckAnchor(
        makeItem({
          target_scope: "document",
          source_anchor: null,
          anchor_hash: null,
        }),
        "anything",
      ),
    ).resolves.toEqual({ status: "document", excerpt: null });
    await expect(
      inspectContentCheckAnchor(
        makeItem({ source_anchor: { block_id: "block-1" } }),
        "anything",
      ),
    ).resolves.toEqual({ status: "unavailable", excerpt: null });
  });

  it("applies only a non-empty proposed patch to a hash-valid exact range", async () => {
    const markdown = "A😀中\r\nB";
    await expect(
      applyContentCheckProposedPatch(makeItem(), markdown),
    ).resolves.toBe("A替换B");
    await expect(
      applyContentCheckProposedPatch(
        makeItem({ evidence: { excerpt_text: "😀中\r\n", proposed_patch: "" } }),
        markdown,
      ),
    ).resolves.toBeNull();
    await expect(
      applyContentCheckProposedPatch(makeItem(), "A😃中\r\nB"),
    ).resolves.toBeNull();
  });
});
