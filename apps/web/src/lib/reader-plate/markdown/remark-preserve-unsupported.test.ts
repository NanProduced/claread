import { describe, expect, it } from "vitest";
import { MarkdownPlugin } from "@platejs/markdown";
import remarkGfm from "remark-gfm";
import { BaseLinkPlugin } from "@platejs/link";
import { BaseListPlugin } from "@platejs/list-classic";
import { createPlateEditor } from "platejs/react";

import { MarkdownKit } from "@/components/editor/plugins/markdown-kit";

import { remarkPreserveUnsupported } from "./remark-preserve-unsupported";

/**
 * 输入端"不静默丢失"合同：image / footnote / task list 经过
 * remarkPreserveUnsupported 降级后，内容以可见形态进入 Plate，
 * 序列化后字面信息仍可读。
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
  it("image 降级为可见链接节点，url 与 alt 不丢失", () => {
    const editor = createInputEditor();
    const value = editor
      .getApi(MarkdownPlugin)
      .markdown.deserialize("![diagram](https://example.com/d.png)");
    const json = JSON.stringify(value);
    expect(json).toContain('"type":"a"');
    expect(json).toContain("https://example.com/d.png");
    expect(json).toContain("diagram");
  });

  it("image 无 alt 时用 url 作为可见文本", () => {
    const editor = createInputEditor();
    const value = editor
      .getApi(MarkdownPlugin)
      .markdown.deserialize("![](https://example.com/x.png)");
    const text = collectText(value);
    expect(text).toContain("https://example.com/x.png");
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

  it("序列化后字面信息可读（不静默消失）", () => {
    const editor = createInputEditor();
    const md =
      "![diagram](https://example.com/d.png)\n\nBody.[^1]\n\n[^1]: Note body.\n\n- [ ] todo one\n";
    const value = editor.getApi(MarkdownPlugin).markdown.deserialize(md);
    editor.tf.setValue(value as never[]);
    const out = editor.getApi(MarkdownPlugin).markdown.serialize();
    expect(out).toContain("https://example.com/d.png");
    expect(out).toContain("diagram");
    expect(out).toContain("[^1]");
    expect(out).toContain("Note body.");
    expect(out).toContain("todo one");
    // 勾选标记字面保留（允许 markdown 转义形式 \[ ]）
    expect(out).toMatch(/\[\s*\]\s*todo one/);
  });
});
