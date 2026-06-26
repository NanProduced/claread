/**
 * Reader Floating Toolbar Buttons — 迁移自 SelectionToolbar 的按钮逻辑
 *
 * 四个按钮：
 * - 高亮（Highlighter）
 * - 笔记（NotebookPen）
 * - 查词（Search）
 * - Ask（MessageSquare）
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
import { Highlighter, MessageSquare, NotebookPen, Search } from "lucide-react";

import { ToolbarButton, ToolbarGroup, ToolbarSeparator } from "@/components/ui/toolbar";

export interface ReaderToolbarActions {
  onAsk: () => void;
  onHighlight: () => void;
  onNote: () => void;
  onLookup: () => void;
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

export function ReaderFloatingToolbarButtons() {
  const actions = useContext(ReaderToolbarActionsContext);
  const disabled = actions === null;

  return (
    <>
      <ToolbarGroup>
        <ToolbarButton
          tooltip="高亮"
          aria-label="高亮"
          disabled={disabled}
          onPointerDown={preventFocusLoss.onPointerDown}
          onClick={actions?.onHighlight}
        >
          <Highlighter className="size-4" />
        </ToolbarButton>
        <ToolbarButton
          tooltip="新建笔记"
          aria-label="新建笔记"
          disabled={disabled}
          onPointerDown={preventFocusLoss.onPointerDown}
          onClick={actions?.onNote}
        >
          <NotebookPen className="size-4" />
        </ToolbarButton>
      </ToolbarGroup>

      <ToolbarSeparator />

      <ToolbarGroup>
        <ToolbarButton
          tooltip="查词"
          aria-label="查词"
          disabled={disabled}
          onPointerDown={preventFocusLoss.onPointerDown}
          onClick={actions?.onLookup}
        >
          <Search className="size-4" />
        </ToolbarButton>
        <ToolbarButton
          tooltip="Ask"
          aria-label="Ask"
          disabled={disabled}
          onPointerDown={preventFocusLoss.onPointerDown}
          onClick={actions?.onAsk}
        >
          <MessageSquare className="size-4" />
        </ToolbarButton>
      </ToolbarGroup>
    </>
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
