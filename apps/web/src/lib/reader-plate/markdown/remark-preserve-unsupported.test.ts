import { describe, expect, it } from "vitest";
import { MarkdownPlugin } from "@platejs/markdown";
import remarkGfm from "remark-gfm";
import { BaseLinkPlugin } from "@platejs/link";
import { BaseListPlugin } from "@platejs/list-classic";
import { createPlateEditor } from "platejs/react";

import { MarkdownKit } from "@/components/editor/plugins/markdown-kit";
import {
  INPUT_MARKDOWN_PLUGIN_OPTIONS,
  InputMarkdownImagePlugin,
} from "@/components/editor/plugins/input-markdown-image-kit";

import { remarkPreserveUnsupported } from "./remark-preserve-unsupported";

/**
 * 输入端"不静默丢失"合同：
 * - image：不再降级为 link，mdast image 原样通过（typed img 由输入端
 *   Markdown options 的 img 规则接管，见 input-markdown-image-kit）。
 * - footnote / task list / raw html：仍以可见形态降级，不静默丢失。
 */

function createInputEditor() {
  return createPlateEditor({
    plugins: [
      // 与生产输入端一致：MarkdownKit（含 allowedNodes 白名单）+
      // 保留插件追加到 remarkPlugins
      MarkdownPlugin.configure({
        options: {
          remarkPlugins: [remarkGfm, remarkPreserveUnsupported],
          allowedNodes: MarkdownKit[0].options.allowedNodes,
        },
      }),
      BaseListPlugin,
      BaseLinkPlugin,
    ],
    value: [{ type: "p", children: [{ text: "" }] }],
  });
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

describe("remarkPreserveUnsupported", () => {
  it("image 不再降级为 link：mdast image 原样通过，url/alt/title 保留", () => {
    const tree = {
      type: "paragraph",
      children: [
        { type: "text", value: "before " },
        {
          type: "image",
          url: "https://example.com/a.png",
          alt: "a",
          title: "T",
        },
        { type: "text", value: " after" },
      ],
    };
    remarkPreserveUnsupported()(tree as never);
    const image = (tree.children as Array<Record<string, unknown>>)[1];
    expect(image.type).toBe("image");
    expect(image).toMatchObject({
      url: "https://example.com/a.png",
      alt: "a",
      title: "T",
    });
    // 不产生代表该图片的 link 节点，前后文本不动
    expect(JSON.stringify(tree)).not.toContain('"type":"link"');
    expect((tree.children as Array<{ value?: string }>)[0].value).toBe(
      "before ",
    );
    expect((tree.children as Array<{ value?: string }>)[2].value).toBe(
      " after",
    );
  });

  it("image 无 alt 时也原样通过（不注入 URL 文本）", () => {
    const tree = {
      type: "paragraph",
      children: [{ type: "image", url: "https://example.com/x.png", alt: null }],
    };
    remarkPreserveUnsupported()(tree as never);
    const image = (tree.children as Array<Record<string, unknown>>)[0];
    expect(image.type).toBe("image");
    expect(image.alt).toBeNull();
    expect(collectText(tree)).not.toContain("https://example.com/x.png");
  });

  it("footnote 引用与定义保留字面文本", () => {
    const editor = createInputEditor();
    const value = editor
      .getApi(MarkdownPlugin)
      .markdown.deserialize("Body.[^1]\n\n[^1]: The footnote body.\n");
    const text = collectText(value);
    expect(text).toContain("[^1]");
    expect(text).toContain("[^1]: ");
    expect(text).toContain("The footnote body.");
  });

  it("task list 保留列表结构与勾选字面标记", () => {
    const editor = createInputEditor();
    const value = editor
      .getApi(MarkdownPlugin)
      .markdown.deserialize("- [ ] todo one\n- [x] done two\n");
    const json = JSON.stringify(value);
    expect(json).toContain('"type":"ul"');
    expect(json).toContain('"type":"li"');
    const text = collectText(value);
    expect(text).toContain("[ ] todo one");
    expect(text).toContain("[x] done two");
  });

  it("inline raw HTML（vector<T> 的 <T>）保留为字面文本", () => {
    const editor = createInputEditor();
    const value = editor
      .getApi(MarkdownPlugin)
      .markdown.deserialize("Use std::vector<T> and std::unordered_map<K, V> carefully.");
    const text = collectText(value);
    expect(text).toContain("vector<T>");
    expect(text).toContain("unordered_map<K, V>");
  });

  it("序列化后字面信息可读（不静默消失；image 部分由 input-markdown-image-kit 合同覆盖）", () => {
    const editor = createInputEditor();
    const md = "Body.[^1]\n\n[^1]: Note body.\n\n- [ ] todo one\n";
    const value = editor.getApi(MarkdownPlugin).markdown.deserialize(md);
    editor.tf.setValue(value as never[]);
    const out = editor.getApi(MarkdownPlugin).markdown.serialize();
    expect(out).toContain("[^1]");
    expect(out).toContain("Note body.");
    expect(out).toContain("todo one");
    // 勾选标记字面保留（允许 markdown 转义形式 \[ ]）
    expect(out).toMatch(/\[\s*\]\s*todo one/);
  });
});

// ===========================================================================
// G1P-A-R2 · RED 2：reference-style image（两遍解析合同）
//
// 现状（RED）：imageReference 与 definition 都不在 allowedNodes 内，输入端
// 编辑后 serialize 把引用图片整体静默丢掉（R1 review F2）。
//
// 合同：pass 1 收集 definition（identifier 由 parser 统一规范化：小写 +
// 空白折叠，ref/def 两侧一致，直接 Map 匹配；first-wins）；pass 2 把
// resolved imageReference 转成标准 image node（url/title 取 definition，
// alt 取引用），unresolved 降级为可见字面文本。不处理 linkReference。
// ===========================================================================

describe("remarkPreserveUnsupported reference-style image（插件级，G1P-A-R2）", () => {
  it("resolved imageReference → image：url/title 来自 definition，alt 来自引用", () => {
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "imageReference", identifier: "ref", label: "ref", alt: "a" },
          ],
        },
        {
          type: "definition",
          identifier: "ref",
          label: "ref",
          url: "https://example.com/a.png",
          title: "T",
        },
      ],
    };
    remarkPreserveUnsupported()(tree as never);
    const converted = (
      (tree.children as Array<{ children: Array<Record<string, unknown>> }>)[0]
        .children[0]
    );
    expect(converted.type).toBe("image");
    expect(converted.url).toBe("https://example.com/a.png");
    expect(converted.title).toBe("T");
    expect(converted.alt).toBe("a");
  });

  it("duplicate definition first-wins", () => {
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "imageReference", identifier: "ref", label: "ref", alt: "a" },
          ],
        },
        { type: "definition", identifier: "ref", label: "ref", url: "https://first/wins.png" },
        { type: "definition", identifier: "ref", label: "ref", url: "https://second/loses.png" },
      ],
    };
    remarkPreserveUnsupported()(tree as never);
    const converted = (
      (tree.children as Array<{ children: Array<Record<string, unknown>> }>)[0]
        .children[0]
    );
    expect(converted.type).toBe("image");
    expect(converted.url).toBe("https://first/wins.png");
  });

  it("definition 之前/之后的引用都能解析（两遍无序依赖）", () => {
    const tree = {
      type: "root",
      children: [
        { type: "definition", identifier: "ref", label: "ref", url: "https://forward/x.png" },
        {
          type: "paragraph",
          children: [
            { type: "imageReference", identifier: "ref", label: "ref", alt: "a" },
          ],
        },
      ],
    };
    remarkPreserveUnsupported()(tree as never);
    const converted = (
      (tree.children as Array<{ children: Array<Record<string, unknown>> }>)[1]
        .children[0]
    );
    expect(converted.type).toBe("image");
    expect(converted.url).toBe("https://forward/x.png");
  });

  it("identifier 匹配使用 parser 已规范化的形式（大小写/空白折叠后两侧一致）", () => {
    // parser 把 "Re F" 与 "re f" 都规范化为 identifier "re f"——插件不做
    // 二次 case-fold，直接以规范化 identifier 作 Map key。
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "imageReference", identifier: "re f", label: "Re F", alt: "a" },
          ],
        },
        { type: "definition", identifier: "re f", label: "re f", url: "https://example.com/c.png" },
      ],
    };
    remarkPreserveUnsupported()(tree as never);
    const converted = (
      (tree.children as Array<{ children: Array<Record<string, unknown>> }>)[0]
        .children[0]
    );
    expect(converted.type).toBe("image");
    expect(converted.url).toBe("https://example.com/c.png");
  });

  it("unresolved imageReference 降级为可见字面文本（防御分支）", () => {
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "imageReference", identifier: "missing", label: "missing", alt: "a" },
          ],
        },
      ],
    };
    remarkPreserveUnsupported()(tree as never);
    const converted = (
      (tree.children as Array<{ children: Array<Record<string, unknown>> }>)[0]
        .children[0]
    );
    expect(converted.type).toBe("text");
    expect(converted.value).toBe("![a][missing]");
  });

  it("linkReference 不被图片转换逻辑改写", () => {
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "linkReference", identifier: "ref", label: "ref", children: [{ type: "text", value: "text" }] },
          ],
        },
        { type: "definition", identifier: "ref", label: "ref", url: "https://example.com/l.png" },
      ],
    };
    remarkPreserveUnsupported()(tree as never);
    const linkRef = (
      (tree.children as Array<{ children: Array<Record<string, unknown>> }>)[0]
        .children[0]
    );
    expect(linkRef.type).toBe("linkReference");
    // definition 节点本身保持原样（去留由 allowedNodes 消费方决定）
    const def = (tree.children as Array<Record<string, unknown>>)[1];
    expect(def.type).toBe("definition");
  });
});

// ---------------------------------------------------------------------------
// G1P-A-R2 · RED 2（集成）：输入端 options 全链路 round-trip 矩阵
// ---------------------------------------------------------------------------

describe("reference-style image 输入端 round-trip（集成，G1P-A-R2）", () => {
  // 引用图片需要 img 走 typed 表示：使用与生产输入端一致的
  // INPUT_MARKDOWN_PLUGIN_OPTIONS + InputMarkdownImagePlugin（serialize
  // 的 inline img 走 buildMdastNode 的 plugin 解析，生产编辑器同构组成）。
  const inputEditor = createPlateEditor({
    plugins: [
      MarkdownPlugin.configure({
        options: INPUT_MARKDOWN_PLUGIN_OPTIONS,
      }),
      InputMarkdownImagePlugin,
    ],
  });

  const REF_CASES: Array<{ name: string; md: string; expected: string }> = [
    {
      name: "full reference（title 保留）",
      md: '![a][ref]\n\n[ref]: https://example.com/a.png "T"',
      expected: '![a](https://example.com/a.png "T")',
    },
    {
      name: "collapsed reference",
      md: "![a][]\n\n[a]: https://example.com/a.png",
      expected: "![a](https://example.com/a.png)",
    },
    {
      name: "shortcut reference",
      md: "![a]\n\n[a]: https://example.com/a.png",
      expected: "![a](https://example.com/a.png)",
    },
    {
      name: "forward definition（引用在前、定义在后）",
      md: "![a][ref]\n\nBody text.\n\n[ref]: https://example.com/f.png",
      expected: "![a](https://example.com/f.png)\n\nBody text.",
    },
    {
      name: "identifier 大小写与空白折叠（parser 规范化后匹配）",
      md: "![a][Re F]\n\n[re f]: https://example.com/c.png",
      expected: "![a](https://example.com/c.png)",
    },
    {
      name: "duplicate definition first-wins",
      md: "![a][ref]\n\n[ref]: https://first/wins.png\n\n[ref]: https://second/loses.png",
      expected: "![a](https://first/wins.png)",
    },
    {
      name: "empty alt",
      md: "![][ref]\n\n[ref]: https://example.com/e.png",
      expected: "![](https://example.com/e.png)",
    },
    {
      name: "inline 位置（正文中的引用图片）",
      md: "Before ![a][ref] after.\n\n[ref]: https://example.com/i.png",
      expected: "Before ![a](https://example.com/i.png) after.",
    },
    {
      name: "unresolved reference 保留可见字面 Markdown（parser 预解析为文本）",
      md: "![a][missing]",
      // remark-stringify 对字面 [ ] 做 markdown 转义（语法层规范化）；
      // 转义形式 re-parse 后语义仍为字面 ![a][missing]，可见不丢失。
      expected: "!\\[a]\\[missing]",
    },
    {
      name: "unsafe destination 保留原 URL（typed，语义保真）",
      md: "![a][x]\n\n[x]: javascript:alert(1)",
      // remark-stringify 转义 destination 内括号（语法层规范化）；
      // re-parse 后 URL 仍为 javascript:alert(1)，原样保留。
      expected: "![a](javascript:alert\\(1\\))",
    },
  ];

  it.each(REF_CASES)(
    "$name：deserialize→serialize 保持图片语义/alt/destination/title",
    ({ md, expected }) => {
      const blocks = inputEditor
        .getApi(MarkdownPlugin)
        .markdown.deserialize(md);
      inputEditor.tf.setValue(blocks as never[]);
      const out = inputEditor.getApi(MarkdownPlugin).markdown.serialize();
      expect(out.trim()).toBe(expected);
      // 不产生空 paragraph / U+200B 污染
      expect(out).not.toContain("\u200B");
      expect(out).not.toMatch(/\n\n\n/);
    },
  );

  it("ordinary link reference 不被图片转换逻辑改写（隔离）", () => {
    // 现状（本轮之前即如此）：linkReference 不在 allowedNodes 内被丢弃，
    // 这是 pre-existing 行为；本断言锁定图片逻辑不改写它——不合成图片、
    // 不合成 inline link。
    const blocks = inputEditor
      .getApi(MarkdownPlugin)
      .markdown.deserialize("[text][ref]\n\n[ref]: https://example.com/l.png");
    inputEditor.tf.setValue(blocks as never[]);
    const out = inputEditor.getApi(MarkdownPlugin).markdown.serialize();
    expect(out).not.toContain("https://example.com/l.png");
    expect(out).not.toContain("![text]");
    expect(out).not.toContain("[text](");
  });
});
