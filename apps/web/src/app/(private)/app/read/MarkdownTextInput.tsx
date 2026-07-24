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
 * 样式策略（参考 Plate.js 官方 @plate/editor + 项目 reader-blocks-kit）：
 * - 直接使用 PlateContent（ESLint 规则限制 src/app/** 不能 import
 *   @/components/ui/*），把 @plate/editor Editor 的关键 className
 *   （whitespace-pre-wrap break-words outline-none [&_strong]:font-bold）
 *   内联到 PlateContent，解决"无换行"问题。
 * - element/leaf component 用自包含 Tailwind utility class，显式给出
 *   字号/间距/list marker/等宽字体等（解决"无字体大小"问题）。
 *   不依赖 reader 的 --reader-record-note-* CSS 变量（输入页未定义）。
 *
 * 边界：本组件只负责输入与实时渲染，不做样式打磨（UI/UX 后续交接）。
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
import {
  lintMarkdownInput,
  type MarkdownLintResult,
} from "./markdown-lint";

// ---------------------------------------------------------------------------
// 最小 element / leaf component（自包含，不耦合 reader 上下文）
// 显式 Tailwind utility class，不依赖 reader CSS 变量。
// ---------------------------------------------------------------------------

function MarkdownParagraph({ children, attributes }: PlateElementProps) {
  return (
    <p {...attributes} className="my-2 leading-relaxed">
      {children}
    </p>
  );
}

function MarkdownHeading({ children, element, attributes }: PlateElementProps) {
  const type = (element as { type?: string }).type ?? "h6";
  const sizeClass: Record<string, string> = {
    h1: "text-2xl font-bold mt-6 mb-2 leading-tight",
    h2: "text-xl font-bold mt-5 mb-2 leading-tight",
    h3: "text-lg font-semibold mt-4 mb-2 leading-snug",
    h4: "text-base font-semibold mt-3 mb-1 leading-snug",
    h5: "text-sm font-semibold mt-3 mb-1 leading-snug",
    h6: "text-sm font-semibold mt-3 mb-1 leading-snug uppercase tracking-wide",
  };
  const className = sizeClass[type] ?? sizeClass.h6;
  const Component = type as React.ElementType;
  return (
    <Component {...attributes} className={className}>
      {children}
    </Component>
  );
}

function MarkdownBlockquote({ children, attributes }: PlateElementProps) {
  return (
    <blockquote
      {...attributes}
      className="my-3 border-l-4 border-hairline pl-4 italic text-ink-soft"
    >
      {children}
    </blockquote>
  );
}

function MarkdownUnorderedList({ children, attributes }: PlateElementProps) {
  return (
    <ul {...attributes} className="my-2 list-disc pl-6 leading-relaxed">
      {children}
    </ul>
  );
}

function MarkdownOrderedList({ children, attributes }: PlateElementProps) {
  return (
    <ol {...attributes} className="my-2 list-decimal pl-6 leading-relaxed">
      {children}
    </ol>
  );
}

function MarkdownListItem({ children, attributes }: PlateElementProps) {
  return <li {...attributes} className="my-1 pl-1">{children}</li>;
}

function MarkdownListContent({ children, attributes }: PlateElementProps) {
  return <span {...attributes}>{children}</span>;
}

function MarkdownCodeBlock({ children, attributes }: PlateElementProps) {
  return (
    <pre
      {...attributes}
      className="my-3 overflow-x-auto rounded-md bg-surface/60 p-3 font-mono text-sm leading-relaxed"
    >
      <code>{children}</code>
    </pre>
  );
}

function MarkdownCodeLine({ children, attributes }: PlateElementProps) {
  return <div {...attributes}>{children}</div>;
}

function MarkdownHr({ children, attributes }: PlateElementProps) {
  return (
    <div {...attributes} className="my-4">
      <hr className="border-hairline" />
      {children}
    </div>
  );
}

function MarkdownTable({ children, attributes }: PlateElementProps) {
  return (
    <table
      {...attributes}
      className="my-3 w-full border-collapse border border-hairline text-sm"
    >
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
      <th
        {...attributes}
        className="border border-hairline bg-surface/40 px-3 py-1.5 text-left font-semibold"
      >
        {children}
      </th>
    );
  }
  return (
    <td {...attributes} className="border border-hairline px-3 py-1.5 text-left">
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
      className="text-lens-blue underline underline-offset-2"
    >
      {children}
    </a>
  );
}

function MarkdownBoldLeaf({ children, attributes }: PlateLeafProps) {
  return <strong {...attributes} className="font-semibold">{children}</strong>;
}

function MarkdownItalicLeaf({ children, attributes }: PlateLeafProps) {
  return <em {...attributes} className="italic">{children}</em>;
}

function MarkdownCodeLeaf({ children, attributes }: PlateLeafProps) {
  return (
    <code
      {...attributes}
      className="rounded bg-surface/60 px-1 py-0.5 font-mono text-[0.9em]"
    >
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
  /**
   * 输入端 lint 结果回调（Phase 1 / P0）。
   *
   * 每次 onChange 时同步触发，父组件据此显示警告 badge。
   * lint 是预警不阻塞，后端仍是 fail-closed 单一真相源。
   */
  onLintResult?: (result: MarkdownLintResult) => void;
  placeholder?: string;
  /** 透传给 Editor 的 className */
  className?: string;
  id?: string;
}

export const MarkdownTextInput = forwardRef<
  MarkdownTextInputHandle,
  MarkdownTextInputProps
>(function MarkdownTextInput(
  { initialValue, onChange, onSubmit, onLintResult, placeholder, className, id },
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
  const onLintResultRef = useRef(onLintResult);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  useEffect(() => {
    onSubmitRef.current = onSubmit;
  }, [onSubmit]);
  useEffect(() => {
    onLintResultRef.current = onLintResult;
  }, [onLintResult]);

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
    // Phase 1 / P0: 输入端预警 lint（非阻塞，后端仍是 fail-closed 单一真相源）
    const lintCallback = onLintResultRef.current;
    if (lintCallback) {
      lintCallback(lintMarkdownInput(md));
    }
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
          "min-h-0 flex-1 resize-none overflow-y-auto bg-transparent",
          "whitespace-pre-wrap break-words outline-none",
          "[&_strong]:font-bold",
          className,
        )}
        placeholder={placeholder}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
      />
    </Plate>
  );
});
