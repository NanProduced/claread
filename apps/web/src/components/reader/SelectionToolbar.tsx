import {
  Eraser,
  Highlighter,
  MessageSquare,
  MoreHorizontal,
  NotebookPen,
  Quote,
  Search,
} from "lucide-react";
import { forwardRef, type CSSProperties } from "react";
import { cn } from "../../lib/cn";
import {
  ReaderToolbarActionButton,
  ReaderToolbarButton,
  ReaderToolbarIconButton,
  ReaderToolbarMenu,
  ReaderToolbarMenuContent,
  ReaderToolbarMenuItem,
  ReaderToolbarMenuLabel,
  ReaderToolbarMenuRadioGroup,
  ReaderToolbarMenuRadioItem,
  ReaderToolbarMenuSeparator,
  ReaderToolbarMenuTrigger,
  ReaderToolbarRoot,
  ReaderToolbarSeparator,
  ReaderToolbarSplitAction,
} from "./plate-ui-adapter";

export type SelectionToolbarAction =
  | "ask"
  | "selectSentence"
  | "highlight"
  | "note"
  | "lookup"
  | "clear";

export type SelectionToolbarColorValue = string;

export interface SelectionToolbarColorOption {
  value: SelectionToolbarColorValue;
  label: string;
  swatchClassName: string;
  disabled?: boolean;
}

export interface SelectionToolbarDisabledStates {
  ask?: boolean;
  selectSentence?: boolean;
  highlight?: boolean;
  note?: boolean;
  lookup?: boolean;
  clear?: boolean;
}

export interface SelectionToolbarProps {
  selectedText: string;
  selectionMode?: "text_range" | "sentence" | "multi_text";
  colorOptions?: SelectionToolbarColorOption[];
  activeColor?: SelectionToolbarColorValue | null;
  hasAnnotation?: boolean;
  hasHighlight?: boolean;
  hasNote?: boolean;
  disabled?: SelectionToolbarDisabledStates;
  statusMessage?: string;
  statusKind?: "saving" | "saved" | "error";
  className?: string;
  style?: CSSProperties;
  onAsk?: (selectedText: string) => void;
  onSelectSentence?: (selectedText: string) => void;
  onHighlight?: (
    color: SelectionToolbarColorValue,
    selectedText: string,
    option: SelectionToolbarColorOption,
  ) => void;
  onNote?: (selectedText: string) => void;
  onClearAnnotation?: () => void;
  onLookup?: (selectedText: string) => void;
}

export const defaultSelectionToolbarColorOptions: SelectionToolbarColorOption[] = [
  {
    value: "warm_yellow",
    label: "暖黄",
    swatchClassName: "bg-vocab-amber/75 ring-vocab-amber/25",
  },
  {
    value: "soft_blue",
    label: "雾青",
    swatchClassName: "bg-context-blue/65 ring-context-blue/25",
  },
  {
    value: "sage_green",
    label: "灰绿",
    swatchClassName: "bg-structure-green/45 ring-structure-green/25",
  },
];

function selectedTextSummary(selectedText: string) {
  const normalized = selectedText.trim().replace(/\s+/g, " ");

  if (normalized.length <= 42) {
    return normalized;
  }

  return `${normalized.slice(0, 42)}...`;
}

function selectionModeLabel(selectionMode: NonNullable<SelectionToolbarProps["selectionMode"]>) {
  if (selectionMode === "sentence") {
    return "整句";
  }
  if (selectionMode === "multi_text") {
    return "多句";
  }
  return "已选片段";
}

export const SelectionToolbar = forwardRef<HTMLDivElement, SelectionToolbarProps>(function SelectionToolbar(
  {
    selectedText,
    selectionMode = "text_range",
    colorOptions = defaultSelectionToolbarColorOptions,
    activeColor = null,
    hasAnnotation = false,
    hasHighlight = false,
    hasNote = false,
    disabled,
    statusMessage,
    statusKind,
    className,
    style,
    onAsk,
    onSelectSentence,
    onHighlight,
    onNote,
    onClearAnnotation,
    onLookup,
  },
  ref,
) {
  const hasSelection = selectedText.trim().length > 0;
  const selectionLabel = selectedTextSummary(selectedText);
  const askComingSoon = Boolean(disabled?.ask);
  const askDisabled = !hasSelection || askComingSoon || !onAsk;
  const selectSentenceDisabled =
    selectionMode !== "text_range" || !hasSelection || Boolean(disabled?.selectSentence) || !onSelectSentence;
  const highlightDisabled = !hasSelection || Boolean(disabled?.highlight) || !onHighlight;
  const noteDisabled = !hasSelection || Boolean(disabled?.note) || !onNote;
  const lookupDisabled = !hasSelection || Boolean(disabled?.lookup) || !onLookup;
  const clearDisabled = !hasAnnotation || Boolean(disabled?.clear) || !onClearAnnotation;
  const moreDisabled = !hasSelection;

  return (
    <div
      ref={ref}
      aria-label={selectionLabel ? `选区工具栏，已选文本：${selectionLabel}` : "选区工具栏"}
      title={selectionLabel ? `已选文本：${selectionLabel}` : undefined}
      style={style}
      className={cn("w-max max-w-[calc(100vw-1rem)] text-ink", className)}
    >
      <ReaderToolbarRoot aria-label={selectionLabel ? `选区工具栏，已选文本：${selectionLabel}` : "选区工具栏"}>
        <span className="inline-flex h-10 shrink-0 items-center rounded-md border border-border/55 bg-background/56 px-2.5 text-[0.72rem] font-medium text-muted-foreground">
          {selectionModeLabel(selectionMode)}
        </span>

        <ReaderToolbarActionButton
          className="gap-2 px-3 font-medium text-foreground/78"
          disabled={askDisabled}
          aria-label={askComingSoon ? "Ask Claread，稍后开放" : "Ask Claread"}
          title={askComingSoon ? "Ask Claread coming soon" : "Ask Claread"}
          onClick={() => onAsk?.(selectedText)}
        >
          <MessageSquare aria-hidden="true" className="h-4 w-4 text-lens-blue/75" />
          <span>Ask</span>
          {askComingSoon ? (
            <span className="rounded-full border border-border bg-background px-1.5 py-0.5 text-[0.625rem] font-semibold leading-none text-muted-foreground">
              soon
            </span>
          ) : null}
        </ReaderToolbarActionButton>

        <ReaderToolbarSeparator aria-hidden="true" />

        <ReaderToolbarSplitAction
          active={hasHighlight}
          disabled={highlightDisabled}
          icon={<Highlighter aria-hidden="true" className="h-4 w-4" />}
          label={hasHighlight ? "更换高亮颜色" : "高亮"}
          onPrimaryClick={() => {
            const defaultOption =
              colorOptions.find((option) => option.value === activeColor && !option.disabled) ??
              colorOptions.find((option) => !option.disabled);
            if (defaultOption) {
              onHighlight?.(defaultOption.value, selectedText, defaultOption);
            }
          }}
        >
          <ReaderToolbarMenuLabel>选择高亮颜色</ReaderToolbarMenuLabel>
          <ReaderToolbarMenuSeparator />
          <ReaderToolbarMenuRadioGroup value={activeColor ?? undefined}>
            {colorOptions.map((option) => (
              <ReaderToolbarMenuRadioItem
                key={option.value}
                value={option.value}
                disabled={Boolean(option.disabled)}
                onSelect={(event) => {
                  event.preventDefault();
                  onHighlight?.(option.value, selectedText, option);
                }}
              >
                <span
                  aria-hidden="true"
                  className={cn("mr-1.5 h-3.5 w-3.5 rounded-[4px] ring-1 ring-inset ring-border/70", option.swatchClassName)}
                />
                {option.label}
              </ReaderToolbarMenuRadioItem>
            ))}
          </ReaderToolbarMenuRadioGroup>
        </ReaderToolbarSplitAction>

        <ReaderToolbarIconButton
          aria-label={hasNote ? "编辑笔记" : "笔记"}
          title={hasNote ? "编辑笔记" : "笔记"}
          disabled={noteDisabled}
          active={hasNote}
          onClick={() => onNote?.(selectedText)}
        >
          <NotebookPen aria-hidden="true" className="h-4 w-4" />
        </ReaderToolbarIconButton>

        <ReaderToolbarMenu>
          <ReaderToolbarMenuTrigger asChild disabled={moreDisabled}>
            <ReaderToolbarIconButton
              aria-label="更多选区操作"
              title="更多选区操作"
            >
              <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
            </ReaderToolbarIconButton>
          </ReaderToolbarMenuTrigger>
          <ReaderToolbarMenuContent align="end" className="w-52">
            <ReaderToolbarMenuLabel>选区操作</ReaderToolbarMenuLabel>
            <ReaderToolbarMenuSeparator />
            <ReaderToolbarMenuItem disabled={selectSentenceDisabled} onSelect={() => onSelectSentence?.(selectedText)}>
              <Quote aria-hidden="true" className="h-4 w-4" />
              选择整句
            </ReaderToolbarMenuItem>
            <ReaderToolbarMenuItem disabled={lookupDisabled} onSelect={() => onLookup?.(selectedText)}>
              <Search aria-hidden="true" className="h-4 w-4" />
              查词
            </ReaderToolbarMenuItem>
            <ReaderToolbarMenuSeparator />
            <ReaderToolbarMenuItem disabled={clearDisabled} onSelect={onClearAnnotation}>
              <Eraser aria-hidden="true" className="h-4 w-4" />
              取消标注
            </ReaderToolbarMenuItem>
          </ReaderToolbarMenuContent>
        </ReaderToolbarMenu>
      </ReaderToolbarRoot>
      {statusMessage ? (
        <div
          className={cn(
            "mt-2 rounded-md border px-3 py-2 text-xs font-medium shadow-sm",
            statusKind === "error"
              ? "border-destructive/20 bg-destructive/10 text-destructive"
              : "border-border/65 bg-background/92 text-muted-foreground",
          )}
        >
          {statusMessage}
        </div>
      ) : null}
    </div>
  );
});
