import { describe, expect, it, vi } from "vitest";

import {
  deserializeMarkdownToBlocks,
  deserializeMarkdownToBlocksWithStatus,
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

  it("preserves bold + inline code children for grammar note patterns", () => {
    // Mirrors the Markdown output contract: **结构名** + `pattern` in a
    // single note string. The deserialized inline must contain both a bold
    // node and a code node so the Plate renderer keeps them as distinct
    // children.
    const md = "这是 **非限制性定语从句**，pattern 为 `prep + which`。";
    const result = deserializeMarkdownInline(md);
    const boldNode = result.find(
      (node) => "bold" in node && (node as { bold?: boolean }).bold === true,
    );
    const codeNode = result.find(
      (node) => "code" in node && (node as { code?: boolean }).code === true,
    );
    expect(boldNode).toBeDefined();
    expect(codeNode).toBeDefined();
    expect((boldNode as { text: string }).text).toBe("非限制性定语从句");
    expect((codeNode as { text: string }).text).toBe("prep + which");
  });
});

describe("deserializeMarkdownToBlocksWithStatus", () => {
  describe("empty status", () => {
    it("returns empty status for empty string", () => {
      const result = deserializeMarkdownToBlocksWithStatus("");
      expect(result.status).toBe("empty");
      expect(result.blocks).toEqual([{ type: "p", children: [{ text: "" }] }]);
      expect(result.error).toBeUndefined();
    });

    it("returns empty status for whitespace-only string", () => {
      const result = deserializeMarkdownToBlocksWithStatus("   \n\n  ");
      expect(result.status).toBe("empty");
      expect(result.blocks).toEqual([{ type: "p", children: [{ text: "" }] }]);
      expect(result.error).toBeUndefined();
    });

    it("returns empty status for null input (optional chaining fallback)", () => {
      // null?.trim() → undefined (falsy), 走 empty 分支而非 degraded。
      // 这是与 deserializeMarkdownToBlocks 旧行为保持一致的兜底。
      const result = deserializeMarkdownToBlocksWithStatus(
        null as unknown as string,
      );
      expect(result.status).toBe("empty");
      expect(result.blocks).toEqual([{ type: "p", children: [{ text: "" }] }]);
      expect(result.error).toBeUndefined();
    });
  });

  describe("success status", () => {
    it("returns success status for plain text", () => {
      const result = deserializeMarkdownToBlocksWithStatus("Hello world");
      expect(result.status).toBe("success");
      expect(result.error).toBeUndefined();
      expect(result.blocks).toHaveLength(1);
      expect(result.blocks[0]).toMatchObject({ type: "p" });
    });

    it("returns success status for heading markdown", () => {
      const result = deserializeMarkdownToBlocksWithStatus("# Heading 1");
      expect(result.status).toBe("success");
      expect(result.error).toBeUndefined();
      expect(result.blocks[0]).toMatchObject({ type: "h1" });
    });

    it("returns success status for unordered list markdown", () => {
      const result = deserializeMarkdownToBlocksWithStatus("- a\n- b");
      expect(result.status).toBe("success");
      expect(result.blocks[0]).toMatchObject({ type: "ul" });
    });

    it("returns success status for mixed markdown", () => {
      const md = "# Title\n\nSome **bold** text.\n\n- Item 1\n- Item 2";
      const result = deserializeMarkdownToBlocksWithStatus(md);
      expect(result.status).toBe("success");
      expect(result.error).toBeUndefined();
      expect(result.blocks.length).toBeGreaterThanOrEqual(3);
      expect(result.blocks[0]).toMatchObject({ type: "h1" });
    });

    it("returns success status for GFM strikethrough", () => {
      const result = deserializeMarkdownToBlocksWithStatus("~~deleted~~");
      expect(result.status).toBe("success");
      const paragraph = result.blocks[0] as {
        type: string;
        children: Array<{ text: string; strikethrough?: boolean }>;
      };
      expect(paragraph.type).toBe("p");
      expect(paragraph.children[0].strikethrough).toBe(true);
    });
  });

  describe("degraded status", () => {
    // remark-gfm 极其宽容，真实输入几乎不会抛异常。为覆盖 catch 分支，
    // 使用 vi.doMock + 动态 import 注入一个会抛异常的 createPlateEditor。
    // vi.doMock 仅影响 doMock 之后动态 import 的模块实例，不影响文件顶部
    // 静态 import 的真实模块，因此不会污染其他 describe 块。
    it("returns degraded status with original markdown as plain text when deserialize throws", async () => {
      // 先 resetModules 清除缓存，再 doMock，动态 import 才会拿到带 mock 的全新模块。
      vi.resetModules();
      vi.doMock("platejs/react", () => ({
        createPlateEditor: () => ({
          getApi: () => ({
            markdown: {
              deserialize: () => {
                throw new Error("mocked deserialize failure");
              },
            },
          }),
          api: {
            markdown: {
              deserializeInline: () => {
                throw new Error("mocked deserializeInline failure");
              },
            },
          },
        }),
      }));

      const { deserializeMarkdownToBlocksWithStatus: degradedDeserialize } =
        await import("./deserialize");

      const original = "### broken";
      const result = degradedDeserialize(original);

      expect(result.status).toBe("degraded");
      expect(result.blocks).toEqual([
        { type: "p", children: [{ text: original }] },
      ]);
      expect(result.error).toBe("mocked deserialize failure");

      vi.doUnmock("platejs/react");
      vi.resetModules();
    });

    it("degraded error string falls back to String(error) for non-Error throws", async () => {
      vi.resetModules();
      vi.doMock("platejs/react", () => ({
        createPlateEditor: () => ({
          getApi: () => ({
            markdown: {
              deserialize: () => {
                // 非 Error 对象：String("string error") → "string error"
                throw "string error";
              },
            },
          }),
          api: {
            markdown: {
              deserializeInline: () => {
                throw "string error";
              },
            },
          },
        }),
      }));

      const { deserializeMarkdownToBlocksWithStatus: degradedDeserialize } =
        await import("./deserialize");

      const result = degradedDeserialize("### broken");

      expect(result.status).toBe("degraded");
      expect(result.error).toBe("string error");

      vi.doUnmock("platejs/react");
      vi.resetModules();
    });
  });
});
