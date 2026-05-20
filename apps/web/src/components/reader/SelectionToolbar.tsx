import {
  Bookmark,
  Eraser,
  Heart,
  Highlighter,
  MessageSquare,
  MoreHorizontal,
  NotebookPen,
  Quote,
  Search,
  Send,
  Trash2,
} from "lucide-react";
import { forwardRef, useId, type CSSProperties } from "react";
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
  ReaderToolbarPopoverCard,
  ReaderToolbarRoot,
  ReaderToolbarSeparator,
  ReaderToolbarSplitAction,
} from "./plate-ui-adapter";

export type SelectionToolbarAction =
  | "ask"
  | "selectSentence"
  | "highlight"
  | "note"
  | "favorite"
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
  favorite?: boolean;
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
  favorited?: boolean;
  disabled?: SelectionToolbarDisabledStates;
  noteOpen?: boolean;
  noteValue?: string;
  noteMaxLength?: number;
  noteSaving?: boolean;
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
  onNoteChange?: (value: string) => void;
  onNoteSave?: () => void;
  onNoteClear?: () => void;
  onClearAnnotation?: () => void;
  onFavorite?: (selectedText: string) => void;
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
    favorited = false,
    disabled,
    noteOpen = false,
    noteValue = "",
    noteMaxLength = 500,
    noteSaving = false,
    statusMessage,
    statusKind,
    className,
    style,
    onAsk,
    onSelectSentence,
    onHighlight,
    onNote,
    onNoteChange,
    onNoteSave,
    onNoteClear,
    onClearAnnotation,
    onFavorite,
    onLookup,
  },
  ref,
) {
  const noteId = useId();
  const hasSelection = selectedText.trim().length > 0;
  const selectionLabel = selectedTextSummary(selectedText);
  const askComingSoon = Boolean(disabled?.ask);
  const askDisabled = !hasSelection || askComingSoon || !onAsk;
  const selectSentenceDisabled =
    selectionMode !== "text_range" || !hasSelection || Boolean(disabled?.selectSentence) || !onSelectSentence;
  const highlightDisabled = !hasSelection || Boolean(disabled?.highlight) || !onHighlight;
  const noteDisabled = !hasSelection || Boolean(disabled?.note) || !onNote;
  const favoriteDisabled = !hasSelection || Boolean(disabled?.favorite) || !onFavorite;
  const lookupDisabled = !hasSelection || Boolean(disabled?.lookup) || !onLookup;
  const clearDisabled = !hasAnnotation || Boolean(disabled?.clear) || !onClearAnnotation;
  const moreDisabled = !hasSelection;
  const noteLength = noteValue.length;

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
          active={noteOpen || hasNote}
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
            <ReaderToolbarMenuItem disabled={favoriteDisabled} onSelect={() => onFavorite?.(selectedText)}>
              <Heart
                aria-hidden="true"
                className={cn("h-4 w-4", favorited && "fill-vocab-amber text-vocab-amber")}
              />
              {favorited ? "取消收藏" : "收藏"}
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

      {noteOpen ? (
        <ReaderToolbarPopoverCard>
          <div className="mb-2 flex items-center justify-between gap-3 text-xs">
            <label htmlFor={noteId} className="font-medium text-foreground">笔记</label>
            <span className={cn("text-muted-foreground", noteLength > noteMaxLength && "text-destructive")}>
              {noteLength}/{noteMaxLength}
            </span>
          </div>
          <textarea
            id={noteId}
            aria-label="选区笔记"
            data-selection-note-input="true"
            className="focus-ring min-h-18 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm leading-6 text-foreground outline-none placeholder:text-muted-foreground/80"
            placeholder="写一句和这段文字绑定的笔记。"
            value={noteValue}
            maxLength={noteMaxLength}
            onChange={(event) => onNoteChange?.(event.target.value)}
          />
          <div className="mt-3 flex items-center justify-between gap-2">
            {statusMessage ? (
              <span
                className={cn(
                  "text-xs font-medium",
                  statusKind === "error" ? "text-destructive" : "text-muted-foreground",
                )}
              >
                {statusMessage}
              </span>
            ) : (
              <span className="text-xs text-muted-foreground">保存后会直接绑定到原文锚点。</span>
            )}
            <span className="flex items-center gap-2">
              {hasNote ? (
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:border-border/80 hover:bg-muted/50 hover:text-foreground disabled:opacity-50"
                  disabled={noteSaving || !onNoteClear}
                  onClick={onNoteClear}
                >
                  <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                  删除
                </button>
              ) : null}
              <button
                type="button"
                className="focus-ring inline-flex min-h-10 items-center gap-1.5 rounded-md border border-transparent bg-primary px-3 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={noteSaving || noteLength === 0 || noteLength > noteMaxLength || !onNoteSave}
                onClick={onNoteSave}
              >
                {noteSaving ? (
                  <Bookmark aria-hidden="true" className="h-3.5 w-3.5" />
                ) : (
                  <Send aria-hidden="true" className="h-3.5 w-3.5" />
                )}
                保存
              </button>
            </span>
          </div>
        </ReaderToolbarPopoverCard>
      ) : null}

      {!noteOpen && statusMessage ? (
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
