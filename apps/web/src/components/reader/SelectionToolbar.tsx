import {
  Eraser,
  Highlighter,
  NotebookPen,
  Quote,
  Search,
  Sparkles,
} from "lucide-react";
import { forwardRef, type CSSProperties } from "react";
import { cn } from "../../lib/cn";
import {
  ReaderToolbarGroup,
  ReaderToolbarIconButton,
  ReaderToolbarRoot,
  ReaderToolbarSeparator,
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
  canToggleHighlightPalette?: boolean;
  hasNote?: boolean;
  highlightPaletteOpen?: boolean;
  disabled?: SelectionToolbarDisabledStates;
  className?: string;
  style?: CSSProperties;
  onAsk?: (selectedText: string) => void;
  onSelectSentence?: (selectedText: string) => void;
  onHighlight?: (
    color: SelectionToolbarColorValue,
    selectedText: string,
    option: SelectionToolbarColorOption,
  ) => void;
  onToggleHighlightPalette?: () => void;
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

export const SelectionToolbar = forwardRef<HTMLDivElement, SelectionToolbarProps>(function SelectionToolbar(
  {
    selectedText,
    selectionMode = "text_range",
    colorOptions = defaultSelectionToolbarColorOptions,
    activeColor = null,
    hasAnnotation = false,
    hasHighlight = false,
    canToggleHighlightPalette = false,
    hasNote = false,
    highlightPaletteOpen = false,
    disabled,
    className,
    style,
    onAsk,
    onSelectSentence,
    onHighlight,
    onToggleHighlightPalette,
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
  const defaultOption =
    colorOptions.find((option) => option.value === activeColor && !option.disabled) ??
    colorOptions.find((option) => !option.disabled);

  return (
    <div
      ref={ref}
      aria-label={selectionLabel ? `选区工具栏，已选文本：${selectionLabel}` : "选区工具栏"}
      title={selectionLabel ? `已选文本：${selectionLabel}` : undefined}
      style={style}
      className={cn("w-max max-w-[calc(100vw-1rem)] text-ink", className)}
    >
      <ReaderToolbarRoot
        aria-label={selectionLabel ? `选区工具栏，已选文本：${selectionLabel}` : "选区工具栏"}
        className="max-w-[min(44rem,calc(100vw-1rem))] gap-1 rounded-[0.95rem] p-1"
      >
        <ReaderToolbarIconButton
          active={hasHighlight}
          aria-label={hasHighlight && canToggleHighlightPalette ? "切换高亮颜色" : "高亮"}
          title={hasHighlight && canToggleHighlightPalette ? "切换高亮颜色" : "高亮"}
          disabled={highlightDisabled}
          className="h-8 min-w-8 rounded-[0.8rem] px-2"
          onClick={() => {
            if (!hasHighlight || !canToggleHighlightPalette) {
              if (defaultOption) {
                onHighlight?.(defaultOption.value, selectedText, defaultOption);
              }
              return;
            }
            onToggleHighlightPalette?.();
          }}
        >
          <Highlighter aria-hidden="true" className="h-4 w-4" />
        </ReaderToolbarIconButton>

        {hasHighlight && highlightPaletteOpen ? (
          <ReaderToolbarGroup className="rounded-[0.8rem] border border-border/60 bg-background/82 px-1.5 py-0.5">
            {colorOptions.map((option) => {
              const selected = option.value === activeColor;
              return (
                <button
                  key={option.value}
                  type="button"
                  className={cn(
                    "focus-ring inline-flex h-6 w-6 items-center justify-center rounded-full border border-transparent transition-transform hover:scale-[1.04] disabled:cursor-not-allowed disabled:opacity-40",
                    selected ? "border-border/60 bg-background shadow-sm" : "hover:bg-muted/40",
                  )}
                  aria-label={`切换为${option.label}`}
                  title={option.label}
                  disabled={Boolean(option.disabled)}
                  onClick={() => onHighlight?.(option.value, selectedText, option)}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      "h-3.5 w-3.5 rounded-full ring-1 ring-inset ring-border/40",
                      option.swatchClassName,
                    )}
                  />
                </button>
              );
            })}
          </ReaderToolbarGroup>
        ) : null}

        <ReaderToolbarSeparator aria-hidden="true" />

        <ReaderToolbarIconButton
          aria-label={hasNote ? "编辑笔记" : "笔记"}
          title={hasNote ? "编辑笔记" : "笔记"}
          disabled={noteDisabled}
          active={hasNote}
          className="h-8 min-w-8 rounded-[0.8rem] px-2"
          onClick={() => onNote?.(selectedText)}
        >
          <NotebookPen aria-hidden="true" className="h-4 w-4" />
        </ReaderToolbarIconButton>

        <ReaderToolbarIconButton
          aria-label="查词"
          title="查词"
          disabled={lookupDisabled}
          className="h-8 min-w-8 rounded-[0.8rem] px-2"
          onClick={() => onLookup?.(selectedText)}
        >
          <Search aria-hidden="true" className="h-4 w-4" />
        </ReaderToolbarIconButton>

        {selectionMode === "text_range" ? (
          <ReaderToolbarIconButton
            aria-label="扩展到整句"
            title="扩展到整句"
            disabled={selectSentenceDisabled}
            className="h-8 min-w-8 rounded-[0.8rem] px-2"
            onClick={() => onSelectSentence?.(selectedText)}
          >
            <Quote aria-hidden="true" className="h-4 w-4" />
          </ReaderToolbarIconButton>
        ) : null}

        <ReaderToolbarIconButton
          aria-label={askComingSoon ? "AI，稍后开放" : "AI"}
          title={askComingSoon ? "AI coming soon" : "AI"}
          disabled={askDisabled}
          className="h-8 min-w-8 rounded-[0.8rem] px-2"
          onClick={() => onAsk?.(selectedText)}
        >
          <Sparkles aria-hidden="true" className="h-4 w-4 text-lens-blue/80" />
        </ReaderToolbarIconButton>

        {hasAnnotation ? (
          <ReaderToolbarIconButton
            aria-label="取消标注"
            title="取消标注"
            disabled={clearDisabled}
            className="h-8 min-w-8 rounded-[0.8rem] px-2"
            onClick={onClearAnnotation}
          >
            <Eraser aria-hidden="true" className="h-4 w-4" />
          </ReaderToolbarIconButton>
        ) : null}
      </ReaderToolbarRoot>
    </div>
  );
});
