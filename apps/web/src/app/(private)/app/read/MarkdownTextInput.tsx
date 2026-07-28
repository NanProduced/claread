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
import remarkGfm from "remark-gfm";
import {
  BaseBlockquotePlugin,
  BaseBoldPlugin,
  BaseCodePlugin,
  BaseH1Plugin,
  BaseH2Plugin,
  BaseH3Plugin,
  BaseH4Plugin,
  BaseH5Plugin,
  BaseH6Plugin,
  BaseHorizontalRulePlugin,
  BaseItalicPlugin,
  BaseStrikethroughPlugin,
} from "@platejs/basic-nodes";
import { BaseCodeBlockPlugin, BaseCodeLinePlugin } from "@platejs/code-block";
import { BaseLinkPlugin } from "@platejs/link";
import {
  BaseBulletedListPlugin,
  BaseListItemContentPlugin,
  BaseListItemPlugin,
  BaseListPlugin,
  BaseNumberedListPlugin,
} from "@platejs/list-classic";
import {
  BaseTableCellHeaderPlugin,
  BaseTableCellPlugin,
  BaseTablePlugin,
  BaseTableRowPlugin,
} from "@platejs/table";
import {
  createPlatePlugin,
  type PlateElementProps,
  type PlateLeafProps,
  Plate,
  PlateContent,
  type PlateEditor,
  usePlateEditor,
} from "platejs/react";
import type { Value } from "platejs";

import { MARKDOWN_PLUGIN_OPTIONS } from "@/components/editor/plugins/markdown-kit";
import { prepareClipboardHtml } from "@/lib/clipboard/prepare-clipboard-html";
import {
  deserializeMarkdownToBlocksWithStatus,
  type DeserializeMarkdownResult,
} from "@/lib/reader-plate/markdown/deserialize";
import { remarkPreserveUnsupported } from "@/lib/reader-plate/markdown/remark-preserve-unsupported";
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
    <p {...attributes} className="my-3">
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
    <ul {...attributes} className="my-3 list-disc pl-6">
      {children}
    </ul>
  );
}

function MarkdownOrderedList({ children, attributes }: PlateElementProps) {
  return (
    <ol {...attributes} className="my-3 list-decimal pl-6">
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
  // R2R Phase 4: `<code>` 仅接受 phrasing content，`<div>` 是 flow content，
  // `<pre><code><div>…</div></code></pre>` 无效。改用 `<span>` + `block`
  // display 实现逐行换行，DOM 语义有效且不依赖 `<div>`。
  return (
    <span {...attributes} className="block">
      {children}
    </span>
  );
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
// Plugins：官方行为插件（@platejs/basic-nodes / list-classic / link /
// code-block / table）+ 复用上方 Markdown* 视觉组件。
//
// L1 接入（替代原 21 个薄 createPlatePlugin 渲染壳）：
// - 官方插件自带 HTML deserializer / normalize / 快捷键等行为，粘贴
//   text/html 不再摊平为段落。
// - 视觉仍由本文件 Markdown* 组件负责，通过 `.configure({ node: { component } })`
//   挂载；不重新设计样式。
// - BaseListPlugin / BaseTablePlugin 自带子插件（ul/ol/taskList/li/lic、
//   tr/td/th）；重复 key 的独立注册会合并（first-wins），用于给子插件
//   挂 component。
// - paragraph 无官方包，保留 core ParagraphPlugin + 薄壳 component。
// - MarkdownKit 的 MarkdownPlugin 追加 remarkPreserveUnsupported：
//   image/footnote/task list 在输入端降级为可见形态，不静默丢失
//   （reader-plate projection 用的 MarkdownKit 不受影响）。
// ---------------------------------------------------------------------------

// 注：不要通过 `MarkdownKit[0].configure({ options: ...MarkdownKit[0].options })`
// 复制配置——Plate configure 的 options 解析会丢 remarkStringifyOptions
// 等字段（已实测）。统一从 markdown-kit 导出的 MARKDOWN_PLUGIN_OPTIONS 展开。
const InputMarkdownPlugin = MarkdownPlugin.configure({
  options: {
    ...MARKDOWN_PLUGIN_OPTIONS,
    remarkPlugins: [remarkGfm, remarkPreserveUnsupported],
  },
});

const markdownTextInputPlugins = [
  InputMarkdownPlugin,
  // basic-nodes：标题/引用/分隔线
  BaseH1Plugin.configure({ node: { component: MarkdownHeading } }),
  BaseH2Plugin.configure({ node: { component: MarkdownHeading } }),
  BaseH3Plugin.configure({ node: { component: MarkdownHeading } }),
  BaseH4Plugin.configure({ node: { component: MarkdownHeading } }),
  BaseH5Plugin.configure({ node: { component: MarkdownHeading } }),
  BaseH6Plugin.configure({ node: { component: MarkdownHeading } }),
  BaseBlockquotePlugin.configure({ node: { component: MarkdownBlockquote } }),
  BaseHorizontalRulePlugin.configure({ node: { component: MarkdownHr } }),
  // basic-nodes：行内 marks
  BaseBoldPlugin.configure({ node: { component: MarkdownBoldLeaf } }),
  BaseItalicPlugin.configure({ node: { component: MarkdownItalicLeaf } }),
  BaseCodePlugin.configure({ node: { component: MarkdownCodeLeaf } }),
  BaseStrikethroughPlugin.configure({ node: { component: MarkdownStrikethroughLeaf } }),
  // list-classic：行为来自 BaseListPlugin（withList），component 挂子插件
  BaseListPlugin,
  BaseBulletedListPlugin.configure({ node: { component: MarkdownUnorderedList } }),
  BaseNumberedListPlugin.configure({ node: { component: MarkdownOrderedList } }),
  BaseListItemPlugin.configure({ node: { component: MarkdownListItem } }),
  BaseListItemContentPlugin.configure({ node: { component: MarkdownListContent } }),
  // link / code-block / table
  BaseLinkPlugin.configure({ node: { component: MarkdownLink } }),
  BaseCodeBlockPlugin.configure({ node: { component: MarkdownCodeBlock } }),
  BaseCodeLinePlugin.configure({ node: { component: MarkdownCodeLine } }),
  BaseTablePlugin.configure({ node: { component: MarkdownTable } }),
  BaseTableRowPlugin.configure({ node: { component: MarkdownTableRow } }),
  BaseTableCellPlugin.configure({ node: { component: MarkdownTableCell } }),
  BaseTableCellHeaderPlugin.configure({ node: { component: MarkdownTableCell } }),
  // paragraph：core 插件 + 薄壳 component
  createPlatePlugin({ key: "p", node: { isElement: true, component: MarkdownParagraph } }),
];

// R1：粘贴静默窗口时长（毫秒）。
// - BEFORE_CHANGE：粘贴事件记录后先开 300ms 窗口等粘贴派生变更到达
//   （Plate v53 的 onChange 走 React passive effect，必然晚于 0ms 宏任务；
//   浏览器中粘贴插入在事件内同步完成，远早于 300ms）。窗口到期仍无
//   变更视为被拒绝的粘贴，强制收口并放弃保真。
// - AFTER_CHANGE：见到粘贴派生变更后改为短窗口，窗口内每个后续变更
//   都重置计时；窗口耗尽即视为批次结束。
//
// 阶段 3 修正（L1 实测证据）：AFTER_CHANGE 不能为 0。Plate v53 的
// editor 级 onChange 在 passive effect 时机触发，必然晚于 setTimeout(0)，
// 因此 0ms 窗口在首个变更后立即耗尽；L1 官方行为插件（list/table 等
// normalizer、id 归一化）会让一次粘贴产生多个跨 effect 批次的变更，
// 后续变更被误判为用户编辑（dirty=true），保真路径稳定不命中
// （浏览器实测：小粘贴 349/323、30k 长文 31311/30400 均退化 serialize）。
// 100ms 的依据：真实用户从粘贴到下一次击键的间隔远超 100ms，
// 不会把用户编辑误吸收进粘贴批次；同时足以覆盖跨 effect 的
// normalizer 追加变更。
const PASTE_QUIET_AFTER_CHANGE_MS = 100;
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
   *
   * R2 Phase 2: 始终直接读 editor，不依赖 debounced 父状态，
   * 因此 submit 不会拿到陈旧 text。
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
  /**
   * 同步吸收 pending debounce，并返回 lint/提交共用的 Markdown 快照。
   */
  flush: () => string;
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
  // C1.3 + R2 Phase 3: 挂载时用带状态 deserialize，失败时兜底为纯文本段落，
  // 并通过 onDegraded 回调通知父组件显示可见降级提示。
  //
  // 修复 refs-during-render：原实现把 deserialize 结果写入 `initialResultRef`
  // 在 useState initializer（render 阶段）中，违反 React 19 render 纯粹性。
  // R2 改为：deserialize 结果直接作为 useState 状态（initializer 仍是纯计算，
  // 不写任何 ref），mount effect 通过闭包读取 stable 状态并通知父组件。
  //
  // Strict Mode 安全：React 18+ StrictMode 会模拟 unmount-remount，但 refs
  // 在该过程中保留。`initialDegradedNotifiedRef` 防止重复触发 `onDegraded`
  // 导致父组件重复显示可见错误提示。
  const [initialResult] = useState<DeserializeMarkdownResult>(
    () => deserializeMarkdownToBlocksWithStatus(initialValue ?? ""),
  );
  const initialDegradedNotifiedRef = useRef(false);

  const editor = usePlateEditor(
    {
      plugins: markdownTextInputPlugins,
      value: initialResult.blocks as never[],
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
  // R2 Phase 3: 改用 stable 状态 + 通知 ref，避免 render 阶段写 ref 与
  // Strict Mode 下的重复可见错误提示。
  useEffect(() => {
    if (initialDegradedNotifiedRef.current) return;
    initialDegradedNotifiedRef.current = true;
    onDegradedRef.current?.(initialResult);
  }, [initialResult]);

  // C1.4 / R1: 粘贴保真状态。
  // - lastPastedTextRef: 用户最后一次"纯 Markdown 整篇粘贴"的原始文本。
  //   富 HTML 粘贴不得保存 companion text/plain，因为它通常是已经摊平的
  //   可访问性表示；此时 Confirmed Source 草稿来自清洗后的 Plate Value。
  // - dirtyRef: 用户是否在粘贴后进行了非粘贴编辑。
  // - pendingPasteRef: 是否存在尚未结束粘贴批次的挂起粘贴。Plate v53 的
  //   editor 级 onChange 在 React effect 时机异步触发，早于任何
  //   setTimeout(0) 宏任务复位——因此不能用"先置旗、定时复位"的时序模型
  //   （会稳定地把粘贴变更误判为用户编辑）。改为：粘贴挂起旗在粘贴派生
  //   变更到达时被消费并延长静默窗口；静默窗口（一个宏任务内再无变更）
  //   结束后才视为粘贴批次完成。用户输入通过 beforeinput/keydown/
  //   composition 显式切断窗口，不依赖“用户一定慢于计时器”的假设。
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

  const readSubmitMarkdown = useCallback((): string => {
    if (!dirtyRef.current && lastPastedTextRef.current) {
      return lastPastedTextRef.current;
    }
    return serializeCurrentMarkdown(editor);
  }, [editor, serializeCurrentMarkdown]);

  // -------------------------------------------------------------------------
  // R2 Phase 2: 分层状态流 —— serialize + lint 调度
  //
  // 旧实现每次 onChange 都同步执行 `serializeCurrentMarkdown(editor)` +
  // `lintMarkdownInput(md)` + `onChange(md)`，长文输入（30k–50k 字符）下
  // 每次按键都会触发整文档处理，导致输入卡顿。
  //
  // R2 分层调度合同：
  // 1. 轻/重分离：空/非空、CTA、placeholder 等父状态通过 `onChange` 同步刷新；
  //    serialize/lint 的结果同样通过 `onChange`/`onLintResult` 回调，但合并
  //    调度：debounce 窗口内多次 edit 最多执行一次回调。
  // 2. 空态立即 flush：editor 由非空变空（或反之）属于"语义边界转换"，
  //    立即同步回调，避免 CTA/placeholder 长时间显示陈旧态。
  // 3. submit 不依赖父状态：`getSubmitText()` 始终直接读取 editor 内容，
  //    不读取 debounced 值，因此 submit 不会拿到陈旧 text。`flush()` 用于
  //    提交或测试场景显式同步父状态。
  // 4. clear/setValue 取消旧任务并立即 fire：避免 stale debounce 把旧内容
  //    写回父状态；同时保证 programmatic 状态变更后父状态与 editor 一致。
  // 5. unmount 取消 pending：避免回调在组件卸载后写入陈旧状态。
  // 6. dedup：相同 md 不重复触发回调（parent state setter 对相同值本身是
  //    no-op，dedup 避免冗余 lint 计算）。
  // -------------------------------------------------------------------------
  const SERIALIZE_DEBOUNCE_MS = 150;
  const serializeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSentMdRef = useRef<string>("");

  const fireSerializeCallbacks = useCallback((md: string) => {
    // dedup：相同 md 不重复触发回调。parent 的 setText 是 no-op，且 lint
    // 结果对相同 md 必然相同，重复调用只会浪费主线程时间。
    if (md === lastSentMdRef.current) return;
    lastSentMdRef.current = md;
    onChangeRef.current(md);
    const lintCallback = onLintResultRef.current;
    if (lintCallback) {
      lintCallback(lintMarkdownInput(md));
    }
  }, []);

  const cancelPendingSerialize = useCallback(() => {
    if (serializeTimerRef.current) {
      clearTimeout(serializeTimerRef.current);
      serializeTimerRef.current = null;
    }
  }, []);

  // Flush returns the exact Markdown snapshot that callers should submit and
  // lint. This avoids serializing a long document once in flush() and again
  // in getSubmitText(), while preserving the untouched raw-paste payload.
  const flushSerialize = useCallback((): string => {
    if (serializeTimerRef.current) {
      clearTimeout(serializeTimerRef.current);
      serializeTimerRef.current = null;
    }
    const md = readSubmitMarkdown();
    fireSerializeCallbacks(md);
    return md;
  }, [fireSerializeCallbacks, readSubmitMarkdown]);

  // R2 Phase 2: unmount 必须取消 pending，避免回调写入已卸载组件的父状态。
  useEffect(() => {
    return () => {
      cancelPendingSerialize();
    };
  }, [cancelPendingSerialize]);

  /**
   * R1：粘贴保真窗口的记录侧。
   * 仅当编辑器当前"实质为空"（整篇粘贴场景）时记录原始文本并开窗；
   * 增量粘贴（非空编辑器）视为编辑，关闭保真。
   */
  const beginPasteWindow = useCallback((rawMarkdown: string | null) => {
    if (rawMarkdown !== null && !rawMarkdown.trim()) {
      return;
    }
    const isEmpty = !hasTextContent(editor.children);
    if (isEmpty) {
      lastPastedTextRef.current = rawMarkdown;
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

  const markUserEdit = useCallback(() => {
    dirtyRef.current = true;
    lastPastedTextRef.current = null;
    endPasteWindow();
  }, [endPasteWindow]);

  useImperativeHandle(
    ref,
    () => ({
      // C1.4: 粘贴保真 — 未编辑时返回原始粘贴文本，消除 serialize 往返损耗。
      // R2 Phase 2: 不依赖 debounced 父状态，始终直接读 editor。
      getSubmitText: () => {
        if (!editor) return "";
        return readSubmitMarkdown();
      },
      getMarkdown: () => {
        if (!editor) return "";
        return serializeCurrentMarkdown(editor);
      },
      focus: () => {
        if (!editor) return;
        editor.tf.focus();
      },
      // 同步 pending 状态，并返回 lint/提交共用的单一 Markdown 快照。
      flush: () => {
        if (!editor) return "";
        return flushSerialize();
      },
      clear: () => {
        if (!editor) return;
        // R2 Phase 2: 取消 pending debounce（避免 stale 内容写回父状态），
        // 然后同步 fire 空态回调，保证父状态立即与 editor 一致。
        cancelPendingSerialize();
        editor.tf.setValue([]);
        // C1.4: 清空时重置粘贴保真状态
        lastPastedTextRef.current = null;
        dirtyRef.current = false;
        endPasteWindow();
        fireSerializeCallbacks("");
      },
      setValue: (markdown: string) => {
        if (!editor) return;
        // R2 Phase 2: 取消 pending debounce，避免 stale 内容覆盖新值。
        cancelPendingSerialize();
        // C1.3: setValue 使用带状态 deserialize，失败时通知父组件。
        const result = deserializeMarkdownToBlocksWithStatus(markdown);
        editor.tf.setValue(result.blocks as never[]);
        onDegradedRef.current?.(result);
        // C1.4: programmatic setValue 重置粘贴保真状态
        lastPastedTextRef.current = null;
        dirtyRef.current = false;
        endPasteWindow();
        // R2 Phase 2: 同步 fire 新值回调，保证父状态与 editor 一致。
        // Plate onChange 可能在 passive effect 中再次触发 handleEditorChange,
        // dedup（lastSentMdRef）会吸收那次重复回调。
        fireSerializeCallbacks(serializeCurrentMarkdown(editor));
      },
    }),
    [
      editor,
      endPasteWindow,
      serializeCurrentMarkdown,
      readSubmitMarkdown,
      cancelPendingSerialize,
      flushSerialize,
      fireSerializeCallbacks,
    ],
  );

  if (!editor) {
    return null;
  }


  // R2R Issue A fix: 轻/重状态分离。
  // 旧实现（RED）：每次 handleEditorChange 都调用 serializeCurrentMarkdown，
  //   150ms debounce 只延后 lint 和回调，没有完成"长文输入硬化"。
  //   30k+ 长文下每次按键都执行整篇 Plate → Markdown 序列化。
  // 新实现（GREEN）：
  //   - 轻状态（empty/non-empty）：用 hasTextContent(children) 判断，
  //     不依赖完整 serialize。空↔非空边界转换立即 fire（serialize 此时
  //     代价低，因为一侧为空）。
  //   - 重状态（serialize + lint）：debounce 窗口内最多执行一次。
  //     timer 回调里新鲜 serialize，不存储陈旧 md。
  //   - selection-only change（value 引用未变）：直接跳过，0 次 serialize。
  //   - 粘贴窗口内的变更：消费挂起旗、延长静默窗口，不置 dirty。
  const handleEditorChange = ({
    editor: changedEditor,
    value,
  }: {
    editor: PlateEditor;
    value: Value;
  }) => {
    if (value === lastValueRef.current) {
      // selection-only change：不触发 serialize 或 lint。
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
    // 轻状态判断：基于 editor.children 文本语义，不执行完整 Markdown serialize。
    const isEmpty = !hasTextContent(changedEditor.children);
    const wasEmpty = lastSentMdRef.current.length === 0;
    const isEmptyTransition = wasEmpty !== isEmpty;
    if (isEmptyTransition) {
      // 空↔非空转换属于语义边界：立即 serialize + fire 让 CTA/placeholder
      // 同步刷新。此时 serialize 代价低（一侧为空）。
      cancelPendingSerialize();
      const md = serializeCurrentMarkdown(changedEditor);
      fireSerializeCallbacks(md);
    } else {
      // 普通编辑（非空→非空 或 空→空）：合并调度。
      // 不在 handleEditorChange 中 serialize —— 把 serialize 延后到
      // timer 回调，debounce 窗口内多次 edit 最多执行一次 serialize。
      if (serializeTimerRef.current) {
        clearTimeout(serializeTimerRef.current);
      }
      serializeTimerRef.current = setTimeout(() => {
        serializeTimerRef.current = null;
        // 新鲜 serialize：读取 editor 当前最新内容。
        // editor 是 stable 引用，timer 回调执行时内容已是最新的。
        const md = serializeCurrentMarkdown(editor);
        fireSerializeCallbacks(md);
      }, SERIALIZE_DEBOUNCE_MS);
    }
  };


  const handlePaste = (event: ClipboardEvent) => {
    const plain = event.clipboardData?.getData("text/plain") ?? "";
    const html = event.clipboardData?.getData("text/html") ?? "";
    if (!plain.trim() && !html.trim()) {
      return;
    }

    // 仅纯 Markdown 粘贴保留 byte-exact 原文。富 HTML 的 text/plain
    // companion 通常缺少标题、列表、表格等结构；提交源必须由清洗并
    // deserialize 后的 Plate Value 序列化得到。
    beginPasteWindow(html ? null : plain);
    // L1: clipboard 同时携带 text/html 时，先清洗（script/iframe/on* /
    // 危险 URL scheme）并做 Notion callout（aside→blockquote）语义映射，
    // 再交给官方插件的 HTML deserializer。preventDefault 后 Slate 默认
    // 粘贴管线短路（slate-react isEventHandled 检查 defaultPrevented），
    // 避免未清洗 HTML 进入 Plate/DOM。
    if (html) {
      event.preventDefault();
      const clean = prepareClipboardHtml(html);
      const doc = new DOMParser().parseFromString(clean, "text/html");
      const fragment = editor.api.html.deserialize({ element: doc.body });
      if (fragment.length > 0) {
        editor.tf.insertFragment(fragment as never[]);
      } else if (plain.trim()) {
        // 清洗后 HTML 没有可反序列化节点时，显式退回 companion text。
        // 仍以 Plate Value 为提交源，避免 preventDefault 造成可见内容丢失。
        const fallback = deserializeMarkdownToBlocksWithStatus(plain);
        editor.tf.insertFragment(fallback.blocks as never[]);
      }
    }
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    const isEditingKey =
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey &&
      (event.key.length === 1 ||
        event.key === "Backspace" ||
        event.key === "Delete" ||
        event.key === "Enter");
    if (isEditingKey) {
      markUserEdit();
    }
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
        onBeforeInput={(event) => {
          const inputType = (event.nativeEvent as InputEvent).inputType;
          if (inputType !== "insertFromPaste") {
            markUserEdit();
          }
        }}
        onCompositionStart={markUserEdit}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />
    </Plate>
  );
});
