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

import { createContext, useContext, useId, useState, type ReactNode } from "react";
import {
  BookOpenText,
  Copy,
  Highlighter,
  MessageSquare,
  NotebookPen,
  Search,
  Sparkles,
  WandSparkles,
} from "lucide-react";

import {
  AIMenu,
  AIMenuAnchor,
  AIMenuCommand,
  AIMenuContent,
  AIMenuEmpty,
  AIMenuInput,
  AIMenuItem,
  AIMenuList,
} from "@/components/ui/ai-menu";
import { ToolbarButton, ToolbarGroup } from "@/components/ui/toolbar";
import type { ReaderAskEntryActionDto } from "@/types/api/reader-ask";

export type ReaderToolbarActionId = "lookup" | "copy" | "ask" | "highlight" | "note";

export interface ReaderToolbarActionState {
  disabled: boolean;
  reason?: string;
}

export interface ReaderToolbarActions {
  onAsk: () => void;
  onAskSubmit?: (request: {
    content: string;
    entryAction?: ReaderAskEntryActionDto;
    submissionMode?: "chat" | "quick_action";
  }) => void;
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
const toolbarButtonClassName =
  "disabled:cursor-not-allowed disabled:opacity-40";

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
      size="default"
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
      <Search className="size-4" />
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
      <Copy className="size-4" />
    </ReaderActionToolbarButton>
  );
}

const askQuickActions: Array<{
  label: string;
  description: string;
  content: string;
  entryAction: ReaderAskEntryActionDto;
  icon: ReactNode;
}> = [
  {
    label: "解释这段",
    description: "结合上下文解释含义",
    content: "请结合上下文解释这段内容。",
    entryAction: "explain_this",
    icon: <BookOpenText className="size-4" />,
  },
  {
    label: "分析语法",
    description: "拆解语法和句子结构",
    content: "请分析这段内容的语法、句子结构和理解难点。",
    entryAction: "why_here",
    icon: <WandSparkles className="size-4" />,
  },
  {
    label: "提取重点词汇",
    description: "找出值得掌握的词和短语",
    content: "请提取这段内容里的重点词汇和短语，并结合语境解释。",
    entryAction: "lookup_in_context",
    icon: <Search className="size-4" />,
  },
];

export function ReaderAskToolbarButton() {
  const actions = useContext(ReaderToolbarActionsContext);
  const state = actionState(actions, "ask");
  const title = toolbarTitle("Ask Claread", state);
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const inputId = useId();

  const submitPrompt = () => {
    const content = prompt.trim();
    if (!content || state.disabled || !actions?.onAskSubmit) {
      return;
    }
    actions.onAskSubmit({
      content,
      entryAction: "ask_about_this",
      submissionMode: "chat",
    });
    setPrompt("");
    setOpen(false);
  };

  const submitQuickAction = (action: (typeof askQuickActions)[number]) => {
    if (state.disabled || !actions?.onAskSubmit) {
      return;
    }
    actions.onAskSubmit({
      content: action.content,
      entryAction: action.entryAction,
      submissionMode: "quick_action",
    });
    setOpen(false);
  };

  return (
    <div
      className="min-w-0"
      data-reader-record-ask-toolbar={open ? "open" : "closed"}
      data-plate-focus="true"
    >
      <AIMenu open={open && !state.disabled} onOpenChange={setOpen}>
        <AIMenuAnchor>
          <span>
            <ToolbarButton
              className={`${toolbarButtonClassName} gap-1.5 px-3 text-lens-blue hover:text-lens-blue`}
              size="default"
              tooltip={title}
              title={title}
              aria-label="Ask Claread"
              aria-expanded={open}
              aria-controls={open ? inputId : undefined}
              data-reader-record-action="ask"
              data-reader-record-toolbar-action="ask"
              data-reader-record-disabled-reason={state.disabled ? state.reason : undefined}
              disabled={state.disabled}
              onPointerDown={preventFocusLoss.onPointerDown}
              onClick={() => {
                if (state.disabled || !actions) {
                  return;
                }
                setOpen((current) => !current);
              }}
            >
              <Sparkles className="size-4" />
              <span className="text-sm font-medium">Ask</span>
            </ToolbarButton>
          </span>
        </AIMenuAnchor>

        <AIMenuContent
          data-reader-record-ask-menu="open"
          onPointerDown={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              setOpen(false);
            }
          }}
        >
          <AIMenuCommand>
            <label htmlFor={inputId} className="sr-only">
              输入 Ask Claread 问题
            </label>
            <AIMenuInput
              id={inputId}
              value={prompt}
              onValueChange={setPrompt}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  submitPrompt();
                }
              }}
              placeholder="Ask Claread anything..."
              data-reader-record-ask-prompt="true"
            />
            <AIMenuList>
              <AIMenuEmpty>输入问题后按 Enter 发送给 Ask Claread。</AIMenuEmpty>
              {askQuickActions.map((action) => (
                <AIMenuItem
                  key={action.entryAction}
                  value={action.label}
                  onSelect={() => submitQuickAction(action)}
                  data-reader-record-ask-quick-action={action.entryAction}
                >
                  <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-lens-blue">
                    {action.icon}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium leading-5 text-foreground">{action.label}</span>
                    <span className="block truncate text-xs leading-4 text-muted-foreground">
                      {action.description}
                    </span>
                  </span>
                </AIMenuItem>
              ))}
              <AIMenuItem
                value="加入 Ask 上下文"
                onSelect={() => {
                  actions?.onAsk();
                  setOpen(false);
                }}
                data-reader-record-ask-attach-context="true"
              >
                <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-lens-blue">
                  <MessageSquare className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block font-medium leading-5 text-foreground">加入 Ask 上下文</span>
                  <span className="block truncate text-xs leading-4 text-muted-foreground">
                    打开 Ask 面板后继续输入
                  </span>
                </span>
              </AIMenuItem>
            </AIMenuList>
          </AIMenuCommand>
        </AIMenuContent>
      </AIMenu>
    </div>
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

  return (
    <>
      <ToolbarGroup className={toolbarGroupClassName} data-reader-record-toolbar-layout="ask-first">
        <ReaderAskToolbarButton />
      </ToolbarGroup>
      <ToolbarGroup className={toolbarGroupClassName}>
        <ReaderLookupToolbarButton />
        <ReaderCopyToolbarButton />
      </ToolbarGroup>
      <ToolbarGroup className={toolbarGroupClassName}>
        <ReaderHighlightToolbarButton />
        <ReaderNoteToolbarButton />
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
