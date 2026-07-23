"use client";

/**
 * MarkdownTextInput：粘贴入口的 Plate WYSIWYG 编辑器
 *
 * 用 Plate + MarkdownKit（与 reader-plate 规则一致）替换原生 textarea，
 * 让用户在输入框输入 Markdown 格式文本后可以实时渲染（标题/列表/代码块/
 * 表格/引用/链接/行内代码/斜体/粗体/删除线）。
 *
 * 数据模型：
 * - 编辑器内部维护 Markdown AST（非受控）。
 * - onChange 时通过 MarkdownPlugin.serialize() 序列化为 Markdown 字符串，
 *   回调通知父组件，父组件据此同步 text 状态（用于 isReadyToSubmit、
 *   charCount、detectMarkdownMarkers、提交）。
 * - 外部恢复/清空通过 ref handle 的 setValue/clear 操作 editor
 *   （programmatic，不会触发 onChange，需父组件同步 setText）。
 *
 * 提交：Cmd/Ctrl+Enter 拦截触发 onSubmit 回调。
 *
 * 边界：本组件只负责输入与实时渲染，不做样式打磨（UI/UX 后续交接）。
 * element/leaf component 用最小实现，与 spike 一致。
 */

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
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
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------------------
// 最小 element / leaf component（自包含，不耦合 reader 上下文）
// ---------------------------------------------------------------------------

function MarkdownParagraph({ children, attributes }: PlateElementProps) {
  return <p {...attributes}>{children}</p>;
}

function MarkdownHeading({ children, element, attributes }: PlateElementProps) {
  const type = (element as { type?: string }).type ?? "h6";
  const Component = type as React.ElementType;
  return <Component {...attributes}>{children}</Component>;
}

function MarkdownBlockquote({ children, attributes }: PlateElementProps) {
  return (
    <blockquote {...attributes} className="border-l-2 border-hairline pl-3 italic">
      {children}
    </blockquote>
  );
}

function MarkdownUnorderedList({ children, attributes }: PlateElementProps) {
  return <ul {...attributes}>{children}</ul>;
}

function MarkdownOrderedList({ children, attributes }: PlateElementProps) {
  return <ol {...attributes}>{children}</ol>;
}

function MarkdownListItem({ children, attributes }: PlateElementProps) {
  return <li {...attributes}>{children}</li>;
}

function MarkdownListContent({ children, attributes }: PlateElementProps) {
  return <span {...attributes}>{children}</span>;
}

function MarkdownCodeBlock({ children, attributes }: PlateElementProps) {
  return (
    <pre {...attributes} className="overflow-x-auto rounded bg-surface/60 p-2">
      <code>{children}</code>
    </pre>
  );
}

function MarkdownCodeLine({ children, attributes }: PlateElementProps) {
  return <div {...attributes}>{children}</div>;
}

function MarkdownHr({ children, attributes }: PlateElementProps) {
  return (
    <div {...attributes}>
      <hr className="my-2 border-hairline" />
      {children}
    </div>
  );
}

function MarkdownTable({ children, attributes }: PlateElementProps) {
  return (
    <table {...attributes} className="border-collapse border border-hairline">
      <tbody>{children}</tbody>
    </table>
  );
}

function MarkdownTableRow({ children, attributes }: PlateElementProps) {
  return <tr {...attributes}>{children}</tr>;
}

function MarkdownTableCell({ children, attributes, element }: PlateElementProps) {
  const isHeader = (element as { type?: string }).type === "th";
  if (isHeader) {
    return (
      <th {...attributes} className="border border-hairline px-2 py-1 font-semibold">
        {children}
      </th>
    );
  }
  return (
    <td {...attributes} className="border border-hairline px-2 py-1">
      {children}
    </td>
  );
}

function MarkdownLink({ children, element, attributes }: PlateElementProps) {
  const url = (element as { url?: string }).url ?? "#";
  return (
    <a
      {...attributes}
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-lens-blue underline"
    >
      {children}
    </a>
  );
}

function MarkdownBoldLeaf({ children, attributes }: PlateLeafProps) {
  return <strong {...attributes}>{children}</strong>;
}

function MarkdownItalicLeaf({ children, attributes }: PlateLeafProps) {
  return <em {...attributes}>{children}</em>;
}

function MarkdownCodeLeaf({ children, attributes }: PlateLeafProps) {
  return (
    <code {...attributes} className="rounded bg-surface/60 px-1">
      {children}
    </code>
  );
}

function MarkdownStrikethroughLeaf({ children, attributes }: PlateLeafProps) {
  return <span {...attributes} className="line-through">{children}</span>;
}

// ---------------------------------------------------------------------------
// Plugins：复用 MarkdownKit + 自包含 element/leaf component
// ---------------------------------------------------------------------------

const markdownTextInputPlugins = [
  ...MarkdownKit,
  // block elements
  createPlatePlugin({ key: "p", node: { isElement: true, component: MarkdownParagraph } }),
  ...(["h1", "h2", "h3", "h4", "h5", "h6"] as const).map((key) =>
    createPlatePlugin({ key, node: { isElement: true, component: MarkdownHeading } }),
  ),
  createPlatePlugin({ key: "blockquote", node: { isElement: true, component: MarkdownBlockquote } }),
  createPlatePlugin({ key: "ul", node: { isElement: true, component: MarkdownUnorderedList } }),
  createPlatePlugin({ key: "ol", node: { isElement: true, component: MarkdownOrderedList } }),
  createPlatePlugin({ key: "li", node: { isElement: true, component: MarkdownListItem } }),
  createPlatePlugin({ key: "lic", node: { isElement: true, component: MarkdownListContent } }),
  createPlatePlugin({ key: "code_block", node: { isElement: true, component: MarkdownCodeBlock } }),
  createPlatePlugin({ key: "code_line", node: { isElement: true, component: MarkdownCodeLine } }),
  createPlatePlugin({ key: "hr", node: { isElement: true, component: MarkdownHr } }),
  createPlatePlugin({ key: "table", node: { isElement: true, component: MarkdownTable } }),
  createPlatePlugin({ key: "tr", node: { isElement: true, component: MarkdownTableRow } }),
  createPlatePlugin({ key: "td", node: { isElement: true, component: MarkdownTableCell } }),
  createPlatePlugin({ key: "th", node: { isElement: true, component: MarkdownTableCell } }),
  createPlatePlugin({ key: "a", node: { isElement: true, component: MarkdownLink } }),
  // inline marks
  createPlatePlugin({ key: "bold", node: { isLeaf: true, component: MarkdownBoldLeaf } }),
  createPlatePlugin({ key: "italic", node: { isLeaf: true, component: MarkdownItalicLeaf } }),
  createPlatePlugin({ key: "code", node: { isLeaf: true, component: MarkdownCodeLeaf } }),
  createPlatePlugin({ key: "strikethrough", node: { isLeaf: true, component: MarkdownStrikethroughLeaf } }),
];

// ---------------------------------------------------------------------------
// Ref handle：暴露给父组件用于提交/清空/恢复/聚焦
// ---------------------------------------------------------------------------

export interface MarkdownTextInputHandle {
  /** 序列化当前编辑器内容为 Markdown 字符串 */
  getMarkdown: () => string;
  /** 聚焦编辑器 */
  focus: () => void;
  /** 清空编辑器（programmatic，不触发 onChange） */
  clear: () => void;
  /** 用 Markdown 字符串重置编辑器内容（programmatic，不触发 onChange） */
  setValue: (markdown: string) => void;
}

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------

export interface MarkdownTextInputProps {
  /** 初始 Markdown 文本（仅挂载时使用） */
  initialValue: string;
  /** 内容变化时回调，传回序列化后的 Markdown 字符串 */
  onChange: (markdown: string) => void;
  /** Cmd/Ctrl+Enter 提交回调 */
  onSubmit: () => void;
  placeholder?: string;
  /** 透传给 PlateContent 的 className */
  className?: string;
  id?: string;
}

export const MarkdownTextInput = forwardRef<
  MarkdownTextInputHandle,
  MarkdownTextInputProps
>(function MarkdownTextInput(
  { initialValue, onChange, onSubmit, placeholder, className, id },
  ref,
) {
  const [initialBlocks] = useState<Descendant[]>(() =>
    initialValue ? deserializeMarkdownToBlocks(initialValue) : [],
  );

  const editor = usePlateEditor(
    {
      plugins: markdownTextInputPlugins,
      value: initialBlocks as never[],
    },
    [],
  );

  // 用 ref 持有最新回调，避免 editor 重建。
  // 必须在 effect 中更新 ref（React 19 不允许 render 阶段写 ref.current）。
  const onChangeRef = useRef(onChange);
  const onSubmitRef = useRef(onSubmit);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  useEffect(() => {
    onSubmitRef.current = onSubmit;
  }, [onSubmit]);

  useImperativeHandle(
    ref,
    () => ({
      getMarkdown: () => {
        if (!editor) return "";
        return editor.getApi(MarkdownPlugin).markdown.serialize();
      },
      focus: () => {
        if (!editor) return;
        editor.tf.focus();
      },
      clear: () => {
        if (!editor) return;
        editor.tf.setValue([]);
      },
      setValue: (markdown: string) => {
        if (!editor) return;
        const blocks = markdown ? deserializeMarkdownToBlocks(markdown) : [];
        editor.tf.setValue(blocks as never[]);
      },
    }),
    [editor],
  );

  if (!editor) {
    return null;
  }

  const handleChange = () => {
    const md = editor.getApi(MarkdownPlugin).markdown.serialize();
    onChangeRef.current(md);
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      onSubmitRef.current();
    }
  };

  return (
    <Plate editor={editor}>
      <PlateContent
        id={id}
        className={cn(
          "min-h-0 flex-1 resize-none overflow-y-auto bg-transparent outline-none",
          className,
        )}
        placeholder={placeholder}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
      />
    </Plate>
  );
});
