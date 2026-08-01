/**
 * Source Callout round-trip 测试 — 序列化/反序列化循环。
 *
 * 覆盖任务要求：
 * - 纯 Markdown `<aside>` → deserialize → `{type:"source_callout"}` → serialize → `<aside>` (round-trip)
 * - GFM alert `> [!NOTE]` → deserialize → `{type:"source_callout"}` → serialize → `<aside>` (归一化)
 * - `<aside class="callout-warning">` → deserialize → `{type:"source_callout",kind:"warning"}` → serialize → `<aside>` (canonical, kind 不写入)
 * - callout 内基础 inline 格式（粗体/斜体/链接）保留
 * - 转义 `\<aside>` 不被反序列化为 source_callout
 *
 * 测试使用与生产输入端一致的 editor 配置：MarkdownKit + remarkPreserveUnsupported。
 * SourceCalloutPlugin（component 注册）不影响 serialize/deserialize 逻辑，
 * 后者完全由 MARKDOWN_PLUGIN_OPTIONS.rules 驱动。
 */
import { describe, expect, it } from "vitest";
import { MarkdownPlugin } from "@platejs/markdown";
import { BaseLinkPlugin } from "@platejs/link";
import { BaseListPlugin } from "@platejs/list-classic";
import { createPlateEditor } from "platejs/react";

import { MARKDOWN_PLUGIN_OPTIONS } from "@/components/editor/plugins/markdown-kit";
import { remarkPreserveUnsupported } from "@/lib/reader-plate/markdown/remark-preserve-unsupported";

function createInputEditor() {
  return createPlateEditor({
    plugins: [
      MarkdownPlugin.configure({
        options: {
          ...MARKDOWN_PLUGIN_OPTIONS,
          // 复用 MARKDOWN_PLUGIN_OPTIONS.remarkPlugins（含 remarkMergeAsideHtml）
          // 并追加输入端专用 remarkPreserveUnsupported。
          remarkPlugins: [
            ...MARKDOWN_PLUGIN_OPTIONS.remarkPlugins,
            remarkPreserveUnsupported,
          ],
        },
      }),
      BaseListPlugin,
      BaseLinkPlugin,
    ],
    value: [{ type: "p", children: [{ text: "" }] }],
  });
}

function deserialize(editor: ReturnType<typeof createPlateEditor>, md: string) {
  return editor.getApi(MarkdownPlugin).markdown.deserialize(md);
}

function serialize(editor: ReturnType<typeof createPlateEditor>) {
  return editor.getApi(MarkdownPlugin).markdown.serialize();
}

function roundTrip(md: string): { blocks: unknown[]; out: string } {
  const editor = createInputEditor();
  const blocks = deserialize(editor, md);
  editor.tf.setValue(blocks as never[]);
  const out = serialize(editor);
  return { blocks, out };
}

function findSourceCallout(blocks: unknown[]): Record<string, unknown> | null {
  for (const b of blocks) {
    const node = b as Record<string, unknown>;
    if (node.type === "source_callout") return node;
    if (Array.isArray(node.children)) {
      const found = findSourceCallout(node.children as unknown[]);
      if (found) return found;
    }
  }
  return null;
}

function collectText(nodes: unknown): string {
  let out = "";
  const walk = (ns: unknown) => {
    if (!Array.isArray(ns)) return;
    for (const n of ns as Record<string, unknown>[]) {
      if (typeof n.text === "string") out += n.text;
      if (n.children) walk(n.children);
    }
  };
  walk(nodes);
  return out;
}

describe("source_callout round-trip", () => {
  it("纯 Markdown <aside> → source_callout → <aside> (round-trip)", () => {
    const md = "<aside>\nThis is a callout\n</aside>";
    const { blocks, out } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout, "deserialize must produce source_callout element").not.toBeNull();
    expect(callout?.type).toBe("source_callout");

    // 序列化回 canonical <aside> 表达
    expect(out).toContain("<aside>");
    expect(out).toContain("</aside>");
    expect(out).toContain("This is a callout");
    // 不应出现可见的 [!NOTE] marker
    expect(out).not.toContain("[!NOTE]");
  });

  it("promotes one safe emoji paragraph to displayIcon without duplicating it", () => {
    const md = "<aside>\n🎯\n\n**Alignment**: body\n</aside>";
    const { blocks, out } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout).toMatchObject({ displayIcon: "🎯" });
    expect(JSON.stringify(callout?.children)).not.toContain("🎯");
    expect(out.match(/🎯/gu)).toHaveLength(1);
    expect(out).toContain("**Alignment**: body");
  });

  it("GFM alert > [!NOTE] → source_callout → <aside> (归一化)", () => {
    const md = "> [!NOTE]\n> This is a note callout";
    const { blocks, out } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout, "GFM alert must deserialize to source_callout").not.toBeNull();
    expect(callout?.type).toBe("source_callout");
    expect(callout?.kind).toBe("note");

    // 序列化为 canonical <aside>（不是 GFM alert marker）
    expect(out).toContain("<aside>");
    expect(out).toContain("</aside>");
    expect(out).toContain("This is a note callout");
    // GFM marker 不得出现在序列化结果中
    expect(out).not.toContain("[!NOTE]");
  });

  it("GFM alert > [!WARNING] → source_callout kind=note (R-Aside-1R B: unified)", () => {
    // R-Aside-1R B: kind 统一为 note。GFM [!WARNING] 不再驱动视觉差异，
    // 因为 kind 无法安全持久化到 Stable Document / Reader reload。
    const md = "> [!WARNING]\n> Be careful with this approach";
    const { blocks, out } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();
    expect(callout?.kind).toBe("note");

    expect(out).toContain("<aside>");
    expect(out).toContain("Be careful with this approach");
    expect(out).not.toContain("[!WARNING]");
  });

  it('<aside class="callout-warning"> → source_callout kind=note (R-Aside-1R B: unified)', () => {
    // R-Aside-1R B: kind 统一为 note。class 属性不再驱动 kind 推断，
    // canonical <aside> 不携带 class，所有 source_callout 均为 note。
    const md = '<aside class="callout-warning">\nCareful\n</aside>';
    const { blocks, out } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();
    expect(callout?.kind).toBe("note");

    // canonical 表达不携带 class 属性
    expect(out).toContain("<aside>");
    expect(out).not.toContain("callout-warning");
    expect(out).not.toContain('class=');
    expect(out).toContain("Careful");
  });

  it("callout 内粗体/斜体格式保留", () => {
    const md = "<aside>\n**bold** and *italic* text\n</aside>";
    const { blocks, out } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();
    // children 中应有 bold/italic mark
    const json = JSON.stringify(callout);
    expect(json).toContain('"bold":true');
    expect(json).toContain('"italic":true');

    // 序列化后格式保留
    expect(out).toContain("**bold**");
    expect(out).toContain("*italic*");
  });

  it("callout 内链接保留", () => {
    const md = "<aside>\nSee [docs](https://example.com) for details\n</aside>";
    const { blocks, out } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();
    const json = JSON.stringify(callout);
    expect(json).toContain('"type":"a"');
    expect(json).toContain("https://example.com");

    expect(out).toContain("[docs](https://example.com)");
  });

  it("callout 内多段落保留", () => {
    const md = "<aside>\nFirst paragraph.\n\nSecond paragraph.\n</aside>";
    const { blocks, out } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();
    // callout children 应有多个段落
    const children = callout?.children as unknown[];
    expect(Array.isArray(children)).toBe(true);
    expect(children.length).toBeGreaterThanOrEqual(2);

    expect(out).toContain("First paragraph.");
    expect(out).toContain("Second paragraph.");
  });

  it("转义 \\<aside> 不被反序列化为 source_callout", () => {
    const md = "\\<aside>This is literal\\</aside>";
    const { blocks } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout, "escaped \\<aside> must NOT become source_callout").toBeNull();

    // 字面文本保留
    const text = collectText(blocks);
    expect(text).toContain("<aside>");
    expect(text).toContain("</aside>");
  });

  it("不完整 <aside>（无闭合）不被反序列化为 source_callout", () => {
    const md = "<aside>No closing tag";
    const { blocks } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout, "incomplete <aside> must NOT become source_callout").toBeNull();
  });

  it("普通 <div> 不被反序列化为 source_callout", () => {
    const md = "<div>Just a div</div>";
    const { blocks } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout, "<div> must NOT become source_callout").toBeNull();
  });

  it("<script> 不被反序列化为 source_callout", () => {
    const md = "<script>alert(1)</script>";
    const { blocks } = roundTrip(md);

    const callout = findSourceCallout(blocks);
    expect(callout, "<script> must NOT become source_callout").toBeNull();
  });

  it("canonical <aside> round-trip 稳定（多次循环不变）", () => {
    const canonical = "<aside>\nStable content\n</aside>";
    const first = roundTrip(canonical);
    const second = roundTrip(first.out);

    expect(second.out).toBe(first.out);
    expect(second.out).toContain("<aside>");
    expect(second.out).toContain("Stable content");
    expect(second.out).toContain("</aside>");
  });
});

// ---------------------------------------------------------------------------
// R-Aside-1R: 结构保真与安全验收
// ---------------------------------------------------------------------------

describe("R-Aside-1R A1: trailing text after </aside> becomes separate paragraph", () => {
  it("</aside> 后紧接正文 → callout + 独立段落（不吞入 callout）", () => {
    const md = "<aside>\nCallout body\n</aside>Peer discussion continues";
    const editor = createInputEditor();
    const blocks = deserialize(editor, md);

    // 找到 source_callout
    const callout = findSourceCallout(blocks);
    expect(callout, "must produce source_callout element").not.toBeNull();

    // callout 内部文本只包含 callout body，不含 trailing
    const calloutText = collectText(callout?.children);
    expect(calloutText).toContain("Callout body");
    expect(calloutText).not.toContain("Peer discussion");

    // 必须有一个独立段落包含 trailing text
    const allText = collectText(blocks);
    expect(allText).toContain("Peer discussion continues");

    // trailing 段落不能是 source_callout 的子节点
    const calloutChildren = (callout?.children as unknown[]) ?? [];
    const trailingInCallout = calloutChildren.some((child) => {
      const node = child as Record<string, unknown>;
      return (
        typeof node.text === "string" &&
        node.text.includes("Peer discussion")
      );
    });
    expect(
      trailingInCallout,
      "trailing text must NOT be inside source_callout children",
    ).toBe(false);
  });

  it("多行 aside + trailing text on closing line → callout + 独立段落", () => {
    const md =
      "<aside>\n**Alignment**: body text.\n\nSecond paragraph.\n</aside>Peer discussion";
    const editor = createInputEditor();
    const blocks = deserialize(editor, md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();

    const calloutText = collectText(callout?.children);
    expect(calloutText).toContain("body text");
    expect(calloutText).toContain("Second paragraph");
    expect(calloutText).not.toContain("Peer discussion");

    const allText = collectText(blocks);
    expect(allText).toContain("Peer discussion");
  });
});

describe("R-Aside-1R A2: internal marks/structure preserved", () => {
  it("多段落 + strong/em 保留", () => {
    const md =
      "<aside>\n**Alignment**: strong text.\n\n*Note*: italic text.\n</aside>";
    const editor = createInputEditor();
    const blocks = deserialize(editor, md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();
    const json = JSON.stringify(callout);
    expect(json).toContain('"bold":true');
    expect(json).toContain('"italic":true');

    // 多段落
    const children = callout?.children as unknown[];
    expect(Array.isArray(children)).toBe(true);
    expect(children.length).toBeGreaterThanOrEqual(2);
  });

  it("列表保留", () => {
    const md = "<aside>\n- Item 1\n- Item 2\n- Item 3\n</aside>";
    const editor = createInputEditor();
    const blocks = deserialize(editor, md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();
    const json = JSON.stringify(callout);
    // 列表节点存在（BaseListPlugin 使用 ul/ol 类型，非 mdast 的 list）
    expect(json).toContain('"type":"ul"');
    expect(json).toContain('"type":"li"');
    expect(json).toContain("Item 1");
    expect(json).toContain("Item 2");
    expect(json).toContain("Item 3");
  });

  it("链接保留", () => {
    const md = "<aside>\nSee [docs](https://example.com) here\n</aside>";
    const editor = createInputEditor();
    const blocks = deserialize(editor, md);

    const callout = findSourceCallout(blocks);
    expect(callout).not.toBeNull();
    const json = JSON.stringify(callout);
    expect(json).toContain('"type":"a"');
    expect(json).toContain("https://example.com");
  });
});

describe("R-Aside-1R C: serializer does not modify live editor state", () => {
  it("调用 serialize 前后 editor.children / selection 不变", () => {
    const editor = createInputEditor();
    const md = "<aside>\n**bold** content\n</aside>";
    const blocks = deserialize(editor, md);
    editor.tf.setValue(blocks as never[]);

    // 捕获 serialize 前的状态
    const childrenBefore = JSON.stringify(editor.children);
    const selectionBefore = JSON.stringify(editor.selection ?? null);

    // 执行 serialize（触发 source_callout serializer）
    const out = serialize(editor);

    // 捕获 serialize 后的状态
    const childrenAfter = JSON.stringify(editor.children);
    const selectionAfter = JSON.stringify(editor.selection ?? null);

    // editor.children 不变
    expect(childrenAfter).toBe(childrenBefore);
    // selection 不变
    expect(selectionAfter).toBe(selectionBefore);

    // 序列化结果正确
    expect(out).toContain("<aside>");
    expect(out).toContain("**bold**");
    expect(out).toContain("</aside>");
  });

  it("多次调用 serialize 结果稳定（幂等）", () => {
    const editor = createInputEditor();
    const md = "<aside>\n**bold** and *italic*\n</aside>";
    const blocks = deserialize(editor, md);
    editor.tf.setValue(blocks as never[]);

    const first = serialize(editor);
    const second = serialize(editor);
    const third = serialize(editor);

    expect(second).toBe(first);
    expect(third).toBe(first);
  });

  it("多个 callout canonical serialize/deserialize 多轮稳定且 icon 不重复", () => {
    const md = [
      "<aside>",
      "🎯",
      "",
      "**First** body",
      "</aside>",
      "",
      "Between paragraphs.",
      "",
      "<aside>",
      "⚠️",
      "",
      "*Second* body",
      "</aside>",
    ].join("\n");
    const first = roundTrip(md);
    const second = roundTrip(first.out);
    const callouts = (second.blocks as Array<Record<string, unknown>>).filter(
      (node) => node.type === "source_callout",
    );

    expect(callouts).toHaveLength(2);
    expect(callouts.map((node) => node.displayIcon)).toEqual(["🎯", "⚠️"]);
    expect(second.out).toBe(first.out);
    expect(second.out.match(/🎯/gu)).toHaveLength(1);
    expect(second.out.match(/⚠️/gu)).toHaveLength(1);
  });
});

describe("R-Aside-1R B: kind unified to note across all input paths", () => {
  it("纯 Markdown <aside> → kind=note", () => {
    const md = "<aside>\nbody\n</aside>";
    const { blocks } = roundTrip(md);
    const callout = findSourceCallout(blocks);
    expect(callout?.kind).toBe("note");
  });

  it("GFM alert [!TIP] → kind=note (not tip)", () => {
    const md = "> [!TIP]\n> helpful hint";
    const { blocks } = roundTrip(md);
    const callout = findSourceCallout(blocks);
    expect(callout?.kind).toBe("note");
  });

  it("GFM alert [!IMPORTANT] → kind=note (not important)", () => {
    const md = "> [!IMPORTANT]\n> critical info";
    const { blocks } = roundTrip(md);
    const callout = findSourceCallout(blocks);
    expect(callout?.kind).toBe("note");
  });

  it('<aside class="callout-tip"> → kind=note (not tip)', () => {
    const md = '<aside class="callout-tip">\ntip body\n</aside>';
    const { blocks } = roundTrip(md);
    const callout = findSourceCallout(blocks);
    expect(callout?.kind).toBe("note");
  });
});

describe("R-Aside-1R safety: dangerous attributes / tags", () => {
  it("aside with onclick/style/data-* → matched but attrs not in canonical", () => {
    const md =
      '<aside onclick="alert(1)" style="color:red" data-track="evil">\nsafe body\n</aside>';
    const { out } = roundTrip(md);

    // canonical 输出只有裸 <aside>
    expect(out).toContain("<aside>");
    expect(out).not.toContain("onclick");
    expect(out).not.toContain("style");
    expect(out).not.toContain("data-track");
    expect(out).toContain("safe body");
  });

  it("aside with href/src → attrs not in canonical", () => {
    const md =
      '<aside href="evil.com" src="x.js">\nbody\n</aside>';
    const { out } = roundTrip(md);
    expect(out).not.toContain("href");
    expect(out).not.toContain("src");
  });

  it("<script> 不被反序列化为 source_callout（已有测试复述）", () => {
    const md = "<script>alert(1)</script>";
    const { blocks } = roundTrip(md);
    expect(findSourceCallout(blocks)).toBeNull();
  });

  it("<iframe> 不被反序列化为 source_callout", () => {
    const md = '<iframe src="evil"></iframe>';
    const { blocks } = roundTrip(md);
    expect(findSourceCallout(blocks)).toBeNull();
  });

  it("未闭合 <aside> 不被反序列化为 source_callout", () => {
    const md = "<aside>No closing";
    const { blocks } = roundTrip(md);
    expect(findSourceCallout(blocks)).toBeNull();
  });

  it("转义 \\<aside> 保持字面文本", () => {
    const md = "\\<aside>literal\\</aside>";
    const { blocks } = roundTrip(md);
    expect(findSourceCallout(blocks)).toBeNull();
    const text = collectText(blocks);
    expect(text).toContain("<aside>");
    expect(text).toContain("</aside>");
  });
});
