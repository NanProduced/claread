/** @vitest-environment jsdom */
/**
 * Math-C 输入预览与 Content Check 一致性 RED 矩阵
 * R-9: $E=mc^2$ / $a*b*c$ / \|A-B\|_F^2 round-trip verbatim
 */
import { act, cleanup, render } from "@testing-library/react";
import React, { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createPlateEditor } from "platejs/react";
import { MarkdownPlugin } from "@platejs/markdown";

import {
  MarkdownTextInput,
  markdownTextInputPlugins,
  type MarkdownTextInputHandle,
} from "@/app/(private)/app/read/MarkdownTextInput";
import { INPUT_MARKDOWN_PLUGIN_OPTIONS } from "@/components/editor/plugins/input-markdown-image-kit";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderMathEditor(props?: { initialValue?: string }) {
  const ref = createRef<MarkdownTextInputHandle>();
  const utils = render(
    <MarkdownTextInput
      ref={ref}
      id="math-test-editor"
      initialValue={props?.initialValue ?? ""}
      onChange={vi.fn()}
      onSubmit={vi.fn()}
    />,
  );
  const editorEl = utils.container.querySelector("#math-test-editor") as HTMLElement;
  return { ref, editorEl, ...utils };
}

function createMathEditor() {
  return createPlateEditor({ plugins: markdownTextInputPlugins });
}

describe("Math-C 输入预览 math 渲染 (plate deserializer + KaTeX)", () => {
  it("$a*b*c$ 在输入预览中渲染为 KaTeX 且保留 * 活性字符", async () => {
    const { ref, editorEl } = renderMathEditor({ initialValue: "before $a*b*c$ after" });
    await act(async () => { await Promise.resolve(); });
    expect(editorEl.querySelector('[data-input-math="true"]')).not.toBeNull();
    expect(editorEl.querySelector(".katex")).not.toBeNull();
    const md = ref.current?.getMarkdown() ?? "";
    expect(md).toContain("$a*b*c$");
    expect(md).not.toContain("$abc$");
  });

  it("\\|A-B\\|_F^2 在 $$ 块中逐字保真且渲染", async () => {
    const md = "$$\n\\|A - B\\|_F^2\n$$";
    const { ref, editorEl } = renderMathEditor({ initialValue: md });
    await act(async () => { await Promise.resolve(); });
    expect(editorEl.querySelector('[data-input-math="true"][data-math-display="true"]')).not.toBeNull();
    expect(editorEl.querySelector(".katex")).not.toBeNull();
    const serialized = ref.current?.getMarkdown() ?? "";
    expect(serialized).toContain("\\|A - B\\|_F^2");
    expect(serialized).not.toContain("|A - B|_F^2");
  });

  it("$E=mc^2$ round-trip 逐字保真", async () => {
    const editor = createMathEditor();
    const original = "Energy is $E=mc^2$ in physics.";
    const blocks = editor.getApi(MarkdownPlugin).markdown.deserialize(original);
    expect(JSON.stringify(blocks)).toContain("inline_equation");
    const serialized = editor.getApi(MarkdownPlugin).markdown.serialize({ value: blocks as never });
    expect(serialized).toContain("$E=mc^2$");
  });

  it("round-trip 矩阵 R-9 三项均保真", async () => {
    const editor = createMathEditor();
    const cases = ["$E=mc^2$", "$a*b*c$"];
    for (const original of cases) {
      const blocks = editor.getApi(MarkdownPlugin).markdown.deserialize(original);
      const serialized = editor.getApi(MarkdownPlugin).markdown.serialize({ value: blocks as never });
      expect(serialized).toContain(original);
      if (original === "$a*b*c$") expect(serialized).not.toContain("$abc$");
    }
    const third = "$$\n\\|A - B\\|_F^2\n$$";
    const blocks3 = editor.getApi(MarkdownPlugin).markdown.deserialize(third);
    const serialized3 = editor.getApi(MarkdownPlugin).markdown.serialize({ value: blocks3 as never });
    expect(serialized3).toContain("\\|A - B\\|_F^2");
  });

  it("编辑后 serialize 仍逐字保真（插入相邻正文不污染 math）", async () => {
    const { ref } = renderMathEditor({ initialValue: "before $a*b*c$ after" });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { ref.current?.setValue("before $a*b*c$ after edited"); });
    const md = ref.current?.getMarkdown() ?? "";
    expect(md).toContain("$a*b*c$");
    expect(md).toContain("edited");
  });

  it("非法 latex fail-closed：不抛错、显示源码占位", async () => {
    const illegal = "$\\frac{$";
    const { editorEl } = renderMathEditor({ initialValue: `hello ${illegal} world` });
    await act(async () => { await Promise.resolve(); });
    const math = editorEl.querySelector('[data-input-math="true"][data-math-state="error"]');
    expect(math).not.toBeNull();
    expect(math?.textContent).toContain("\\frac{");
    expect(editorEl.textContent).toContain("hello");
  });

  it("Content Check 预览与输入预览一致性：同一 markdown 在两者中均渲染为 KaTeX", async () => {
    const input = renderMathEditor({ initialValue: "check $E=mc^2$ end" });
    await act(async () => { await Promise.resolve(); });
    expect(input.editorEl.querySelector('[data-input-math="true"]')).not.toBeNull();
    expect(input.editorEl.querySelector(".katex")).not.toBeNull();
    input.unmount();
    const editor = createMathEditor();
    const blocks = editor.getApi(MarkdownPlugin).markdown.deserialize("check $E=mc^2$ end");
    expect(JSON.stringify(blocks)).toContain("inline_equation");
    const serialized = editor.getApi(MarkdownPlugin).markdown.serialize({ value: blocks as never });
    expect(serialized).toContain("$E=mc^2$");
  });

  it("math-only 编辑器判非空（hasTextContent 覆盖 equation）", async () => {
    const { ref, editorEl } = renderMathEditor({ initialValue: "$x^2$" });
    await act(async () => { await Promise.resolve(); });
    expect(editorEl.getAttribute("data-empty")).toBe("false");
    expect(ref.current?.getMarkdown()).toContain("$x^2$");
  });
});

describe("Math-C deserializer 能力（INPUT_MARKDOWN_PLUGIN_OPTIONS）", () => {
  it("INPUT options 包含 remark-math 与 equation 允许节点", () => {
    expect(INPUT_MARKDOWN_PLUGIN_OPTIONS.allowedNodes).toContain("equation");
    expect(INPUT_MARKDOWN_PLUGIN_OPTIONS.allowedNodes).toContain("inline_equation");
    const editor = createMathEditor();
    const blocks = editor.getApi(MarkdownPlugin).markdown.deserialize("$a*b*c$");
    expect(JSON.stringify(blocks)).toContain("inline_equation");
  });
});
