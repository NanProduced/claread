/**
 * Reader Floating Toolbar Buttons — Reader Record Plate 的默认划选工具栏
 *
 * 按钮布局（ask-first，全部一级按钮，无二级菜单）：
 * - Ask（Sparkles，蓝色主按钮，第一位）：打开 Ask 快捷框（Surface 层
 *   托管的浮层，输入问题或点快捷指令后落入右侧 Ask 面板）。
 * - 查词（Search）：词典 quick peek。
 * - 高亮（Highlighter）：一键高亮。
 * - 复制（Copy）。
 * - 新建笔记（NotebookPen）。
 *
 * 通过 ReaderToolbarActionsContext 获取回调，由 ReaderRecordPlateSurface
 * 在渲染 Plate editor 时通过 Provider 注入。
 */
"use client";

import {
  createContext,
  useContext,
  useId,
  useState,
  type ReactNode,
} from "react";
import {
  Copy,
  Highlighter,
  NotebookPen,
  Search,
  Sparkles,
} from "lucide-react";

import { READER_ASK_QUICK_ACTIONS } from "@/components/reader/ask/quick-actions";
import { ToolbarButton, ToolbarGroup } from "@/components/ui/toolbar";
import type { ReaderAskEntryActionDto } from "@/types/api/reader-ask";

export type ReaderToolbarActionId =
  | "lookup"
  | "copy"
  | "ask"
  | "highlight"
  | "note";

export interface ReaderToolbarActionState {
  disabled: boolean;
  reason?: string;
}

export interface ReaderToolbarActions {
  /**
   * Toggle the Ask quick menu (Surface-owned floating layer with prompt
   * input + quick actions). The current selection is frozen into the menu
   * at open time; submitting lands in the Ask panel conversation.
   */
  onAsk: () => void;
  onCopy: () => void;
  onHighlight: () => void;
  onNote: () => void;
  onLookup: () => void;
  suppressToolbar?: boolean;
  state: Record<ReaderToolbarActionId, ReaderToolbarActionState>;
}

export const ReaderToolbarActionsContext = createContext<ReaderToolbarActions | null>(null);

export function useReaderToolbarActions(): ReaderToolbarActions | null {
  return useContext(ReaderToolbarActionsContext);
}

const preventFocusLoss = {
  onPointerDown: (e: React.PointerEvent) => {
    e.preventDefault();
  },
} as const;

const toolbarGroupClassName = "items-center";
// 统一中性 hover（Notion/Plate 式灰阶），比浅蓝底更清晰；所有按钮含
// Ask 共用同一套，Ask 仅用文字标签 + 字重区分主次。
const toolbarButtonClassName =
  "rounded-[8px] text-ink/80 transition-colors hover:bg-ink/[0.06] hover:text-ink active:bg-ink/[0.1] disabled:cursor-not-allowed disabled:opacity-40 max-[420px]:h-8 max-[420px]:min-w-8 max-[420px]:px-1.5";
const toolbarShortcutClassName =
  "ml-1 inline-flex min-w-0 items-center rounded bg-background/14 px-1.5 py-0.5 font-sans text-xs font-semibold leading-none text-background/78";

function actionState(
  actions: ReaderToolbarActions | null,
  id: ReaderToolbarActionId,
): ReaderToolbarActionState {
  if (!actions) {
    return { disabled: true, reason: "请选择稳定原文后再操作" };
  }
  return actions.state[id];
}

function toolbarTitle(label: string, state: ReaderToolbarActionState) {
  return state.disabled && state.reason ? `${label}不可用：${state.reason}` : label;
}

function toolbarTooltip(
  label: string,
  state: ReaderToolbarActionState,
  shortcut?: string,
) {
  const title = toolbarTitle(label, state);
  if (!shortcut || state.disabled) {
    return title;
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      <span>{title}</span>
      <kbd className={toolbarShortcutClassName}>{shortcut}</kbd>
    </span>
  );
}

function ReaderActionToolbarButton({
  actionId,
  label,
  onAction,
  children,
  shortcut,
}: {
  actionId: ReaderToolbarActionId;
  label: string;
  onAction: (actions: ReaderToolbarActions) => void;
  children: ReactNode;
  shortcut?: string;
}) {
  const actions = useContext(ReaderToolbarActionsContext);
  const state = actionState(actions, actionId);
  const tooltip = toolbarTooltip(label, state, shortcut);

  return (
    <ToolbarButton
      className={toolbarButtonClassName}
      size="default"
      tooltip={tooltip}
      aria-label={label}
      data-reader-record-action={actionId}
      data-reader-record-toolbar-action={actionId}
      data-reader-record-disabled-reason={state.disabled ? state.reason : undefined}
      disabled={state.disabled}
      onPointerDown={preventFocusLoss.onPointerDown}
      onClick={state.disabled || !actions ? undefined : () => onAction(actions)}
    >
      {children}
    </ToolbarButton>
  );
}

export function ReaderLookupToolbarButton() {
  return (
    <ReaderActionToolbarButton
      actionId="lookup"
      label="查词"
      onAction={(actions) => actions.onLookup()}
    >
      <Search className="size-4" />
    </ReaderActionToolbarButton>
  );
}

export function ReaderCopyToolbarButton() {
  return (
    <ReaderActionToolbarButton
      actionId="copy"
      label="复制"
      shortcut="Ctrl+C"
      onAction={(actions) => actions.onCopy()}
    >
      <Copy className="size-4" />
    </ReaderActionToolbarButton>
  );
}

/**
 * Ask 主按钮（第一位）。打开 Surface 层托管的 Ask 快捷框——浮层状态
 * 不活在工具栏里，焦点进入输入框导致选区/toolbar 卸载时菜单依然存活。
 * 图标与全栏统一为墨色，主从关系由文字标签承载（Plate "Ask AI" 式）。
 */
export function ReaderAskToolbarButton() {
  const actions = useContext(ReaderToolbarActionsContext);
  const state = actionState(actions, "ask");

  return (
    <ToolbarButton
      className={`${toolbarButtonClassName} gap-1.5 px-3 font-medium text-ink`}
      size="default"
      tooltip={toolbarTooltip("Ask Claread", state, "Ctrl+J")}
      aria-label="Ask Claread"
      data-reader-record-action="ask"
      data-reader-record-toolbar-action="ask"
      data-reader-record-disabled-reason={state.disabled ? state.reason : undefined}
      disabled={state.disabled}
      onPointerDown={preventFocusLoss.onPointerDown}
      onClick={() => {
        if (state.disabled || !actions) {
          return;
        }
        actions.onAsk();
      }}
    >
      <Sparkles className="size-4" />
      <span className="text-sm font-medium">Ask Claread</span>
    </ToolbarButton>
  );
}

export function ReaderHighlightToolbarButton() {
  return (
    <ReaderActionToolbarButton
      actionId="highlight"
      label="高亮"
      onAction={(actions) => actions.onHighlight()}
    >
      <Highlighter className="size-4" />
    </ReaderActionToolbarButton>
  );
}

// Local registry does not include CommentToolbarButton; this wrapper keeps the
// Plate toolbar primitive while the action delegates to CommentKit setDraft/activeId.
export function ReaderNoteToolbarButton() {
  return (
    <ReaderActionToolbarButton
      actionId="note"
      label="新建笔记"
      onAction={(actions) => actions.onNote()}
    >
      <NotebookPen className="size-4" />
    </ReaderActionToolbarButton>
  );
}

export function ReaderFloatingToolbarButtons() {
  const actions = useReaderToolbarActions();
  if (actions?.suppressToolbar) {
    return null;
  }

  // 不在此处包 <Toolbar>：调用方负责提供 Toolbar root（Radix
  // ToolbarPrimitive.Root = RovingFocusGroup 上下文）。ToolbarButton
  // (ToolbarPrimitive.Button = RovingFocusGroupItem) 必须有 RovingFocusGroup
  // 祖先，否则会抛 "RovingFocusGroupItem must be used within
  // RovingFocusGroup" 并清空原生选区。两条调用路径各自提供 root：
  // - ReaderRecordPlateSurface（Reader-owned）：在 ReaderFloatingSurface 内
  //   显式包 <Toolbar>，data-reader-record-floating-toolbar="selection-actions"
  // - 旧 FloatingToolbarKit：通过 <FloatingToolbar> 已提供 <Toolbar>，
  //   此处再包一层会导致 <Toolbar><Toolbar></Toolbar></Toolbar> 嵌套
  //   role=toolbar / RovingFocusGroup。
  return (
    <>
      <ToolbarGroup className={toolbarGroupClassName} data-reader-record-toolbar-layout="ask-first">
        <ReaderAskToolbarButton />
      </ToolbarGroup>
      <ToolbarGroup className={toolbarGroupClassName}>
        <ReaderLookupToolbarButton />
        <ReaderHighlightToolbarButton />
        <ReaderCopyToolbarButton />
        <ReaderNoteToolbarButton />
      </ToolbarGroup>
    </>
  );
}

/**
 * Ask 快捷框（Plate AI Editor 式）：选区下方的输入框 + 快捷指令列表。
 * 由 Surface 托管渲染与状态（attachment/rect 打开时冻结），本组件只负责
 * 输入与选择。Enter 发送自由提问；快捷指令点击即发；Esc 关闭。
 * 提交统一落入右侧 Ask 面板会话（阅读器不做就地文本改写）。
 */
export function ReaderAskQuickMenu({
  onSubmit,
  onClose,
}: {
  onSubmit: (request: {
    content: string;
    entryAction?: ReaderAskEntryActionDto;
    submissionMode?: "chat" | "quick_action";
  }) => void;
  onClose: () => void;
}) {
  const [prompt, setPrompt] = useState("");
  const inputId = useId();

  const submitPrompt = () => {
    const content = prompt.trim();
    if (!content) {
      return;
    }
    onSubmit({ content, submissionMode: "chat" });
  };

  return (
    <div
      className="w-[26rem]"
      data-reader-record-ask-menu="open"
      data-plate-focus="true"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <label htmlFor={inputId} className="sr-only">
        输入 Ask Claread 问题
      </label>
      <input
        id={inputId}
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submitPrompt();
          }
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
          }
        }}
        placeholder="Ask Claread anything..."
        autoFocus
        data-plate-focus="true"
        data-reader-record-ask-prompt="true"
        className="h-11 w-full border-b border-hairline/70 bg-transparent px-4 text-[0.95rem] text-ink outline-none placeholder:text-muted-foreground/70"
      />
      <div className="flex flex-col gap-0.5 p-1.5" role="list">
        {READER_ASK_QUICK_ACTIONS.map((action) => (
          <button
            key={action.entryAction}
            type="button"
            role="listitem"
            className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-ink/[0.05] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30"
            data-reader-record-ask-quick-action={action.entryAction}
            onClick={() =>
              onSubmit({
                content: action.content,
                entryAction: action.entryAction,
                submissionMode: "quick_action",
              })
            }
          >
            <span className="inline-flex size-5 shrink-0 items-center justify-center text-ink/70">
              {action.icon}
            </span>
            <span className="text-sm font-medium text-ink">{action.label}</span>
            <span className="ml-auto shrink-0 whitespace-nowrap text-xs text-muted-foreground/80">
              {action.description}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function ReaderToolbarActionsProvider({
  value,
  children,
}: {
  value: ReaderToolbarActions;
  children: ReactNode;
}) {
  return (
    <ReaderToolbarActionsContext.Provider value={value}>
      {children}
    </ReaderToolbarActionsContext.Provider>
  );
}
