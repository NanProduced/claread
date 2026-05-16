import {
  Bookmark,
  Check,
  Eraser,
  Heart,
  Highlighter,
  MessageCircle,
  MessageSquare,
  MoreHorizontal,
  NotebookPen,
  Quote,
  Search,
  Send,
  Trash2,
} from "lucide-react";
import { forwardRef, type CSSProperties, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export type SelectionToolbarAction =
  | "ask"
  | "selectSentence"
  | "highlight"
  | "note"
  | "favorite"
  | "lookup"
  | "feedback"
  | "clear"
  | "more";

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
  feedback?: boolean;
  clear?: boolean;
  more?: boolean;
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
  onFeedback?: (selectedText: string) => void;
  onMore?: (selectedText: string) => void;
}

export const defaultSelectionToolbarColorOptions: SelectionToolbarColorOption[] = [
  {
    value: "warm_yellow",
    label: "珊瑚",
    swatchClassName: "bg-[#E8A18C]/90 ring-[#9D5F4A]/25",
  },
  {
    value: "soft_blue",
    label: "雾青",
    swatchClassName: "bg-[#8ECAD0]/88 ring-[#2F747B]/25",
  },
  {
    value: "sage_green",
    label: "灰绿",
    swatchClassName: "bg-[#BCC2A3]/90 ring-[#6A704F]/25",
  },
];

interface ToolbarButtonProps {
  label: string;
  icon: ReactNode;
  disabled: boolean;
  active?: boolean;
  pressed?: boolean;
  className?: string;
  title?: string;
  onClick?: () => void;
}

function selectedTextSummary(selectedText: string) {
  const normalized = selectedText.trim().replace(/\s+/g, " ");

  if (normalized.length <= 54) {
    return normalized;
  }

  return `${normalized.slice(0, 54)}...`;
}

function selectionModeLabel(selectionMode: NonNullable<SelectionToolbarProps["selectionMode"]>) {
  if (selectionMode === "sentence") {
    return "整句";
  }
  if (selectionMode === "multi_text") {
    return "跨句选区";
  }
  return "局部选区";
}

function ToolbarDivider() {
  return <span aria-hidden="true" className="mx-1 hidden h-6 w-px shrink-0 bg-hairline sm:inline-flex" />;
}

function ToolbarButton({
  label,
  icon,
  disabled,
  active = false,
  pressed,
  className,
  title,
  onClick,
}: ToolbarButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "focus-ring group relative inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[9px] border border-transparent text-muted transition-colors hover:border-hairline hover:bg-reader-paper hover:text-ink disabled:cursor-not-allowed disabled:opacity-40",
        active && "border-hairline bg-reader-paper text-ink shadow-surface-quiet",
        className,
      )}
      disabled={disabled}
      aria-label={label}
      aria-pressed={pressed}
      title={title ?? label}
      onClick={onClick}
    >
      {icon}
      <span className="pointer-events-none absolute -top-9 left-1/2 z-10 hidden -translate-x-1/2 whitespace-nowrap rounded-[7px] bg-ink px-2 py-1 text-[0.68rem] font-semibold text-white shadow-surface-quiet group-hover:block group-focus-visible:block">
        {label}
      </span>
    </button>
  );
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
    onFeedback,
    onMore,
  },
  ref,
) {
  const hasSelection = selectedText.trim().length > 0;
  const selectionLabel = selectedTextSummary(selectedText);
  const askComingSoon = disabled?.ask ?? true;
  const askDisabled = !hasSelection || askComingSoon || !onAsk;
  const selectSentenceDisabled =
    selectionMode !== "text_range" || !hasSelection || Boolean(disabled?.selectSentence) || !onSelectSentence;
  const highlightDisabled = !hasSelection || Boolean(disabled?.highlight) || !onHighlight;
  const noteDisabled = !hasSelection || Boolean(disabled?.note) || !onNote;
  const favoriteDisabled = !hasSelection || Boolean(disabled?.favorite) || !onFavorite;
  const lookupDisabled = !hasSelection || Boolean(disabled?.lookup) || !onLookup;
  const feedbackDisabled = !hasSelection || Boolean(disabled?.feedback) || !onFeedback;
  const clearDisabled = !hasAnnotation || Boolean(disabled?.clear) || !onClearAnnotation;
  const moreDisabled = !hasSelection || Boolean(disabled?.more) || !onMore;
  const noteLength = noteValue.length;

  return (
    <div
      ref={ref}
      role="toolbar"
      aria-label={selectionLabel ? `选区工具栏，已选文本：${selectionLabel}` : "选区工具栏"}
      title={selectionLabel ? `已选文本：${selectionLabel}` : undefined}
      style={style}
      className={cn(
        "w-max max-w-[calc(100vw-1rem)] rounded-[14px] border border-hairline bg-surface-warm/96 p-1.5 text-ink shadow-[0_18px_54px_rgba(28,24,18,0.14),0_1px_2px_rgba(17,17,17,0.04)] backdrop-blur-sm",
        "supports-[backdrop-filter]:bg-surface-warm/90",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-1">
        <button
          type="button"
          className="focus-ring inline-flex h-9 shrink-0 items-center gap-2 rounded-[9px] border border-hairline bg-reader-paper px-2.5 text-xs font-semibold text-muted transition-colors hover:border-muted hover:text-ink disabled:cursor-not-allowed disabled:opacity-55"
          disabled={askDisabled}
          aria-label={askComingSoon ? "Ask Claread，稍后开放" : "Ask Claread"}
          title={askComingSoon ? "Ask Claread coming soon" : "Ask Claread"}
          onClick={() => onAsk?.(selectedText)}
        >
          <MessageSquare aria-hidden="true" className="h-4 w-4 text-lens-blue/70" />
          <span>Ask Claread</span>
          {askComingSoon ? (
            <span className="rounded-pill border border-hairline bg-surface px-1.5 py-0.5 text-[0.625rem] font-semibold leading-none text-subtle">
              soon
            </span>
          ) : null}
        </button>

        <span className="inline-flex h-9 shrink-0 items-center rounded-[9px] border border-hairline bg-surface px-3 text-xs font-semibold text-ink-soft">
          {selectionModeLabel(selectionMode)}
        </span>

        <ToolbarDivider />

        <ToolbarButton
          label="选择当前句子"
          icon={<Quote aria-hidden="true" className="h-4 w-4" />}
          disabled={selectSentenceDisabled}
          active={selectionMode === "sentence"}
          onClick={() => onSelectSentence?.(selectedText)}
        />

        <div className="flex shrink-0 items-center gap-1 rounded-[10px] border border-transparent px-0.5" aria-label="用户高亮颜色">
          <span
            className="inline-flex h-9 w-8 items-center justify-center rounded-[9px] text-muted"
            title="用户高亮"
            aria-hidden="true"
          >
            <Highlighter className="h-4 w-4" />
          </span>
          {colorOptions.map((option) => {
            const colorDisabled = highlightDisabled || Boolean(option.disabled);
            const active = activeColor === option.value && hasHighlight;

            return (
              <button
                key={option.value}
                type="button"
                className={cn(
                  "focus-ring relative inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[9px] border border-transparent bg-transparent transition-colors hover:border-hairline hover:bg-reader-paper disabled:cursor-not-allowed disabled:opacity-40",
                  active && "border-lens-blue/35 bg-reader-paper ring-2 ring-lens-blue/15",
                )}
                disabled={colorDisabled}
                aria-label={`用${option.label}标注选区`}
                aria-pressed={active}
                title={`${option.label}高亮`}
                onClick={() => onHighlight?.(option.value, selectedText, option)}
              >
                <span
                  aria-hidden="true"
                  className={cn("h-[1.125rem] w-[1.125rem] rounded-[5px] ring-1 ring-inset ring-hairline", option.swatchClassName)}
                />
                {active ? (
                  <Check aria-hidden="true" className="absolute h-3 w-3 text-ink drop-shadow-[0_1px_0_rgba(255,255,255,0.8)]" />
                ) : null}
              </button>
            );
          })}
        </div>

        <ToolbarDivider />

        <ToolbarButton
          label={hasNote ? "编辑笔记" : "笔记"}
          icon={<NotebookPen aria-hidden="true" className="h-4 w-4" />}
          disabled={noteDisabled}
          active={noteOpen || hasNote}
          onClick={() => onNote?.(selectedText)}
        />
        <ToolbarButton
          label={favorited ? "取消收藏" : "收藏"}
          icon={<Heart aria-hidden="true" className={cn("h-4 w-4", favorited && "fill-vocab-amber text-vocab-amber")} />}
          disabled={favoriteDisabled}
          active={favorited}
          pressed={favorited}
          onClick={() => onFavorite?.(selectedText)}
        />
        <ToolbarButton
          label="查词"
          icon={<Search aria-hidden="true" className="h-4 w-4" />}
          disabled={lookupDisabled}
          onClick={() => onLookup?.(selectedText)}
        />
        <ToolbarButton
          label="反馈"
          icon={<MessageCircle aria-hidden="true" className="h-4 w-4" />}
          disabled={feedbackDisabled}
          onClick={() => onFeedback?.(selectedText)}
        />
        <ToolbarButton
          label="取消标注"
          icon={<Eraser aria-hidden="true" className="h-4 w-4" />}
          disabled={clearDisabled}
          onClick={onClearAnnotation}
        />
        <ToolbarButton
          label="更多"
          icon={<MoreHorizontal aria-hidden="true" className="h-4 w-4" />}
          disabled={moreDisabled}
          onClick={() => onMore?.(selectedText)}
        />
      </div>

      {noteOpen ? (
        <div className="mt-1.5 rounded-[12px] border border-hairline bg-reader-paper/92 p-2.5">
          <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
            <span className="font-semibold text-ink">笔记</span>
            <span className={cn("text-subtle", noteLength > noteMaxLength && "text-destructive")}>
              {noteLength}/{noteMaxLength}
            </span>
          </div>
          <textarea
            data-selection-note-input="true"
            className="focus-ring min-h-20 w-full resize-none rounded-[10px] border border-hairline bg-surface px-3 py-2 text-sm leading-6 text-ink outline-none placeholder:text-subtle"
            placeholder="写一句和这段文字绑定的笔记。"
            value={noteValue}
            maxLength={noteMaxLength}
            onChange={(event) => onNoteChange?.(event.target.value)}
          />
          <div className="mt-2 flex items-center justify-end gap-2">
            {hasNote ? (
              <button
                type="button"
                className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-[8px] border border-hairline bg-surface px-2.5 text-xs font-semibold text-muted transition-colors hover:border-muted hover:text-ink disabled:opacity-50"
                disabled={noteSaving || !onNoteClear}
                onClick={onNoteClear}
              >
                <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                删除
              </button>
            ) : null}
            <button
              type="button"
              className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-[8px] border border-lens-blue/30 bg-lens-blue px-3 text-xs font-semibold text-white transition-colors hover:bg-lens-blue/90 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={noteSaving || noteLength === 0 || noteLength > noteMaxLength || !onNoteSave}
              onClick={onNoteSave}
            >
              {noteSaving ? <Bookmark aria-hidden="true" className="h-3.5 w-3.5" /> : <Send aria-hidden="true" className="h-3.5 w-3.5" />}
              保存
            </button>
          </div>
        </div>
      ) : null}
      {!noteOpen && statusMessage ? (
        <div
          className={cn(
            "mt-1.5 rounded-[9px] border px-2.5 py-1.5 text-xs font-medium",
            statusKind === "error"
              ? "border-destructive/20 bg-destructive/10 text-destructive"
              : "border-hairline bg-reader-paper/75 text-muted",
          )}
        >
          {statusMessage}
        </div>
      ) : null}
    </div>
  );
});
