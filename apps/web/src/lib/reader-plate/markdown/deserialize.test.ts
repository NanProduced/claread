import { describe, expect, it } from "vitest";

import {
  deserializeMarkdownToBlocks,
  deserializeMarkdownInline,
} from "./deserialize";

describe("deserializeMarkdownToBlocks", () => {
  it("returns empty paragraph for empty string", () => {
    const result = deserializeMarkdownToBlocks("");
    expect(result).toEqual([{ type: "p", children: [{ text: "" }] }]);
  });

  it("returns empty paragraph for whitespace-only string", () => {
    const result = deserializeMarkdownToBlocks("   \n\n  ");
    expect(result).toEqual([{ type: "p", children: [{ text: "" }] }]);
  });

  it("deserializes plain text into a paragraph", () => {
    const result = deserializeMarkdownToBlocks("Hello world");
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ type: "p" });
    const paragraph = result[0] as { type: string; children: Array<{ text: string }> };
    expect(paragraph.children).toHaveLength(1);
    expect(paragraph.children[0].text).toBe("Hello world");
  });

  it("deserializes bold markdown", () => {
    const result = deserializeMarkdownToBlocks("**bold text**");
    expect(result).toHaveLength(1);
    const paragraph = result[0] as {
      type: string;
      children: Array<{ text: string; bold?: boolean }>;
    };
    expect(paragraph.type).toBe("p");
    expect(paragraph.children[0].bold).toBe(true);
    expect(paragraph.children[0].text).toBe("bold text");
  });

  it("deserializes italic markdown", () => {
    const result = deserializeMarkdownToBlocks("*italic text*");
    expect(result).toHaveLength(1);
    const paragraph = result[0] as {
      type: string;
      children: Array<{ text: string; italic?: boolean }>;
    };
    expect(paragraph.type).toBe("p");
    expect(paragraph.children[0].italic).toBe(true);
    expect(paragraph.children[0].text).toBe("italic text");
  });

  it("deserializes inline code markdown", () => {
    const result = deserializeMarkdownToBlocks("`code snippet`");
    expect(result).toHaveLength(1);
    const paragraph = result[0] as {
      type: string;
      children: Array<{ text: string; code?: boolean }>;
    };
    expect(paragraph.type).toBe("p");
    expect(paragraph.children[0].code).toBe(true);
    expect(paragraph.children[0].text).toBe("code snippet");
  });

  it("deserializes heading markdown", () => {
    const result = deserializeMarkdownToBlocks("# Heading 1");
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ type: "h1" });
  });

  it("deserializes blockquote markdown", () => {
    const result = deserializeMarkdownToBlocks("> quoted text");
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ type: "blockquote" });
  });

  it("deserializes unordered list markdown", () => {
    const result = deserializeMarkdownToBlocks("- item 1\n- item 2");
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ type: "ul" });
  });

  it("deserializes ordered list markdown", () => {
    const result = deserializeMarkdownToBlocks("1. first\n2. second");
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ type: "ol" });
  });

  it("deserializes mixed markdown with multiple blocks", () => {
    const md = "# Title\n\nSome **bold** text.\n\n- Item 1\n- Item 2";
    const result = deserializeMarkdownToBlocks(md);
    expect(result.length).toBeGreaterThanOrEqual(3);
    expect(result[0]).toMatchObject({ type: "h1" });
  });

  it("deserializes GFM strikethrough", () => {
    const result = deserializeMarkdownToBlocks("~~deleted~~");
    expect(result).toHaveLength(1);
    const paragraph = result[0] as {
      type: string;
      children: Array<{ text: string; strikethrough?: boolean }>;
    };
    expect(paragraph.type).toBe("p");
    expect(paragraph.children[0].strikethrough).toBe(true);
  });

  it("falls back to plain text paragraph on invalid markdown", () => {
    // Pass a non-string to test fallback (type cast to bypass TS)
    const result = deserializeMarkdownToBlocks(null as unknown as string);
    expect(result).toEqual([
      { type: "p", children: [{ text: "" }] },
    ]);
  });
});

describe("deserializeMarkdownInline", () => {
  it("returns empty text node for empty string", () => {
    const result = deserializeMarkdownInline("");
    expect(result).toEqual([{ text: "" }]);
  });

  it("returns empty text node for whitespace-only string", () => {
    const result = deserializeMarkdownInline("   ");
    expect(result).toEqual([{ text: "" }]);
  });

  it("deserializes inline bold", () => {
    const result = deserializeMarkdownInline("**bold**");
    expect(result.length).toBeGreaterThanOrEqual(1);
    const boldNode = result.find(
      (node) => "bold" in node && (node as { bold?: boolean }).bold === true,
    );
    expect(boldNode).toBeDefined();
  });

  it("deserializes plain text as text node", () => {
    const result = deserializeMarkdownInline("plain text");
    expect(result).toHaveLength(1);
    expect((result[0] as { text: string }).text).toBe("plain text");
  });
});
