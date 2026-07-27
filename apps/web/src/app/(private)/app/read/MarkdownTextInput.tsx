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
 * - R1：value lifecycle 由 `<Plate onChange>`（editor 级、同步）统一驱动。
 *   不再使用 PlateContent 的 DOM onChange —— Slate 拦截所有 beforeinput
 *   （insertText / insertFromPaste）并 preventDefault，React 合成 change
 *   事件从不触发，旧实现导致用户输入/粘贴后父状态永远为空（placeholder
 *   覆盖内容、CTA 未就绪）。editor 级 onChange 覆盖键入、粘贴与程序化
 *   setValue/clear；仅 selection 变化（value 引用不变）直接跳过序列化。
 *   序列化结果去重后回调父组件，父组件据此同步 text 状态（用于
 *   isReadyToSubmit、charCount、detectMarkdownMarkers、提交）。
 * - 外部恢复/清空通过 ref handle 的 setValue/clear 操作 editor；
 *   R1 起这些程序化变更同样触发 onChange，父页面状态与编辑器始终一致，
 *   不存在两套真相。父组件不会把 text 写回编辑器，因此没有循环。
 *
 * 提交：Cmd/Ctrl+Enter 拦截触发 onSubmit 回调。
 *
 * C1.2 placeholder 修复：
 * - 移除 PlateContent 自带 placeholder（与父组件 overlay 重叠）。
 * - 父组件 AnalyzeSubmitForm 在 `!text.trim()` 时渲染 overlay 提示，
 *   是 placeholder 的单一真相源；本组件不再重复渲染。
 *
 * C1.3 可见降级：
 * - 初值与 setValue 使用 `deserializeMarkdownToBlocksWithStatus`，
 *   失败时通过 `onDegraded` 回调通知父组件，UI 显示"Markdown 解析失败，
 *   已按纯文本处理"提示态，禁止原始标记静默上屏。
 *
 * C1.4 粘贴保真提交：
 * - onPaste 记录用户原始粘贴文本、dirty=false，并挂起粘贴批次标记。
 * - 粘贴派生的 editor 变更（R1 起经 editor 级 onChange 到达）消费挂起
 *   标记并延长静默窗口，不置 dirty；窗口内无后续变更视为批次结束。
 *   不再依赖"定时器复位 isPasting"——Plate v53 的 onChange 在 effect
 *   时机触发，时序上必然晚于 setTimeout(0)，旧模型会把粘贴变更稳定地
 *   误判为用户编辑。
 * - `getSubmitText()` 在 `!dirty && lastPastedText` 时返回原始粘贴文本，
 *   消除 Plate serialize 往返损耗；编辑后返回 serialize 结果。
 * - 上传 `.md` 路径不经过本组件，维持直接提交文件内容不变。
 *
 * C1.5 serialize 配置：
 * - serialize 选项由 MarkdownKit 的 remarkStringifyOptions 统一锁定
 *   （bullet/emphasis/strong/fence/rule/incrementListMarker 等），
 *   本组件调用 `editor.getApi(MarkdownPlugin).markdown.serialize()` 时
 *   自动继承，无需额外传 options。
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
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import { MarkdownPlugin } from "@platejs/markdown";
import {
  createPlatePlugin,
  type PlateElementProps,
  type PlateLeafProps,
  Plate,
  PlateContent,
  type PlateEditor,
  usePlateEditor,
} from "platejs/react";
import type { Descendant, Value } from "platejs";

import { MarkdownKit } from "@/components/editor/plugins/markdown-kit";
import {
  deserializeMarkdownToBlocksWithStatus,
  type DeserializeMarkdownResult,
} from "@/lib/reader-plate/markdown/deserialize";
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

// R1：粘贴静默窗口时长（毫秒）。
// - BEFORE_CHANGE：粘贴事件记录后先开 300ms 窗口等粘贴派生变更到达
//   （Plate v53 的 onChange 走 React passive effect，必然晚于 0ms 宏任务；
//   浏览器中粘贴插入在事件内同步完成，远早于 300ms）。窗口到期仍无
//   变更视为被拒绝的粘贴，强制收口并放弃保真。
// - AFTER_CHANGE：见到粘贴派生变更后改为 0ms 窗口，窗口内无后续变更
//   即视为批次结束（Slate 多次 normalize 的后续变更会持续重置窗口）。
const PASTE_QUIET_AFTER_CHANGE_MS = 0;
const PASTE_QUIET_BEFORE_CHANGE_MS = 300;

function hasTextContent(nodes: unknown[]): boolean {
  return nodes.some((node) => {
    if (!node || typeof node !== "object") {
      return false;
    }
    if ("text" in node) {
      return typeof node.text === "string" && node.text.length > 0;
    }
    if ("children" in node && Array.isArray(node.children)) {
      return hasTextContent(node.children);
    }
    return false;
  });
}

// ---------------------------------------------------------------------------
// Ref handle：暴露给父组件用于提交/清空/恢复/聚焦
// ---------------------------------------------------------------------------

export interface MarkdownTextInputHandle {
  /**
   * C1.4: 获取提交文本（粘贴保真优先）。
   *
   * 若用户粘贴后未编辑（dirty=false 且有 lastPastedText），返回原始粘贴文本，
   * 消除 Plate serialize 往返损耗；编辑后返回 serialize 结果。
   */
  getSubmitText: () => string;
  /** 序列化当前编辑器内容为 Markdown 字符串 */
  getMarkdown: () => string;
  /** 聚焦编辑器 */
  focus: () => void;
  /** 清空编辑器（R1 起会触发 onChange，父状态随之复位） */
  clear: () => void;
  /** 用 Markdown 字符串重置编辑器内容（R1 起会触发 onChange，父状态随之同步） */
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
  /**
   * C1.3: deserialize 降级回调。
   *
   * 挂载与 setValue 时触发：status === "degraded" 表示解析失败，
   * blocks 兜底为纯文本段落；调用方应显示可见降级提示，
   * 禁止原始标记静默上屏。status === "success" | "empty" 时也会触发，
   * 调用方可据清除降级提示。
   */
  onDegraded?: (result: DeserializeMarkdownResult) => void;
  /** 透传给 Editor 的 className */
  className?: string;
  id?: string;
  /**
   * R1：contenteditable 不是 labelable 元素，`<label for>` 不能可靠命名它。
   * 父组件应提供可见/程序化标签元素的 id，这里透传为 aria-labelledby。
   */
  ariaLabelledBy?: string;
  /**
   * R1：程序化帮助关系 id（如输入提示），透传为 aria-describedby。
   */
  ariaDescribedBy?: string;
}

export const MarkdownTextInput = forwardRef<
  MarkdownTextInputHandle,
  MarkdownTextInputProps
>(function MarkdownTextInput(
  { initialValue, onChange, onSubmit, onLintResult, onDegraded, className, id, ariaLabelledBy, ariaDescribedBy },
  ref,
) {
  // C1.3: 挂载时用带状态 deserialize，失败时兜底为纯文本段落，
  // 并通过 onDegraded 回调通知父组件显示可见降级提示。
  // useState initializer 不能有副作用，先把结果存 ref，mount effect 中回调。
  const initialResultRef = useRef<DeserializeMarkdownResult | null>(null);
  const [initialBlocks] = useState<Descendant[]>(() => {
    const result = deserializeMarkdownToBlocksWithStatus(initialValue ?? "");
    initialResultRef.current = result;
    return result.blocks;
  });

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
  const onDegradedRef = useRef(onDegraded);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  useEffect(() => {
    onSubmitRef.current = onSubmit;
  }, [onSubmit]);
  useEffect(() => {
    onLintResultRef.current = onLintResult;
  }, [onLintResult]);
  useEffect(() => {
    onDegradedRef.current = onDegraded;
  }, [onDegraded]);

  // C1.3: 挂载时通知父组件初始 deserialize 状态（仅一次）。
  useEffect(() => {
    const result = initialResultRef.current;
    if (result) {
      initialResultRef.current = null;
      onDegradedRef.current?.(result);
    }
  }, []);

  // C1.4 / R1: 粘贴保真状态。
  // - lastPastedTextRef: 用户最后一次"整篇粘贴"的原始文本（编辑器为空时粘贴）。
  // - dirtyRef: 用户是否在粘贴后进行了非粘贴编辑。
  // - pendingPasteRef: 是否存在尚未结束粘贴批次的挂起粘贴。Plate v53 的
  //   editor 级 onChange 在 React effect 时机异步触发，早于任何
  //   setTimeout(0) 宏任务复位——因此不能用"先置旗、定时复位"的时序模型
  //   （会稳定地把粘贴变更误判为用户编辑）。改为：粘贴挂起旗在粘贴派生
  //   变更到达时被消费并延长静默窗口；静默窗口（一个宏任务内再无变更）
  //   结束后才视为粘贴批次完成。真实用户的下一次输入永远发生在窗口之外。
  const lastPastedTextRef = useRef<string | null>(null);
  const dirtyRef = useRef(false);
  const pendingPasteRef = useRef(false);
  const pasteChangeSeenRef = useRef(false);
  const pasteQuietTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // R1：上一次处理过的 editor value 引用，用于跳过仅 selection 变化。
  const lastValueRef = useRef<Value | null>(null);

  const armPasteQuietReset = useCallback((interval: number) => {
    if (pasteQuietTimerRef.current) {
      clearTimeout(pasteQuietTimerRef.current);
    }
    pasteQuietTimerRef.current = setTimeout(() => {
      pasteQuietTimerRef.current = null;
      if (pasteChangeSeenRef.current) {
        // 粘贴批次完成（见到过粘贴派生变更且窗口内无后续变更）。
        pendingPasteRef.current = false;
      } else {
        // 粘贴始终没产生变更（被拒绝的粘贴）：强制收口并放弃保真，
        // 避免把之后的用户编辑误判为粘贴派生。
        pendingPasteRef.current = false;
        lastPastedTextRef.current = null;
      }
    }, interval);
  }, []);

  const endPasteWindow = useCallback(() => {
    pendingPasteRef.current = false;
    pasteChangeSeenRef.current = false;
    if (pasteQuietTimerRef.current) {
      clearTimeout(pasteQuietTimerRef.current);
      pasteQuietTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      if (pasteQuietTimerRef.current) {
        clearTimeout(pasteQuietTimerRef.current);
      }
    };
  }, []);

  // R1：@platejs/markdown serialize 会为空文本节点输出 U+200B 零宽空格。
  // 必须用编辑器语义判断空状态，不能全文 replace U+200B：真实正文可能
  // 合法携带零宽空格，删除它会改变 canonical text 与 UTF-16 offset。
  const serializeCurrentMarkdown = useCallback((target: PlateEditor): string => {
    if (!hasTextContent(target.children)) {
      return "";
    }
    const md = target.getApi(MarkdownPlugin).markdown.serialize();
    return md.trim().length === 0 ? "" : md;
  }, []);

  /**
   * R1：粘贴保真窗口的记录侧。
   * 仅当编辑器当前"实质为空"（整篇粘贴场景）时记录原始文本并开窗；
   * 增量粘贴（非空编辑器）视为编辑，关闭保真。
   */
  const recordPasteText = useCallback((clipboardText: string) => {
    if (!clipboardText.trim()) {
      return;
    }
    const isEmpty = !hasTextContent(editor.children);
    if (isEmpty) {
      lastPastedTextRef.current = clipboardText;
      dirtyRef.current = false;
      pendingPasteRef.current = true;
      pasteChangeSeenRef.current = false;
      armPasteQuietReset(PASTE_QUIET_BEFORE_CHANGE_MS);
    } else {
      // 粘贴到非空编辑器 → 视为编辑，不再保真
      dirtyRef.current = true;
      lastPastedTextRef.current = null;
      endPasteWindow();
    }
  }, [editor, armPasteQuietReset, endPasteWindow]);

  useImperativeHandle(
    ref,
    () => ({
      // C1.4: 粘贴保真 — 未编辑时返回原始粘贴文本，消除 serialize 往返损耗。
      getSubmitText: () => {
        if (!editor) return "";
        if (!dirtyRef.current && lastPastedTextRef.current) {
          return lastPastedTextRef.current;
        }
        return serializeCurrentMarkdown(editor);
      },
      getMarkdown: () => {
        if (!editor) return "";
        return serializeCurrentMarkdown(editor);
      },
      focus: () => {
        if (!editor) return;
        editor.tf.focus();
      },
      clear: () => {
        if (!editor) return;
        editor.tf.setValue([]);
        // C1.4: 清空时重置粘贴保真状态
        lastPastedTextRef.current = null;
        dirtyRef.current = false;
        endPasteWindow();
      },
      setValue: (markdown: string) => {
        if (!editor) return;
        // C1.3: setValue 使用带状态 deserialize，失败时通知父组件。
        const result = deserializeMarkdownToBlocksWithStatus(markdown);
        editor.tf.setValue(result.blocks as never[]);
        onDegradedRef.current?.(result);
        // C1.4: programmatic setValue 重置粘贴保真状态
        lastPastedTextRef.current = null;
        dirtyRef.current = false;
        endPasteWindow();
      },
    }),
    [editor, endPasteWindow, serializeCurrentMarkdown],
  );

  if (!editor) {
    return null;
  }


  // R1：editor 级 value lifecycle。
  // - value 引用未变（仅 selection 变化）时直接跳过，避免每次光标移动
  //   都执行整文档序列化与 lint。
  // - 粘贴窗口内（pendingPasteRef）的变更是粘贴派生：消费挂起旗并延长
  //   静默窗口，不置 dirty，保留 C1.4 粘贴保真。
  const handleEditorChange = ({
    editor: changedEditor,
    value,
  }: {
    editor: PlateEditor;
    value: Value;
  }) => {
    if (value === lastValueRef.current) {
      return;
    }
    lastValueRef.current = value;
    if (pendingPasteRef.current) {
      pasteChangeSeenRef.current = true;
      armPasteQuietReset(PASTE_QUIET_AFTER_CHANGE_MS);
    } else {
      dirtyRef.current = true;
      lastPastedTextRef.current = null;
    }
    const md = serializeCurrentMarkdown(changedEditor);
    onChangeRef.current(md);
    // Phase 1 / P0: 输入端预警 lint（非阻塞，后端仍是 fail-closed 单一真相源）
    const lintCallback = onLintResultRef.current;
    if (lintCallback) {
      lintCallback(lintMarkdownInput(md));
    }
  };


  const handlePaste = (event: ClipboardEvent) => {
    // C1.4: 记录用户原始粘贴文本，用于提交保真。
    recordPasteText(event.clipboardData?.getData("text/plain") ?? "");
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      onSubmitRef.current();
    }
  };

  return (
    <Plate editor={editor} onChange={handleEditorChange}>
      <PlateContent
        id={id}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        className={cn(
          "min-h-0 flex-1 resize-none overflow-y-auto bg-transparent",
          "whitespace-pre-wrap break-words outline-none",
          "[&_strong]:font-bold",
          className,
        )}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />
    </Plate>
  );
});
