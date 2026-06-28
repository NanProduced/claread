/**
 * Reader Floating Toolbar Buttons — Reader Record Plate 的默认划选工具栏
 *
 * 五个按钮：
 * - 查词（Search）
 * - 复制（Copy）
 * - Ask（MessageSquare）
 * - 高亮（Highlighter）
 * - 笔记（NotebookPen）
 *
 * 通过 ReaderToolbarActionsContext 获取回调，由 ReaderRecordPlateSurface
 * 在渲染 Plate editor 时通过 Provider 注入。
 *
 * FloatingToolbar 的显示/隐藏由 Plate editor selection 自动管理
 *（useFloatingToolbar + showWhenReadOnly: true）。
 * 按钮触发时直接调用 context 回调，回调内部从 activeSelection 读取所需信息。
 */
"use client";

import { createContext, useContext, type ReactNode } from "react";
import { Copy, Highlighter, MessageSquare, NotebookPen, Search } from "lucide-react";

import { ToolbarButton, ToolbarGroup, ToolbarSeparator } from "@/components/ui/toolbar";

export type ReaderToolbarActionId = "lookup" | "copy" | "ask" | "highlight" | "note";

export interface ReaderToolbarActionState {
  disabled: boolean;
  reason?: string;
}

export interface ReaderToolbarActions {
  onAsk: () => void;
  onCopy: () => void;
  onHighlight: () => void;
  onNote: () => void;
  onLookup: () => void;
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

const toolbarGroupClassName = "items-center gap-0.5";
const toolbarButtonClassName =
  "h-7 min-w-7 rounded-[5px] px-1.5 text-ink-soft hover:bg-lens-blue-soft/65 hover:text-ink focus-visible:ring-2 focus-visible:ring-lens-blue/25 disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:text-ink-soft";

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

function ReaderActionToolbarButton({
  actionId,
  label,
  onAction,
  children,
}: {
  actionId: ReaderToolbarActionId;
  label: string;
  onAction: (actions: ReaderToolbarActions) => void;
  children: ReactNode;
}) {
  const actions = useContext(ReaderToolbarActionsContext);
  const state = actionState(actions, actionId);
  const title = toolbarTitle(label, state);

  return (
    <ToolbarButton
      className={toolbarButtonClassName}
      tooltip={title}
      title={title}
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
      <Search className="size-3.5" />
    </ReaderActionToolbarButton>
  );
}

export function ReaderCopyToolbarButton() {
  return (
    <ReaderActionToolbarButton
      actionId="copy"
      label="复制"
      onAction={(actions) => actions.onCopy()}
    >
      <Copy className="size-3.5" />
    </ReaderActionToolbarButton>
  );
}

export function ReaderAskToolbarButton() {
  return (
    <ReaderActionToolbarButton
      actionId="ask"
      label="Ask"
      onAction={(actions) => actions.onAsk()}
    >
      <MessageSquare className="size-3.5" />
    </ReaderActionToolbarButton>
  );
}

export function ReaderHighlightToolbarButton() {
  return (
    <ReaderActionToolbarButton
      actionId="highlight"
      label="高亮"
      onAction={(actions) => actions.onHighlight()}
    >
      <Highlighter className="size-3.5" />
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
      <NotebookPen className="size-3.5" />
    </ReaderActionToolbarButton>
  );
}

export function ReaderFloatingToolbarButtons() {
  return (
    <ToolbarGroup className={toolbarGroupClassName}>
      <ReaderLookupToolbarButton />
      <ReaderCopyToolbarButton />
      <ReaderAskToolbarButton />
      <ToolbarSeparator
        orientation="vertical"
        className="mx-1 h-4 w-px bg-border/70"
      />
      <ReaderHighlightToolbarButton />
      <ReaderNoteToolbarButton />
    </ToolbarGroup>
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
