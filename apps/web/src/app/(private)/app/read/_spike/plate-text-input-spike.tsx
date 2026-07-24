"use client";

/**
 * Spike: Plate WYSIWYG 替换粘贴入口 textarea 的可行性验证
 *
 * 目标：验证以下 5 项 ——
 * 1. 实时渲染：粘贴 Markdown 文本能实时渲染（标题/列表/代码块/表格/引用/链接）
 * 2. 序列化回 Markdown 字符串：editor.getApi(MarkdownPlugin).markdown.serialize()
 * 3. 大文本性能：5000+ 字 Markdown 不卡顿（依赖实际浏览器验证）
 * 4. Cmd/Ctrl+Enter 拦截触发提交
 * 5. 复用 markdown-kit.ts 的 MarkdownPlugin 配置，与 reader-plate 规则一致
 *
 * 边界：spike 只新建此文件，不修改主文件 / markdown-kit / package.json。
 * 不做样式打磨，能用即可。
 *
 * 实现说明：
 * - 复用 `MarkdownKit`（MarkdownPlugin + remarkGfm）做 deserialize/serialize，规则与
 *   reader-plate surface 一致（requirement 5）。
 * - 项目未安装 @platejs/basic-elements / basic-marks，reader-plate 在 reader-blocks-kit
 *   里为标准 markdown 节点注册了薄 component。spike 为保持自包含、不耦合 reader 上下文，
 *   在本文件内为标准 markdown 节点类型注册最小 element/leaf component。parse/serialize
 *   规则仍由 MarkdownKit 统一，渲染组件是独立的最小实现。
 * - 初始值用 deserializeMarkdownToBlocks（内部复用 MarkdownKit）。
 * - 运行时注入用 editor.tf.setValue + editor.getApi(MarkdownPlugin).markdown.deserialize。
 * - 序列化用 editor.getApi(MarkdownPlugin).markdown.serialize()。
 * - Cmd/Ctrl+Enter 通过 Editor 的 onKeyDown 拦截。
 */

import { useState } from "react";
import { MarkdownPlugin } from "@platejs/markdown";
import {
  createPlatePlugin,
  type PlateElementProps,
  type PlateLeafProps,
  Plate,
  PlateContent,
  usePlateEditor,
} from "platejs/react";
import type { Descendant } from "platejs";

import { MarkdownKit } from "@/components/editor/plugins/markdown-kit";
import { deserializeMarkdownToBlocks } from "@/lib/reader-plate/markdown/deserialize";

// ---------------------------------------------------------------------------
// 预设测试文本（含标题/列表/代码块/表格/引用/链接/行内代码/斜体）
// ---------------------------------------------------------------------------

const TEST_MARKDOWN = `### Industry / academic commentary

The recent ruling in *Smith v. Jones (2024)* establishes that:

1. First principle with \`inline code\`
2. Second principle referencing [external source](https://example.com)

\`\`\`python
def example():
    return "code block test"
\`\`\`

| Column A | Column B |
|----------|----------|
| Cell 1   | Cell 2   |
`;

// ---------------------------------------------------------------------------
// 最小 element / leaf component（自包含，不耦合 reader 上下文）
// ---------------------------------------------------------------------------

function SpikeParagraph({ children, attributes }: PlateElementProps) {
  return <p {...attributes}>{children}</p>;
}

function SpikeHeading({ children, element, attributes }: PlateElementProps) {
  const type = (element as { type?: string }).type ?? "h6";
  const Tag = (type as "h1" | "h2" | "h3" | "h4" | "h5" | "h6") ?? "h6";
  const Component = Tag as React.ElementType;
  return <Component {...attributes}>{children}</Component>;
}

function SpikeBlockquote({ children, attributes }: PlateElementProps) {
  return (
    <blockquote {...attributes} className="border-l-2 border-gray-300 pl-3 italic">
      {children}
    </blockquote>
  );
}

function SpikeUnorderedList({ children, attributes }: PlateElementProps) {
  return <ul {...attributes}>{children}</ul>;
}

function SpikeOrderedList({ children, attributes }: PlateElementProps) {
  return <ol {...attributes}>{children}</ol>;
}

function SpikeListItem({ children, attributes }: PlateElementProps) {
  return <li {...attributes}>{children}</li>;
}

function SpikeListContent({ children, attributes }: PlateElementProps) {
  return <span {...attributes}>{children}</span>;
}

function SpikeCodeBlock({ children, attributes }: PlateElementProps) {
  return (
    <pre {...attributes} className="overflow-x-auto rounded bg-gray-100 p-2">
      <code>{children}</code>
    </pre>
  );
}

function SpikeCodeLine({ children, attributes }: PlateElementProps) {
  return <div {...attributes}>{children}</div>;
}

function SpikeHr({ children, attributes }: PlateElementProps) {
  return (
    <div {...attributes}>
      <hr className="my-2 border-gray-300" />
      {children}
    </div>
  );
}

function SpikeTable({ children, attributes }: PlateElementProps) {
  return (
    <table {...attributes} className="border-collapse border border-gray-400">
      <tbody>{children}</tbody>
    </table>
  );
}

function SpikeTableRow({ children, attributes }: PlateElementProps) {
  return <tr {...attributes}>{children}</tr>;
}

function SpikeTableCell({ children, attributes, element }: PlateElementProps) {
  const isHeader = (element as { type?: string }).type === "th";
  if (isHeader) {
    return (
      <th {...attributes} className="border border-gray-400 px-2 py-1 font-semibold">
        {children}
      </th>
    );
  }
  return (
    <td {...attributes} className="border border-gray-400 px-2 py-1">
      {children}
    </td>
  );
}

function SpikeLink({ children, element, attributes }: PlateElementProps) {
  const url = (element as { url?: string }).url ?? "#";
  return (
    <a
      {...attributes}
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-600 underline"
    >
      {children}
    </a>
  );
}

function SpikeBoldLeaf({ children, attributes }: PlateLeafProps) {
  return <strong {...attributes}>{children}</strong>;
}

function SpikeItalicLeaf({ children, attributes }: PlateLeafProps) {
  return <em {...attributes}>{children}</em>;
}

function SpikeCodeLeaf({ children, attributes }: PlateLeafProps) {
  return (
    <code {...attributes} className="rounded bg-gray-100 px-1">
      {children}
    </code>
  );
}

function SpikeStrikethroughLeaf({ children, attributes }: PlateLeafProps) {
  return <span {...attributes} className="line-through">{children}</span>;
}

// ---------------------------------------------------------------------------
// Spike plugins：复用 MarkdownKit + 自包含 element/leaf component
// ---------------------------------------------------------------------------

const spikePlugins = [
  ...MarkdownKit,
  // block elements
  createPlatePlugin({ key: "p", node: { isElement: true, component: SpikeParagraph } }),
  ...(["h1", "h2", "h3", "h4", "h5", "h6"] as const).map((key) =>
    createPlatePlugin({ key, node: { isElement: true, component: SpikeHeading } }),
  ),
  createPlatePlugin({ key: "blockquote", node: { isElement: true, component: SpikeBlockquote } }),
  createPlatePlugin({ key: "ul", node: { isElement: true, component: SpikeUnorderedList } }),
  createPlatePlugin({ key: "ol", node: { isElement: true, component: SpikeOrderedList } }),
  createPlatePlugin({ key: "li", node: { isElement: true, component: SpikeListItem } }),
  createPlatePlugin({ key: "lic", node: { isElement: true, component: SpikeListContent } }),
  createPlatePlugin({ key: "code_block", node: { isElement: true, component: SpikeCodeBlock } }),
  createPlatePlugin({ key: "code_line", node: { isElement: true, component: SpikeCodeLine } }),
  createPlatePlugin({ key: "hr", node: { isElement: true, component: SpikeHr } }),
  createPlatePlugin({ key: "table", node: { isElement: true, component: SpikeTable } }),
  createPlatePlugin({ key: "tr", node: { isElement: true, component: SpikeTableRow } }),
  createPlatePlugin({ key: "td", node: { isElement: true, component: SpikeTableCell } }),
  createPlatePlugin({ key: "th", node: { isElement: true, component: SpikeTableCell } }),
  createPlatePlugin({ key: "a", node: { isElement: true, component: SpikeLink } }),
  // inline marks
  createPlatePlugin({ key: "bold", node: { isLeaf: true, component: SpikeBoldLeaf } }),
  createPlatePlugin({ key: "italic", node: { isLeaf: true, component: SpikeItalicLeaf } }),
  createPlatePlugin({ key: "code", node: { isLeaf: true, component: SpikeCodeLeaf } }),
  createPlatePlugin({ key: "strikethrough", node: { isLeaf: true, component: SpikeStrikethroughLeaf } }),
];

// ---------------------------------------------------------------------------
// Spike 组件
// ---------------------------------------------------------------------------

export interface PlateTextInputSpikeProps {
  initialMarkdown: string;
  onSubmit: (markdown: string) => void;
}

export default function PlateTextInputSpike({
  initialMarkdown,
  onSubmit,
}: PlateTextInputSpikeProps) {
  // 初始值只在挂载时计算一次（deserializeMarkdownToBlocks 内部复用 MarkdownKit）
  const [initialValue] = useState<Descendant[]>(() =>
    deserializeMarkdownToBlocks(initialMarkdown),
  );

  // 显示最近一次序列化结果，方便验证 requirement 2
  const [lastSerialized, setLastSerialized] = useState<string>("");

  const editor = usePlateEditor(
    {
      plugins: spikePlugins,
      value: initialValue as never[],
    },
    [],
  );

  if (!editor) {
    return null;
  }

  // 顶部按钮：注入预设测试文本
  const handleInjectTestText = () => {
    const value = editor.getApi(MarkdownPlugin).markdown.deserialize(TEST_MARKDOWN);
    editor.tf.setValue(value);
  };

  // 提交：序列化为 Markdown 字符串
  const handleSubmit = () => {
    const md = editor.getApi(MarkdownPlugin).markdown.serialize();
    setLastSerialized(md);
    onSubmit(md);
  };

  // 拦截 Cmd/Ctrl+Enter 触发提交
  const handleKeyDown = (event: React.KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-gray-300 p-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleInjectTestText}
          className="rounded border border-gray-300 px-3 py-1 text-sm hover:bg-gray-50"
        >
          粘贴测试文本
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          className="rounded border border-gray-400 bg-gray-100 px-3 py-1 text-sm font-medium hover:bg-gray-200"
        >
          提交（序列化为 Markdown）
        </button>
        <span className="text-xs text-gray-500">Cmd/Ctrl+Enter 也可提交</span>
      </div>

      <Plate editor={editor}>
        <div className="min-h-[200px] border border-gray-200 bg-white">
          <PlateContent
            className="space-y-2 px-3 py-2 text-sm outline-none"
            onKeyDown={handleKeyDown}
          />
        </div>
      </Plate>

      {lastSerialized ? (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs text-gray-500">
            查看最近序列化结果（验证 serialize）
          </summary>
          <pre className="mt-1 max-h-48 overflow-auto rounded bg-gray-50 p-2 text-xs">
            {lastSerialized}
          </pre>
        </details>
      ) : null}
    </div>
  );
}
