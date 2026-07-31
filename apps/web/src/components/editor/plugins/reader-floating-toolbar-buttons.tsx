/**
 * Reader Floating Toolbar Buttons — Reader Record Plate 的默认划选工具栏
 *
 * 六个按钮：
 * - 查词（Search）
 * - 复制（Copy）
 * - Ask（Sparkles）
 * - 加入 Ask（MessageSquarePlus）
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

import { createContext, useContext, useEffect, useId, useState, type ReactNode } from "react";
import {
  BookOpenText,
  Copy,
  Highlighter,
  MessageSquarePlus,
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
  /**
   * ASK-UX-COT-COMPOSER-R3 P1 — pin the current selection into the Ask
   * composer's manual selection slots (auto→manual promotion or append,
   * anchor-fingerprint dedupe, max 3). Hosts that do not implement the
   * selection-slot model fall back to {@link onAsk}.
   */
  onPinSelectionToAsk?: () => void;
  /**
   * Disabled state for the pin action (independent of `state.ask`): set
   * when the manual selection cap is reached for a not-yet-pinned
   * selection. Carries the user-facing reason.
   */
  pinSelectionState?: ReaderToolbarActionState;
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
  "rounded-[8px] text-ink/80 transition-colors hover:bg-lens-blue-soft/35 hover:text-ink active:bg-lens-blue-soft/50 disabled:cursor-not-allowed disabled:opacity-40";
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
  const tooltip = toolbarTooltip("Ask Claread", state, "Ctrl+J");
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const inputId = useId();

  useEffect(() => {
    if (state.disabled || !actions) {
      return;
    }

    function handleAskShortcut(event: KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey) || event.shiftKey || event.altKey) {
        return;
      }
      if (event.key.toLowerCase() !== "j") {
        return;
      }
      event.preventDefault();
      setOpen(true);
    }

    window.addEventListener("keydown", handleAskShortcut);
    return () => {
      window.removeEventListener("keydown", handleAskShortcut);
    };
  }, [actions, state.disabled]);

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
      <AIMenu open={open} onOpenChange={setOpen}>
        <AIMenuAnchor>
          <span>
            <ToolbarButton
              className={`${toolbarButtonClassName} ${
                open ? "bg-lens-blue-soft/60 " : ""
              }gap-1.5 px-3 text-lens-blue hover:bg-lens-blue-soft/50 hover:text-lens-blue active:bg-lens-blue-soft/65`}
              size="default"
              tooltip={tooltip}
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
              autoFocus
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
            </AIMenuList>
          </AIMenuCommand>
        </AIMenuContent>
      </AIMenu>
    </div>
  );
}

export function ReaderPinAskToolbarButton() {
  const actions = useContext(ReaderToolbarActionsContext);
  const askState = actionState(actions, "ask");
  const pinState = actions?.pinSelectionState;
  const state: ReaderToolbarActionState = {
    disabled: askState.disabled || Boolean(pinState?.disabled),
    reason: askState.disabled ? askState.reason : pinState?.reason,
  };

  return (
    <ToolbarButton
      className={`${toolbarButtonClassName} gap-1.5 px-3 text-lens-blue hover:bg-lens-blue-soft/50 hover:text-lens-blue active:bg-lens-blue-soft/65`}
      size="default"
      tooltip={toolbarTooltip("加入 Ask Claread", state)}
      aria-label="加入 Ask Claread"
      data-reader-record-action="pin-ask"
      data-reader-record-ask-pin-selection="true"
      data-reader-record-disabled-reason={state.disabled ? state.reason : undefined}
      disabled={state.disabled}
      onPointerDown={preventFocusLoss.onPointerDown}
      onClick={() => {
        if (state.disabled || !actions) {
          return;
        }
        if (actions.onPinSelectionToAsk) {
          actions.onPinSelectionToAsk();
        } else {
          actions.onAsk();
        }
      }}
    >
      <MessageSquarePlus className="size-4" />
      <span className="text-sm font-medium">加入 Ask</span>
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
        <ReaderPinAskToolbarButton />
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
