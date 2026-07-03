import {
  Eraser,
  Flag,
  Highlighter,
  MessageSquare,
  NotebookPen,
  Quote,
  Search,
} from "lucide-react";
import { forwardRef, type CSSProperties } from "react";
import { cn } from "../../lib/cn";
import {
  ReaderToolbarActionButton,
  ReaderToolbarIconButton,
  ReaderToolbarRoot,
  ReaderToolbarSeparator,
} from "./plate-ui-adapter";
import { readerInlineFocusRing, readerTransitionFast } from "./interaction";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Kbd } from "@/components/primitives";

export type SelectionToolbarAction =
  | "ask"
  | "selectSentence"
  | "highlight"
  | "note"
  | "lookup"
  | "clear"
  | "feedback";

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
  feedback?: boolean;
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
  onFeedback?: (selectedText: string) => void;
  canToggleHighlightPalette?: boolean;
  highlightPaletteOpen?: boolean;
  onToggleHighlightPalette?: () => void;
}

export const defaultSelectionToolbarColorOptions: SelectionToolbarColorOption[] = [
  {
    value: "warm_yellow",
    label: "暖黄",
    swatchClassName: "bg-vocab-amber/75 ring-vocab-amber/25",
  },
  {
    value: "soft_mint",
    label: "薄荷",
    swatchClassName: "bg-emerald-200/80 ring-emerald-300/50",
  },
  {
    value: "soft_rose",
    label: "柔玫",
    swatchClassName: "bg-rose-200/80 ring-rose-300/50",
  },
];

function compactStatusLabel(
  statusMessage: string,
  statusKind: "saving" | "saved" | "error" | undefined,
) {
  if (statusKind === "saving") {
    return "保存中";
  }
  if (statusKind === "error") {
    return "保存失败";
  }
  if (/颜色/.test(statusMessage)) {
    return "颜色已更新";
  }
  if (/取消/.test(statusMessage)) {
    return "已取消高亮";
  }
  return "已高亮";
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
    onFeedback,
    canToggleHighlightPalette = false,
    onToggleHighlightPalette,
    highlightPaletteOpen,
  },
  ref,
) {
  const hasSelection = selectedText.trim().length > 0;
  const askComingSoon = Boolean(disabled?.ask);
  const askDisabled = !hasSelection || askComingSoon || !onAsk;
  const selectSentenceDisabled =
    selectionMode !== "text_range" || !hasSelection || Boolean(disabled?.selectSentence) || !onSelectSentence;
  const highlightDisabled = !hasSelection || Boolean(disabled?.highlight) || !onHighlight;
  const noteDisabled = !hasSelection || Boolean(disabled?.note) || !onNote;
  const lookupDisabled = !hasSelection || Boolean(disabled?.lookup) || !onLookup;
  const clearDisabled = !hasAnnotation || Boolean(disabled?.clear) || !onClearAnnotation;

  const shouldToggleHighlightPalette = hasHighlight && canToggleHighlightPalette;

  const handleHighlight = () => {
    if (!shouldToggleHighlightPalette) {
      const defaultOption =
        colorOptions.find((option) => option.value === activeColor && !option.disabled) ??
        colorOptions.find((option) => !option.disabled);
      if (defaultOption) {
        onHighlight?.(defaultOption.value, selectedText, defaultOption);
      }
    } else {
      onToggleHighlightPalette?.();
    }
  };

  const compactStatus = statusMessage ? compactStatusLabel(statusMessage, statusKind) : null;

  return (
    <div
      ref={ref}
      style={style}
      className={cn("w-max max-w-[calc(100vw-1rem)] text-ink", className)}
    >
      <TooltipProvider delayDuration={200}>
        <div className="relative flex flex-col items-center gap-2">
          {/* Floating Color Palette */}
          {hasHighlight && highlightPaletteOpen && (
            <div className="flex items-center gap-3 rounded-[10px] border border-border/60 bg-background/95 p-2 px-3 shadow-sm backdrop-blur-sm animate-in fade-in zoom-in-95 slide-in-from-bottom-2">
              {colorOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-label={`切换为${option.label}`}
                  disabled={Boolean(option.disabled)}
                  onClick={(e) => {
                    e.preventDefault();
                    onHighlight?.(option.value, selectedText, option);
                  }}
                  className={cn(
                    "h-4 w-4 rounded-[4px] ring-1 ring-inset ring-border/70 hover:ring-2 hover:ring-lens-blue/20",
                    readerInlineFocusRing,
                    readerTransitionFast,
                    "hover:scale-110",
                    option.swatchClassName,
                    activeColor === option.value ? "ring-ring ring-offset-2 ring-offset-background" : ""
                  )}
                />
              ))}
            </div>
          )}

          <ReaderToolbarRoot>
            {/* Highlight Action */}
            <Tooltip>
              <TooltipTrigger asChild>
                <ReaderToolbarIconButton
                  active={shouldToggleHighlightPalette || highlightPaletteOpen}
                  disabled={highlightDisabled}
                  onClick={handleHighlight}
                  aria-label={shouldToggleHighlightPalette ? "切换高亮颜色" : "高亮"}
                  className={cn(
                    statusKind === "saving" && "animate-pulse text-muted-foreground",
                    statusKind === "saved" &&
                      "bg-structure-green/10 text-structure-green hover:bg-structure-green/15 hover:text-structure-green",
                    statusKind === "error" &&
                      "bg-destructive/10 text-destructive hover:bg-destructive/15 hover:text-destructive",
                  )}
                >
                  <Highlighter aria-hidden="true" className="h-4 w-4" />
                </ReaderToolbarIconButton>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">
                <span className="inline-flex items-center gap-2">
                  <span>{shouldToggleHighlightPalette ? "更换高亮颜色" : "高亮"}</span>
                  <Kbd>H</Kbd>
                </span>
              </TooltipContent>
            </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={noteDisabled}
                active={hasNote}
                onClick={() => onNote?.(selectedText)}
                aria-label={hasNote ? "编辑笔记" : "新建笔记"}
              >
                <NotebookPen aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              <span className="inline-flex items-center gap-2">
                <span>{hasNote ? "编辑笔记" : "新建笔记"}</span>
                <Kbd>E</Kbd>
              </span>
            </TooltipContent>
          </Tooltip>

          <ReaderToolbarSeparator aria-hidden="true" />

          {/* Flattened Menu Items */}
          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarActionButton
                disabled={selectSentenceDisabled}
                onClick={() => onSelectSentence?.(selectedText)}
                aria-label="扩展到整句"
                className="px-2.5 text-xs font-semibold"
              >
                <Quote aria-hidden="true" className="h-4 w-4" />
                <span>整句</span>
              </ReaderToolbarActionButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">扩展到整句</TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={lookupDisabled}
                onClick={() => onLookup?.(selectedText)}
                aria-label="查词"
              >
                <Search aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">查词</TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={clearDisabled}
                onClick={onClearAnnotation}
                aria-label="取消高亮"
              >
                <Eraser aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">取消标注</TooltipContent>
          </Tooltip>
          
          <ReaderToolbarSeparator aria-hidden="true" />

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={askDisabled}
                onClick={() => onAsk?.(selectedText)}
                aria-label={askComingSoon ? "Ask Claread（稍后开放）" : "Ask Claread"}
                className="text-lens-blue/80 hover:border-lens-blue/20 hover:bg-transparent hover:text-lens-blue active:bg-transparent active:text-lens-blue"
              >
                <MessageSquare aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              {askComingSoon ? "Ask Claread (稍后开放)" : "Ask Claread"}
            </TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={!hasSelection || Boolean(disabled?.feedback) || !onFeedback}
                onClick={() => onFeedback?.(selectedText)}
                aria-label="反馈"
              >
                <Flag aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">反馈</TooltipContent>
          </Tooltip>

          {compactStatus ? (
            <>
              <ReaderToolbarSeparator aria-hidden="true" />
              <div
                aria-live="polite"
                className={cn(
                  "inline-flex min-h-8 items-center rounded-full px-2.5 text-[11px] font-medium tracking-[0.01em] motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-right-2 motion-safe:duration-200",
                  statusKind === "saving" &&
                    "bg-background/88 text-muted-foreground",
                  statusKind === "saved" &&
                    "bg-structure-green/10 text-structure-green",
                  statusKind === "error" &&
                    "bg-destructive/10 text-destructive",
                )}
              >
                {compactStatus}
              </div>
            </>
          ) : null}
        </ReaderToolbarRoot>
        </div>
      </TooltipProvider>
    </div>
  );
});
